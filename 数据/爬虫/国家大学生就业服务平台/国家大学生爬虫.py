#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国家大学生就业服务平台（NCSS）独立版批量采集脚本
================================================

特点：
- 不依赖 batch_crawler.py，不 import 任何项目内爬虫脚本；
- 仅依赖 skill_dictionary.csv 与 major_dictionary.csv 两个词典文件；
- 独立定义城市、岗位、输出字段、薪资/学历/经验解析、技能/专业提取、质量汇总；
- 使用官网 JSON 列表接口与 Playwright 详情页采集；
- 单线程串行，带请求间隔；
- 支持 SQLite 指纹去重、断点续爬、质量汇总、分析数据集导出；
- 支持由使用者在官网手工登录，并在本机专用浏览器资料目录中复用会话；
- 不自动填写账号、密码或验证码，不投递，不绕过访问控制；遇到验证/限流/登录失效会保存检查点。
- 可选启用人工恢复：遇到登录/验证中断时打开可见浏览器，等待使用者手工处理后重试当前页。

运行前准备：
1. 将本脚本、skill_dictionary.csv、major_dictionary.csv 放在同一目录；
2. 安装依赖：pip install playwright
3. 安装浏览器：playwright install chromium

常用运行示例：
python ncss_crawler_standalone.py --city 北京 --job 数据分析师 --max-pages 3 --headful
python ncss_crawler_standalone.py --max-pages 10 --interval 6 --detail-interval 1.5
python ncss_crawler_standalone.py --login-only
python ncss_crawler_standalone.py --auth-mode required --city 北京 --job 数据分析师 --max-pages 3
python ncss_crawler_standalone.py --login --auth-mode required --manual-recovery --city 上海 --job 数据分析师 --interval 8 --detail-interval 2

如果 NCSS 搜索 URL 参数变化，可覆盖搜索模板：
python ncss_crawler_standalone.py --search-url-template "https://www.ncss.cn/student/jobs/index.html?jobName={keyword_q}&areaCode={city_code}"

模板支持占位符：
{keyword}, {keyword_q}, {city}, {city_q}, {city_code}, {page}
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


# ============================================================
# 基础路径、版本、数据源配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.join(BASE_DIR, "国家大学生就业服务平台_爬取数据_独立版")
FINGERPRINT_DB = os.path.join(DATA_ROOT, "ncss_fingerprints_standalone.db")

CRAWLER_VERSION = "2026-06-23.ncss.standalone.10-streak"
CHECKPOINT_VERSION = 1
SOURCE_NAME = "国家大学生就业服务平台"
SOURCE_KEY = "ncss"

DEFAULT_SKILL_DICT = os.path.join(BASE_DIR, "skill_dictionary.csv")
DEFAULT_MAJOR_DICT = os.path.join(BASE_DIR, "major_dictionary.csv")

DEFAULT_SEARCH_URL_TEMPLATE = os.getenv(
    "NCSS_SEARCH_URL_TEMPLATE",
    "https://www.ncss.cn/student/jobs/index.html?jobName={keyword_q}&areaCode={city_code}",
)
DEFAULT_LIST_API_URL = "https://www.ncss.cn/student/jobs/jobslist/ajax/"
DEFAULT_LOGIN_URL = "https://www.ncss.cn/student/signin.html"
DEFAULT_SESSION_CHECK_URL = "https://www.ncss.cn/student/index.html"
SESSION_CHECK_URLS = [
    "https://www.ncss.cn/student/index.html",
    "https://www.ncss.cn/student/jobs/index.html",
    "https://job.ncss.cn/student/index.html",
    "https://job.ncss.cn/student/jobs/index.html",
]
DEFAULT_PROFILE_DIR = os.path.join(BASE_DIR, ".ncss_browser_profile")
AUTH_STATE_FILENAME = "storage_state.json"


# ============================================================
# 独立定义：采集城市与岗位
# ============================================================
CITIES: List[Dict[str, str]] = [
    {"name": "重庆", "code": "50", "query": "重庆"},
    {"name": "北京", "code": "11", "query": "北京"},
    {"name": "上海", "code": "31", "query": "上海"},
    {"name": "广州", "code": "440100", "query": "广州"},
    {"name": "深圳", "code": "440300", "query": "深圳"},
    {"name": "杭州", "code": "330100", "query": "杭州"},
    {"name": "南京", "code": "320100", "query": "南京"},
    {"name": "武汉", "code": "420100", "query": "武汉"},
    {"name": "成都", "code": "510100", "query": "成都"},
    {"name": "西安", "code": "610100", "query": "西安"},
]

CORE_JOB_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "keyword": "数据分析师",
        "display_name": "数据分析师",
        "aliases": ["数据分析", "数据分析师", "数据专员", "商业分析", "经营分析"],
    },
    {
        "keyword": "BI分析师",
        "display_name": "BI分析师",
        "aliases": ["BI分析", "BI分析师", "BI工程师", "BI开发", "报表开发", "报表分析", "数据可视化", "Tableau", "Power BI", "FineBI", "商业智能"],
    },
    {
        "keyword": "数据开发工程师",
        "display_name": "数据开发工程师",
        "aliases": ["数据开发", "数据开发工程师", "数据工程师", "数据平台开发"],
    },
    {
        "keyword": "大数据开发工程师",
        "display_name": "大数据开发工程师",
        "aliases": ["大数据开发", "大数据开发工程师", "大数据工程师", "大数据平台", "Spark", "Hive", "Flink", "Hadoop", "离线开发", "实时开发"],
    },
    {
        "keyword": "数据仓库工程师",
        "display_name": "数据仓库工程师",
        "aliases": ["数据仓库", "数据仓库工程师", "数据仓库开发", "数仓", "数仓开发", "数仓工程师", "数仓开发工程师", "ETL", "ETL开发", "数据建模", "数仓建模"],
    },
    {
        "keyword": "Python开发工程师",
        "display_name": "Python开发工程师",
        "aliases": ["Python开发", "Python工程师", "Python后端", "Python爬虫", "Python爬虫开发"],
    },
    {
        "keyword": "机器学习工程师",
        "display_name": "机器学习工程师",
        "aliases": ["机器学习", "机器学习工程师", "深度学习", "深度学习工程师", "AI工程师", "人工智能工程师", "模型训练", "模型算法"],
    },
    {
        "keyword": "算法工程师",
        "display_name": "算法工程师",
        "aliases": ["算法", "算法工程师", "算法开发", "算法研发", "推荐算法", "搜索算法", "NLP", "自然语言处理", "计算机视觉", "CV算法", "视觉算法"],
    },
]

CORE_JOBS: List[str] = [item["keyword"] for item in CORE_JOB_DEFINITIONS]
JOB_DEFINITION_MAP: Dict[str, Dict[str, Any]] = {item["keyword"]: item for item in CORE_JOB_DEFINITIONS}


# ============================================================
# 独立定义：输出字段规格
# key 为内部字段名，label 为 CSV 中文列名
# ============================================================
JOB_OUTPUT_FIELD_SPECS: List[Tuple[str, str]] = [
    ("record_no", "序号"),
    ("source", "数据源"),
    ("crawler_version", "爬虫版本"),
    ("skill_dict_ver", "技能词典版本"),
    ("major_dict_ver", "专业词典版本"),
    ("search_keyword", "搜索岗位"),
    ("search_city", "搜索城市"),
    ("search_city_code", "搜索城市编码"),
    ("source_job_id", "来源岗位ID"),
    ("source_url", "来源链接"),
    ("crawl_time", "采集时间"),
    ("fingerprint", "来源岗位指纹"),
    ("content_fingerprint", "内容指纹"),
    ("job_title", "岗位名称"),
    ("company_name", "公司名称"),
    ("city", "实际城市"),
    ("district", "区县"),
    ("salary_text", "薪资原文"),
    ("salary_min", "最低月薪"),
    ("salary_max", "最高月薪"),
    ("salary_months", "年薪月数"),
    ("education", "学历要求"),
    ("education_raw", "学历原文"),
    ("experience", "经验要求"),
    ("experience_raw", "经验原文"),
    ("experience_min_years", "最低经验年限"),
    ("experience_max_years", "最高经验年限"),
    ("experience_requirement_type", "经验要求类型"),
    ("job_type", "岗位类型"),
    ("industry", "行业"),
    ("company_size", "公司规模"),
    ("company_type", "公司性质"),
    ("publish_date", "发布日期"),
    ("publish_date_raw", "发布日期原文"),
    ("longitude", "经度"),
    ("latitude", "纬度"),
    ("platform_major_validation", "API隐藏专业标签核验状态"),
    ("major_candidates_raw", "正文专业候选原文"),
    ("major_candidates", "正文确认标准专业"),
    ("major_categories", "专业类别"),
    ("major_requirement_level", "专业要求级别"),
    ("major_evidence", "专业证据"),
    ("major_source", "专业判定来源"),
    ("major_decision_note", "专业判定说明"),
    ("job_description_raw", "岗位描述原文"),
    ("job_description", "岗位描述清洗文本"),
    ("requirement_text", "任职要求文本"),
    ("responsibility_text", "岗位职责文本"),
    ("job_tags", "官网岗位标签"),
    ("description_skill_candidates", "描述技能候选"),
    ("requirement_skill_candidates", "任职要求技能候选"),
    ("skill_candidates", "确认技能候选"),
    ("skill_categories", "技能类别"),
    ("skill_evidence", "技能证据"),
    ("skill_extraction_scope", "技能提取范围"),
    ("quality_flags", "数据质量标记"),
]

ANALYSIS_EXTRA_FIELD_SPECS: List[Tuple[str, str]] = [
    ("city_role_sample_size", "城市岗位样本量"),
    ("city_sample_level", "城市样本等级代码"),
    ("city_sample_label", "城市样本等级说明"),
    ("analysis_weight", "城市等权分析权重"),
]

QUALITY_OUTPUT_FIELD_SPECS: List[Tuple[str, str]] = [
    ("record_type", "记录类型"),
    ("job_name", "岗位名称"),
    ("keyword", "搜索岗位"),
    ("city", "搜索城市"),
    ("city_code", "城市编码"),
    ("status", "采集状态代码"),
    ("status_label", "采集状态说明"),
    ("passed", "是否正常完成"),
    ("valid_count", "有效岗位数"),
    ("raw_item_count", "API原始返回数"),
    ("raw_unique_count", "原始唯一岗位数"),
    ("sample_level", "样本等级代码"),
    ("sample_label", "样本等级说明"),
    ("source_total_count", "官网报告总数"),
    ("stop_reason", "停止原因"),
    ("last_page_attempted", "最后尝试页"),
    ("last_successful_page", "最后成功页"),
    ("zero_valid_streak", "结束时连续零有效页"),
    ("retryable", "是否待补采"),
    ("retry_count", "重试次数"),
    ("checkpoint_file", "检查点文件"),
    ("duplicate_count", "跳过重复数"),
    ("irrelevant_title_count", "标题不相关过滤数"),
    ("invalid_record_count", "无效记录过滤数"),
    ("parse_error_count", "解析错误数"),
    ("role_target", "岗位总体目标"),
    ("role_target_met", "岗位总体是否达标"),
    ("role_shortfall", "岗位总体缺口"),
    ("city_count", "城市数量"),
    ("city_counts", "各城市有效岗位数"),
]

LEGACY_NCSS_LABEL_TO_KEY: Dict[str, str] = {
    "数据来源": "source",
    "搜索关键词": "search_keyword",
    "搜索城市代码": "search_city_code",
    "岗位指纹": "fingerprint",
    "城市": "city",
    "薪资文本": "salary_text",
    "薪资下限": "salary_min",
    "薪资上限": "salary_max",
    "薪资月数": "salary_months",
    "经验下限年": "experience_min_years",
    "经验上限年": "experience_max_years",
    "工作类型": "job_type",
    "平台专业校验": "platform_major_validation",
    "专业候选原文": "major_candidates_raw",
    "标准专业候选": "major_candidates",
    "专业要求强度": "major_requirement_level",
    "专业来源": "major_source",
    "岗位要求文本": "requirement_text",
    "岗位标签": "job_tags",
    "要求技能候选": "requirement_skill_candidates",
    "标准技能候选": "skill_candidates",
    "质量标记": "quality_flags",
    "城市均衡权重": "city_balance_weight",
    "是否进入分析样本": "analysis_ready",
}
LABEL_TO_KEY: Dict[str, str] = dict(LEGACY_NCSS_LABEL_TO_KEY)
LABEL_TO_KEY.update({label: key for key, label in JOB_OUTPUT_FIELD_SPECS + ANALYSIS_EXTRA_FIELD_SPECS})


# ============================================================
# 独立配置
# ============================================================
NCSS_CONFIG: Dict[str, Any] = {
    "page_size": 10,
    "max_pages": 50,
    "max_jobs_per_combo": 0,              # 0 表示不限制每个城市×岗位有效数量
    "request_interval": 3.0,             # 翻页/组合间隔；触发限流时再调大
    "detail_interval": 0.8,              # 同一页打开详情的间隔
    "page_load_timeout": 30,
    "headless": True,
    "viewport_width": 1920,
    "viewport_height": 1080,
    "filter_title": True,
    "city_descriptive_min": 30,
    "city_analysis_min": 50,
    "role_valid_target": 500,
    "resume_completed": True,
    "fresh": False,
    "max_page_retries": 3,
    "max_detail_retries": 2,
    "max_combo_retries": 2,
    "max_zero_valid_pages_per_combo": 5,
    "search_url_template": DEFAULT_SEARCH_URL_TEMPLATE,
    "list_api_url": DEFAULT_LIST_API_URL,
    "auth_mode": "auto",                # auto: 复用会话；required: 必须已登录；off: 临时无痕会话
    "interactive_login": False,
    "login_only": False,
    "login_timeout": 300,
    "manual_recovery": False,
    "manual_recovery_timeout": 300,
    "manual_recovery_attempts": 1,
    "profile_dir": DEFAULT_PROFILE_DIR,
}

TERMINAL_STATUSES = {
    "source_exhausted",
    "max_pages_reached",
    "optional_cap_reached",
    "relevance_exhausted",
    "no_result",
}

VALIDATION_STATUS_LABELS = {
    "source_exhausted": "官网列表已采集完毕",
    "max_pages_reached": "达到最大翻页安全上限",
    "optional_cap_reached": "达到可选单组合有效岗位上限",
    "relevance_exhausted": "连续多页无有效岗位，跳过当前组合",
    "no_result": "未发现岗位卡片",
    "verification_interrupted": "网站验证或登录中断，等待人工确认",
    "authentication_required": "登录会话不存在或已经失效",
    "rate_limited": "网站请求频率受限，已停止本批次",
    "browser_closed": "浏览器窗口或会话已关闭，请重新运行续爬命令",
    "page_retry_exhausted": "页面加载或解析失败，等待补采",
    "collection_incomplete": "采集未完成",
    "pending_retry": "等待重试",
    "in_progress": "采集中",
}

SPAM_KEYWORDS = [
    "培训", "包就业", "学费", "招生", "零基础", "实训", "招生老师", "课程顾问",
    "电话销售", "保险", "房产", "置业顾问", "客服专员", "地推",
]

SALARY_PATTERN = re.compile(
    r"(?:(?:\d+(?:\.\d+)?\s*[-~～—–至到]\s*\d+(?:\.\d+)?)|(?:\d+(?:\.\d+)?))\s*"
    r"(?:[kKＫｋ]|千|万|元|块)?(?:\s*[-~～—–至到]\s*\d+(?:\.\d+)?\s*(?:[kKＫｋ]|千|万|元|块))?"
    r"\s*(?:/\s*(?:月|年|天|日)|每月|每年|每天|月薪|年薪|日薪|月|年|天|日)?(?:\s*[·・]\s*\d+\s*薪)?"
)
DATE_PATTERN = re.compile(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}")
DETAIL_ID_PATTERN = re.compile(r"/student/jobs/([^/]+)/detail\.html")

REQUIREMENT_HEADINGS = [
    "任职要求", "岗位要求", "职位要求", "任职资格", "资格要求", "专业要求", "招聘要求", "岗位条件", "能力要求",
]
RESPONSIBILITY_HEADINGS = [
    "岗位职责", "工作职责", "职位描述", "工作内容", "职责描述", "岗位描述", "工作任务",
]
SECTION_END_HEADINGS = [
    "福利待遇", "薪资福利", "联系方式", "公司简介", "企业简介", "工作地点", "招聘人数", "投递方式",
] + REQUIREMENT_HEADINGS + RESPONSIBILITY_HEADINGS


# ============================================================
# 日志与通用工具
# ============================================================
def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def normalize_job_text(text: str) -> str:
    """清洗岗位文本，保留换行结构，但压缩多余空白。"""
    value = html.unescape(text or "")
    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return value.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_job_text(text)).lower()


def atomic_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def load_checkpoint(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
            return None
        return payload
    except Exception:
        return None


def flatten_output_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        return "|".join(str(item).replace("|", "/") for item in value)
    if isinstance(value, dict):
        return " || ".join(
            f"{key}=>{str(item).replace('||', '/').strip()}"
            for key, item in value.items()
        )
    return value


def write_csv_by_specs(path: str, rows: List[Dict[str, Any]], specs: List[Tuple[str, str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = [label for _, label in specs]
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, 1):
            output = {label: flatten_output_value(row.get(key, "")) for key, label in specs}
            if "序号" in output:
                output["序号"] = row.get("record_no") or index
            writer.writerow(output)
    os.replace(temp_path, path)


def write_job_csv(path: str, rows: List[Dict[str, Any]], include_analysis_fields: bool = False) -> None:
    specs = list(JOB_OUTPUT_FIELD_SPECS)
    if include_analysis_fields:
        specs.extend(ANALYSIS_EXTRA_FIELD_SPECS)
    write_csv_by_specs(path, rows, specs)


def read_job_csv(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row: Dict[str, Any] = {}
            for label, value in raw.items():
                key = LABEL_TO_KEY.get(label, label)
                row[key] = value
            rows.append(row)
    return rows


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def extract_detail_id(url: str) -> str:
    match = DETAIL_ID_PATTERN.search(url or "")
    return match.group(1) if match else ""


def compute_source_fingerprint(job_id: str, url: str = "") -> str:
    raw_key = (job_id or "").strip() or canonicalize_url(url)
    if not raw_key:
        return ""
    return hashlib.sha256(f"{SOURCE_KEY}|{raw_key}".encode("utf-8")).hexdigest()[:16]


def compute_content_fingerprint(company_name: str, description: str) -> str:
    normalized = compact_text(description or "")
    raw = f"{company_name.strip()}|{normalized[:300]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def find_first(patterns: Iterable[str], text: str, default: str = "") -> str:
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.I | re.S)
        if match:
            value = match.group(1) if match.groups() else match.group(0)
            value = re.sub(r"\s+", " ", value).strip(" ：:，,。\n\t")
            if value:
                return value
    return default


def split_sentences(text: str) -> List[str]:
    value = normalize_job_text(text)
    parts = re.split(r"(?<=[。；;！!？?\n])", value)
    return [p.strip() for p in parts if p.strip()]


def alias_pattern(alias: str) -> str:
    if not alias:
        return r"(?!x)x"
    alias = alias.strip()
    if not alias:
        return r"(?!x)x"
    if re.fullmatch(r"[A-Za-z0-9_+.#\-/ ]+", alias):
        return r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])"
    return re.escape(alias)


def text_contains_alias(text: str, alias: str) -> bool:
    return re.search(alias_pattern(alias), text or "", flags=re.I) is not None


def is_negated_skill_mention(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 16):start]
    suffix = text[end:min(len(text), end + 16)]
    prefix_pattern = re.compile(
        r"(?:不要求|不需要|不必|无需|无须)(?:具备|掌握|熟悉|了解|使用|会)?[\s、，,:：]*$"
    )
    suffix_pattern = re.compile(r"^[\s、，,:：]*(?:不是必需|非必需|不作要求|不做要求)")
    return bool(prefix_pattern.search(prefix) or suffix_pattern.search(suffix))


def first_evidence(text: str, aliases: Sequence[str], max_len: int = 120) -> str:
    for sentence in split_sentences(text):
        if any(text_contains_alias(sentence, alias) for alias in aliases):
            return sentence[:max_len]
    return ""


def extract_major_evidence_text(text: str) -> str:
    evidence: List[str] = []
    contexts = (
        "学历", "本科", "大专", "硕士", "博士", "毕业", "相关专业",
        "专业背景", "专业方向", "专业类别", "类专业", "专业不限", "不限专业", "优先",
    )
    for sentence in re.split(r"[\n。；;]", normalize_job_text(text)):
        value = sentence.strip()
        if value and "专业" in value and any(token in value for token in contexts):
            if value not in evidence:
                evidence.append(value)
    return " || ".join(evidence)


def classify_access_issue(page_text: str = "", url: str = "", status_code: int = 0) -> str:
    """把访问异常分型，防止对登录失效、验证码或 429 做无意义重试。"""
    value = (page_text or "")[:3000].lower()
    lowered_url = (url or "").lower()
    if status_code == 429 or any(token in value for token in ("访问过于频繁", "请求过于频繁", "操作频繁")):
        return "rate_limited"
    login_url_tokens = ("/signin", "/login", "account.chsi.com.cn")
    login_text_tokens = ("请先登录", "登录后查看", "求职者登录/注册", "登录已失效", "重新登录")
    if status_code == 401 or any(token in lowered_url for token in login_url_tokens):
        return "authentication_required"
    if any(token in value for token in login_text_tokens):
        return "authentication_required"
    if status_code == 403:
        return "verification_required"
    verification_tokens = ("验证码", "安全验证", "人机验证", "waf", "forbidden")
    if any(token in value or token in lowered_url for token in verification_tokens):
        return "verification_required"
    return ""


def is_browser_closed_error(error: BaseException | str) -> bool:
    message = str(error).lower()
    return "targetclosederror" in message or "target page, context or browser has been closed" in message


def looks_blocked(page_text: str, url: str = "") -> bool:
    return bool(classify_access_issue(page_text, url))


def is_authenticated_page(
    page_text: str,
    url: str = "",
    signin_link_visible: bool = False,
    authenticated_nav_visible: bool = False,
) -> bool:
    """根据官网可见导航判断求职者会话；不读取、不记录任何 Cookie 值。"""
    if classify_access_issue("", url) == "authentication_required":
        return False
    value = (page_text or "")[:5000]
    if signin_link_visible:
        return False
    if authenticated_nav_visible:
        return True
    authenticated_markers = ("退出登录", "个人中心", "我的简历", "求职中心", "账号设置")
    return any(marker in value for marker in authenticated_markers)


# ============================================================
# 词典读取与技能/专业提取
# ============================================================
class DictionaryStore:
    def __init__(self, skill_path: str, major_path: str):
        self.skill_path = skill_path
        self.major_path = major_path
        self.skill_entries: List[Dict[str, Any]] = []
        self.major_entries: List[Dict[str, Any]] = []
        self.skill_category: Dict[str, str] = {}
        self.major_category: Dict[str, str] = {}
        self.major_alias_map: Dict[str, str] = {}
        self.skill_dict_ver = ""
        self.major_dict_ver = ""
        self.load()

    @staticmethod
    def _load_entries(path: str, dict_name: str) -> Tuple[List[Dict[str, Any]], str]:
        if not os.path.exists(path):
            raise SystemExit(f"缺少 {dict_name}：{path}")
        entries: List[Dict[str, Any]] = []
        versions: List[str] = []
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"canonical_name", "aliases", "category", "version"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise SystemExit(f"{dict_name} 缺少字段：{', '.join(sorted(missing))}")
            for row in reader:
                canonical = (row.get("canonical_name") or "").strip()
                if not canonical:
                    continue
                aliases = [x.strip() for x in (row.get("aliases") or "").split("|") if x.strip()]
                if canonical not in aliases:
                    aliases.insert(0, canonical)
                category = (row.get("category") or "未分类").strip() or "未分类"
                version = (row.get("version") or "").strip()
                if version:
                    versions.append(version)
                entries.append(
                    {
                        "canonical_name": canonical,
                        "aliases": aliases,
                        "category": category,
                        "version": version,
                    }
                )
        declared_version = ";".join(sorted(set(versions))) if versions else "unknown"
        with open(path, "rb") as source:
            content_digest = hashlib.sha256(source.read()).hexdigest()[:8]
        version_label = f"{declared_version}+sha256:{content_digest}"
        return entries, version_label

    def load(self) -> None:
        self.skill_entries, self.skill_dict_ver = self._load_entries(self.skill_path, "技能词典")
        self.major_entries, self.major_dict_ver = self._load_entries(self.major_path, "专业词典")
        self.skill_category = {entry["canonical_name"]: entry["category"] for entry in self.skill_entries}
        self.major_category = {entry["canonical_name"]: entry["category"] for entry in self.major_entries}
        self.major_alias_map = {
            alias.casefold(): entry["canonical_name"]
            for entry in self.major_entries
            for alias in entry["aliases"]
        }

    def extract_skills(self, text: str) -> Tuple[List[str], Dict[str, str]]:
        found: List[str] = []
        evidence: Dict[str, str] = {}
        for entry in self.skill_entries:
            aliases = entry["aliases"]
            valid_match = None
            for alias in aliases:
                for match in re.finditer(alias_pattern(alias), text or "", flags=re.I):
                    if not is_negated_skill_mention(text or "", match.start(), match.end()):
                        valid_match = match
                        break
                if valid_match:
                    break
            name = entry["canonical_name"]
            if valid_match and name not in found:
                found.append(name)
                evidence[name] = first_evidence(text, aliases)
        return found, evidence

    def extract_major_raw(self, text: str) -> Tuple[List[str], str]:
        found: List[str] = []
        evidence = extract_major_evidence_text(text)
        for entry in self.major_entries:
            for alias in entry["aliases"]:
                match = re.search(alias_pattern(alias), evidence, flags=re.I)
                if match:
                    raw_value = match.group(0)
                    if raw_value not in found:
                        found.append(raw_value)
                    break
        return found, evidence

    def normalize_majors(self, majors: Sequence[str]) -> List[str]:
        normalized: List[str] = []
        for item in majors:
            canonical = self.major_alias_map.get(str(item).casefold(), str(item))
            if canonical in self.major_category and canonical not in normalized:
                normalized.append(canonical)
        return normalized


def classify_major_requirement(text: str, majors: Sequence[str]) -> str:
    evidence = extract_major_evidence_text(text)
    if not evidence:
        return "未说明"
    unlimited = "专业不限" in evidence or "不限专业" in evidence or "不限制专业" in evidence
    preferred = any(token in evidence for token in ("优先", "更佳", "加分"))
    if unlimited and preferred:
        return "不限_相关专业优先"
    if unlimited:
        return "不限"
    if preferred:
        return "优先"
    return "要求" if majors else "未说明"


# ============================================================
# 字段解析：薪资、学历、经验、城市、公司、行业等
# ============================================================
def extract_salary_text(text: str) -> str:
    value = normalize_job_text(text).replace("Ｋ", "K").replace("ｋ", "k")
    if "面议" in value or "薪资面议" in value:
        return "薪资面议"
    candidates = []
    for match in SALARY_PATTERN.finditer(value):
        item = re.sub(r"\s+", "", match.group(0))
        if not item:
            continue
        has_salary_unit = re.search(r"[kK千万元块]", item) is not None
        has_salary_period = re.search(r"(?:薪|/(?:月|年|天|日)|每(?:月|年|天)|月薪|年薪|日薪)", item) is not None
        if re.search(r"\d", item) and (has_salary_unit or has_salary_period):
            candidates.append(item.replace("块", "元"))
    if not candidates:
        return ""
    # 优先选择看起来最完整的薪资片段
    candidates.sort(key=lambda x: (len(x), bool(re.search(r"[-~～—–至到]", x))), reverse=True)
    return candidates[0]


def parse_salary(salary_text: str) -> Tuple[Any, Any]:
    """把薪资估算为月薪区间。无法识别时返回空字符串。"""
    text = (salary_text or "").replace("Ｋ", "K").replace("ｋ", "k")
    if not text or "面议" in text:
        return "", ""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return "", ""
    if len(nums) >= 2 and re.search(r"[-~～—–至到]", text):
        low, high = nums[0], nums[1]
    else:
        low = high = nums[0]

    unit_multiplier = 1.0
    if re.search(r"[kK千]", text):
        unit_multiplier = 1000.0
    elif "万" in text:
        unit_multiplier = 10000.0
    elif "元" in text or "块" in text:
        unit_multiplier = 1.0
    else:
        # 无单位但数值较小的薪资，常见为 K
        if high <= 200:
            unit_multiplier = 1000.0

    low *= unit_multiplier
    high *= unit_multiplier

    if re.search(r"(/年|每年|年薪|[^\d]年$)", text):
        low /= 12.0
        high /= 12.0
    elif re.search(r"(/天|/日|每天|日薪|天$|日$)", text):
        low *= 22
        high *= 22

    return int(round(low)), int(round(high))


def parse_salary_months(salary_text: str) -> int:
    match = re.search(r"[·・]\s*(\d+)\s*薪", salary_text or "")
    if match:
        return int(match.group(1))
    return 12


def parse_education(text: str) -> str:
    value = normalize_job_text(text)
    if not value:
        return ""
    rules = [
        (r"(学历不限|不限学历|无学历要求)", "学历不限"),
        (r"博士", "博士"),
        (r"硕士|研究生", "硕士"),
        (r"本科", "本科"),
        (r"大专|专科|高职", "大专"),
        (r"中专|中技", "中专"),
        (r"高中", "高中"),
    ]
    for pattern, label in rules:
        if re.search(pattern, value):
            return label
    return ""


def parse_experience_details(text: str) -> Tuple[str, Any, Any, str]:
    value = normalize_job_text(text)
    if not value:
        return "", None, None, "未说明"
    if re.search(r"(经验不限|不限经验|工作经验不限|无需经验|无经验要求)", value):
        return "经验不限", 0, None, "不限"
    if re.search(r"(应届生|应届毕业生|校招|毕业生)", value):
        return "应届生", 0, 0, "应届"
    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:年)?\s*[-~～—–至到]\s*(\d+(?:\.\d+)?)\s*年",
        value,
    )
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        low_value = int(low) if low.is_integer() else low
        high_value = int(high) if high.is_integer() else high
        return f"{low:g}-{high:g}年", low_value, high_value, "区间"
    min_match = re.search(r"(\d+(?:\.\d+)?)\s*年\s*(?:以上|及以上|\+)", value)
    if min_match:
        low = float(min_match.group(1))
        low_value = int(low) if low.is_integer() else low
        return f"{low:g}年以上", low_value, None, "下限"
    any_year = re.search(r"(\d+(?:\.\d+)?)\s*年", value)
    if any_year:
        low = float(any_year.group(1))
        low_value = int(low) if low.is_integer() else low
        return f"{low:g}年", low_value, low_value, "精确"
    return value, None, None, "其他"


def parse_epoch_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp).astimezone().date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def extract_publish_date(text: str) -> str:
    match = DATE_PATTERN.search(text or "")
    if not match:
        return ""
    return match.group(0).replace("年", "-").replace("月", "-").replace("/", "-").rstrip("日")


def extract_city_and_district(text: str, expected_city: str) -> Tuple[str, str]:
    """从官网独立位置标签解析城市、区县；不扫描公司名或岗位正文。"""
    value = re.sub(r"\s+", "", normalize_job_text(text))
    city_name = (expected_city or "").removesuffix("市")
    if not value:
        return city_name, ""

    if not city_name or city_name not in value:
        for city in CITIES:
            candidate = city["name"].removesuffix("市")
            if candidate in value:
                city_name = candidate
                break
    if not city_name or city_name not in value:
        return (expected_city or "").removesuffix("市"), ""

    remainder = value.split(city_name, 1)[1]
    remainder = re.sub(r"^市", "", remainder)
    if remainder in {"", "市辖区", "辖区", "市区", "城区", "全市", "县"}:
        return city_name, ""

    district_match = re.match(
        r"([^省市，,。；;：:]{1,20}?(?:自治县|高新区|开发区|新区|区|县|市|镇))",
        remainder,
    )
    district = district_match.group(1) if district_match else ""
    if district in {"市辖区", "辖区", "市区", "城区", "全市", "县"}:
        district = ""
    return city_name, district


def extract_company_type(text: str) -> str:
    return find_first(
        [
            r"公司性质\s*[：:]\s*([^\n]+)",
            r"企业性质\s*[：:]\s*([^\n]+)",
            r"(国有企业|央企|事业单位|民营企业|私营企业|外资企业|合资企业|上市公司|股份制企业|其他)",
        ],
        text,
    )


def extract_company_size(text: str) -> str:
    return find_first(
        [
            r"公司规模\s*[：:]\s*([^\n]+)",
            r"企业规模\s*[：:]\s*([^\n]+)",
            r"(\d+\s*[-~～—–至到]\s*\d+\s*人|\d+\s*人以上|少于\s*\d+\s*人)",
        ],
        text,
    )


def extract_industry(text: str) -> str:
    return find_first(
        [
            r"所属行业\s*[：:]\s*([^\n]+)",
            r"涉及领域\s*[：:]\s*([^\n]+)",
            r"行业\s*[：:]\s*([^\n]+)",
        ],
        text,
    )


def extract_recruit_count(text: str) -> str:
    return find_first([r"招聘\s*(?:人数)?\s*[：:]?\s*(\d+\s*人)", r"招\s*(\d+\s*人)"], text)


def extract_job_type(text: str) -> str:
    return find_first([r"(全职|实习|兼职|校招|社招)"], text, "全职")


def extract_section(text: str, headings: Sequence[str]) -> str:
    value = normalize_job_text(text)
    if not value:
        return ""
    heading_pattern = "|".join(re.escape(h) for h in headings)
    start = re.search(rf"(?:^|\n|[。；;])\s*({heading_pattern})\s*[：:]?", value)
    if not start:
        return ""
    section_start = start.end()
    remaining = value[section_start:]
    end_candidates = [h for h in SECTION_END_HEADINGS if h not in headings]
    if end_candidates:
        end_pattern = "|".join(re.escape(h) for h in end_candidates)
        end = re.search(rf"(?:\n|[。；;])\s*(?:{end_pattern})\s*[：:]?", remaining)
        if end:
            remaining = remaining[: end.start()]
    return normalize_job_text(remaining).strip(" ：:")


def extract_responsibility_text(text: str) -> str:
    result = extract_section(text, RESPONSIBILITY_HEADINGS)
    if result:
        return result
    # 有些平台把职位描述整体作为职责，取正文前半部分作为兜底
    value = normalize_job_text(text)
    if not value:
        return ""
    requirement = extract_section(value, REQUIREMENT_HEADINGS)
    if requirement and requirement in value:
        return normalize_job_text(value.split(requirement, 1)[0])[:800]
    return value[:800]


def is_job_title_relevant(keyword: str, title: str) -> bool:
    if not title:
        return False
    definition = JOB_DEFINITION_MAP.get(keyword, {})
    aliases = definition.get("aliases") or [keyword]
    return any(text_contains_alias(title, alias) for alias in aliases)


def get_job_display_name(keyword: str) -> str:
    return JOB_DEFINITION_MAP.get(keyword, {}).get("display_name", keyword)


# ============================================================
# 样本质量与分析数据处理
# ============================================================
def classify_city_sample(
    count: int,
    descriptive_min: int = 30,
    analysis_min: int = 50,
) -> Dict[str, Any]:
    """使用与前程无忧一致的城市×岗位样本等级。"""
    if count < descriptive_min:
        level = "descriptive_only"
        label = "样本较少，仅作描述性分析"
    elif count < analysis_min:
        level = "limited"
        label = "可以分析，但需说明样本量限制"
    else:
        level = "sufficient"
        label = "适合一般城市特征分析"
    return {
        "sample_level": level,
        "sample_label": label,
        "valid_count": count,
    }


def summarize_role_samples(combo_results: List[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for result in combo_results:
        grouped[result.get("keyword", "")].append(result)
    summaries: List[Dict[str, Any]] = []
    for keyword, results in grouped.items():
        city_counts = {item.get("city", ""): int(item.get("parsed_count") or 0) for item in results}
        total = sum(city_counts.values())
        summaries.append(
            {
                "keyword": keyword,
                "job_name": results[0].get("job_name", keyword) if results else keyword,
                "target": target,
                "valid_total": total,
                "target_met": total >= target,
                "shortfall": max(0, target - total),
                "city_count": len([c for c, n in city_counts.items() if c and n > 0]),
                "city_counts": city_counts,
            }
        )
    return summaries


def add_city_balance_weights(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """保留全部岗位，并让同一岗位下每个城市的权重总和均为1。"""
    stratum_counts: Dict[Tuple[str, str], int] = {}
    for row in rows:
        key = (row.get("search_keyword", ""), row.get("search_city", ""))
        stratum_counts[key] = stratum_counts.get(key, 0) + 1

    weighted: List[Dict[str, Any]] = []
    for row in rows:
        key = (row.get("search_keyword", ""), row.get("search_city", ""))
        count = stratum_counts[key]
        sample = classify_city_sample(count)
        weighted.append({
            **row,
            "city_role_sample_size": count,
            "city_sample_level": sample["sample_level"],
            "city_sample_label": sample["sample_label"],
            "analysis_weight": 1.0 / count,
        })
    return weighted


# ============================================================
# SQLite 指纹与进度
# ============================================================
def init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(FINGERPRINT_DB), exist_ok=True)
    conn = sqlite3.connect(FINGERPRINT_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fingerprints (
            fp TEXT PRIMARY KEY,
            source_job_id TEXT,
            job_title TEXT,
            company_name TEXT,
            city TEXT,
            keyword TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS content_fingerprints (
            cfp TEXT PRIMARY KEY,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS crawl_progress (
            city_code TEXT,
            keyword TEXT,
            last_page INTEGER,
            total_collected INTEGER,
            raw_unique_count INTEGER DEFAULT 0,
            next_page INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            checkpoint_file TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            updated_at TEXT,
            PRIMARY KEY (city_code, keyword)
        )"""
    )
    conn.commit()
    return conn


def load_seen_fingerprints(conn: sqlite3.Connection) -> Tuple[set[str], set[str]]:
    fps = {row[0] for row in conn.execute("SELECT fp FROM fingerprints")}
    cfps = {row[0] for row in conn.execute("SELECT cfp FROM content_fingerprints")}
    return fps, cfps


def save_fingerprint(conn: sqlite3.Connection, fp: str, cfp: str, job: Dict[str, Any]) -> None:
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO fingerprints VALUES (?,?,?,?,?,?,?)",
        (
            fp,
            job.get("source_job_id", ""),
            job.get("job_title", ""),
            job.get("company_name", ""),
            job.get("city", ""),
            job.get("search_keyword", ""),
            now,
        ),
    )
    if cfp:
        conn.execute("INSERT OR IGNORE INTO content_fingerprints VALUES (?,?)", (cfp, now))
    conn.commit()


def save_progress(
    conn: sqlite3.Connection,
    city_code: str,
    keyword: str,
    last_page: int,
    collected: int,
    raw_unique_count: int = 0,
    next_page: int = 1,
    status: str = "in_progress",
    checkpoint_file: str = "",
    retry_count: int = 0,
    last_error: str = "",
) -> None:
    conn.execute(
        """INSERT INTO crawl_progress (
               city_code, keyword, last_page, total_collected, raw_unique_count,
               next_page, status, checkpoint_file, retry_count, last_error, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(city_code, keyword) DO UPDATE SET
               last_page=excluded.last_page,
               total_collected=excluded.total_collected,
               raw_unique_count=excluded.raw_unique_count,
               next_page=excluded.next_page,
               status=excluded.status,
               checkpoint_file=excluded.checkpoint_file,
               retry_count=excluded.retry_count,
               last_error=excluded.last_error,
               updated_at=excluded.updated_at""",
        (
            city_code,
            keyword,
            last_page,
            collected,
            raw_unique_count,
            next_page,
            status,
            checkpoint_file,
            retry_count,
            last_error,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def load_progress(conn: sqlite3.Connection, city_code: str, keyword: str) -> Optional[Dict[str, Any]]:
    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM crawl_progress WHERE city_code=? AND keyword=?",
            (city_code, keyword),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.row_factory = previous_factory


# ============================================================
# 爬虫主体
# ============================================================
class StandaloneNCSSCrawler:
    def __init__(self, db_conn: sqlite3.Connection, dictionaries: DictionaryStore):
        self.conn = db_conn
        self.dictionaries = dictionaries
        if NCSS_CONFIG.get("fresh"):
            self.seen_fps, self.seen_cfps = set(), set()
        else:
            self.seen_fps, self.seen_cfps = load_seen_fingerprints(db_conn)
        self.current_city: Dict[str, str] = {}
        self.current_keyword = ""
        self.batch_start = datetime.now()
        self.combo_results: List[Dict[str, Any]] = []
        self.total_collected = 0

        self.collected_jobs: List[Dict[str, Any]] = []
        self.raw_items: List[Dict[str, Any]] = []
        self.combo_seen_fps: set[str] = set()
        self.combo_seen_cfps: set[str] = set()
        self.total_skipped_dup = 0
        self.total_filtered_irrelevant = 0
        self.total_filtered_invalid = 0
        self.total_parse_errors = 0

    def build_search_url(self, page_num: int) -> str:
        template = NCSS_CONFIG["search_url_template"]
        city_name = self.current_city["name"]
        mapping = {
            "keyword": self.current_keyword,
            "keyword_q": quote(self.current_keyword),
            "city": city_name,
            "city_q": quote(city_name),
            "city_code": self.current_city.get("code", ""),
            "page": str(page_num),
        }
        return template.format(**mapping)

    def build_list_api_url(self, page_num: int) -> str:
        params = {
            "jobName": self.current_keyword,
            "areaCode": self.current_city.get("code", ""),
            "offset": page_num,
            "limit": int(NCSS_CONFIG["page_size"]),
        }
        return f"{NCSS_CONFIG['list_api_url']}?{urlencode(params)}"

    def fetch_list_page(self, browser_context, page_num: int) -> Tuple[List[Dict[str, Any]], str, int]:
        """通过官网 JSON 接口读取一页职位，复用浏览器上下文中的登录会话。"""
        api_url = self.build_list_api_url(page_num)
        last_error = ""
        for attempt in range(1, int(NCSS_CONFIG["max_page_retries"]) + 1):
            response = None
            try:
                response = browser_context.request.get(
                    api_url,
                    headers={"Referer": self.build_search_url(1)},
                    timeout=NCSS_CONFIG["page_load_timeout"] * 1000,
                )
                access_issue = classify_access_issue("", response.url, response.status)
                if access_issue:
                    return [], access_issue, 0
                if not response.ok:
                    last_error = f"HTTP {response.status}"
                    raise RuntimeError(last_error)
                content_type = (response.headers.get("content-type") or "").lower()
                if "json" not in content_type:
                    response_text = response.text()
                    access_issue = classify_access_issue(response_text, response.url)
                    if access_issue:
                        return [], access_issue, 0
                    raise RuntimeError(f"unexpected_content_type:{content_type or 'unknown'}")
                payload = response.json()
                if not payload.get("flag"):
                    message = json.dumps(payload.get("global") or payload.get("errors") or "", ensure_ascii=False)
                    access_issue = classify_access_issue(message, api_url)
                    if access_issue:
                        return [], access_issue, 0
                    last_error = message or "list_api_flag_false"
                    raise RuntimeError(last_error)

                data = payload.get("data") or {}
                pagination = data.get("pagenation") or {}
                source_total = int(pagination.get("count") or 0)
                cards: List[Dict[str, Any]] = []
                for index, item in enumerate(data.get("list") or [], 1):
                    job_id = str(item.get("jobId") or "").strip()
                    detail_url = (
                        urljoin("https://www.ncss.cn", f"/student/jobs/{job_id}/detail.html")
                        if job_id else ""
                    )
                    structured_text = "\n".join(
                        str(value).strip()
                        for value in (
                            item.get("jobName"), item.get("recName"), item.get("areaCodeName"),
                            item.get("degreeName"), item.get("recScale"), item.get("recProperty"),
                            item.get("major"), item.get("recTags"),
                        )
                        if value not in (None, "")
                    )
                    cards.append({
                        "index": index,
                        "source_job_id": job_id,
                        "job_title": normalize_job_text(item.get("jobName") or ""),
                        "company_name": normalize_job_text(item.get("recName") or ""),
                        "detail_url": detail_url,
                        "card_text": normalize_job_text(structured_text),
                        "api_item": item,
                    })
                return cards, "ok", source_total
            except Exception as exc:
                if is_browser_closed_error(exc):
                    log(f"  浏览器窗口或会话已关闭，无法继续请求第 {page_num} 页。")
                    return [], "browser_closed", 0
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < int(NCSS_CONFIG["max_page_retries"]):
                    log(f"  列表接口第 {page_num} 页失败，第 {attempt} 次重试: {last_error}")
                    time.sleep(min(float(NCSS_CONFIG["request_interval"]), 5.0) * attempt)
            finally:
                if response is not None:
                    try:
                        response.dispose()
                    except Exception:
                        pass
        log(f"  ✗ 列表接口第 {page_num} 页重试耗尽: {last_error}")
        return [], "page_retry_exhausted", 0

    def fetch_list_page_with_recovery(self, browser_context, page_num: int) -> Tuple[List[Dict[str, Any]], str, int]:
        """列表接口遇到登录/验证中断时，可选等待人工处理后重试当前页。"""
        cards, page_status, source_total = self.fetch_list_page(browser_context, page_num)
        recoverable_statuses = {"verification_required", "authentication_required"}
        attempts = int(NCSS_CONFIG.get("manual_recovery_attempts") or 0)
        while page_status in recoverable_statuses and attempts > 0:
            attempts -= 1
            recovered = self._wait_for_manual_recovery(
                browser_context,
                page_status,
                self.build_search_url(page_num),
                f"列表第 {page_num} 页",
            )
            if not recovered:
                break
            cards, page_status, source_total = self.fetch_list_page(browser_context, page_num)
        return cards, page_status, source_total

    @staticmethod
    def _guess_title(text: str) -> str:
        lines = [x.strip() for x in normalize_job_text(text).split("\n") if x.strip()]
        noise = ["投递", "收藏", "分享", "申请", "查看"]
        for line in lines[:6]:
            if len(line) <= 45 and not any(k in line for k in noise):
                return line
        return lines[0] if lines else ""

    @staticmethod
    def _guess_company(text: str) -> str:
        lines = [x.strip() for x in normalize_job_text(text).split("\n") if x.strip()]
        suffixes = ["有限公司", "公司", "集团", "研究院", "中心", "学校", "大学", "事务所", "银行"]
        for line in lines[:10]:
            if any(suffix in line for suffix in suffixes):
                return line
        return ""

    def open_detail_page(self, context, detail_url: str) -> Dict[str, Any]:
        if not detail_url:
            return {"detail_text": "", "detail_title": "", "access_issue": "", "error": "missing_detail_url"}

        last_error = "detail_content_missing"
        for attempt in range(1, int(NCSS_CONFIG["max_detail_retries"]) + 1):
            page = None
            try:
                page = context.new_page()
                try:
                    page.goto(
                        detail_url,
                        wait_until="domcontentloaded",
                        timeout=NCSS_CONFIG["page_load_timeout"] * 1000,
                    )
                except PlaywrightTimeoutError:
                    # 某些第三方职位资源长期挂起，但正文可能已经渲染；继续检查当前 DOM。
                    last_error = "navigation_timeout"

                current_url = page.url
                try:
                    body_text = page.locator("body").inner_text(timeout=8000)
                except Exception:
                    body_text = ""
                access_issue = classify_access_issue(body_text, current_url)
                if access_issue:
                    return {
                        "detail_url": canonicalize_url(current_url or detail_url),
                        "detail_text": "",
                        "detail_title": normalize_job_text(page.title()),
                        "access_issue": access_issue,
                        "error": access_issue,
                    }

                detail_locator = page.locator(".mainContent").first
                detail_text = detail_locator.inner_text(timeout=8000) if detail_locator.count() else ""
                metadata_locator = page.locator("ul.details").first
                metadata_text = metadata_locator.inner_text(timeout=5000) if metadata_locator.count() else ""
                location_locator = page.locator("ul.address li").first
                location_text = location_locator.inner_text(timeout=5000) if location_locator.count() else ""
                if detail_text:
                    return {
                        "detail_url": canonicalize_url(current_url or detail_url),
                        "detail_text": normalize_job_text(detail_text),
                        "metadata_text": normalize_job_text(metadata_text),
                        "location_text": normalize_job_text(location_text),
                        "detail_title": normalize_job_text(page.title()),
                        "access_issue": "",
                        "error": "",
                    }
                last_error = "detail_content_missing"
            except Exception as exc:
                if is_browser_closed_error(exc):
                    return {
                        "detail_url": detail_url,
                        "detail_text": "",
                        "detail_title": "",
                        "access_issue": "browser_closed",
                        "error": "browser_closed",
                    }
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

            if attempt < int(NCSS_CONFIG["max_detail_retries"]):
                log(f"  详情页读取失败，第 {attempt} 次重试: {last_error}")
                time.sleep(min(float(NCSS_CONFIG["detail_interval"]), 3.0))

        return {
            "detail_url": detail_url,
            "detail_text": "",
            "detail_title": "",
            "access_issue": "",
            "error": last_error,
        }

    def open_detail_page_with_recovery(self, context, detail_url: str) -> Dict[str, Any]:
        """详情页遇到登录/验证中断时，可选等待人工处理后重试该详情页。"""
        detail = self.open_detail_page(context, detail_url)
        recoverable_statuses = {"verification_required", "authentication_required"}
        attempts = int(NCSS_CONFIG.get("manual_recovery_attempts") or 0)
        while detail.get("access_issue") in recoverable_statuses and attempts > 0:
            attempts -= 1
            recovered = self._wait_for_manual_recovery(
                context,
                str(detail.get("access_issue") or ""),
                detail_url,
                "详情页",
            )
            if not recovered:
                break
            detail = self.open_detail_page(context, detail_url)
        return detail

    def _parse_job(self, card: Dict[str, Any], detail: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            api_item = card.get("api_item") or {}
            title = normalize_job_text(card.get("job_title") or "")
            company = normalize_job_text(card.get("company_name") or "")
            detail_url = canonicalize_url(detail.get("detail_url") or card.get("detail_url") or "")
            source_job_id = card.get("source_job_id") or extract_detail_id(detail_url)
            card_text = normalize_job_text(card.get("card_text") or "")
            detail_text = normalize_job_text(detail.get("detail_text") or "")
            metadata_text = normalize_job_text(detail.get("metadata_text") or "")
            merged_text = normalize_job_text("\n".join(x for x in [card_text, detail_text, metadata_text] if x))

            if not title:
                title = self._guess_title(merged_text)
            if not company:
                company = self._guess_company(merged_text)

            fp = compute_source_fingerprint(source_job_id, detail_url)
            if not fp:
                self.total_filtered_invalid += 1
                return None
            if fp in self.seen_fps or fp in self.combo_seen_fps:
                self.total_skipped_dup += 1
                return None
            self.combo_seen_fps.add(fp)

            if NCSS_CONFIG.get("filter_title", True) and not is_job_title_relevant(self.current_keyword, title):
                self.total_filtered_irrelevant += 1
                return None

            if any(keyword in title for keyword in SPAM_KEYWORDS):
                self.total_filtered_invalid += 1
                return None

            description = detail_text
            if len(description) < 20:
                self.total_filtered_invalid += 1
                return None
            requirement_text = extract_section(description, REQUIREMENT_HEADINGS)
            responsibility_text = extract_responsibility_text(description)
            requirement_source = requirement_text or description

            cfp = compute_content_fingerprint(company, description)
            if cfp and (cfp in self.seen_cfps or cfp in self.combo_seen_cfps):
                self.total_skipped_dup += 1
                return None
            if cfp:
                self.combo_seen_cfps.add(cfp)

            low_month_pay = api_item.get("lowMonthPay")
            high_month_pay = api_item.get("highMonthPay")
            if low_month_pay not in (None, "") or high_month_pay not in (None, ""):
                try:
                    low_value = float(low_month_pay or 0)
                    high_value = float(high_month_pay or low_value)
                except (TypeError, ValueError):
                    low_value = high_value = 0
                if low_value or high_value:
                    salary_text = f"{low_value:g}-{high_value:g}K/月"
                    salary_min = int(round(low_value * 1000))
                    salary_max = int(round(high_value * 1000))
                elif str(low_month_pay or high_month_pay or "").strip() in {"0", "0.0"}:
                    salary_text = "薪资面议"
                    salary_min, salary_max = None, None
                else:
                    salary_text = extract_salary_text(merged_text)
                    salary_min, salary_max = parse_salary(salary_text)
            else:
                salary_text = extract_salary_text(merged_text)
                salary_min, salary_max = parse_salary(salary_text)
            salary_months = parse_salary_months(salary_text)

            edu_raw = normalize_job_text(api_item.get("degreeName") or "") or find_first(
                [
                    r"学历(?:要求)?\s*[：:]\s*([^\n]+)",
                    r"(博士|硕士|研究生|本科|大专|专科|中专|高中)(?:及以上|以上)?",
                    r"(学历不限|不限学历|无学历要求)",
                ],
                requirement_source,
            )
            education = parse_education(edu_raw)

            exp_raw = find_first(
                [
                    r"经验(?:要求)?\s*[：:]\s*([^\n]+)",
                    r"(\d+(?:\.\d+)?\s*(?:[-~～—–至到]\s*\d+(?:\.\d+)?)?\s*年(?:及以上|以上|经验)?)",
                    r"(经验不限|不限经验|无需经验|应届生|应届毕业生)",
                ],
                requirement_source,
            )
            experience, exp_min, exp_max, exp_type = parse_experience_details(exp_raw)

            location_text = normalize_job_text(detail.get("location_text") or "")
            actual_city, district = extract_city_and_district(location_text, self.current_city["name"])
            industry = extract_industry(merged_text)
            company_size = normalize_job_text(api_item.get("recScale") or "") or extract_company_size(merged_text)
            company_type = normalize_job_text(api_item.get("recProperty") or "") or extract_company_type(merged_text)
            publish_date_raw = api_item.get("publishDate") or api_item.get("updateDate") or ""
            publish_date = parse_epoch_date(publish_date_raw) or extract_publish_date(merged_text)

            desc_skills, desc_evidence = self.dictionaries.extract_skills(description)
            req_skills, req_evidence = self.dictionaries.extract_skills(requirement_text)
            if requirement_text:
                skills = req_skills
                skill_evidence = req_evidence
                skill_scope = "requirement_text"
            else:
                skills = desc_skills
                skill_evidence = desc_evidence
                skill_scope = "job_description_fallback"
            skill_categories = {skill: self.dictionaries.skill_category.get(skill, "未分类") for skill in skills}

            majors_raw, major_evidence = self.dictionaries.extract_major_raw(description)
            majors = self.dictionaries.normalize_majors(majors_raw)
            major_categories = {major: self.dictionaries.major_category.get(major, "未映射") for major in majors}
            major_level = classify_major_requirement(description, majors)
            platform_major_raw = normalize_job_text(api_item.get("major") or "")
            platform_major_values = [
                value.strip() for value in re.split(r"[|,，、;/；]", platform_major_raw) if value.strip()
            ]
            platform_majors = self.dictionaries.normalize_majors(platform_major_values)
            if majors:
                overlap = set(platform_majors) & set(majors)
                if not platform_major_values:
                    platform_major_validation = "not_provided"
                elif platform_majors and set(platform_majors).issubset(set(majors)):
                    platform_major_validation = "confirmed_by_description"
                elif overlap:
                    platform_major_validation = "partially_confirmed"
                else:
                    platform_major_validation = "not_supported_by_description"
                major_source = "description"
                major_note = "仅依据岗位正文中的专业要求及证据生成标准专业候选"
            else:
                platform_major_validation = "unverified_api_only" if platform_major_values else "not_provided"
                major_source = "not_specified"
                major_note = "岗位正文未明确专业要求，平台列表专业标签不纳入标准专业候选"

            raw_tags = normalize_job_text(api_item.get("recTags") or "")
            job_tags = [tag.strip() for tag in re.split(r"[|,，、;/；]", raw_tags) if tag.strip()]

            flags: List[str] = []
            if not requirement_text:
                flags.append("requirement_section_missing")
            if not majors:
                flags.append("major_missing")
            if platform_major_validation == "unverified_api_only":
                flags.append("platform_major_unverified")
            elif platform_major_validation == "not_supported_by_description":
                flags.append("platform_major_not_supported")
            if not skills:
                flags.append("skill_candidate_missing")
            if not education:
                flags.append("education_missing")
            location_is_city_level_only = bool(
                location_text
                and any(token in re.sub(r"\s+", "", location_text) for token in ("市辖区", "辖区", "全市"))
            )
            if location_is_city_level_only:
                flags.append("district_city_level_only")
            elif not district:
                flags.append("district_missing")
            if not salary_text:
                flags.append("salary_missing")
            if len(description) < 80:
                flags.append("description_short")

            return {
                "source": SOURCE_KEY,
                "crawler_version": CRAWLER_VERSION,
                "skill_dict_ver": self.dictionaries.skill_dict_ver,
                "major_dict_ver": self.dictionaries.major_dict_ver,
                "search_keyword": self.current_keyword,
                "search_city": self.current_city["name"],
                "search_city_code": self.current_city["code"],
                "source_job_id": source_job_id,
                "source_url": detail_url,
                "crawl_time": datetime.now().isoformat(),
                "fingerprint": fp,
                "content_fingerprint": cfp,
                "job_title": title,
                "company_name": company,
                "city": actual_city,
                "district": district,
                "salary_text": salary_text,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_months": salary_months,
                "education": education,
                "education_raw": edu_raw,
                "experience": experience,
                "experience_raw": exp_raw,
                "experience_min_years": exp_min,
                "experience_max_years": exp_max,
                "experience_requirement_type": exp_type,
                "job_type": extract_job_type(merged_text),
                "industry": industry,
                "company_size": company_size,
                "company_type": company_type,
                "publish_date": publish_date,
                "publish_date_raw": str(publish_date_raw),
                "longitude": "",
                "latitude": "",
                "platform_major_validation": platform_major_validation,
                "major_candidates_raw": majors_raw,
                "major_candidates": majors,
                "major_categories": major_categories,
                "major_requirement_level": major_level,
                "major_evidence": major_evidence,
                "major_source": major_source,
                "major_decision_note": major_note,
                "job_description_raw": detail.get("detail_text") or card.get("card_text") or "",
                "job_description": description,
                "requirement_text": requirement_text,
                "responsibility_text": responsibility_text,
                "job_tags": job_tags,
                "description_skill_candidates": desc_skills,
                "requirement_skill_candidates": req_skills,
                "skill_candidates": skills,
                "skill_categories": skill_categories,
                "skill_evidence": skill_evidence,
                "skill_extraction_scope": skill_scope,
                "quality_flags": flags,
            }
        except Exception as exc:
            self.total_parse_errors += 1
            log(f"  ✗ 岗位解析失败: {type(exc).__name__}: {exc}")
            return None

    def _find_latest_combo_csv(self, job_name: str, city_name: str) -> str:
        out_dir = os.path.join(DATA_ROOT, job_name)
        if not os.path.isdir(out_dir):
            return ""
        prefix = f"{job_name}_{city_name}_"
        candidates = [
            os.path.join(out_dir, name)
            for name in os.listdir(out_dir)
            if name.startswith(prefix) and name.endswith("_ncss_jobs.csv")
        ]
        return max(candidates, key=os.path.getmtime) if candidates else ""

    def run_one_combo(self, browser_context, city: Dict[str, str], keyword: str, retry_count: int = 0) -> Dict[str, Any]:
        self.current_city = city
        self.current_keyword = keyword
        self.collected_jobs = []
        self.raw_items = []
        self.combo_seen_fps = set()
        self.combo_seen_cfps = set()
        self.total_skipped_dup = 0
        self.total_filtered_irrelevant = 0
        self.total_filtered_invalid = 0
        self.total_parse_errors = 0

        city_name = city["name"]
        job_name = get_job_display_name(keyword)
        out_dir = os.path.join(DATA_ROOT, job_name)
        os.makedirs(out_dir, exist_ok=True)
        checkpoint_file = os.path.join(out_dir, f"_{job_name}_{city_name}_ncss_checkpoint.json")

        progress = load_progress(self.conn, city["code"], keyword)
        existing_csv = self._find_latest_combo_csv(job_name, city_name)
        if (
            NCSS_CONFIG.get("resume_completed", True)
            and progress
            and progress.get("status") in TERMINAL_STATUSES
            and existing_csv
        ):
            parsed_count = int(progress.get("total_collected") or 0)
            raw_unique_count = int(progress.get("raw_unique_count") or 0)
            sample = classify_city_sample(
                parsed_count,
                NCSS_CONFIG["city_descriptive_min"],
                NCSS_CONFIG["city_analysis_min"],
            )
            log(f"组合已有终态记录，跳过：{city_name} · {keyword} | 有效 {parsed_count} 条")
            return {
                "city": city_name,
                "city_code": city["code"],
                "keyword": keyword,
                "job_name": job_name,
                "status": progress.get("status"),
                "status_label": VALIDATION_STATUS_LABELS.get(progress.get("status"), progress.get("status")),
                "passed": True,
                "raw_item_count": raw_unique_count,
                "raw_unique_count": raw_unique_count,
                "parsed_count": parsed_count,
                **sample,
                "source_total_count": "",
                "stop_reason": "already_terminal",
                "last_page_attempted": int(progress.get("last_page") or 0),
                "last_successful_page": int(progress.get("last_page") or 0),
                "zero_valid_streak": 0,
                "retryable": False,
                "retry_count": int(progress.get("retry_count") or 0),
                "checkpoint_file": "",
                "csv_file": existing_csv,
                "duplicate_count": 0,
                "irrelevant_title_count": 0,
                "invalid_record_count": 0,
                "parse_error_count": 0,
            }

        if NCSS_CONFIG.get("fresh") and os.path.exists(checkpoint_file):
            os.remove(checkpoint_file)
        checkpoint = None if NCSS_CONFIG.get("fresh") else load_checkpoint(checkpoint_file)
        page_num = 1
        last_page_attempted = 0
        last_successful_page = 0
        empty_streak = 0
        zero_valid_streak = 0
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if checkpoint:
            self.collected_jobs = checkpoint.get("collected_jobs", [])
            self.raw_items = checkpoint.get("raw_items", [])
            self.combo_seen_fps = set(checkpoint.get("combo_seen_fps", []))
            self.combo_seen_cfps = set(checkpoint.get("combo_seen_cfps", []))
            page_num = max(1, int(checkpoint.get("next_page", 1)))
            run_timestamp = checkpoint.get("run_timestamp", run_timestamp)
            last_page_attempted = int(checkpoint.get("last_page_attempted", 0))
            last_successful_page = int(checkpoint.get("last_successful_page", 0))
            empty_streak = int(checkpoint.get("empty_streak", 0))
            zero_valid_streak = int(checkpoint.get("zero_valid_streak", 0))
            log(f"发现检查点：{city_name} · {keyword} 从第 {page_num} 页恢复")

        raw_job_keys = {
            str(item.get("source_job_id") or item.get("detail_url") or "")
            for raw_page in self.raw_items
            for item in raw_page.get("items", [])
            if item.get("source_job_id") or item.get("detail_url")
        }
        source_total_count = 0

        def persist_checkpoint(status: str, next_page: int, last_error: str = "") -> None:
            payload = {
                "checkpoint_version": CHECKPOINT_VERSION,
                "crawler_version": CRAWLER_VERSION,
                "city": city_name,
                "city_code": city["code"],
                "keyword": keyword,
                "job_name": job_name,
                "run_timestamp": run_timestamp,
                "status": status,
                "next_page": next_page,
                "last_page_attempted": last_page_attempted,
                "last_successful_page": last_successful_page,
                "empty_streak": empty_streak,
                "zero_valid_streak": zero_valid_streak,
                "raw_items": self.raw_items,
                "combo_seen_fps": sorted(self.combo_seen_fps),
                "combo_seen_cfps": sorted(self.combo_seen_cfps),
                "total_skipped_dup": self.total_skipped_dup,
                "total_filtered_irrelevant": self.total_filtered_irrelevant,
                "total_filtered_invalid": self.total_filtered_invalid,
                "total_parse_errors": self.total_parse_errors,
                "retry_count": retry_count,
                "last_error": last_error,
                "collected_jobs": self.collected_jobs,
                "updated_at": datetime.now().isoformat(),
            }
            atomic_write_json(checkpoint_file, payload)
            save_progress(
                self.conn,
                city["code"],
                keyword,
                last_page_attempted,
                len(self.collected_jobs),
                raw_unique_count=len(raw_job_keys),
                next_page=next_page,
                status=status,
                checkpoint_file=checkpoint_file,
                retry_count=retry_count,
                last_error=last_error,
            )

        log("=" * 60)
        log(f"开始采集 NCSS 独立版: {city_name} · {keyword}")
        log(f"输出目录: {out_dir}")
        log("=" * 60)

        stop_reason = "collection_incomplete"
        try:
            while page_num <= int(NCSS_CONFIG["max_pages"]):
                last_page_attempted = page_num
                log(f"  正在解析第 {page_num} 页...")
                cards, page_status, reported_total = self.fetch_list_page_with_recovery(browser_context, page_num)
                if reported_total:
                    source_total_count = reported_total
                if page_status in {"verification_required", "authentication_required", "rate_limited", "browser_closed"}:
                    stop_reason = "verification_interrupted" if page_status == "verification_required" else page_status
                    persist_checkpoint("pending_retry", page_num, stop_reason)
                    break
                if page_status == "page_retry_exhausted":
                    stop_reason = "page_retry_exhausted"
                    persist_checkpoint("pending_retry", page_num, stop_reason)
                    break
                if not cards:
                    stop_reason = "no_result" if not self.collected_jobs else "source_exhausted"
                    break

                last_successful_page = page_num
                empty_streak = 0
                page_new_count = 0
                raw_page = {
                    "page": page_num,
                    "url": self.build_list_api_url(page_num),
                    "captured_at": datetime.now().isoformat(),
                    "items": cards,
                }
                self.raw_items.append(raw_page)
                raw_job_keys.update(
                    str(card.get("source_job_id") or card.get("detail_url"))
                    for card in cards
                    if card.get("source_job_id") or card.get("detail_url")
                )

                for card in cards:
                    max_jobs = int(NCSS_CONFIG["max_jobs_per_combo"] or 0)
                    if max_jobs > 0 and len(self.collected_jobs) >= max_jobs:
                        stop_reason = "optional_cap_reached"
                        break

                    title = card.get("job_title", "")
                    if NCSS_CONFIG.get("filter_title", True) and not is_job_title_relevant(keyword, title):
                        self.total_filtered_irrelevant += 1
                        continue

                    detail = self.open_detail_page_with_recovery(browser_context, card.get("detail_url", ""))
                    access_issue = detail.get("access_issue") or ""
                    if access_issue:
                        stop_reason = "verification_interrupted" if access_issue == "verification_required" else access_issue
                        persist_checkpoint("pending_retry", page_num, stop_reason)
                        break
                    if detail.get("error") and not detail.get("detail_text"):
                        self.total_parse_errors += 1
                        log(
                            f"  跳过详情读取失败岗位: {card.get('source_job_id') or '未知ID'} | "
                            f"{detail.get('error', 'detail_error')}"
                        )
                        continue
                    job = self._parse_job(card, detail)
                    if job:
                        job["record_no"] = len(self.collected_jobs) + 1
                        self.collected_jobs.append(job)
                        save_fingerprint(self.conn, job["fingerprint"], job.get("content_fingerprint", ""), job)
                        self.seen_fps.add(job["fingerprint"])
                        if job.get("content_fingerprint"):
                            self.seen_cfps.add(job["content_fingerprint"])
                        page_new_count += 1
                    time.sleep(float(NCSS_CONFIG["detail_interval"]))

                if stop_reason in {
                    "optional_cap_reached", "verification_interrupted", "authentication_required",
                    "rate_limited", "browser_closed", "page_retry_exhausted",
                }:
                    break

                if page_new_count:
                    zero_valid_streak = 0
                else:
                    zero_valid_streak += 1

                log(
                    f"  本页有效: {page_new_count} | 有效累计: {len(self.collected_jobs)} | "
                    f"连续零有效页: {zero_valid_streak} | 标题不相关: {self.total_filtered_irrelevant} | "
                    f"重复: {self.total_skipped_dup}"
                )

                persist_checkpoint("in_progress", page_num + 1)

                if zero_valid_streak >= int(NCSS_CONFIG["max_zero_valid_pages_per_combo"]):
                    stop_reason = "relevance_exhausted"
                    log(
                        f"  连续零有效页已达到 {NCSS_CONFIG['max_zero_valid_pages_per_combo']} 页，"
                        "跳过当前城市×岗位组合。"
                    )
                    break

                max_jobs = int(NCSS_CONFIG["max_jobs_per_combo"] or 0)
                if max_jobs > 0 and len(self.collected_jobs) >= max_jobs:
                    stop_reason = "optional_cap_reached"
                    break

                page_size = int(NCSS_CONFIG["page_size"])
                if len(cards) < page_size or (
                    source_total_count and page_num * page_size >= source_total_count
                ):
                    stop_reason = "source_exhausted"
                    break
                page_num += 1
                time.sleep(NCSS_CONFIG["request_interval"])

            if page_num > int(NCSS_CONFIG["max_pages"]):
                stop_reason = "max_pages_reached"

        except Exception as exc:
            stop_reason = "page_retry_exhausted"
            persist_checkpoint("pending_retry", page_num, f"{type(exc).__name__}: {exc}")

        status = stop_reason if stop_reason in VALIDATION_STATUS_LABELS else "collection_incomplete"
        retryable = status in {"page_retry_exhausted", "collection_incomplete"}
        passed = status in TERMINAL_STATUSES
        checkpoint_required = not passed
        sample = classify_city_sample(
            len(self.collected_jobs),
            NCSS_CONFIG["city_descriptive_min"],
            NCSS_CONFIG["city_analysis_min"],
        )

        timestamp = run_timestamp
        raw_file = os.path.join(out_dir, f"{job_name}_{city_name}_{timestamp}_ncss_raw_items.json")
        csv_file = os.path.join(out_dir, f"{job_name}_{city_name}_{timestamp}_ncss_jobs.csv")
        log_file = os.path.join(out_dir, f"{job_name}_{city_name}_{timestamp}_ncss_run_log.txt")

        atomic_write_json(
            raw_file,
            {
                "source": SOURCE_NAME,
                "search_url_template": NCSS_CONFIG["search_url_template"],
                "list_api_url": NCSS_CONFIG["list_api_url"],
                "city": city,
                "keyword": keyword,
                "items": self.raw_items,
            },
        )
        write_job_csv(csv_file, self.collected_jobs)

        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(f"数据源: {SOURCE_NAME}\n")
            handle.write(f"脚本版本: {CRAWLER_VERSION}\n")
            handle.write("依赖说明: 独立版，不依赖 batch_crawler.py\n")
            handle.write(f"技能词典版本: {self.dictionaries.skill_dict_ver}\n")
            handle.write(f"专业词典版本: {self.dictionaries.major_dict_ver}\n")
            handle.write(f"城市: {city_name} ({city['code']})\n")
            handle.write(f"岗位关键词: {keyword}\n")
            handle.write(f"状态: {VALIDATION_STATUS_LABELS.get(status, status)} ({status})\n")
            handle.write(f"停止原因: {stop_reason}\n")
            handle.write(f"CSV有效数量: {len(self.collected_jobs)} 条\n")
            handle.write(f"原始页数量: {len(self.raw_items)} 页\n")
            handle.write(f"API原始返回数量: {sum(len(x.get('items', [])) for x in self.raw_items)} 条\n")
            handle.write(f"原始唯一岗位数量: {len(raw_job_keys)} 条\n")
            handle.write(f"官网报告总数: {source_total_count or '未知'}\n")
            handle.write(f"跳过重复: {self.total_skipped_dup} 条\n")
            handle.write(f"标题不相关过滤: {self.total_filtered_irrelevant} 条\n")
            handle.write(f"无效记录过滤: {self.total_filtered_invalid} 条\n")
            handle.write(f"连续零有效页: {zero_valid_streak} 页\n")
            handle.write(f"解析错误: {self.total_parse_errors} 条\n")
            handle.write(f"搜索URL模板: {NCSS_CONFIG['search_url_template']}\n")
            handle.write(f"职位列表接口: {NCSS_CONFIG['list_api_url']}\n")
            handle.write(f"输出CSV: {os.path.basename(csv_file)}\n")
            handle.write(f"输出原始文件: {os.path.basename(raw_file)}\n")

        if checkpoint_required:
            persist_checkpoint("pending_retry", page_num, stop_reason)
        else:
            save_progress(
                self.conn,
                city["code"],
                keyword,
                last_page_attempted,
                len(self.collected_jobs),
                raw_unique_count=len(raw_job_keys),
                next_page=page_num,
                status=status,
                checkpoint_file="",
                retry_count=retry_count,
                last_error="",
            )
            if os.path.exists(checkpoint_file):
                os.remove(checkpoint_file)

        return {
            "city": city_name,
            "city_code": city["code"],
            "keyword": keyword,
            "job_name": job_name,
            "status": status,
            "status_label": VALIDATION_STATUS_LABELS.get(status, status),
            "passed": passed,
            "raw_item_count": sum(len(x.get("items", [])) for x in self.raw_items),
            "raw_unique_count": len(raw_job_keys),
            "parsed_count": len(self.collected_jobs),
            **sample,
            "source_total_count": source_total_count,
            "stop_reason": stop_reason,
            "last_page_attempted": last_page_attempted,
            "last_successful_page": last_successful_page,
            "zero_valid_streak": zero_valid_streak,
            "retryable": retryable,
            "retry_count": retry_count,
            "checkpoint_file": checkpoint_file if checkpoint_required else "",
            "csv_file": csv_file,
            "duplicate_count": self.total_skipped_dup,
            "irrelevant_title_count": self.total_filtered_irrelevant,
            "invalid_record_count": self.total_filtered_invalid,
            "parse_error_count": self.total_parse_errors,
        }

    def save_quality_summary(self) -> str:
        os.makedirs(DATA_ROOT, exist_ok=True)
        timestamp = self.batch_start.strftime("%Y%m%d_%H%M%S")
        summary_file = os.path.join(DATA_ROOT, f"NCSS_爬取质量汇总_{timestamp}.csv")
        role_summaries = summarize_role_samples(self.combo_results, NCSS_CONFIG["role_valid_target"])
        rows: List[Dict[str, Any]] = []
        for result in self.combo_results:
            rows.append({
                **result,
                "record_type": "city_role",
                "valid_count": result.get("parsed_count", 0),
            })
        for summary in role_summaries:
            target_met = summary["target_met"]
            rows.append({
                "record_type": "role_total",
                "job_name": summary["job_name"],
                "keyword": summary["keyword"],
                "status": "role_target_met" if target_met else "role_target_shortfall",
                "status_label": "岗位总体样本已达标" if target_met else "岗位总体样本不足",
                "passed": target_met,
                "valid_count": summary["valid_total"],
                "role_target": summary["target"],
                "role_target_met": target_met,
                "role_shortfall": summary["shortfall"],
                "city_count": summary["city_count"],
                "city_counts": json.dumps(summary["city_counts"], ensure_ascii=False, sort_keys=True),
            })
        write_csv_by_specs(summary_file, rows, QUALITY_OUTPUT_FIELD_SPECS)
        log(f"质量汇总已保存: {summary_file}")
        return summary_file

    def save_analysis_dataset(self) -> str:
        rows: List[Dict[str, Any]] = []
        for result in self.combo_results:
            csv_file = result.get("csv_file")
            if not csv_file or not os.path.exists(csv_file):
                continue
            try:
                rows.extend(read_job_csv(csv_file))
            except Exception as exc:
                log(f"读取 CSV 失败: {csv_file} | {type(exc).__name__}: {exc}")
        weighted = add_city_balance_weights(rows) if rows else []
        timestamp = self.batch_start.strftime("%Y%m%d_%H%M%S")
        analysis_file = os.path.join(DATA_ROOT, f"NCSS_岗位分析数据_{timestamp}.csv")
        write_job_csv(analysis_file, weighted, include_analysis_fields=True)
        log(f"分析数据集已保存: {analysis_file}")
        return analysis_file

    @staticmethod
    def _context_options() -> Dict[str, Any]:
        return {
            "viewport": {
                "width": NCSS_CONFIG["viewport_width"],
                "height": NCSS_CONFIG["viewport_height"],
            },
            "locale": "zh-CN",
            "accept_downloads": False,
        }

    def _launch_browser_context(self, playwright):
        """登录模式使用独立持久化资料目录；关闭登录模式时保留原来的临时上下文。"""
        auth_mode = NCSS_CONFIG.get("auth_mode", "auto")
        headless = bool(NCSS_CONFIG["headless"])
        if auth_mode == "off":
            browser = playwright.chromium.launch(headless=headless)
            return browser, browser.new_context(**self._context_options())

        profile_dir = os.path.abspath(NCSS_CONFIG["profile_dir"])
        os.makedirs(profile_dir, exist_ok=True)
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
                **self._context_options(),
            )
        except Exception as exc:
            message = str(exc).lower()
            if any(token in message for token in ("processsingleton", "profile in use", "user data directory is already in use")):
                raise SystemExit(f"登录资料目录正在被另一个浏览器占用，请关闭对应窗口后重试: {profile_dir}") from None
            raise
        self._restore_session_cookies(context)
        return None, context

    @staticmethod
    def _auth_state_path() -> str:
        return os.path.join(os.path.abspath(NCSS_CONFIG["profile_dir"]), AUTH_STATE_FILENAME)

    def _has_saved_session_state(self) -> bool:
        """检查本地是否保存过 NCSS 会话痕迹，不输出任何 Cookie 内容。"""
        state_path = self._auth_state_path()
        if not os.path.exists(state_path):
            return False
        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            cookies = state.get("cookies") if isinstance(state, dict) else None
            if not isinstance(cookies, list):
                return False
            for cookie in cookies:
                if not isinstance(cookie, dict):
                    continue
                domain = str(cookie.get("domain") or "")
                name = str(cookie.get("name") or "")
                if domain.endswith("ncss.cn") and name in {"SESSION", "CHSICC01", "CHSICC_CLIENTFLAGSTUDENT"}:
                    return True
        except Exception:
            return False
        return False

    def _restore_session_cookies(self, context) -> None:
        """补回 Chromium 退出时可能清理的会话 Cookie；文件内容从不进入日志。"""
        state_path = self._auth_state_path()
        if not os.path.exists(state_path):
            return
        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            cookies = state.get("cookies") if isinstance(state, dict) else None
            if isinstance(cookies, list) and cookies:
                context.add_cookies(cookies)
        except Exception as exc:
            log(f"本地登录状态恢复失败，将使用浏览器资料中的现有状态: {type(exc).__name__}")

    def _save_session_state(self, context) -> None:
        state_path = self._auth_state_path()
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        temp_path = f"{state_path}.tmp"
        try:
            context.storage_state(path=temp_path)
            os.replace(temp_path, state_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _check_authentication(self, context) -> bool:
        page = context.new_page()
        try:
            for check_url in SESSION_CHECK_URLS:
                page.goto(
                    check_url,
                    wait_until="domcontentloaded",
                    timeout=NCSS_CONFIG["page_load_timeout"] * 1000,
                )
                body_text = page.locator("body").inner_text(timeout=10000)
                signin_locator = page.locator('a[href*="/student/signin.html"]').first
                signin_visible = bool(signin_locator.count() and signin_locator.is_visible())
                authenticated_nav = page.locator(".loginhas-ul").first
                authenticated_nav_visible = bool(authenticated_nav.count() and authenticated_nav.is_visible())
                if is_authenticated_page(body_text, page.url, signin_visible, authenticated_nav_visible):
                    return True
            if self._has_saved_session_state():
                log("页面登录检查未能确认状态，但发现本地 NCSS 会话文件；继续使用该会话采集，若接口返回登录失效会自动停止。")
                return True
            return False
        except Exception as exc:
            log(f"登录状态检查失败: {type(exc).__name__}: {exc}")
            if self._has_saved_session_state():
                log("登录状态页面检查失败，但发现本地 NCSS 会话文件；继续使用该会话采集，若接口返回登录失效会自动停止。")
                return True
            return False
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _interactive_login(self, context) -> bool:
        """只打开官网登录页并等待使用者手工完成认证，不接触凭据或验证码。"""
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            DEFAULT_LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=NCSS_CONFIG["page_load_timeout"] * 1000,
        )
        log("已打开 NCSS 官方登录页，请在浏览器窗口中手工完成登录；程序不会读取账号、密码或验证码。")
        deadline = time.time() + float(NCSS_CONFIG["login_timeout"])
        while time.time() < deadline:
            if page.is_closed():
                log("登录窗口已关闭，未能确认登录状态。")
                return False
            for candidate in reversed(context.pages):
                try:
                    body_text = candidate.locator("body").inner_text(timeout=2000)
                    signin_locator = candidate.locator('a[href*="/student/signin.html"]').first
                    signin_visible = bool(signin_locator.count() and signin_locator.is_visible())
                    authenticated_nav = candidate.locator(".loginhas-ul").first
                    authenticated_nav_visible = bool(authenticated_nav.count() and authenticated_nav.is_visible())
                    if is_authenticated_page(body_text, candidate.url, signin_visible, authenticated_nav_visible):
                        self._save_session_state(context)
                        log("登录成功，会话已保存在本机专用浏览器资料目录。")
                        return True
                except Exception:
                    continue
            page.wait_for_timeout(3000)
        log("等待登录超时；已保留浏览器资料，可稍后重新执行 --login-only。")
        return False

    def _context_has_manual_recovery(self, context, issue: str) -> bool:
        for candidate in reversed(context.pages):
            if candidate.is_closed():
                continue
            try:
                url = candidate.url or ""
                lowered_url = url.lower()
                if not any(domain in lowered_url for domain in ("ncss.cn", "chsi.com.cn")):
                    continue
                body_text = candidate.locator("body").inner_text(timeout=2000)
                signin_locator = candidate.locator('a[href*="/student/signin.html"]').first
                signin_visible = bool(signin_locator.count() and signin_locator.is_visible())
                authenticated_nav = candidate.locator(".loginhas-ul").first
                authenticated_nav_visible = bool(authenticated_nav.count() and authenticated_nav.is_visible())
                if issue == "authentication_required":
                    if is_authenticated_page(body_text, url, signin_visible, authenticated_nav_visible):
                        return True
                    continue
                if not classify_access_issue(body_text, url):
                    return True
            except Exception:
                continue
        return False

    def _wait_for_manual_recovery(self, context, issue: str, target_url: str, label: str) -> bool:
        """等待使用者在官网可见页面中手工完成登录/验证，不读取凭据、不处理验证码。"""
        if not NCSS_CONFIG.get("manual_recovery"):
            return False
        if issue not in {"verification_required", "authentication_required"}:
            return False
        if NCSS_CONFIG.get("auth_mode") == "off" and issue == "authentication_required":
            log("登录会话已失效，但当前为 --auth-mode off，无法保存/恢复登录状态。")
            return False

        page = context.new_page()
        url = DEFAULT_LOGIN_URL if issue == "authentication_required" else target_url
        try:
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=NCSS_CONFIG["page_load_timeout"] * 1000,
                )
            except PlaywrightTimeoutError:
                pass
            label_text = "登录" if issue == "authentication_required" else "网站验证"
            timeout_seconds = float(NCSS_CONFIG["manual_recovery_timeout"])
            log(
                f"检测到 {label} 需要{label_text}，已打开浏览器窗口；"
                f"请手工处理，程序最多等待 {int(timeout_seconds)} 秒。"
            )
            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                if page.is_closed():
                    log("人工处理窗口已关闭，未能确认恢复。")
                    return False
                if self._context_has_manual_recovery(context, issue):
                    self._save_session_state(context)
                    log(f"{label_text}已确认，继续重试 {label}。")
                    return True
                page.wait_for_timeout(3000)
            log(f"等待人工处理超时，保留检查点后停止: {label}")
            return False
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass

    def run(self, cities: List[Dict[str, str]], jobs: List[str]) -> None:
        os.makedirs(DATA_ROOT, exist_ok=True)
        with sync_playwright() as playwright:
            browser, context = self._launch_browser_context(playwright)
            session_authenticated = False
            try:
                if NCSS_CONFIG.get("interactive_login"):
                    if not self._interactive_login(context):
                        raise SystemExit("未确认登录成功，未开始采集。")
                    session_authenticated = True
                elif NCSS_CONFIG.get("auth_mode") == "required":
                    session_authenticated = self._check_authentication(context)
                    if not session_authenticated:
                        raise SystemExit("登录会话不存在或已经失效。请先运行: python ncss_crawler_standalone.py --login-only")
                elif NCSS_CONFIG.get("auth_mode") == "auto":
                    session_authenticated = self._check_authentication(context)
                    auth_label = "已登录" if session_authenticated else "未登录（继续采集公开岗位）"
                    log(f"浏览器会话状态: {auth_label}")

                if NCSS_CONFIG.get("login_only"):
                    return

                pending = [(city, job, 0) for city in cities for job in jobs]
                while pending:
                    city, job, retry_count = pending.pop(0)
                    result = self.run_one_combo(context, city, job, retry_count=retry_count)
                    if result.get("status") in {"authentication_required", "rate_limited", "verification_interrupted", "browser_closed"}:
                        self.combo_results.append(result)
                        self.total_collected += int(result.get("parsed_count") or 0)
                        log(f"批次提前停止: {result.get('status_label')}")
                        break
                    if result.get("retryable") and retry_count < int(NCSS_CONFIG["max_combo_retries"]):
                        next_retry = retry_count + 1
                        log(f"组合等待第 {next_retry} 次补采: {city['name']} · {job}")
                        pending.append((city, job, next_retry))
                    else:
                        self.combo_results.append(result)
                        self.total_collected += int(result.get("parsed_count") or 0)
                    time.sleep(float(NCSS_CONFIG["request_interval"]))
            finally:
                if session_authenticated and NCSS_CONFIG.get("auth_mode") != "off":
                    try:
                        self._save_session_state(context)
                    except Exception as exc:
                        log(f"登录状态刷新保存失败: {type(exc).__name__}")
                context.close()
                if browser is not None:
                    browser.close()
        self.save_quality_summary()
        self.save_analysis_dataset()
        log(f"全部完成，有效岗位总数: {self.total_collected}")


# ============================================================
# 命令行入口
# ============================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="国家大学生就业服务平台 NCSS 独立版批量岗位采集脚本")
    parser.add_argument("--city", action="append", help="只采集指定城市，可重复传入，如 --city 北京 --city 上海")
    parser.add_argument("--job", action="append", help="只采集指定岗位，可重复传入，如 --job 数据分析师")
    parser.add_argument("--max-pages", type=int, default=NCSS_CONFIG["max_pages"], help="每个城市×岗位最大翻页数")
    parser.add_argument("--max-jobs", type=int, default=NCSS_CONFIG["max_jobs_per_combo"], help="每个城市×岗位最多有效岗位数，0 表示不限")
    parser.add_argument("--interval", type=float, default=NCSS_CONFIG["request_interval"], help="翻页/组合间隔秒数")
    parser.add_argument("--detail-interval", type=float, default=NCSS_CONFIG["detail_interval"], help="详情页间隔秒数")
    parser.add_argument("--detail-retries", type=int, default=NCSS_CONFIG["max_detail_retries"], help="单个详情页瞬时失败重试次数")
    parser.add_argument(
        "--max-zero-valid-pages",
        "--max-invalid-pages",
        type=int,
        dest="max_zero_valid_pages",
        default=NCSS_CONFIG["max_zero_valid_pages_per_combo"],
        help="每个城市×岗位连续零有效页达到该值后跳过，继续下一个组合",
    )
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口，便于首次调试")
    parser.add_argument(
        "--auth-mode",
        choices=("auto", "required", "off"),
        default=NCSS_CONFIG["auth_mode"],
        help="登录会话策略：auto 自动复用，required 必须已登录，off 不使用持久会话",
    )
    parser.add_argument("--login", action="store_true", help="先打开官网登录页，手工登录成功后继续采集")
    parser.add_argument("--login-only", action="store_true", help="只完成手工登录并保存本机会话，不采集岗位")
    parser.add_argument("--login-timeout", type=int, default=NCSS_CONFIG["login_timeout"], help="等待手工登录的最长秒数")
    parser.add_argument(
        "--manual-recovery",
        "--pause-on-verification",
        action="store_true",
        dest="manual_recovery",
        help="采集中遇到登录/网站验证中断时，打开可见浏览器等待手工处理后重试当前页",
    )
    parser.add_argument(
        "--manual-recovery-timeout",
        type=int,
        default=NCSS_CONFIG["manual_recovery_timeout"],
        help="等待手工处理登录/网站验证的最长秒数",
    )
    parser.add_argument(
        "--manual-recovery-attempts",
        type=int,
        default=NCSS_CONFIG["manual_recovery_attempts"],
        help="同一页登录/网站验证中断后的人工恢复重试次数",
    )
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR, help="专用浏览器登录资料目录（包含敏感会话，请勿共享）")
    parser.add_argument("--no-title-filter", action="store_true", help="关闭标题相关性过滤")
    parser.add_argument("--search-url-template", default="", help="覆盖 NCSS 搜索页 Referer URL 模板")
    parser.add_argument("--list-api-url", default=NCSS_CONFIG["list_api_url"], help="覆盖 NCSS 公开职位列表接口")
    parser.add_argument("--fresh", action="store_true", help="忽略旧指纹、进度和检查点，重新采集")
    parser.add_argument("--skill-dict", default=DEFAULT_SKILL_DICT, help="技能词典 CSV 路径")
    parser.add_argument("--major-dict", default=DEFAULT_MAJOR_DICT, help="专业词典 CSV 路径")
    parser.add_argument("--data-root", default=DATA_ROOT, help="输出目录")
    return parser.parse_args()


def select_cities(names: Optional[List[str]]) -> List[Dict[str, str]]:
    if not names:
        return CITIES
    selected = []
    normalized = {name.strip() for name in names if name.strip()}
    for city in CITIES:
        if city["name"] in normalized:
            selected.append(city)
    missing = normalized - {city["name"] for city in selected}
    if missing:
        raise SystemExit(f"未知城市: {', '.join(sorted(missing))}\n可选城市: {', '.join(city['name'] for city in CITIES)}")
    return selected


def select_jobs(names: Optional[List[str]]) -> List[str]:
    if not names:
        return CORE_JOBS
    normalized_map = {re.sub(r"\s+", "", job): job for job in CORE_JOBS}
    selected = []
    missing = []
    for name in names:
        key = re.sub(r"\s+", "", name or "")
        if key in normalized_map:
            selected.append(normalized_map[key])
        else:
            missing.append(name)
    if missing:
        raise SystemExit(f"未知岗位: {', '.join(missing)}\n可选岗位: {', '.join(CORE_JOBS)}")
    return selected


def main() -> None:
    global DATA_ROOT, FINGERPRINT_DB
    args = parse_args()
    DATA_ROOT = os.path.abspath(args.data_root)
    FINGERPRINT_DB = os.path.join(DATA_ROOT, "ncss_fingerprints_standalone.db")

    if args.max_pages < 1:
        raise SystemExit("--max-pages 必须大于等于 1")
    if args.max_jobs < 0:
        raise SystemExit("--max-jobs 不能小于 0")
    if args.detail_retries < 1:
        raise SystemExit("--detail-retries 必须大于等于 1")
    if args.max_zero_valid_pages < 1:
        raise SystemExit("--max-zero-valid-pages 必须大于等于 1")
    if args.interval < 0 or args.detail_interval < 0:
        raise SystemExit("请求间隔不能小于 0")
    if args.login_timeout < 30:
        raise SystemExit("--login-timeout 不能小于 30 秒")
    if args.manual_recovery_timeout < 30:
        raise SystemExit("--manual-recovery-timeout 不能小于 30 秒")
    if args.manual_recovery_attempts < 1:
        raise SystemExit("--manual-recovery-attempts 必须大于等于 1")
    if args.auth_mode == "off" and (args.login or args.login_only):
        raise SystemExit("--login/--login-only 不能与 --auth-mode off 同时使用")

    NCSS_CONFIG["max_pages"] = args.max_pages
    NCSS_CONFIG["max_jobs_per_combo"] = args.max_jobs
    NCSS_CONFIG["request_interval"] = args.interval
    NCSS_CONFIG["detail_interval"] = args.detail_interval
    NCSS_CONFIG["max_detail_retries"] = args.detail_retries
    NCSS_CONFIG["max_zero_valid_pages_per_combo"] = args.max_zero_valid_pages
    NCSS_CONFIG["headless"] = not (args.headful or args.login or args.login_only or args.manual_recovery)
    NCSS_CONFIG["filter_title"] = not args.no_title_filter
    NCSS_CONFIG["fresh"] = bool(args.fresh)
    NCSS_CONFIG["auth_mode"] = args.auth_mode
    NCSS_CONFIG["interactive_login"] = bool(args.login or args.login_only)
    NCSS_CONFIG["login_only"] = bool(args.login_only)
    NCSS_CONFIG["login_timeout"] = args.login_timeout
    NCSS_CONFIG["manual_recovery"] = bool(args.manual_recovery)
    NCSS_CONFIG["manual_recovery_timeout"] = args.manual_recovery_timeout
    NCSS_CONFIG["manual_recovery_attempts"] = args.manual_recovery_attempts
    NCSS_CONFIG["profile_dir"] = os.path.abspath(args.profile_dir)
    if args.search_url_template:
        NCSS_CONFIG["search_url_template"] = args.search_url_template
    NCSS_CONFIG["list_api_url"] = args.list_api_url
    if args.fresh:
        NCSS_CONFIG["resume_completed"] = False

    cities = select_cities(args.city)
    jobs = select_jobs(args.job)
    dictionaries = DictionaryStore(args.skill_dict, args.major_dict)

    log("NCSS 独立版爬虫启动：不依赖 batch_crawler.py")
    log(f"采集城市: {', '.join(city['name'] for city in cities)}")
    log(f"采集岗位: {', '.join(jobs)}")
    log(f"技能词典: {args.skill_dict} | 版本: {dictionaries.skill_dict_ver} | 条目: {len(dictionaries.skill_entries)}")
    log(f"专业词典: {args.major_dict} | 版本: {dictionaries.major_dict_ver} | 条目: {len(dictionaries.major_entries)}")
    log(f"输出目录: {DATA_ROOT}")
    log(f"登录策略: {NCSS_CONFIG['auth_mode']} | 会话资料目录: {NCSS_CONFIG['profile_dir'] if args.auth_mode != 'off' else '不使用'}")
    if NCSS_CONFIG.get("manual_recovery"):
        log(
            "人工恢复: 已开启 | "
            f"等待 {NCSS_CONFIG['manual_recovery_timeout']} 秒 | "
            f"每页最多重试 {NCSS_CONFIG['manual_recovery_attempts']} 次"
        )
    log(
        "速度参数: "
        f"翻页/组合间隔 {NCSS_CONFIG['request_interval']} 秒 | "
        f"详情间隔 {NCSS_CONFIG['detail_interval']} 秒 | "
        f"连续零有效页阈值 {NCSS_CONFIG['max_zero_valid_pages_per_combo']} 页"
    )
    log(f"搜索URL模板: {NCSS_CONFIG['search_url_template']}")
    log(f"职位列表接口: {NCSS_CONFIG['list_api_url']}")
    log("合规提示: 登录/验证仅由使用者在官网手工完成；不自动处理账号、密码或验证码，不投递；触发限流会保存检查点并停止。")

    conn = init_db()
    try:
        crawler = StandaloneNCSSCrawler(conn, dictionaries)
        crawler.run(cities, jobs)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
