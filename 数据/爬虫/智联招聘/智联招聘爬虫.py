"""
智联招聘岗位采集脚本。

目标：
- 复用参考 notebook 的智联前端 JSON 接口请求方式。
- 使用本毕业设计自己的城市、岗位和字段口径。
- 输出与现有前程无忧/NCSS 采集结果兼容的中文表头 CSV，同时保留内部英文字段名。

合规边界：
- 只请求公开搜索接口，不登录、不绕过验证码或安全校验。
- 默认只采集小样本；完整批量任务需要显式指定。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import html
import json
import os
import random
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = BASE_DIR / "爬取数据"
SOURCE_KEY = "zhaopin"
SOURCE_NAME = "智联招聘"
CRAWLER_VERSION = "2026-06-25.1"
CSV_SCHEMA_VERSION = "job-posting-csv-v1"

SEARCH_API_URL = "https://fe-api.zhaopin.com/c/i/search/positions"
REQUEST_TIMEOUT = 25
REQUEST_RETRIES = 3
RETRY_BACKOFF_BASE = 1.2
RETRY_SLEEP_RANGE = (0.5, 1.5)
PAGE_SLEEP_RANGE = (2.0, 5.0)
EMPTY_PAGE_STOP = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.zhaopin.com",
    "Referer": "https://www.zhaopin.com/",
    "Connection": "keep-alive",
    "x-zp-page-code": "4019",
    "x-zp-platform": "13",
    "x-zp-business-system": "1",
}

SECURITY_MARKERS = (
    "Security Verification",
    "TencentEOCaptcha",
    "EO-Bot-Captcha-Token",
    "正在验证连接安全性",
    "Protected by Tencent Cloud EdgeOne",
)

# 本项目核心城市。代码来自参考 notebook 中已核对的智联 citymap 映射。
CORE_CITIES: List[Dict[str, str]] = [
    {"name": "北京", "code": "530"},
    {"name": "上海", "code": "538"},
    {"name": "广州", "code": "763"},
    {"name": "深圳", "code": "765"},
    {"name": "杭州", "code": "653"},
    {"name": "南京", "code": "635"},
    {"name": "武汉", "code": "736"},
    {"name": "成都", "code": "801"},
    {"name": "重庆", "code": "551"},
    {"name": "西安", "code": "854"},
]

CITY_CODE_MAP = {item["name"]: item["code"] for item in CORE_CITIES}

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
        "exclude_aliases": ["大数据开发"],
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

CORE_JOBS = [item["keyword"] for item in CORE_JOB_DEFINITIONS]
JOB_DEFINITION_MAP = {item["keyword"]: item for item in CORE_JOB_DEFINITIONS}

# 内部字段名与 CSV 中文表头。核心字段与项目需求文档、前程无忧/NCSS 输出保持一致。
JOB_FIELD_SPECS: List[Tuple[str, str]] = [
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

SUMMARY_FIELD_SPECS: List[Tuple[str, str]] = [
    ("source", "数据源"),
    ("keyword", "搜索岗位"),
    ("city", "搜索城市"),
    ("city_code", "城市编码"),
    ("status", "采集状态代码"),
    ("status_label", "采集状态说明"),
    ("valid_count", "有效岗位数"),
    ("raw_item_count", "API原始返回数"),
    ("duplicate_count", "重复过滤数"),
    ("irrelevant_title_count", "标题不相关过滤数"),
    ("source_total_count", "官网报告总数"),
    ("last_page_attempted", "最后尝试页"),
    ("last_successful_page", "最后成功页"),
    ("stop_reason", "停止原因"),
    ("csv_file", "输出CSV"),
    ("raw_jsonl_file", "原始JSONL"),
    ("log_file", "运行日志"),
]


def _alias_pattern(aliases: Sequence[str]) -> str:
    patterns = []
    for alias in sorted(aliases, key=len, reverse=True):
        escaped = re.escape(alias).replace(r"\ ", r"\s*")
        if alias and alias[0].isascii() and alias[0].isalnum():
            escaped = rf"(?<![A-Za-z0-9]){escaped}"
        if alias and alias[-1].isascii() and alias[-1].isalnum():
            escaped = rf"{escaped}(?![A-Za-z0-9])"
        patterns.append(escaped)
    return "|".join(patterns)


def _dictionary_candidates(filename: str) -> List[Path]:
    return [
        BASE_DIR / filename,
        BASE_DIR.parent / "国家大学生就业服务平台" / filename,
        BASE_DIR.parent / "前程无忧" / filename,
    ]


def load_alias_dictionary(filename: str, path: Optional[Path] = None) -> Tuple[List[Dict[str, str]], str]:
    selected = path
    if selected is None:
        selected = next((candidate for candidate in _dictionary_candidates(filename) if candidate.exists()), None)
    if selected is None or not selected.exists():
        return [], "missing"

    entries: List[Dict[str, str]] = []
    versions: List[str] = []
    with selected.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"canonical_name", "aliases", "category", "version"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{selected} 缺少字段：{', '.join(sorted(missing))}")
        for row in reader:
            canonical = (row.get("canonical_name") or "").strip()
            aliases = [x.strip() for x in (row.get("aliases") or "").split("|") if x.strip()]
            category = (row.get("category") or "").strip()
            version = (row.get("version") or "").strip()
            if not canonical or not aliases or not category or not version:
                raise ValueError(f"{selected} 存在不完整记录：{row}")
            entries.append(
                {
                    "canonical_name": canonical,
                    "aliases": aliases,
                    "category": category,
                    "version": version,
                    "pattern": _alias_pattern(aliases),
                }
            )
            versions.append(version)
    declared_version = ";".join(sorted(set(versions))) if versions else "unknown"
    content_digest = hashlib.sha256(selected.read_bytes()).hexdigest()[:8]
    return entries, f"{declared_version}+sha256:{content_digest}"


SKILL_ENTRIES, SKILL_DICT_VERSION = load_alias_dictionary("skill_dictionary.csv")
MAJOR_ENTRIES, MAJOR_DICT_VERSION = load_alias_dictionary("major_dictionary.csv")
SKILL_CATEGORY_BY_NAME = {item["canonical_name"]: item["category"] for item in SKILL_ENTRIES}
MAJOR_CATEGORY_BY_NAME = {item["canonical_name"]: item["category"] for item in MAJOR_ENTRIES}


def make_request_session(pool_size: int = 8) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def looks_like_security_verification(text: str) -> bool:
    return any(marker in text for marker in SECURITY_MARKERS)


def resolve_city_code(city: str, city_code: Optional[str] = None) -> str:
    if city_code:
        return str(city_code)
    if city in CITY_CODE_MAP:
        return CITY_CODE_MAP[city]
    raise ValueError(f"未知城市：{city}。可选城市：{', '.join(CITY_CODE_MAP)}")


def build_search_page_url(
    keyword: str,
    city_code: str,
    page: int,
    extra_params: Optional[Dict[str, Any]] = None,
) -> str:
    params: Dict[str, Any] = {"kw": keyword, "p": int(page)}
    if city_code:
        params["jl"] = str(city_code)
    if extra_params:
        for key, value in extra_params.items():
            if value not in (None, "") and key not in {
                "S_SOU_FULL_INDEX",
                "S_SOU_WORK_CITY",
                "pageIndex",
                "pageSize",
            }:
                params[key] = value
    return "https://www.zhaopin.com/sou/?" + urlencode(params)


def build_api_query_params() -> Dict[str, str]:
    return {
        "_v": f"{random.random():.8f}",
        "x-zp-page-request-id": f"{int(time.time() * 1000)}-{random.randint(100000, 999999)}",
        "x-zp-client-id": str(uuid.uuid4()),
    }


def build_search_payload(
    keyword: str,
    city_code: str,
    page: int,
    page_size: int = 20,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "S_SOU_FULL_INDEX": keyword,
        "pageIndex": int(page),
        "pageSize": int(page_size),
        "anonymous": 1,
        "eventScenario": "pcSearchedSouSearch",
        "platform": 13,
        "version": "0.0.0",
        "order": 4,
    }
    if city_code:
        payload["S_SOU_WORK_CITY"] = str(city_code)
    if extra_params:
        payload.update({k: v for k, v in extra_params.items() if v not in (None, "")})
    return payload


def retry_wait_seconds(attempt: int) -> float:
    base = RETRY_BACKOFF_BASE * (2 ** max(attempt - 1, 0))
    return min(base + random.uniform(*RETRY_SLEEP_RANGE), 30.0)


class PageRequestError(RuntimeError):
    """单页接口请求失败。"""


def _is_ok_code(value: Any) -> bool:
    return value in (None, 0, 200, "0", "200")


def fetch_position_page(
    keyword: str,
    city_code: str,
    page: int,
    page_size: int = 20,
    extra_params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = REQUEST_RETRIES,
    request_session: Optional[requests.Session] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    payload = build_search_payload(keyword, city_code, page, page_size, extra_params)
    page_url = build_search_page_url(keyword, city_code, page, extra_params)
    headers = dict(HEADERS)
    headers["Referer"] = page_url
    active_session = request_session or make_request_session()
    close_session = request_session is None
    last_error: Optional[BaseException] = None

    try:
        for attempt in range(1, retries + 1):
            try:
                response = active_session.post(
                    SEARCH_API_URL,
                    params=build_api_query_params(),
                    headers=headers,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=timeout,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:200]!r}")
                response.raise_for_status()
                text = response.text
                if looks_like_security_verification(text):
                    raise RuntimeError("接口返回安全验证页面，已停止当前页。")
                try:
                    result = response.json()
                except ValueError as exc:
                    raise RuntimeError(f"接口未返回 JSON：{text[:300]!r}") from exc
                if not _is_ok_code(result.get("code")) or not _is_ok_code(result.get("apiCode")):
                    raise RuntimeError(f"接口返回异常：{json.dumps(result, ensure_ascii=False)[:500]}")
                data = result.get("data") or {}
                if data.get("isVerification"):
                    raise RuntimeError("接口提示需要安全验证，已停止当前页。")
                if not isinstance(data.get("list", []), list):
                    raise RuntimeError("接口 data.list 不是列表。")
                return data, payload, response.url
            except (requests.ConnectionError, requests.Timeout, requests.ChunkedEncodingError, requests.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(retry_wait_seconds(attempt))
        raise PageRequestError(f"第 {page} 页连续 {retries} 次请求失败：{last_error!r}") from last_error
    finally:
        if close_session:
            active_session.close()


def extract_position_page_data(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int, bool]:
    jobs = data.get("list") or []
    count = int(data.get("count") or 0)
    is_end_page = bool(data.get("isEndPage"))
    return jobs, count, is_end_page


def get_path(obj: Any, path: Sequence[str], default: Any = "") -> Any:
    current = obj
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return default if current is None else current


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\u00a0", " ")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned: List[str] = []
    previous_blank = False
    for line in lines:
        if line:
            cleaned.append(line)
            previous_blank = False
        elif cleaned and not previous_blank:
            cleaned.append("")
            previous_blank = True
    return "\n".join(cleaned).strip()


def unique_join(values: Iterable[Any], sep: str = "|") -> str:
    seen = set()
    result = []
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return sep.join(result)


def parse_json_field(value: Any) -> Dict[str, Any]:
    if not value or not isinstance(value, str):
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def list_values(
    items: Any,
    keys: Tuple[str, ...] = ("name", "value", "tag", "itemValue", "title", "text", "label", "description"),
) -> List[str]:
    if not items:
        return []
    if isinstance(items, dict):
        items = [items]
    if isinstance(items, (str, int, float)):
        return [clean_text(items)]

    values = []
    for item in items:
        if isinstance(item, dict):
            for key in keys:
                if item.get(key) not in (None, ""):
                    values.append(item[key])
                    break
        else:
            values.append(item)
    return [clean_text(item) for item in values if clean_text(item)]


def key_value_items(items: Any, name_key: str = "name", value_key: str = "value") -> List[str]:
    if not items:
        return []
    values = []
    for item in items:
        if not isinstance(item, dict):
            values.append(clean_text(item))
            continue
        name = clean_text(item.get(name_key, ""))
        value = clean_text(item.get(value_key, ""))
        if name and value and name != value:
            values.append(f"{name}:{value}")
        elif name:
            values.append(name)
        elif value:
            values.append(value)
    return values


def collect_all_tags(job: Dict[str, Any]) -> str:
    detail = job.get("jobDetailData") or {}
    custom = detail.get("customAttributeInfo") or {}
    desc = get_path(detail, ["position", "desc"], {})
    tags: List[str] = []
    tags += list_values(job.get("jobSkillTags"), ("name", "value", "tag"))
    tags += list_values(job.get("skillLabel"), ("value", "name", "tag"))
    tags += list_values(job.get("showSkillTags"), ("tag", "name", "value"))
    tags += list_values(get_path(job, ["jobKeyword", "keywords"], []), ("itemValue", "name", "value"))
    tags += list_values(desc.get("labels"))
    tags += list_values(job.get("searchTagList"))
    tags += list_values(job.get("commercialLabel"))
    tags += list_values(custom.get("reportItems"))
    tags += key_value_items(custom.get("welfareItems"))
    tags += key_value_items(custom.get("workTimeItems"))
    if job.get("tagABC"):
        tags.append(job.get("tagABC"))
    return unique_join(tags)


def collect_skill_tags(job: Dict[str, Any]) -> str:
    tags: List[str] = []
    tags += list_values(job.get("jobSkillTags"), ("name", "value", "tag"))
    tags += list_values(job.get("skillLabel"), ("value", "name", "tag"))
    tags += list_values(job.get("showSkillTags"), ("tag", "name", "value"))
    return unique_join(tags)


def collect_welfare_tags(job: Dict[str, Any]) -> str:
    detail = job.get("jobDetailData") or {}
    custom = detail.get("customAttributeInfo") or {}
    desc = get_path(detail, ["position", "desc"], {})
    tags: List[str] = []
    tags += list_values(desc.get("welfareLabel"))
    tags += list_values(desc.get("welfareTags"))
    tags += list_values(job.get("welfareLabel"))
    tags += key_value_items(custom.get("welfareItems"))
    return unique_join(tags)


REQUIREMENT_HEADINGS = [
    "任职要求", "任职资格", "岗位要求", "职位要求", "工作要求", "任职条件",
    "招聘要求", "基本要求", "能力要求", "资格要求",
]
RESPONSIBILITY_HEADINGS = [
    "岗位职责", "工作职责", "职位描述", "工作内容", "职责描述", "岗位介绍", "主要职责",
]
OTHER_SECTION_HEADINGS = [
    "福利", "福利待遇", "薪资福利", "岗位亮点", "工作地点", "上班地址",
    "公司地址", "公司简介", "联系方式", "作息安排",
]


def _heading_pattern(headings: Sequence[str]) -> re.Pattern[str]:
    names = "|".join(sorted((re.escape(x) for x in headings), key=len, reverse=True))
    prefix = r"(?:(?:[一二三四五六七八九十]+|\d+)[、.．）)\s]*)?"
    return re.compile(rf"^[ \t]*{prefix}(?:{names})[ \t]*[:：]?[ \t]*", re.MULTILINE | re.IGNORECASE)


def extract_section(text: str, target_headings: Sequence[str]) -> str:
    value = clean_text(text)
    if not value:
        return ""
    target_match = _heading_pattern(target_headings).search(value)
    if not target_match:
        return ""
    boundary = _heading_pattern([*REQUIREMENT_HEADINGS, *RESPONSIBILITY_HEADINGS, *OTHER_SECTION_HEADINGS])
    following = boundary.search(value, target_match.end())
    end = following.start() if following else len(value)
    return clean_text(value[target_match.end():end]).strip(" :：")


def extract_requirement_text(text: str) -> str:
    return extract_section(text, REQUIREMENT_HEADINGS)


def extract_responsibility_text(text: str) -> str:
    result = extract_section(text, RESPONSIBILITY_HEADINGS)
    if result:
        return result
    value = clean_text(text)
    requirement = extract_requirement_text(value)
    if requirement and requirement in value:
        return clean_text(value.split(requirement, 1)[0])[:800]
    return value[:800]


MAJOR_CONTEXT_TERMS = (
    "学历", "本科", "大专", "硕士", "博士", "毕业", "相关专业",
    "专业背景", "专业方向", "专业类别", "类专业", "专业不限", "不限专业",
    "优先", "更佳", "加分",
)


def extract_major_evidence_text(text: str) -> str:
    value = clean_text(text)
    evidence: List[str] = []
    for line in re.split(r"[\n。；;]", value):
        line = line.strip()
        if line and any(term in line for term in MAJOR_CONTEXT_TERMS) and line not in evidence:
            evidence.append(line)
    return "\n".join(evidence)


def extract_major_candidates(text: str) -> Tuple[List[str], List[str], Dict[str, str]]:
    evidence_text = extract_major_evidence_text(text)
    raw_candidates: List[str] = []
    normalized_candidates: List[str] = []
    evidence: Dict[str, str] = {}
    if not evidence_text:
        return raw_candidates, normalized_candidates, evidence

    for entry in MAJOR_ENTRIES:
        canonical = entry["canonical_name"]
        for match in re.finditer(entry["pattern"], evidence_text, re.IGNORECASE):
            raw_value = match.group(0)
            if raw_value not in raw_candidates:
                raw_candidates.append(raw_value)
            if canonical not in normalized_candidates:
                normalized_candidates.append(canonical)
                evidence[canonical] = _evidence_snippet(evidence_text, match.start(), match.end())
            break
    return raw_candidates, normalized_candidates, evidence


def classify_major_requirement(text: str, majors: Sequence[str]) -> str:
    evidence_text = extract_major_evidence_text(text)
    if not evidence_text:
        return "未说明"
    unlimited = any(token in evidence_text for token in ("专业不限", "不限专业", "不限制专业"))
    preferred = any(token in evidence_text for token in ("优先", "更佳", "加分"))
    if unlimited and preferred:
        return "不限专业，相关专业优先"
    if unlimited:
        return "不限专业"
    if majors and preferred:
        return "相关专业优先"
    if majors:
        return "要求"
    return "未说明"


def _evidence_snippet(text: str, start: int, end: int, radius: int = 45) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return clean_text(text[left:right])


def _is_negated_skill_mention(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 16):start]
    suffix = text[end:min(len(text), end + 16)]
    prefix_pattern = re.compile(r"(?:不要求|不需要|不必|无需|无须)(?:具备|掌握|熟悉|了解|使用|会)?[\s、，,:：]*$")
    suffix_pattern = re.compile(r"^(?:经验)?(?:不限|不是必须|非必需)")
    return bool(prefix_pattern.search(prefix) or suffix_pattern.search(suffix))


def extract_skill_candidates(text: str) -> Tuple[List[str], Dict[str, str]]:
    value = clean_text(text)
    skills: List[str] = []
    evidence: Dict[str, str] = {}
    if not value or not SKILL_ENTRIES:
        return skills, evidence
    for entry in SKILL_ENTRIES:
        canonical = entry["canonical_name"]
        pattern = entry["pattern"]
        matched = None
        for match in re.finditer(pattern, value, re.IGNORECASE):
            if not _is_negated_skill_mention(value, match.start(), match.end()):
                matched = match
                break
        if matched:
            skills.append(canonical)
            evidence[canonical] = _evidence_snippet(value, matched.start(), matched.end())
    return skills, evidence


def normalize_title_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def text_contains_alias(text: str, alias: str) -> bool:
    normalized = normalize_title_text(text)
    target = normalize_title_text(alias)
    return bool(target and target in normalized)


def is_job_title_relevant(keyword: str, title: str) -> bool:
    definition = JOB_DEFINITION_MAP.get(keyword, {})
    aliases = definition.get("aliases") or [keyword]
    exclude_aliases = definition.get("exclude_aliases") or []
    return (
        bool(title)
        and any(text_contains_alias(title, alias) for alias in aliases)
        and not any(text_contains_alias(title, alias) for alias in exclude_aliases)
    )


def get_job_display_name(keyword: str) -> str:
    return JOB_DEFINITION_MAP.get(keyword, {}).get("display_name", keyword)


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def compute_source_fingerprint(job_id: Any, source_url: str = "") -> str:
    raw_key = clean_text(job_id) or canonicalize_url(source_url)
    if not raw_key:
        return ""
    return hashlib.sha256(f"{SOURCE_KEY}|{raw_key}".encode("utf-8")).hexdigest()[:16]


def compute_content_fingerprint(company_name: str, description: str) -> str:
    normalized = re.sub(r"\s+", "", clean_text(description)).casefold()
    raw = f"{company_name.strip()}|{normalized}"
    if not raw.strip("|"):
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _salary_number(value: str, unit: str) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    unit_lower = (unit or "").lower()
    if "万" in unit_lower:
        return number * 10000
    if "千" in unit_lower or "k" in unit_lower:
        return number * 1000
    return number


def parse_salary(salary_text: str) -> Tuple[Optional[int], Optional[int], int, str]:
    text = clean_text(salary_text)
    if not text or "面议" in text:
        return None, None, 12, "面议或未说明"

    month_match = re.search(r"(\d+)\s*薪", text)
    salary_months = int(month_match.group(1)) if month_match else 12
    daily = "天" in text or "/日" in text or "/天" in text
    annual = "年" in text or "年薪" in text
    period = "日薪" if daily else ("年薪" if annual else "月薪")

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(万|千|k|K|元)?\s*[-~至]\s*(\d+(?:\.\d+)?)\s*(万|千|k|K|元)?",
        text,
    )
    single_match = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|k|K|元)", text)

    low = high = None
    if range_match:
        unit1 = range_match.group(2) or range_match.group(4) or ""
        unit2 = range_match.group(4) or unit1
        low = _salary_number(range_match.group(1), unit1)
        high = _salary_number(range_match.group(3), unit2)
    elif single_match:
        low = high = _salary_number(single_match.group(1), single_match.group(2))

    if low is None or high is None:
        return None, None, salary_months, period
    if annual:
        low /= 12
        high /= 12
    elif daily:
        low *= 21.75
        high *= 21.75
    return int(round(low)), int(round(high)), salary_months, period


def parse_experience(experience_text: str) -> Tuple[str, Optional[int], Optional[int], str]:
    text = clean_text(experience_text)
    if not text:
        return "", None, None, ""
    if any(token in text for token in ("经验不限", "不限", "无经验")):
        return "经验不限", None, None, "不限"
    if "应届" in text or "在校" in text or "实习" in text:
        return text, 0, 0, "应届/实习"
    range_match = re.search(r"(\d+)\s*[-~至]\s*(\d+)\s*年", text)
    if range_match:
        return text, int(range_match.group(1)), int(range_match.group(2)), "区间"
    min_match = re.search(r"(\d+)\s*年(?:以上|及以上|\+)", text)
    if min_match:
        return text, int(min_match.group(1)), None, "下限"
    max_match = re.search(r"(\d+)\s*年(?:以下|以内)", text)
    if max_match:
        return text, None, int(max_match.group(1)), "上限"
    exact_match = re.search(r"(\d+)\s*年", text)
    if exact_match:
        value = int(exact_match.group(1))
        return text, value, value, "精确"
    return text, None, None, "文本"


def parse_publish_date(value: str, now: Optional[datetime] = None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    current = now or datetime.now()
    if "今天" in text:
        return current.strftime("%Y-%m-%d")
    if "昨天" in text:
        return (current - timedelta(days=1)).strftime("%Y-%m-%d")
    days_match = re.search(r"(\d+)\s*天前", text)
    if days_match:
        return (current - timedelta(days=int(days_match.group(1)))).strftime("%Y-%m-%d")
    full = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if full:
        return f"{int(full.group(1)):04d}-{int(full.group(2)):02d}-{int(full.group(3)):02d}"
    month_day = re.search(r"(\d{1,2})[-/.月](\d{1,2})", text)
    if month_day:
        return f"{current.year:04d}-{int(month_day.group(1)):02d}-{int(month_day.group(2)):02d}"
    return text


def flatten_job(job: Dict[str, Any], page: int, keyword: str, city: str, city_code: str) -> Dict[str, Any]:
    detail = job.get("jobDetailData") or {}
    position = detail.get("position") or {}
    base = position.get("base") or {}
    desc = position.get("desc") or {}
    workloc = detail.get("workLocation") or {}
    state = get_path(detail, ["stateInfo", "state"], {})
    card = parse_json_field(job.get("cardCustomJson"))

    source_job_id = job.get("jobId") or base.get("positionId")
    job_title = clean_text(job.get("name") or base.get("positionName"))
    source_url = canonicalize_url(job.get("positionURL") or job.get("positionUrl") or base.get("positionUrl") or "")
    company_name = clean_text(job.get("companyName") or card.get("companyName"))
    salary_text = clean_text(job.get("salary60") or base.get("salary") or card.get("salary60") or job.get("salaryReal"))
    salary_min, salary_max, salary_months, salary_period = parse_salary(salary_text)
    experience_raw = clean_text(job.get("workingExp") or base.get("positionWorkingExp"))
    experience, exp_min, exp_max, exp_type = parse_experience(experience_raw)
    publish_date_raw = clean_text(
        job.get("publishTime")
        or get_path(position, ["date", "positionPublishTime"])
        or get_path(position, ["date", "positionUpdateTimeText"])
    )
    raw_description = desc.get("description") or get_path(position, ["desc", "description"]) or job.get("jobSummary", "")
    job_description = clean_text(raw_description)
    requirement_text = extract_requirement_text(job_description)
    responsibility_text = extract_responsibility_text(job_description)
    major_candidates_raw, major_candidates, major_evidence = extract_major_candidates(job_description)
    major_categories = {major: MAJOR_CATEGORY_BY_NAME.get(major, "未映射") for major in major_candidates}
    major_requirement_level = classify_major_requirement(job_description, major_candidates)
    if major_candidates:
        major_source = "description"
        major_decision_note = "仅依据岗位正文中的专业要求语境抽取"
    else:
        major_source = "not_specified"
        major_decision_note = "岗位正文未识别到明确专业要求"

    desc_skills, desc_evidence = extract_skill_candidates(job_description)
    req_skills, req_evidence = extract_skill_candidates(requirement_text)
    if requirement_text and req_skills:
        skills = req_skills
        evidence = req_evidence
        skill_scope = "requirement_text"
    else:
        skills = desc_skills
        evidence = desc_evidence
        skill_scope = "job_description"
    skill_categories = {skill: SKILL_CATEGORY_BY_NAME.get(skill, "") for skill in skills}

    actual_city = clean_text(job.get("workCity") or workloc.get("positionWorkCity") or city)
    district = clean_text(job.get("cityDistrict") or workloc.get("positionCityDistrict"))
    quality_flags = []
    if not job_title:
        quality_flags.append("job_title_missing")
    if not company_name:
        quality_flags.append("company_missing")
    if not job_description:
        quality_flags.append("description_missing")
    if not skills:
        quality_flags.append("skill_candidate_missing")
    if not district:
        quality_flags.append("district_missing")

    is_relevant = is_job_title_relevant(keyword, job_title)
    if not is_relevant:
        quality_flags.append("title_not_relevant")

    return {
        "source": SOURCE_KEY,
        "crawler_version": CRAWLER_VERSION,
        "skill_dict_ver": SKILL_DICT_VERSION,
        "major_dict_ver": MAJOR_DICT_VERSION,
        "search_keyword": keyword,
        "search_city": city,
        "search_city_code": city_code,
        "source_job_id": source_job_id,
        "source_url": source_url,
        "crawl_time": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": compute_source_fingerprint(source_job_id, source_url),
        "content_fingerprint": compute_content_fingerprint(company_name, job_description),
        "job_title": job_title,
        "company_name": company_name,
        "city": actual_city,
        "district": district,
        "business_area": unique_join([job.get("tradingArea"), job.get("streetName"), workloc.get("tradingArea")]),
        "salary_text": salary_text,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_months": salary_months,
        "salary_period": salary_period,
        "education": clean_text(job.get("education") or base.get("education")),
        "education_raw": clean_text(job.get("education") or base.get("education")),
        "experience": experience,
        "experience_raw": experience_raw,
        "experience_min_years": exp_min,
        "experience_max_years": exp_max,
        "experience_requirement_type": exp_type,
        "job_type": clean_text(job.get("workType") or base.get("workType")),
        "work_mode": clean_text(job.get("workMode") or state.get("workModeDesc") or state.get("workMode")),
        "industry": clean_text(job.get("industryName")),
        "company_size": clean_text(job.get("companySize")),
        "company_type": clean_text(job.get("propertyName") or job.get("property")),
        "financing_stage": clean_text(job.get("financingStage") or card.get("strengthLabel")),
        "publish_date": parse_publish_date(publish_date_raw),
        "publish_date_raw": publish_date_raw,
        "longitude": clean_text(workloc.get("longitude")),
        "latitude": clean_text(workloc.get("latitude")),
        "platform_major_validation": "not_provided",
        "major_candidates_raw": major_candidates_raw,
        "major_candidates": major_candidates,
        "major_categories": major_categories,
        "major_requirement_level": major_requirement_level,
        "major_evidence": major_evidence,
        "major_source": major_source,
        "major_decision_note": major_decision_note,
        "job_description_raw": clean_text(raw_description),
        "job_description": job_description,
        "requirement_text": requirement_text,
        "responsibility_text": responsibility_text,
        "job_tags": collect_all_tags(job),
        "welfare_tags": collect_welfare_tags(job),
        "platform_skill_tags": collect_skill_tags(job),
        "description_skill_candidates": desc_skills,
        "requirement_skill_candidates": req_skills,
        "skill_candidates": skills,
        "skill_categories": skill_categories,
        "skill_evidence": evidence,
        "skill_extraction_scope": skill_scope,
        "is_title_relevant": is_relevant,
        "quality_flags": quality_flags,
        "_page": page,
        "_raw_json": json.dumps(job, ensure_ascii=False),
    }


@dataclass
class CrawlResult:
    keyword: str
    city: str
    city_code: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    raw_items: List[Dict[str, Any]] = field(default_factory=list)
    page_logs: List[Dict[str, Any]] = field(default_factory=list)
    duplicate_count: int = 0
    irrelevant_title_count: int = 0
    stop_reason: str = ""
    source_total_count: int = 0

    @property
    def status(self) -> str:
        if self.stop_reason.startswith("error:"):
            return "partial" if self.rows else "failed"
        return "success"

    @property
    def status_label(self) -> str:
        if self.status == "success":
            return "采集完成"
        if self.status == "partial":
            return "部分成功，后续页面失败"
        return "采集失败"

    @property
    def last_page_attempted(self) -> int:
        return max((int(item.get("page") or 0) for item in self.page_logs), default=0)

    @property
    def last_successful_page(self) -> int:
        return max((int(item.get("page") or 0) for item in self.page_logs if not item.get("error")), default=0)


def crawl_combo(
    keyword: str,
    city: str,
    city_code: Optional[str] = None,
    start_page: int = 1,
    max_pages: int = 1,
    page_size: int = 20,
    max_jobs: int = 0,
    extra_params: Optional[Dict[str, Any]] = None,
    empty_page_stop: int = EMPTY_PAGE_STOP,
    filter_title: bool = True,
    page_sleep_range: Tuple[float, float] = PAGE_SLEEP_RANGE,
    timeout: int = REQUEST_TIMEOUT,
) -> CrawlResult:
    resolved_city_code = resolve_city_code(city, city_code)
    result = CrawlResult(keyword=keyword, city=city, city_code=resolved_city_code)
    session = make_request_session()
    seen_fingerprints = set()
    seen_content_fingerprints = set()
    empty_pages = 0
    stop_page = start_page + max_pages - 1

    try:
        for page in range(start_page, stop_page + 1):
            page_url = build_search_page_url(keyword, resolved_city_code, page, extra_params)
            try:
                data, payload, api_url = fetch_position_page(
                    keyword=keyword,
                    city_code=resolved_city_code,
                    page=page,
                    page_size=page_size,
                    extra_params=extra_params,
                    timeout=timeout,
                    request_session=session,
                )
            except PageRequestError as exc:
                result.page_logs.append(
                    {
                        "page": page,
                        "url": page_url,
                        "api_url": SEARCH_API_URL,
                        "jobs": 0,
                        "position_count": result.source_total_count,
                        "is_end_page": "",
                        "error": repr(exc),
                    }
                )
                result.stop_reason = f"error: {exc}"
                break

            jobs, source_total_count, is_end_page = extract_position_page_data(data)
            result.source_total_count = source_total_count or result.source_total_count
            result.page_logs.append(
                {
                    "page": page,
                    "url": page_url,
                    "api_url": api_url,
                    "jobs": len(jobs),
                    "position_count": source_total_count,
                    "is_end_page": int(is_end_page),
                    "error": "",
                    "request_payload": json.dumps(payload, ensure_ascii=False),
                }
            )

            if not jobs:
                empty_pages += 1
                if empty_pages >= empty_page_stop:
                    result.stop_reason = "empty_pages_reached"
                    break
            else:
                empty_pages = 0

            for item in jobs:
                raw_item = dict(item)
                raw_item["_page"] = page
                result.raw_items.append(raw_item)
                row = flatten_job(raw_item, page=page, keyword=keyword, city=city, city_code=resolved_city_code)
                if row["fingerprint"] and row["fingerprint"] in seen_fingerprints:
                    result.duplicate_count += 1
                    continue
                if row["content_fingerprint"] and row["content_fingerprint"] in seen_content_fingerprints:
                    result.duplicate_count += 1
                    continue
                if filter_title and not row["is_title_relevant"]:
                    result.irrelevant_title_count += 1
                    continue
                seen_fingerprints.add(row["fingerprint"])
                seen_content_fingerprints.add(row["content_fingerprint"])
                result.rows.append(row)
                if max_jobs > 0 and len(result.rows) >= max_jobs:
                    result.stop_reason = "max_jobs_reached"
                    return result

            if is_end_page:
                result.stop_reason = "source_exhausted"
                break
            if page < stop_page:
                time.sleep(random.uniform(*page_sleep_range))
        if not result.stop_reason:
            result.stop_reason = "max_pages_reached"
        return result
    finally:
        session.close()


def flatten_output_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        return "|".join(str(item).replace("|", "/") for item in value)
    if isinstance(value, dict):
        return " || ".join(f"{key}=>{val}" for key, val in value.items())
    return str(value)


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]', "_", value.strip())
    return value or "未命名"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.remove(temp_name)
        except OSError:
            pass
        raise


def write_csv_by_specs(path: Path, rows: List[Dict[str, Any]], specs: List[Tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    labels = [label for _, label in specs]
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=labels)
            writer.writeheader()
            for index, row in enumerate(rows, 1):
                internal = dict(row)
                internal.setdefault("record_no", index)
                writer.writerow({label: flatten_output_value(internal.get(key, "")) for key, label in specs})
        os.replace(temp_name, path)
    except Exception:
        try:
            os.remove(temp_name)
        except OSError:
            pass
        raise


def write_raw_jsonl(path: Path, raw_items: List[Dict[str, Any]]) -> None:
    lines = [json.dumps(item, ensure_ascii=False) for item in raw_items]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def write_combo_outputs(result: CrawlResult, run_dir: Path, timestamp: str) -> Dict[str, str]:
    display = get_job_display_name(result.keyword)
    combo_dir = run_dir / safe_filename(display)
    prefix = f"{safe_filename(display)}_{safe_filename(result.city)}_{timestamp}_zhaopin"
    csv_file = combo_dir / f"{prefix}_jobs.csv"
    raw_file = combo_dir / f"{prefix}_raw_items.jsonl"
    log_file = combo_dir / f"{prefix}_run_log.txt"
    write_csv_by_specs(csv_file, result.rows, JOB_FIELD_SPECS)
    write_raw_jsonl(raw_file, result.raw_items)
    log_text = "\n".join(
        [
            "智联招聘采集运行日志",
            f"时间: {timestamp}",
            f"岗位: {result.keyword}",
            f"城市: {result.city} ({result.city_code})",
            f"有效岗位数: {len(result.rows)}",
            f"API原始岗位数: {len(result.raw_items)}",
            f"重复过滤数: {result.duplicate_count}",
            f"标题不相关过滤数: {result.irrelevant_title_count}",
            f"官网报告总数: {result.source_total_count}",
            f"停止原因: {result.stop_reason}",
            f"输出CSV: {csv_file.name}",
            f"原始JSONL: {raw_file.name}",
        ]
    )
    atomic_write_text(log_file, log_text + "\n")
    return {"csv_file": str(csv_file), "raw_jsonl_file": str(raw_file), "log_file": str(log_file)}


def summary_row(result: CrawlResult, output_paths: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    output_paths = output_paths or {}
    return {
        "source": SOURCE_KEY,
        "keyword": result.keyword,
        "city": result.city,
        "city_code": result.city_code,
        "status": result.status,
        "status_label": result.status_label,
        "valid_count": len(result.rows),
        "raw_item_count": len(result.raw_items),
        "duplicate_count": result.duplicate_count,
        "irrelevant_title_count": result.irrelevant_title_count,
        "source_total_count": result.source_total_count,
        "last_page_attempted": result.last_page_attempted,
        "last_successful_page": result.last_successful_page,
        "stop_reason": result.stop_reason,
        "csv_file": output_paths.get("csv_file", ""),
        "raw_jsonl_file": output_paths.get("raw_jsonl_file", ""),
        "log_file": output_paths.get("log_file", ""),
    }


def classify_city_sample(valid_count: int, descriptive_min: int = 30, analysis_min: int = 50) -> Dict[str, Any]:
    if valid_count < descriptive_min:
        return {
            "city_sample_level": "descriptive_only",
            "city_sample_label": "样本较少，仅作描述性分析",
        }
    if valid_count < analysis_min:
        return {
            "city_sample_level": "limited",
            "city_sample_label": "可以分析，但需说明样本量限制",
        }
    return {
        "city_sample_level": "sufficient",
        "city_sample_label": "适合一般城市特征分析",
    }


def add_city_balance_weights(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stratum_counts: Dict[Tuple[str, str], int] = {}
    for row in rows:
        key = (row.get("search_keyword", ""), row.get("search_city", ""))
        stratum_counts[key] = stratum_counts.get(key, 0) + 1

    weighted_rows: List[Dict[str, Any]] = []
    for row in rows:
        key = (row.get("search_keyword", ""), row.get("search_city", ""))
        count = stratum_counts.get(key, 0)
        sample = classify_city_sample(count)
        weighted_rows.append(
            {
                **row,
                "city_role_sample_size": count,
                "city_sample_level": sample["city_sample_level"],
                "city_sample_label": sample["city_sample_label"],
                "analysis_weight": (1.0 / count) if count else "",
            }
        )
    return weighted_rows


def select_cities(names: Optional[Sequence[str]] = None) -> List[Dict[str, str]]:
    if not names:
        return CORE_CITIES
    requested = {name.strip() for name in names if name.strip()}
    selected = [item for item in CORE_CITIES if item["name"] in requested]
    missing = sorted(requested - {item["name"] for item in selected})
    if missing:
        raise ValueError(f"未知城市：{', '.join(missing)}。可选城市：{', '.join(CITY_CODE_MAP)}")
    return selected


def select_keywords(keywords: Optional[Sequence[str]] = None) -> List[str]:
    if not keywords:
        return CORE_JOBS
    normalized_map = {normalize_title_text(job): job for job in CORE_JOBS}
    selected = []
    missing = []
    for keyword in keywords:
        key = normalize_title_text(keyword)
        if key in normalized_map:
            selected.append(normalized_map[key])
        elif keyword.strip():
            missing.append(keyword.strip())
    if missing:
        raise ValueError(f"未知岗位：{', '.join(missing)}。可选岗位：{', '.join(CORE_JOBS)}")
    return selected


def resolve_city_worker_count(max_city_workers: int, city_count: int) -> int:
    try:
        requested = int(max_city_workers)
    except (TypeError, ValueError):
        requested = 1
    return max(1, min(requested, city_count or 1))


def run_batch(
    cities: Optional[Sequence[str]] = None,
    keywords: Optional[Sequence[str]] = None,
    max_pages: int = 1,
    page_size: int = 20,
    max_jobs_per_combo: int = 20,
    output_root: Path = OUTPUT_ROOT,
    filter_title: bool = True,
    page_sleep_range: Tuple[float, float] = PAGE_SLEEP_RANGE,
    timeout: int = REQUEST_TIMEOUT,
    max_city_workers: int = 1,
) -> Dict[str, Any]:
    selected_cities = select_cities(cities)
    selected_keywords = select_keywords(keywords)
    city_worker_count = resolve_city_worker_count(max_city_workers, len(selected_cities))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root
    all_rows: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []

    for keyword in selected_keywords:
        keyword_results: List[Tuple[int, CrawlResult, Dict[str, str]]] = []

        if city_worker_count == 1:
            for city_index, city in enumerate(selected_cities):
                print(f"[智联] 岗位={get_job_display_name(keyword)} 城市={city['name']}")
                result = crawl_combo(
                    keyword=keyword,
                    city=city["name"],
                    city_code=city["code"],
                    max_pages=max_pages,
                    page_size=page_size,
                    max_jobs=max_jobs_per_combo,
                    filter_title=filter_title,
                    page_sleep_range=page_sleep_range,
                    timeout=timeout,
                )
                output_paths = write_combo_outputs(result, run_dir, timestamp)
                keyword_results.append((city_index, result, output_paths))
        else:
            print(
                f"[智联] 岗位={get_job_display_name(keyword)}，"
                f"多城市并发数={city_worker_count}，城市数={len(selected_cities)}"
            )
            with ThreadPoolExecutor(max_workers=city_worker_count) as executor:
                future_to_city = {
                    executor.submit(
                        crawl_combo,
                        keyword=keyword,
                        city=city["name"],
                        city_code=city["code"],
                        max_pages=max_pages,
                        page_size=page_size,
                        max_jobs=max_jobs_per_combo,
                        filter_title=filter_title,
                        page_sleep_range=page_sleep_range,
                        timeout=timeout,
                    ): (city_index, city)
                    for city_index, city in enumerate(selected_cities)
                }
                for future in as_completed(future_to_city):
                    city_index, city = future_to_city[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = CrawlResult(
                            keyword=keyword,
                            city=city["name"],
                            city_code=city["code"],
                            stop_reason=f"error: {type(exc).__name__}: {exc}",
                        )
                    output_paths = write_combo_outputs(result, run_dir, timestamp)
                    print(
                        f"[智联] 完成 岗位={get_job_display_name(keyword)} "
                        f"城市={city['name']} 有效={len(result.rows)} 状态={result.status_label}"
                    )
                    keyword_results.append((city_index, result, output_paths))

        for _, result, output_paths in sorted(keyword_results, key=lambda item: item[0]):
            summaries.append(summary_row(result, output_paths))
            all_rows.extend(result.rows)

    combined_file = run_dir / f"智联招聘岗位分析数据_{timestamp}.csv"
    summary_file = run_dir / f"智联招聘爬取质量汇总_{timestamp}.csv"
    weighted_rows = add_city_balance_weights(all_rows)
    write_csv_by_specs(combined_file, weighted_rows, JOB_FIELD_SPECS + ANALYSIS_EXTRA_FIELD_SPECS)
    write_csv_by_specs(summary_file, summaries, SUMMARY_FIELD_SPECS)
    return {
        "run_dir": str(run_dir),
        "combined_file": str(combined_file),
        "summary_file": str(summary_file),
        "row_count": len(all_rows),
        "combo_count": len(summaries),
        "summaries": summaries,
    }


def _split_csv_arg(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="智联招聘岗位采集器（项目字段版）")
    parser.add_argument("--city", default="重庆", help="城市名，逗号分隔；默认只采重庆")
    parser.add_argument("--keyword", default="数据分析师", help="岗位关键词，逗号分隔；默认只采数据分析师")
    parser.add_argument("--all", action="store_true", help="采集全部 10 个核心城市 x 8 个核心岗位")
    parser.add_argument("--max-pages", type=int, default=1, help="每个城市×岗位最多页数")
    parser.add_argument("--page-size", type=int, default=20, help="每页岗位数")
    parser.add_argument("--max-jobs-per-combo", type=int, default=20, help="每个城市×岗位有效岗位上限，0=不限")
    parser.add_argument("--output-dir", default=str(OUTPUT_ROOT), help="输出目录")
    parser.add_argument("--no-title-filter", action="store_true", help="不按岗位标题白名单过滤")
    parser.add_argument("--sleep-min", type=float, default=PAGE_SLEEP_RANGE[0], help="翻页最小等待秒数")
    parser.add_argument("--sleep-max", type=float, default=PAGE_SLEEP_RANGE[1], help="翻页最大等待秒数")
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT, help="单次请求超时秒数")
    parser.add_argument("--max-city-workers", type=int, default=1, help="同一岗位下并发爬取城市数，默认 1")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    args = build_arg_parser().parse_args(argv)
    if args.all:
        cities = None
        keywords = None
    else:
        cities = _split_csv_arg(args.city)
        keywords = _split_csv_arg(args.keyword)
    result = run_batch(
        cities=cities,
        keywords=keywords,
        max_pages=args.max_pages,
        page_size=args.page_size,
        max_jobs_per_combo=args.max_jobs_per_combo,
        output_root=Path(args.output_dir),
        filter_title=not args.no_title_filter,
        page_sleep_range=(args.sleep_min, args.sleep_max),
        timeout=args.timeout,
        max_city_workers=args.max_city_workers,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "summaries"}, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
