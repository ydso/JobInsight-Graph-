"""
前程无忧(51job) 批量采集脚本
============================
功能：批量爬取 10个核心城市 × 8个核心岗位 的招聘数据
数据组织：按岗位分文件夹，每个岗位内保存10个城市的采集文件
存储格式：原始API JSON + 解析CSV + 指纹库（断点续采）

城市编码对照：
  北京 010000  上海 020000  广州 030200  深圳 040000
  杭州 080200  南京 070200  武汉 180200  成都 090200
  重庆 060000  西安 200200

核心岗位（8个）：
  数据分析师、BI分析师、数据开发工程师、大数据开发工程师、
  数据仓库工程师、Python开发工程师、机器学习工程师、算法工程师

合规原则：
  - 仅采集公开可访问的岗位信息
  - 单线程串行，请求间隔 ≥ 5秒
  - 仅用于毕业设计学术研究
  - 不绕过验证码、登录等安全机制
"""

import argparse
import csv
import html
import json
import re
import os
import sqlite3
import sys
import time
import hashlib
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ============================================================
# 路径与全局常量
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DICT_FILE = os.path.join(BASE_DIR, "skill_dictionary.csv")
MAJOR_DICT_FILE = os.path.join(BASE_DIR, "major_dictionary.csv")
FINGERPRINT_DB = os.path.join(BASE_DIR, "fingerprints.db")
DATA_ROOT = os.path.join(BASE_DIR, "爬取数据")
CRAWLER_VERSION = "2026-06-23.1"
CHECKPOINT_VERSION = 7  # v7 起专业仅依据正文证据，CSV统一使用中文表头

# ============================================================
# 城市 & 岗位配置
# ============================================================
CITIES = [
    {"code": "060000", "name": "重庆"},
    {"code": "010000", "name": "北京"},
    {"code": "020000", "name": "上海"},
    {"code": "030200", "name": "广州"},
    {"code": "040000", "name": "深圳"},
    {"code": "080200", "name": "杭州"},
    {"code": "070200", "name": "南京"},
    {"code": "180200", "name": "武汉"},
    {"code": "090200", "name": "成都"},
    {"code": "200200", "name": "西安"},
]

CORE_JOBS = [
    "数据分析师",
    "BI分析师",
    "数据开发工程师",
    "大数据开发工程师",
    "数据仓库工程师",
    "Python开发工程师",
    "机器学习工程师",
    "算法工程师",
]

# 搜索关键词保持紧凑写法，目录和文件名使用统一的展示名称。
JOB_DISPLAY_NAMES = {
    "数据分析师": "数据分析师",
    "BI分析师": "BI 分析师",
    "数据开发工程师": "数据开发工程师",
    "大数据开发工程师": "大数据开发工程师",
    "数据仓库工程师": "数据仓库工程师",
    "Python开发工程师": "Python 开发工程师",
    "机器学习工程师": "机器学习工程师",
    "算法工程师": "算法工程师",
}


def get_job_display_name(keyword):
    """返回用于目录和文件名的规范岗位名称。"""
    normalized = re.sub(r"\s+", "", keyword or "")
    return JOB_DISPLAY_NAMES.get(normalized, (keyword or "").strip())


JOB_TITLE_INCLUDE_RULES = {
    "数据分析师": ("数据分析",),
    "bi分析师": ("bi分析", "bi工程师", "bi数据", "商业智能", "businessintelligence"),
    "数据开发工程师": ("数据开发",),
    "大数据开发工程师": ("大数据开发",),
    "数据仓库工程师": ("数据仓库", "数仓"),
    "python开发工程师": ("python开发", "python工程师", "python后端"),
    "机器学习工程师": ("机器学习", "machinelearning", "ml工程师", "mlengineer"),
    "算法工程师": ("算法工程师", "算法研发", "算法研究", "算法开发"),
}

JOB_TITLE_EXCLUDE_RULES = {
    # “大数据开发”属于单独岗位，避免同时落入“数据开发工程师”。
    "数据开发工程师": ("大数据开发",),
}


def _normalize_title_text(value):
    return re.sub(r"\s+", "", value or "").casefold()


def derive_job_title_terms(keyword):
    """返回目标岗位的保守标题白名单；未知岗位回退到去后缀后的核心词。"""
    normalized_keyword = _normalize_title_text(keyword)
    if normalized_keyword in JOB_TITLE_INCLUDE_RULES:
        return JOB_TITLE_INCLUDE_RULES[normalized_keyword]
    core = normalized_keyword
    for suffix in ("高级工程师", "工程师", "专员", "主管", "经理", "师", "员"):
        if core.endswith(suffix) and len(core) > len(suffix) + 1:
            core = core[:-len(suffix)]
            break
    return (core,) if core else ()


def is_job_title_relevant(keyword, title):
    normalized_keyword = _normalize_title_text(keyword)
    normalized_title = _normalize_title_text(title)
    include_terms = derive_job_title_terms(keyword)
    exclude_terms = JOB_TITLE_EXCLUDE_RULES.get(normalized_keyword, ())
    return (
        bool(normalized_title)
        and any(_normalize_title_text(term) in normalized_title for term in include_terms)
        and not any(_normalize_title_text(term) in normalized_title for term in exclude_terms)
    )


def canonicalize_job_url(url):
    """移除搜索追踪参数，保留稳定的官网岗位详情地址。"""
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def api_response_page_num(url):
    """读取官网搜索 API 的实际页码；无法识别时返回 None。"""
    try:
        values = parse_qs(urlsplit(url).query).get("pageNum", [])
        return int(values[0]) if values else None
    except (TypeError, ValueError):
        return None

# ============================================================
# 采集参数
# ============================================================
CRAWL_CONFIG = {
    "max_jobs_per_combo": 0,        # 0=不设置城市×岗位有效数据硬上限
    "min_raw_jobs_per_combo": 0,    # 已停用，仅保留旧命令兼容
    "role_valid_target": 500,       # 每个岗位跨全部城市的有效数据总体目标
    "city_descriptive_min": 30,     # 少于30条仅作描述性分析
    "city_analysis_min": 50,        # 50条起适合一般城市特征分析
    "page_size": 20,
    "max_pages": 50,                 # 官网搜索结果通常最多1000条（20×50页）
    "max_consecutive_empty_pages": 3,
    "min_pages_before_relevance_stop": 5,
    "max_consecutive_zero_valid_pages": 5,
    "max_page_retries": 3,          # 单页超时/无API响应的最大重试次数
    "max_combo_retries": 2,         # 待补采组合在本批次末尾的最大重试次数
    "retry_backoff_sec": 20,        # 单页重试退避基数
    "combo_retry_delay_sec": 60,    # 待补采组合再次执行前的等待时间
    "resume_completed": True,       # 重启批次时跳过已终态完成的组合
    "request_interval": 6,           # 翻页间隔
    "page_load_timeout": 40,
    "data_wait_timeout": 18,
    "search_type": 2,
    "headless": True,
    "viewport_width": 1920,
    "viewport_height": 1080,
    "filter_title": True,            # 仅保留与目标岗位标题白名单匹配的结果
}

# CSV对外使用中文表头；代码内部仍使用稳定的英文键名。
JOB_OUTPUT_FIELD_SPECS = [
    ("record_no", "序号"), ("source", "数据源"),
    ("crawler_version", "爬虫版本"), ("skill_dict_ver", "技能词典版本"),
    ("major_dict_ver", "专业词典版本"), ("search_keyword", "搜索岗位"),
    ("search_city", "搜索城市"), ("search_city_code", "搜索城市编码"),
    ("source_job_id", "来源岗位ID"), ("source_url", "来源链接"),
    ("crawl_time", "采集时间"), ("fingerprint", "来源岗位指纹"),
    ("content_fingerprint", "内容指纹"), ("job_title", "岗位名称"),
    ("company_name", "公司名称"), ("city", "实际城市"),
    ("district", "区县"), ("salary_text", "薪资原文"),
    ("salary_min", "最低月薪"), ("salary_max", "最高月薪"),
    ("salary_months", "年薪月数"), ("education", "学历要求"),
    ("education_raw", "学历原文"), ("experience", "经验要求"),
    ("experience_raw", "经验原文"), ("experience_min_years", "最低经验年限"),
    ("experience_max_years", "最高经验年限"),
    ("experience_requirement_type", "经验要求类型"), ("job_type", "岗位类型"),
    ("industry", "行业"), ("company_size", "公司规模"),
    ("company_type", "公司性质"), ("publish_date", "发布日期"),
    ("publish_date_raw", "发布日期原文"), ("longitude", "经度"),
    ("latitude", "纬度"), ("platform_major_validation", "API隐藏专业标签核验状态"),
    ("major_candidates_raw", "正文专业候选原文"),
    ("major_candidates", "正文确认标准专业"), ("major_categories", "专业类别"),
    ("major_requirement_level", "专业要求级别"), ("major_evidence", "专业证据"),
    ("major_source", "专业判定来源"), ("major_decision_note", "专业判定说明"),
    ("job_description_raw", "岗位描述原文"),
    ("job_description", "岗位描述清洗文本"),
    ("requirement_text", "任职要求文本"), ("responsibility_text", "岗位职责文本"),
    ("job_tags", "官网岗位标签"),
    ("description_skill_candidates", "描述技能候选"),
    ("requirement_skill_candidates", "任职要求技能候选"),
    ("skill_candidates", "确认技能候选"), ("skill_categories", "技能类别"),
    ("skill_evidence", "技能证据"), ("skill_extraction_scope", "技能提取范围"),
    ("quality_flags", "数据质量标记"),
]

ANALYSIS_EXTRA_FIELD_SPECS = [
    ("city_role_sample_size", "城市岗位样本量"),
    ("city_sample_level", "城市样本等级代码"),
    ("city_sample_label", "城市样本等级说明"),
    ("analysis_weight", "城市等权分析权重"),
]

QUALITY_OUTPUT_FIELD_SPECS = [
    ("record_type", "记录类型"), ("job_name", "岗位名称"),
    ("keyword", "搜索岗位"), ("city", "搜索城市"), ("city_code", "城市编码"),
    ("status", "采集状态代码"), ("status_label", "采集状态说明"),
    ("passed", "是否正常完成"), ("valid_count", "有效岗位数"),
    ("raw_item_count", "API原始返回数"), ("raw_unique_count", "原始唯一岗位数"),
    ("sample_level", "样本等级代码"), ("sample_label", "样本等级说明"),
    ("source_total_count", "官网报告总数"), ("stop_reason", "停止原因"),
    ("last_page_attempted", "最后尝试页"),
    ("last_successful_page", "最后成功页"),
    ("zero_valid_streak", "结束时连续零有效页"), ("retryable", "是否待补采"),
    ("retry_count", "重试次数"), ("checkpoint_file", "检查点文件"),
    ("duplicate_count", "跳过重复数"),
    ("irrelevant_title_count", "标题不相关过滤数"),
    ("invalid_record_count", "无效记录过滤数"), ("parse_error_count", "解析错误数"),
    ("role_target", "岗位总体目标"), ("role_target_met", "岗位总体是否达标"),
    ("role_shortfall", "岗位总体缺口"), ("city_count", "城市数量"),
    ("city_counts", "各城市有效岗位数"),
]

VALIDATION_STATUS_LABELS = {
    "relevance_exhausted": "连续多页无目标岗位（相关结果已基本耗尽）",
    "source_exhausted": "官网结果已采集完毕",
    "max_pages_reached": "达到最大翻页安全上限",
    "optional_cap_reached": "达到可选单组合有效岗位上限",
    "source_exhausted_insufficient": "官网页数采集完毕，原始数据不足",
    "max_pages_insufficient": "达到最大翻页数，原始数据不足",
    "empty_pages_insufficient": "连续空页，原始数据不足",
    "verification_interrupted": "网站认证中断，等待补采",
    "page_retry_exhausted": "单页重试耗尽，等待补采",
    "valid_target_below_raw_minimum": "有效数据目标先达到，但原始数据未达保底值",
    "collection_incomplete": "采集未完成，原始数据不足",
    "combo_failed": "组合采集失败",
}


def source_has_more_pages(page_num, page_size, total_count, source_item_count):
    """根据官网原始结果判断是否还有下一页。"""
    if source_item_count <= 0:
        return False
    if isinstance(total_count, int) and total_count >= 0:
        return page_num * page_size < total_count
    return source_item_count >= page_size


def should_stop_for_relevance(
    page_num,
    zero_valid_streak,
    minimum_pages=5,
    maximum_zero_valid_pages=5,
):
    """达到最少探索页数后，连续多页无新增有效岗位则及时停止。"""
    return (
        page_num >= minimum_pages
        and zero_valid_streak >= maximum_zero_valid_pages
    )


def classify_city_sample(valid_count, descriptive_min=30, analysis_min=50):
    """按有效岗位数量标记城市×岗位样本的适用范围。"""
    if valid_count < descriptive_min:
        level = "descriptive_only"
        label = "样本较少，仅作描述性分析"
    elif valid_count < analysis_min:
        level = "limited"
        label = "可以分析，但需说明样本量限制"
    else:
        level = "sufficient"
        label = "适合一般城市特征分析"
    return {
        "sample_level": level,
        "sample_label": label,
        "valid_count": valid_count,
    }


def evaluate_collection(valid_count, stop_reason):
    """城市组合不再按500条判失败，只区分正常结束与可恢复中断。"""
    status = {
        "relevance_exhausted": "relevance_exhausted",
        "source_exhausted": "source_exhausted",
        "consecutive_empty_pages": "source_exhausted",
        "max_pages_reached": "max_pages_reached",
        "valid_cap_reached": "optional_cap_reached",
        "verification_required": "verification_interrupted",
        "page_retries_exhausted": "page_retry_exhausted",
    }.get(stop_reason, "collection_incomplete")
    completed_statuses = {
        "relevance_exhausted",
        "source_exhausted",
        "max_pages_reached",
        "optional_cap_reached",
    }
    sample = classify_city_sample(
        valid_count,
        CRAWL_CONFIG["city_descriptive_min"],
        CRAWL_CONFIG["city_analysis_min"],
    )
    return {
        "status": status,
        "status_label": VALIDATION_STATUS_LABELS[status],
        "passed": status in completed_statuses,
        "stop_reason": stop_reason,
        **sample,
    }


def summarize_role_samples(combo_results, target=500):
    """跨城市汇总同一岗位；保留各城市实际数量，不改变城市归属。"""
    grouped = {}
    for result in combo_results:
        keyword = result.get("keyword", "")
        entry = grouped.setdefault(keyword, {
            "keyword": keyword,
            "job_name": result.get("job_name", keyword),
            "valid_total": 0,
            "city_counts": {},
        })
        count = int(result.get("parsed_count") or 0)
        city = result.get("city", "")
        entry["valid_total"] += count
        entry["city_counts"][city] = count

    summaries = []
    for entry in grouped.values():
        total = entry["valid_total"]
        summaries.append({
            **entry,
            "target": target,
            "target_met": total >= target,
            "shortfall": max(0, target - total),
            "city_count": len(entry["city_counts"]),
        })
    return summaries


def add_city_balance_weights(rows):
    """保留全部岗位，并让同一岗位下每个城市的权重总和均为1。"""
    stratum_counts = {}
    for row in rows:
        key = (row.get("search_keyword", ""), row.get("search_city", ""))
        stratum_counts[key] = stratum_counts.get(key, 0) + 1

    weighted_rows = []
    for row in rows:
        key = (row.get("search_keyword", ""), row.get("search_city", ""))
        count = stratum_counts[key]
        sample = classify_city_sample(count)
        weighted_rows.append({
            **row,
            "city_role_sample_size": count,
            "city_sample_level": sample["sample_level"],
            "city_sample_label": sample["sample_label"],
            "analysis_weight": 1.0 / count,
        })
    return weighted_rows


def flatten_output_value(value):
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


def normalize_output_row(row):
    """兼容读取历史英文表头和新版中文表头，恢复为内部英文键。"""
    normalized = {}
    for key, label in JOB_OUTPUT_FIELD_SPECS + ANALYSIS_EXTRA_FIELD_SPECS:
        if key in row:
            normalized[key] = row.get(key, "")
        elif label in row:
            normalized[key] = row.get(label, "")
    # 旧文件中的API隐藏专业字段只用于迁移核验，不再写入分析CSV。
    for legacy_key in ("major1_raw", "major2_raw"):
        if legacy_key in row:
            normalized[legacy_key] = row.get(legacy_key, "")
    return normalized


def repair_major_fields(row):
    """仅依据岗位正文重建专业字段，API隐藏标签只参与核验。"""
    repaired = dict(row)
    description = normalize_job_text(
        repaired.get("job_description") or repaired.get("job_description_raw") or ""
    )
    platform_tags = extract_platform_major_tags(
        repaired.get("major1_raw", ""), repaired.get("major2_raw", "")
    )
    platform_normalized = normalize_major_candidates(platform_tags)
    candidates_raw = extract_major_candidates(description)
    candidates = normalize_major_candidates(candidates_raw)
    evidence = extract_major_evidence(description)

    if candidates:
        overlap = set(platform_normalized) & set(candidates)
        if not platform_tags:
            validation = "not_provided"
        elif set(platform_normalized).issubset(set(candidates)):
            validation = "confirmed_by_description"
        elif overlap:
            validation = "partially_confirmed"
        else:
            validation = "not_supported_by_description"
        source = "description"
        note = "仅依据岗位正文中的专业要求及证据生成标准专业候选"
    else:
        validation = "unverified_api_only" if platform_tags else "not_provided"
        source = "not_specified"
        note = "岗位正文未明确专业要求，API隐藏专业标签不纳入标准专业候选"

    raw_flags = repaired.get("quality_flags", [])
    if isinstance(raw_flags, str):
        flags = [item for item in raw_flags.split("|") if item]
    else:
        flags = list(raw_flags or [])
    flags = [
        flag for flag in flags
        if flag not in {
            "major_missing", "major_api_only", "platform_major_unverified",
            "platform_major_not_supported",
        }
    ]
    if not candidates:
        flags.append("major_missing")
    if validation == "unverified_api_only":
        flags.append("platform_major_unverified")
    elif validation == "not_supported_by_description":
        flags.append("platform_major_not_supported")

    repaired.update({
        "platform_major_validation": validation,
        "major_candidates_raw": candidates_raw,
        "major_candidates": candidates,
        "major_categories": {
            major: MAJOR_CATEGORY.get(major, "未映射") for major in candidates
        },
        "major_requirement_level": classify_major(description),
        "major_evidence": evidence,
        "major_source": source,
        "major_decision_note": note,
        "quality_flags": flags,
    })
    return repaired


def read_job_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [normalize_output_row(row) for row in csv.DictReader(handle)]


def write_job_csv(path, rows, include_analysis_fields=False):
    """使用中文表头原子写入岗位CSV。"""
    specs = list(JOB_OUTPUT_FIELD_SPECS)
    if include_analysis_fields:
        specs.extend(ANALYSIS_EXTRA_FIELD_SPECS)
    fieldnames = [label for _, label in specs]
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, 1):
            output = {
                label: flatten_output_value(row.get(key, ""))
                for key, label in specs
            }
            output["序号"] = row.get("record_no") or index
            writer.writerow(output)
    os.replace(temp_path, path)


def find_latest_combo_csv(job_name, city_name):
    """查找某城市岗位最近一次完整CSV，供跨城市分析数据集汇总。"""
    out_dir = os.path.join(DATA_ROOT, job_name)
    if not os.path.isdir(out_dir):
        return ""
    prefix = f"{job_name}_{city_name}_"
    candidates = [
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        if name.startswith(prefix) and name.endswith("_jobs.csv")
    ]
    return max(candidates, key=os.path.getmtime) if candidates else ""


# ============================================================
# 指纹持久化 SQLite
# ============================================================
def init_fingerprint_db():
    conn = sqlite3.connect(FINGERPRINT_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS fingerprints (
        fp TEXT PRIMARY KEY,
        source_job_id TEXT,
        job_title TEXT,
        company_name TEXT,
        city TEXT,
        keyword TEXT,
        created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS content_fingerprints (
        cfp TEXT PRIMARY KEY,
        created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS crawl_progress (
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
    )""")
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(crawl_progress)")
    }
    required_columns = {
        "raw_unique_count": "INTEGER DEFAULT 0",
        "next_page": "INTEGER DEFAULT 1",
        "status": "TEXT DEFAULT 'pending'",
        "checkpoint_file": "TEXT DEFAULT ''",
        "retry_count": "INTEGER DEFAULT 0",
        "last_error": "TEXT DEFAULT ''",
    }
    for column, definition in required_columns.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE crawl_progress ADD COLUMN {column} {definition}")
    conn.commit()
    return conn


def load_seen_fingerprints(conn):
    fps = set()
    cfps = set()
    cur = conn.execute("SELECT fp FROM fingerprints")
    for row in cur:
        fps.add(row[0])
    cur = conn.execute("SELECT cfp FROM content_fingerprints")
    for row in cur:
        cfps.add(row[0])
    return fps, cfps


def save_fingerprint(conn, fp, cfp, job):
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO fingerprints VALUES (?,?,?,?,?,?,?)",
        (fp, job.get("source_job_id", ""), job.get("job_title", ""),
         job.get("company_name", ""), job.get("city", ""),
         job.get("search_keyword", ""), now)
    )
    if cfp:
        conn.execute("INSERT OR IGNORE INTO content_fingerprints VALUES (?,?)", (cfp, now))
    conn.commit()


def save_progress(
    conn,
    city_code,
    keyword,
    last_page,
    collected,
    raw_unique_count=0,
    next_page=1,
    status="in_progress",
    checkpoint_file="",
    retry_count=0,
    last_error="",
):
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
            city_code, keyword, last_page, collected, raw_unique_count,
            next_page, status, checkpoint_file, retry_count, last_error,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def load_progress(conn, city_code, keyword):
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


def atomic_write_json(path, payload):
    """先完整写入临时文件再原子替换，避免中断留下半个检查点。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def load_checkpoint(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ============================================================
# 词典加载
# ============================================================
def _alias_pattern(aliases):
    patterns = []
    for alias in sorted(aliases, key=len, reverse=True):
        escaped = re.escape(alias).replace(r"\ ", r"\s*")
        if alias and alias[0].isascii() and alias[0].isalnum():
            escaped = rf"(?<![A-Za-z0-9]){escaped}"
        if alias and alias[-1].isascii() and alias[-1].isalnum():
            escaped = rf"{escaped}(?![A-Za-z0-9])"
        patterns.append(escaped)
    return "|".join(patterns)


def _load_alias_dictionary(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"词典文件不存在: {path}")
    entries = []
    versions = set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            canonical = (row.get("canonical_name") or "").strip()
            aliases = [x.strip() for x in (row.get("aliases") or "").split("|") if x.strip()]
            category = (row.get("category") or "").strip()
            version = (row.get("version") or "").strip()
            if not canonical or not aliases or not version:
                continue
            entries.append({
                "canonical_name": canonical,
                "aliases": aliases,
                "category": category,
                "pattern": _alias_pattern(aliases),
            })
            versions.add(version)
    if len(versions) != 1:
        raise ValueError(f"词典版本不一致: {path}")
    return entries, versions.pop()


SKILL_ENTRIES, SKILL_DICT_VER = _load_alias_dictionary(SKILL_DICT_FILE)
MAJOR_ENTRIES, MAJOR_DICT_VER = _load_alias_dictionary(MAJOR_DICT_FILE)
SKILL_PATTERNS = [(x["canonical_name"], x["pattern"]) for x in SKILL_ENTRIES]
MAJOR_PATTERNS = [(x["canonical_name"], x["pattern"]) for x in MAJOR_ENTRIES]
SKILL_CATEGORY = {x["canonical_name"]: x["category"] for x in SKILL_ENTRIES}
MAJOR_CATEGORY = {x["canonical_name"]: x["category"] for x in MAJOR_ENTRIES}
MAJOR_ALIAS_MAP = {
    alias.casefold(): entry["canonical_name"]
    for entry in MAJOR_ENTRIES
    for alias in entry["aliases"]
}

REQUIREMENT_HEADINGS = [
    "任职要求", "任职资格", "岗位要求", "职位要求", "工作要求",
    "任职条件", "招聘要求", "岗位基本需求", "基本要求",
]
RESPONSIBILITY_HEADINGS = [
    "岗位职责", "工作职责", "职位描述", "工作内容", "岗位介绍", "核心岗位职责",
]
OTHER_HEADINGS = [
    "福利", "福利待遇", "薪资福利", "薪酬福利", "岗位亮点", "工作地址",
    "上班地址", "公司地址", "公司简介", "联系方式", "作息安排",
]


# ============================================================
# 文本处理工具
# ============================================================
def compute_source_fingerprint(job_id):
    if job_id:
        return hashlib.sha256(f"51job|{str(job_id).strip()}".encode()).hexdigest()[:16]
    return ""


def compute_content_fingerprint(company_name, description):
    """内容指纹：只基于公司名+清洗后描述（不含标题，避免福利后缀干扰）"""
    normalized = re.sub(r"\s+", "", (description or "")).lower()
    raw = f"{company_name.strip()}|{normalized[:300]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_job_text(text):
    if not text:
        return ""
    value = html.unescape(str(text)).replace(" ", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    cleaned = []
    prev_blank = False
    for line in lines:
        if line:
            cleaned.append(line)
            prev_blank = False
        elif cleaned and not prev_blank:
            cleaned.append("")
            prev_blank = True
    return "\n".join(cleaned).strip()


def _heading_pattern(headings):
    names = "|".join(sorted((re.escape(x) for x in headings), key=len, reverse=True))
    enum = r"(?:(?:[一二三四五六七八九十]+|\d+)[、.．)）]\s*)?"
    return re.compile(
        rf"^[ \t]*{enum}[【\[]?[ \t]*(?:{names})[ \t]*[】\]]?[ \t]*[：:]?[ \t]*",
        re.MULTILINE | re.IGNORECASE,
    )


def _extract_section(description, target_headings):
    text = normalize_job_text(description)
    if not text:
        return ""
    tpat = _heading_pattern(target_headings)
    m = tpat.search(text)
    if not m:
        return ""
    all_h = REQUIREMENT_HEADINGS + RESPONSIBILITY_HEADINGS + OTHER_HEADINGS
    bpat = _heading_pattern(all_h)
    m2 = bpat.search(text, m.end())
    end = m2.start() if m2 else len(text)
    return text[m.end():end].strip()


def extract_responsibility_text(description):
    """提取岗位职责；无显式职责标题时，使用任职要求之前的正文。"""
    explicit = _extract_section(description, RESPONSIBILITY_HEADINGS)
    if explicit:
        return explicit
    text = normalize_job_text(description)
    requirement_match = _heading_pattern(REQUIREMENT_HEADINGS).search(text)
    if requirement_match:
        inferred = text[:requirement_match.start()].strip()
        if len(inferred) >= 20:
            return inferred
    return ""


def _evidence_snippet(text, start, end, max_len=140):
    left_candidates = [text.rfind(m, 0, start) for m in "\n。；;！!?？"]
    left = max(left_candidates) + 1
    right_candidates = [text.find(m, end) for m in "\n。；;！!?？"]
    right_candidates = [p for p in right_candidates if p >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    snippet = text[left:right].strip()
    return snippet[:max_len].rstrip() + "…" if len(snippet) > max_len else snippet


def _is_negated_skill_mention(text, start, end):
    """识别“无需掌握Python”等否定语境，避免把非要求技能计入统计。"""
    prefix = text[max(0, start - 16):start]
    suffix = text[end:min(len(text), end + 16)]
    prefix_pattern = re.compile(
        r"(?:不要求|不需要|不必|无需|无须)(?:具备|掌握|熟悉|了解|使用|会)?[\s、，,:：]*$"
    )
    suffix_pattern = re.compile(r"^[\s、，,:：]*(?:不是必需|非必需|不作要求|不做要求)")
    return bool(prefix_pattern.search(prefix) or suffix_pattern.search(suffix))


def extract_skill_candidates(description):
    text = normalize_job_text(description)
    skills = []
    evidence = {}
    for name, pat in SKILL_PATTERNS:
        valid_match = None
        for match in re.finditer(pat, text, re.IGNORECASE):
            if not _is_negated_skill_mention(text, match.start(), match.end()):
                valid_match = match
                break
        if valid_match:
            skills.append(name)
            evidence[name] = _evidence_snippet(
                text, valid_match.start(), valid_match.end()
            )
    return skills, evidence


def _is_major_line(line):
    if "专业" not in line:
        return False
    ctx = ("学历", "本科", "大专", "硕士", "博士", "毕业", "相关专业",
           "专业背景", "专业方向", "专业类别", "类专业", "专业不限", "不限专业", "优先")
    return any(w in line for w in ctx)


def extract_major_evidence(description):
    text = normalize_job_text(description)
    lines = []
    for line in re.split(r"[\n。；;]", text):
        line = line.strip()
        if _is_major_line(line) and line not in lines:
            lines.append(line)
    return " || ".join(lines)


def extract_platform_major_tags(api_m1="", api_m2=""):
    """保留API隐藏专业标签用于核验，但不将其视为岗位专业要求。"""
    tags = []
    for value in (api_m1, api_m2):
        value = normalize_job_text(value)
        if value and value not in tags:
            tags.append(value)
    return tags


def extract_major_candidates(description):
    """只从岗位正文中有学历/专业语境证据的句子提取专业。"""
    candidates = []
    evtext = extract_major_evidence(description)
    for _, pat in MAJOR_PATTERNS:
        m = re.search(pat, evtext, re.IGNORECASE)
        if m and m.group(0) not in candidates:
            candidates.append(m.group(0))
    return candidates


def normalize_major_candidates(candidates):
    normalized = []
    for value in candidates:
        value = normalize_job_text(value)
        canonical = MAJOR_ALIAS_MAP.get(value.casefold(), value)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized


def classify_major(description):
    ev = extract_major_evidence(description)
    if not ev:
        return "未说明"
    unlimited = "专业不限" in ev or "不限专业" in ev
    preferred = "优先" in ev
    if unlimited and preferred:
        return "不限_相关专业优先"
    if unlimited:
        return "不限"
    if preferred:
        return "优先"
    return "要求"


def parse_salary(salary_text):
    if not salary_text or salary_text == "薪资面议":
        return None, None
    text = salary_text.strip()
    is_year = "年" in text
    is_day = "天" in text or "日" in text
    tc = text.replace("/年", "").replace("/月", "").replace("/天", "").replace("/日", "")
    has_wan = "万" in text
    has_qian = "千" in text

    if "-" in tc and (has_wan or has_qian):
        try:
            vals = []
            for part in tc.split("-"):
                part = part.strip()
                ns = re.findall(r'[\d.]+', part)
                if not ns:
                    continue
                num = float(ns[0])
                if "万" in part:
                    num *= 10000
                elif "千" in part:
                    num *= 1000
                elif "百" in part:
                    num *= 100
                elif "万" in tc:
                    num *= 10000
                elif "千" in tc:
                    num *= 1000
                elif num < 100 and not is_day:
                    num *= 1000
                vals.append(num)
            if len(vals) == 2:
                mn, mx = min(vals), max(vals)
            elif len(vals) == 1:
                mn = mx = vals[0]
            else:
                mn = mx = 0
        except (ValueError, TypeError):
            mn = mx = 0
    else:
        tc = tc.replace("万", "").replace("千", "").replace(",", "")
        ns = re.findall(r'[\d.]+', tc)
        if not ns:
            return None, None
        try:
            nums = [float(x) for x in ns]
            mn, mx = min(nums), max(nums) if len(nums) >= 2 else min(nums)
            if has_wan:
                mn *= 10000; mx *= 10000
            elif has_qian:
                mn *= 1000; mx *= 1000
            elif mn < 100 and not is_day:
                mn *= 1000; mx *= 1000
        except (ValueError, TypeError):
            return None, None
    if is_year:
        mn, mx = round(mn / 12), round(mx / 12)
    if is_day:
        mn, mx = round(mn * 22), round(mx * 22)
    return int(mn), int(mx)


def _format_years(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value).rstrip("0").rstrip(".")


def parse_experience_details(text):
    """返回(标准文本, 最低年限, 最高年限, 类型)，避免区间被压成单一下限。"""
    if not text:
        return "", None, None, "未说明"
    value = text.strip()
    if "应届" in value:
        return "应届生", 0, 0, "应届"
    if "不限" in value or "无需经验" in value:
        return "经验不限", 0, None, "不限"

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:年)?\s*[-~～—–至到]\s*(\d+(?:\.\d+)?)\s*年",
        value,
    )
    if range_match:
        minimum = float(range_match.group(1))
        maximum = float(range_match.group(2))
        return (
            f"{_format_years(minimum)}-{_format_years(maximum)}年",
            int(minimum) if minimum.is_integer() else minimum,
            int(maximum) if maximum.is_integer() else maximum,
            "区间",
        )

    lower_match = re.search(r"(\d+(?:\.\d+)?)\s*年\s*(?:及以上|以上|\+)", value)
    if lower_match:
        minimum = float(lower_match.group(1))
        minimum_value = int(minimum) if minimum.is_integer() else minimum
        return f"{_format_years(minimum)}年以上", minimum_value, None, "下限"

    exact_match = re.search(r"(\d+(?:\.\d+)?)\s*年", value)
    if exact_match:
        years = float(exact_match.group(1))
        years_value = int(years) if years.is_integer() else years
        return f"{_format_years(years)}年", years_value, years_value, "精确"
    return value, None, None, "其他"


def parse_experience(text):
    """标准化经验要求，详细上下限由parse_experience_details同时提供。"""
    return parse_experience_details(text)[0]


def parse_education(text):
    if not text:
        return ""
    t = text.strip()
    for k, v in [("博士", "博士"), ("硕士", "硕士"), ("研究生", "硕士"),
                 ("本科", "本科"), ("大专", "大专"), ("中专", "中专"), ("高中", "高中")]:
        if k in t:
            return v
    return t


def clean_job_title(title):
    """去除标题中括号内的福利后缀"""
    return re.sub(r'[（(]\s*(?:周末双休|五险一金|六险一金|双休|节日福利|带薪年假|弹性工作|年终奖|高薪|急聘|包吃住|包食宿|不加班|可实习|应届|提供住宿|餐饮补贴)\s*[）)]', '', title).strip()


# ============================================================
# 主爬虫类
# ============================================================
class Batch51JobCrawler:
    def __init__(self, db_conn):
        self.conn = db_conn
        self.seen_fps, self.seen_cfps = load_seen_fingerprints(db_conn)
        self.api_responses = []
        self.selected_api_response = None
        self.collected_jobs = []
        self.current_city = {}
        self.current_keyword = ""
        self.batch_start = datetime.now()
        self.total_collected = 0
        self.total_skipped_dup = 0
        self.total_filtered_irrelevant = 0
        self.total_filtered_invalid = 0
        self.total_parse_errors = 0
        self.combo_results = []
        self.combo_seen_fps = set()
        self.combo_seen_cfps = set()

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)

    def setup_response_interception(self, page):
        def handle_response(response):
            url = response.url
            if "api/job/search-pc" in url and response.status == 200:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = response.body()
                        text = body.decode("utf-8", errors="ignore")
                        data = json.loads(text)
                        if "resultbody" in data and "job" in data.get("resultbody", {}):
                            self.api_responses.append({
                                "url": url,
                                "data": data,
                                "page_num": api_response_page_num(url),
                                "captured_at": datetime.now().isoformat()
                            })
                except Exception:
                    pass
        page.on("response", handle_response)

    def build_search_url(self, page_num=1):
        params = {
            "keyword": self.current_keyword,
            "searchType": CRAWL_CONFIG["search_type"],
            "jobArea": self.current_city["code"],
            "pageNum": page_num,
        }
        return f"https://we.51job.com/pc/search?{urlencode(params)}"

    @staticmethod
    def _active_search_page(page):
        """读取分页控件当前激活页；分页控件尚未渲染时返回 None。"""
        try:
            active = page.locator(".el-pagination li.number.active")
            if active.count() == 0:
                return None
            text = active.first.inner_text(timeout=2000).strip()
            return int(text)
        except (AttributeError, TypeError, ValueError, PlaywrightTimeoutError):
            return None

    def _wait_for_search_page(self, page, page_num):
        """等待目标页 API 与分页激活状态就绪，避免使用固定长等待。"""
        timeout_seconds = CRAWL_CONFIG["data_wait_timeout"]
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            has_matching_response = any(
                response.get("page_num") == page_num
                for response in self.api_responses
            )
            active_page = self._active_search_page(page)
            if has_matching_response and active_page == page_num:
                return True
            page.wait_for_timeout(250)
        return False

    def _click_search_page(self, page, page_num):
        """点击精确页码；目标页码不可见时点击下一页按钮。"""
        pagination = page.locator(".el-pagination")
        page_link = pagination.locator("li.number").filter(
            has_text=re.compile(rf"^\s*{page_num}\s*$")
        )
        click_timeout = CRAWL_CONFIG["page_load_timeout"] * 1000
        if page_link.count() > 0:
            page_link.first.click(timeout=click_timeout)
            return

        next_button = pagination.locator("button.btn-next")
        if next_button.count() == 0 or next_button.first.is_disabled():
            raise RuntimeError(f"分页控件无法前往第 {page_num} 页")
        next_button.first.click(timeout=click_timeout)

    def _navigate_to_search_page(self, page, page_num):
        """使用官网分页控件进入目标页，并保留该页 API 响应供解析。"""
        active_page = self._active_search_page(page)

        # 新浏览器、回退页码或重试当前页时都先回到第一页，确保点击会重新发起请求。
        if active_page is None or active_page >= page_num:
            self.api_responses.clear()
            page.goto(
                self.build_search_url(1),
                timeout=CRAWL_CONFIG["page_load_timeout"] * 1000,
                wait_until="domcontentloaded",
            )
            self._wait_for_search_page(page, 1)
            active_page = self._active_search_page(page) or 1

        if page_num == 1:
            return

        # 正常采集只点击一次；断点恢复时依次点击到目标页。
        while active_page < page_num:
            target_page = active_page + 1
            self.api_responses.clear()
            self._click_search_page(page, target_page)
            if not self._wait_for_search_page(page, target_page):
                return
            active_page = target_page

    def crawl_page(self, page, page_num):
        self.log(f"  正在加载第 {page_num} 页...")

        try:
            self.api_responses.clear()
            self.selected_api_response = None
            self._navigate_to_search_page(page, page_num)

            if "验证" in page.title() or "waf" in page.url.lower():
                self.log("  ⚠ 触发验证码，当前组合进入待补采队列")
                return [], "verification_required"

            jobs = []
            if self.api_responses:
                matching_responses = [
                    response
                    for response in self.api_responses
                    if response.get("page_num") == page_num
                ]
                if not matching_responses:
                    observed_pages = sorted({
                        response.get("page_num")
                        for response in self.api_responses
                        if response.get("page_num") is not None
                    })
                    self.log(
                        f"  ⚠ API页码不匹配：请求第 {page_num} 页，"
                        f"实际响应页码 {observed_pages or ['未知']}"
                    )
                    return [], "page_mismatch"

                self.selected_api_response = matching_responses[-1]
                api_data = self.selected_api_response["data"]
                items = api_data.get("resultbody", {}).get("job", {}).get("items", [])
                self.log(f"  API返回第 {page_num} 页: {len(items)} 条")
                for item in items:
                    job = self._parse_item(item)
                    if job:
                        jobs.append(job)
            else:
                self.log("  ⚠ 未拦截到API响应")
                return [], "no_api_response"

            return jobs, "ok"

        except PlaywrightTimeoutError:
            self.log("  ✗ 页面加载超时")
            return [], "page_timeout"
        except Exception as e:
            self.log(f"  ✗ 错误: {e}")
            return [], f"page_error:{type(e).__name__}"

    def _parse_item(self, item):
        try:
            job_id = item.get("jobId", "")
            job_title = clean_job_title(item.get("jobName", ""))
            company_name = item.get("fullCompanyName", "") or item.get("companyName", "")

            # 去重
            fp = compute_source_fingerprint(job_id)
            if not fp:
                self.total_filtered_invalid += 1
                return None
            if fp in self.seen_fps or fp in self.combo_seen_fps:
                self.total_skipped_dup += 1
                return None
            # 所有首次出现的原始岗位都先登记，避免重复页反复累计过滤原因。
            self.combo_seen_fps.add(fp)

            if CRAWL_CONFIG.get("filter_title", True) and not is_job_title_relevant(
                self.current_keyword, job_title
            ):
                self.total_filtered_irrelevant += 1
                return None

            # 内容指纹 - 不同jobId才检查内容重复
            desc_raw = item.get("jobDescribe", "") or ""
            desc = normalize_job_text(desc_raw)
            cfp = compute_content_fingerprint(company_name, desc)

            # 薪资
            salary_text = item.get("provideSalaryString", "")
            smin = item.get("jobSalaryMin")
            smax = item.get("jobSalaryMax")
            try:
                smin = int(float(smin)) if smin not in (None, "") else None
                smax = int(float(smax)) if smax not in (None, "") else None
            except (TypeError, ValueError):
                smin = smax = None
            if smin is None or smax is None:
                smin, smax = parse_salary(salary_text)
            smonths = 12
            m = re.search(r"(\d+)\s*薪", salary_text)
            if m:
                smonths = int(m.group(1))

            # 地点
            area_detail = item.get("jobAreaLevelDetail", {})
            city = area_detail.get("cityString", "") or area_detail.get("provinceString", "")
            district = area_detail.get("districtString", "")
            work_area = item.get("jobAreaString", "") or ""
            if not city and work_area:
                if "·" in work_area:
                    parts = work_area.split("·", 1)
                    city, district = parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
                else:
                    city = work_area.strip()

            # 经验学历
            exp_raw = item.get("workYearString", "")
            exp, exp_min, exp_max, exp_type = parse_experience_details(exp_raw)
            edu_raw = item.get("degreeString", "")
            edu = parse_education(edu_raw)

            # 公司
            csize = item.get("companySizeString", "")
            ind = item.get("companyIndustryType1Str", "")
            ctype = item.get("companyTypeString", "")
            term = item.get("termStr", "全职")

            # 日期
            issue = item.get("issueDateString", "")
            pub_date = issue[:10] if issue and len(issue) == 19 else issue

            # 文本分段
            req_text = _extract_section(desc, REQUIREMENT_HEADINGS)
            resp_text = extract_responsibility_text(desc)

            # 专业
            m1r = item.get("major1Str", "") or ""
            m2r = item.get("major2Str", "") or ""
            platform_major_tags = extract_platform_major_tags(m1r, m2r)
            platform_major_normalized = normalize_major_candidates(platform_major_tags)
            majors_raw = extract_major_candidates(desc)
            majors = normalize_major_candidates(majors_raw)
            mcats = {m: MAJOR_CATEGORY.get(m, "未映射") for m in majors}
            mev = extract_major_evidence(desc)
            mlevel = classify_major(desc)
            if majors:
                msrc = "description"
                overlap = set(platform_major_normalized) & set(majors)
                if not platform_major_tags:
                    platform_major_validation = "not_provided"
                elif set(platform_major_normalized).issubset(set(majors)):
                    platform_major_validation = "confirmed_by_description"
                elif overlap:
                    platform_major_validation = "partially_confirmed"
                else:
                    platform_major_validation = "not_supported_by_description"
                major_decision_note = "仅依据岗位正文中的专业要求及证据生成标准专业候选"
            else:
                msrc = "not_specified"
                platform_major_validation = (
                    "unverified_api_only" if platform_major_tags else "not_provided"
                )
                major_decision_note = "岗位正文未明确专业要求，API隐藏专业标签不纳入标准专业候选"

            # 技能
            desc_skills, desc_ev = extract_skill_candidates(desc)
            req_skills, req_ev = extract_skill_candidates(req_text)
            if req_text:
                skills = req_skills
                sev = req_ev
                sscope = "requirement_text"
            else:
                skills = desc_skills
                sev = desc_ev
                sscope = "job_description_fallback"
            scats = {s: SKILL_CATEGORY.get(s, "未分类") for s in skills}

            job_tags = item.get("jobTags", [])

            # URL
            job_url = canonicalize_job_url(item.get("jobHref", ""))

            # 构建记录
            job = {
                "source": "51job",
                "crawler_version": CRAWLER_VERSION,
                "skill_dict_ver": SKILL_DICT_VER,
                "major_dict_ver": MAJOR_DICT_VER,
                "search_keyword": self.current_keyword,
                "search_city": self.current_city["name"],
                "search_city_code": self.current_city["code"],
                "source_job_id": job_id,
                "source_url": job_url,
                "crawl_time": datetime.now().isoformat(),
                "fingerprint": fp,
                "content_fingerprint": cfp,
                "job_title": job_title,
                "company_name": company_name,
                "city": city,
                "district": district,
                "salary_text": salary_text,
                "salary_min": smin,
                "salary_max": smax,
                "salary_months": smonths,
                "education": edu,
                "education_raw": edu_raw,
                "experience": exp,
                "experience_raw": exp_raw,
                "experience_min_years": exp_min,
                "experience_max_years": exp_max,
                "experience_requirement_type": exp_type,
                "job_type": term if term else "全职",
                "industry": ind,
                "company_size": csize,
                "company_type": ctype,
                "publish_date": pub_date,
                "publish_date_raw": issue,
                "longitude": item.get("lon", ""),
                "latitude": item.get("lat", ""),
                "major1_raw": m1r,
                "major2_raw": m2r,
                "platform_major_tags": platform_major_tags,
                "platform_major_validation": platform_major_validation,
                "major_candidates_raw": majors_raw,
                "major_candidates": majors,
                "major_categories": mcats,
                "major_requirement_level": mlevel,
                "major_evidence": mev,
                "major_source": msrc,
                "major_decision_note": major_decision_note,
                "job_description_raw": desc_raw,
                "job_description": desc,
                "requirement_text": req_text,
                "responsibility_text": resp_text,
                "job_tags": job_tags,
                "skill_candidates": skills,
                "skill_categories": scats,
                "skill_evidence": sev,
                "skill_extraction_scope": sscope,
                "description_skill_candidates": desc_skills,
                "requirement_skill_candidates": req_skills,
            }

            # 质量标志
            flags = []
            if not req_text:
                flags.append("requirement_section_missing")
            if not majors:
                flags.append("major_missing")
            if platform_major_validation == "unverified_api_only":
                flags.append("platform_major_unverified")
            elif platform_major_validation == "not_supported_by_description":
                flags.append("platform_major_not_supported")
            if not skills:
                flags.append("skill_candidate_missing")
            if not edu:
                flags.append("education_missing")
            if not district:
                flags.append("district_missing")
            if len(desc) < 20:
                flags.append("description_too_short")
            job["quality_flags"] = flags

            # 过滤无效
            spam_kw = ["培训", "包就业", "学费", "招生", "零基础", "实训",
                        "招生老师", "课程顾问", "电话销售", "保险", "房产"]
            if any(k in job_title for k in spam_kw):
                self.total_filtered_invalid += 1
                return None
            if len(desc) < 20:
                self.total_filtered_invalid += 1
                return None

            if cfp and (cfp in self.seen_cfps or cfp in self.combo_seen_cfps):
                self.total_skipped_dup += 1
                return None
            if cfp:
                self.combo_seen_cfps.add(cfp)

            return job

        except Exception as e:
            self.total_parse_errors += 1
            self.log(f"  ✗ 解析出错: {e}")
            return None

    def run_one_combo(self, city, keyword, retry_count=0):
        self.current_city = city
        self.current_keyword = keyword
        self.collected_jobs = []
        self.api_responses = []
        self.selected_api_response = None
        self.total_skipped_dup = 0
        self.total_filtered_irrelevant = 0
        self.total_filtered_invalid = 0
        self.total_parse_errors = 0
        self.combo_seen_fps = set()
        self.combo_seen_cfps = set()

        city_name = city["name"]
        job_name = get_job_display_name(keyword)
        out_dir = os.path.join(DATA_ROOT, job_name)
        os.makedirs(out_dir, exist_ok=True)
        checkpoint_file = os.path.join(
            out_dir, f"_{job_name}_{city_name}_checkpoint.json"
        )

        checkpoint = load_checkpoint(checkpoint_file)
        if checkpoint and checkpoint.get("checkpoint_version") != CHECKPOINT_VERSION:
            self.log("旧检查点版本不兼容，已丢弃并从第1页重新采集")
            os.remove(checkpoint_file)
            checkpoint = None
        progress = load_progress(self.conn, city["code"], keyword)
        terminal_statuses = {
            "relevance_exhausted",
            "source_exhausted",
            "max_pages_reached",
            "optional_cap_reached",
            "passed",
            "source_exhausted_insufficient",
            "max_pages_insufficient",
            "empty_pages_insufficient",
            "valid_target_below_raw_minimum",
        }
        completed_csv_file = find_latest_combo_csv(job_name, city_name)
        if (
            not checkpoint
            and progress
            and progress.get("status") in terminal_statuses
            and not completed_csv_file
        ):
            self.log("已有终态进度但CSV文件缺失，将从第1页重新采集")
        if (
            not checkpoint
            and CRAWL_CONFIG.get("resume_completed", True)
            and progress
            and progress.get("status") in terminal_statuses
            and completed_csv_file
        ):
            parsed_count = int(progress.get("total_collected") or 0)
            raw_unique_count = int(progress.get("raw_unique_count") or 0)
            status = progress["status"]
            sample = classify_city_sample(
                parsed_count,
                CRAWL_CONFIG["city_descriptive_min"],
                CRAWL_CONFIG["city_analysis_min"],
            )
            self.total_collected += parsed_count
            self.log(
                f"组合已有终态记录，跳过重复采集：{status} | "
                f"有效 {parsed_count} 条，原始唯一 {raw_unique_count} 条"
            )
            return {
                "city": city_name,
                "city_code": city["code"],
                "keyword": keyword,
                "job_name": job_name,
                "status": status,
                "status_label": VALIDATION_STATUS_LABELS.get(status, status),
                "passed": True,
                "raw_item_count": raw_unique_count,
                "raw_unique_count": raw_unique_count,
                "parsed_count": parsed_count,
                **sample,
                "source_total_count": "",
                "stop_reason": "already_terminal",
                "last_page_attempted": int(progress.get("last_page") or 0),
                "last_successful_page": int(progress.get("last_page") or 0),
                "retryable": False,
                "retry_count": int(progress.get("retry_count") or 0),
                "checkpoint_file": "",
                "csv_file": completed_csv_file,
                "resumed_completed": True,
            }

        if checkpoint:
            self.collected_jobs = checkpoint.get("collected_jobs", [])
            all_api_data = checkpoint.get("api_responses", [])
            raw_item_count = int(checkpoint.get("raw_item_count", 0))
            raw_job_keys = set(checkpoint.get("raw_job_keys", []))
            source_total_count = checkpoint.get("source_total_count")
            last_page_attempted = int(checkpoint.get("last_page_attempted", 0))
            last_successful_page = int(checkpoint.get("last_successful_page", 0))
            empty_streak = int(checkpoint.get("empty_streak", 0))
            zero_valid_streak = int(checkpoint.get("zero_valid_streak", 0))
            page_num = max(1, int(checkpoint.get("next_page", 1)))
            run_timestamp = checkpoint.get("run_timestamp") or datetime.now().strftime("%Y%m%d_%H%M%S")
            self.total_skipped_dup = int(checkpoint.get("total_skipped_dup", 0))
            self.total_filtered_irrelevant = int(
                checkpoint.get("total_filtered_irrelevant", 0)
            )
            self.total_filtered_invalid = int(checkpoint.get("total_filtered_invalid", 0))
            self.total_parse_errors = int(checkpoint.get("total_parse_errors", 0))
            saved_seen_fps = checkpoint.get("combo_seen_fps")
            if saved_seen_fps is not None:
                self.combo_seen_fps = set(saved_seen_fps)
            else:
                self.combo_seen_fps = {
                    job.get("fingerprint")
                    for job in self.collected_jobs
                    if job.get("fingerprint")
                }
            saved_seen_cfps = checkpoint.get("combo_seen_cfps")
            if saved_seen_cfps is not None:
                self.combo_seen_cfps = set(saved_seen_cfps)
            else:
                self.combo_seen_cfps = {
                    job.get("content_fingerprint")
                    for job in self.collected_jobs
                    if job.get("content_fingerprint")
                }
            self.log(
                f"发现检查点：从第 {page_num} 页恢复 | "
                f"已落盘有效 {len(self.collected_jobs)} 条，原始唯一 {len(raw_job_keys)} 条"
            )
        else:
            all_api_data = []
            raw_item_count = 0
            raw_job_keys = set()
            source_total_count = None
            last_page_attempted = 0
            last_successful_page = 0
            empty_streak = 0
            zero_valid_streak = 0
            page_num = 1
            run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        def persist_checkpoint(status, next_page, last_error=""):
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
                "raw_item_count": raw_item_count,
                "raw_job_keys": sorted(raw_job_keys),
                "combo_seen_fps": sorted(self.combo_seen_fps),
                "combo_seen_cfps": sorted(self.combo_seen_cfps),
                "source_total_count": source_total_count,
                "total_skipped_dup": self.total_skipped_dup,
                "total_filtered_irrelevant": self.total_filtered_irrelevant,
                "total_filtered_invalid": self.total_filtered_invalid,
                "total_parse_errors": self.total_parse_errors,
                "retry_count": retry_count,
                "last_error": last_error,
                "api_responses": all_api_data,
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

        # 浏览器启动前也建立检查点；启动失败后仍可进入待补采队列。
        persist_checkpoint("in_progress", page_num)

        self.log("=" * 60)
        self.log(f"开始采集: {city_name} · {keyword}")
        self.log(f"输出目录: {out_dir}")
        self.log("=" * 60)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=CRAWL_CONFIG["headless"],
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": CRAWL_CONFIG["viewport_width"],
                           "height": CRAWL_CONFIG["viewport_height"]},
                locale="zh-CN",
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            """)
            self.setup_response_interception(page)

            stop_reason = None
            max_pages = CRAWL_CONFIG["max_pages"]
            max_jobs = CRAWL_CONFIG["max_jobs_per_combo"]
            max_empty = CRAWL_CONFIG["max_consecutive_empty_pages"]
            min_relevance_pages = CRAWL_CONFIG["min_pages_before_relevance_stop"]
            max_zero_valid = CRAWL_CONFIG["max_consecutive_zero_valid_pages"]

            while (
                (max_jobs <= 0 or len(self.collected_jobs) < max_jobs)
                and page_num <= max_pages
            ):
                last_page_attempted = page_num
                jobs = []
                page_status = "collection_incomplete"
                for page_retry in range(CRAWL_CONFIG["max_page_retries"] + 1):
                    jobs, page_status = self.crawl_page(page, page_num)
                    if page_status in ("ok", "verification_required"):
                        break
                    persist_checkpoint(page_status, page_num, page_status)
                    if page_retry < CRAWL_CONFIG["max_page_retries"]:
                        wait_seconds = CRAWL_CONFIG["retry_backoff_sec"] * (page_retry + 1)
                        self.log(
                            f"  第 {page_num} 页采集失败，{wait_seconds} 秒后进行第 "
                            f"{page_retry + 1} 次重试"
                        )
                        time.sleep(wait_seconds)

                if page_status == "verification_required":
                    stop_reason = "verification_required"
                    persist_checkpoint("pending_retry", page_num, stop_reason)
                    break
                if page_status != "ok":
                    stop_reason = "page_retries_exhausted"
                    persist_checkpoint("pending_retry", page_num, page_status)
                    break

                # 只保存与请求页码一致的官网响应，忽略页面初始化产生的第1页响应。
                if self.selected_api_response:
                    all_api_data.append({
                        "url": self.selected_api_response["url"],
                        "page_num": self.selected_api_response["page_num"],
                        "data": self.selected_api_response["data"],
                    })

                source_items = []
                if self.selected_api_response:
                    source_job_data = (
                        self.selected_api_response["data"]
                        .get("resultbody", {})
                        .get("job", {})
                    )
                    source_items = source_job_data.get("items", []) or []
                    total_count = source_job_data.get(
                        "totalCount", source_job_data.get("totalcount")
                    )
                    try:
                        source_total_count = (
                            int(total_count) if total_count not in (None, "") else source_total_count
                        )
                    except (TypeError, ValueError):
                        pass

                raw_item_count += len(source_items)
                for item in source_items:
                    source_job_id = str(item.get("jobId", "")).strip()
                    if source_job_id:
                        raw_job_keys.add(f"id:{source_job_id}")
                    else:
                        raw_text = json.dumps(item, ensure_ascii=False, sort_keys=True)
                        raw_job_keys.add(
                            "hash:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                        )
                if source_items:
                    last_successful_page = page_num

                if max_jobs > 0:
                    new_jobs = jobs[:max_jobs - len(self.collected_jobs)]
                else:
                    new_jobs = jobs
                self.collected_jobs.extend(new_jobs)
                if new_jobs:
                    zero_valid_streak = 0
                else:
                    zero_valid_streak += 1

                valid_target = str(max_jobs) if max_jobs > 0 else "不限"
                self.log(
                    f"  本页新岗位: {len(new_jobs)} | "
                    f"有效累计: {len(self.collected_jobs)}/{valid_target} | "
                    f"连续零有效页: {zero_valid_streak}/{max_zero_valid} | "
                    f"原始唯一岗位: {len(raw_job_keys)} | "
                    f"标题不相关: {self.total_filtered_irrelevant} | 跳过重复: {self.total_skipped_dup}"
                )

                if len(source_items) == 0:
                    empty_streak += 1
                    if (
                        source_total_count is not None
                        and page_num * CRAWL_CONFIG["page_size"] >= source_total_count
                    ):
                        self.log("  已到达官网搜索结果末页，停止翻页")
                        stop_reason = "source_exhausted"
                    elif empty_streak >= max_empty:
                        self.log(f"  连续 {empty_streak} 页无数据，停止翻页")
                        stop_reason = "consecutive_empty_pages"
                else:
                    empty_streak = 0

                    if not source_has_more_pages(
                        page_num,
                        CRAWL_CONFIG["page_size"],
                        source_total_count,
                        len(source_items),
                    ):
                        self.log("  已到达官网搜索结果末页，停止翻页")
                        stop_reason = "source_exhausted"

                if (
                    stop_reason is None
                    and should_stop_for_relevance(
                        page_num,
                        zero_valid_streak,
                        min_relevance_pages,
                        max_zero_valid,
                    )
                ):
                    self.log(
                        f"  连续 {zero_valid_streak} 页无新增有效岗位，"
                        "相关结果已基本耗尽，转入下一个城市"
                    )
                    stop_reason = "relevance_exhausted"

                if (
                    stop_reason is None
                    and max_jobs > 0
                    and len(self.collected_jobs) >= max_jobs
                ):
                    stop_reason = "valid_cap_reached"

                next_page = page_num + 1
                persist_checkpoint(stop_reason or "in_progress", next_page)
                if stop_reason is not None:
                    break

                page_num = next_page
                time.sleep(CRAWL_CONFIG["request_interval"])

            if stop_reason is None:
                if max_jobs > 0 and len(self.collected_jobs) >= max_jobs:
                    stop_reason = "valid_cap_reached"
                else:
                    stop_reason = "max_pages_reached"
                persist_checkpoint(stop_reason, page_num)

            browser.close()

        validation = evaluate_collection(len(self.collected_jobs), stop_reason)
        validation.update({
            "raw_item_count": raw_item_count,
            "raw_unique_count": len(raw_job_keys),
            "parsed_count": len(self.collected_jobs),
            "source_total_count": source_total_count,
            "last_page_attempted": last_page_attempted,
            "last_successful_page": last_successful_page,
            "zero_valid_streak": zero_valid_streak,
            "duplicate_count": self.total_skipped_dup,
            "irrelevant_title_count": self.total_filtered_irrelevant,
            "invalid_record_count": self.total_filtered_invalid,
            "parse_error_count": self.total_parse_errors,
            "retryable": stop_reason in {
                "verification_required", "page_retries_exhausted"
            },
            "retry_count": retry_count,
            "checkpoint_file": checkpoint_file,
        })
        self.log(
            f"  验证结果: {validation['status_label']} | "
            f"有效岗位 {validation['parsed_count']} 条 | {validation['sample_label']}"
        )

        # 保存数据
        ts = run_timestamp
        fn_prefix = f"{job_name}_{city_name}_{ts}"

        # 1. 原始API JSON
        raw_file = os.path.join(out_dir, f"{fn_prefix}_raw_api.json")
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump({
                "config": {"city": city_name, "city_code": city["code"],
                            "keyword": keyword, "crawl_time": ts,
                            "total": len(self.collected_jobs),
                            "raw_item_count": raw_item_count,
                            "raw_unique_count": len(raw_job_keys)},
                "validation": validation,
                "api_responses": all_api_data,
            }, f, ensure_ascii=False, indent=2)
        self.log(f"  原始API: {os.path.basename(raw_file)}")

        # 2. CSV
        csv_file = os.path.join(out_dir, f"{fn_prefix}_jobs.csv")
        write_job_csv(csv_file, self.collected_jobs)
        self.log(f"  CSV: {os.path.basename(csv_file)} ({len(self.collected_jobs)} 条)")

        # 3. 写入指纹库
        for job in self.collected_jobs:
            save_fingerprint(self.conn, job["fingerprint"], job.get("content_fingerprint", ""), job)
            self.seen_fps.add(job["fingerprint"])
            if job.get("content_fingerprint"):
                self.seen_cfps.add(job["content_fingerprint"])

        # 4. 运行日志
        log_file = os.path.join(out_dir, f"{fn_prefix}_run_log.txt")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"采集时间: {ts}\n")
            f.write(f"数据源: 前程无忧 51job\n")
            f.write(f"城市: {city_name} ({city['code']})\n")
            f.write(f"岗位关键词: {keyword}\n")
            f.write(f"API原始返回数量: {raw_item_count} 条\n")
            f.write(f"原始唯一岗位数量: {len(raw_job_keys)} 条\n")
            f.write(f"验证状态: {validation['status_label']} ({validation['status']})\n")
            f.write(f"城市样本等级: {validation['sample_label']} ({validation['sample_level']})\n")
            f.write(f"停止原因: {validation['stop_reason']}\n")
            f.write(f"官网报告总数: {source_total_count if source_total_count is not None else '未知'}\n")
            f.write(f"最后尝试页: {last_page_attempted}\n")
            f.write(f"最后成功页: {last_successful_page}\n")
            f.write(f"结束时连续零有效页: {zero_valid_streak}\n")
            f.write(f"CSV有效数量: {len(self.collected_jobs)} 条\n")
            f.write(f"跳过重复: {self.total_skipped_dup} 条\n")
            f.write(f"标题不相关过滤: {self.total_filtered_irrelevant} 条\n")
            f.write(f"无效记录过滤: {self.total_filtered_invalid} 条\n")
            f.write(f"解析错误: {self.total_parse_errors} 条\n")
            f.write(f"输出: {os.path.basename(csv_file)}\n")

        if validation["retryable"]:
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
                status=validation["status"],
                checkpoint_file="",
                retry_count=retry_count,
                last_error="",
            )
            if os.path.exists(checkpoint_file):
                os.remove(checkpoint_file)
            self.total_collected += len(self.collected_jobs)

        return {
            "city": city_name,
            "city_code": city["code"],
            "keyword": keyword,
            "job_name": job_name,
            "csv_file": csv_file,
            **validation,
        }

    def save_quality_summary(self):
        """将城市组合质量与岗位跨城市总体目标合并到一个汇总文件。"""
        os.makedirs(DATA_ROOT, exist_ok=True)
        timestamp = self.batch_start.strftime("%Y%m%d_%H%M%S")
        summary_file = os.path.join(DATA_ROOT, f"爬取质量汇总_{timestamp}.csv")
        role_summaries = summarize_role_samples(
            self.combo_results,
            CRAWL_CONFIG["role_valid_target"],
        )
        fieldnames = [label for _, label in QUALITY_OUTPUT_FIELD_SPECS]
        temp_file = f"{summary_file}.tmp"
        with open(temp_file, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for result in self.combo_results:
                internal_row = {
                    **result,
                    "record_type": "city_role",
                    "valid_count": result.get("parsed_count", 0),
                }
                writer.writerow({
                    label: flatten_output_value(internal_row.get(key, ""))
                    for key, label in QUALITY_OUTPUT_FIELD_SPECS
                })
            for summary in role_summaries:
                target_met = summary["target_met"]
                internal_row = {
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
                    "city_counts": json.dumps(
                        summary["city_counts"], ensure_ascii=False, sort_keys=True
                    ),
                }
                writer.writerow({
                    label: flatten_output_value(internal_row.get(key, ""))
                    for key, label in QUALITY_OUTPUT_FIELD_SPECS
                })
        os.replace(temp_file, summary_file)
        self.log(f"爬取质量汇总: {summary_file}")
        return summary_file, role_summaries

    def save_analysis_dataset(self):
        """合并全部有效岗位，并在同一文件中附加城市等权分析字段。"""
        input_files = []
        for result in self.combo_results:
            csv_file = result.get("csv_file") or find_latest_combo_csv(
                result.get("job_name", ""), result.get("city", "")
            )
            if csv_file and os.path.exists(csv_file) and csv_file not in input_files:
                input_files.append(csv_file)

        rows = []
        for csv_file in input_files:
            rows.extend(repair_major_fields(row) for row in read_job_csv(csv_file))

        if not rows:
            self.log("未发现可合并的有效岗位CSV，跳过分析数据集输出")
            return ""

        os.makedirs(DATA_ROOT, exist_ok=True)
        timestamp = self.batch_start.strftime("%Y%m%d_%H%M%S")
        analysis_file = os.path.join(DATA_ROOT, f"岗位分析数据_{timestamp}.csv")
        weighted_rows = add_city_balance_weights(rows)
        write_job_csv(analysis_file, weighted_rows, include_analysis_fields=True)

        self.log(f"岗位分析数据: {analysis_file} ({len(rows)} 条，含城市分析权重)")
        return analysis_file

    def run_all(self):
        cities = getattr(self, "cities", CITIES)
        jobs = getattr(self, "jobs", CORE_JOBS)
        self.combo_results = []

        self.log("=" * 60)
        self.log("前程无忧批量采集 - 启动")
        self.log(f"城市数: {len(cities)} | 岗位数: {len(jobs)}")
        self.log(f"总组合数: {len(cities) * len(jobs)}")
        combo_cap = CRAWL_CONFIG["max_jobs_per_combo"]
        self.log(f"每组合有效岗位上限: {combo_cap if combo_cap > 0 else '不设硬上限'}")
        self.log(
            f"自适应停止: 至少读取 {CRAWL_CONFIG['min_pages_before_relevance_stop']} 页，"
            f"连续 {CRAWL_CONFIG['max_consecutive_zero_valid_pages']} 页零有效岗位后停止"
        )
        self.log(f"每岗位跨城市总体目标: {CRAWL_CONFIG['role_valid_target']} 条有效岗位")
        self.log("=" * 60)

        total_combos = len(cities) * len(jobs)
        # 队列项：(城市配置, 岗位关键词, 已执行的组合级重试次数)
        pending_queue = [
            (city, keyword, 0)
            for keyword in jobs
            for city in cities
        ]
        results_by_combo = {}
        attempt_index = 0

        while pending_queue:
            city, keyword, retry_count = pending_queue.pop(0)
            attempt_index += 1
            combo_key = (city["code"], keyword)
            elapsed = (datetime.now() - self.batch_start).total_seconds()
            retry_text = f"，补采重试 {retry_count}" if retry_count else ""
            self.log(
                f"\n[执行 {attempt_index}] 岗位={get_job_display_name(keyword)} "
                f"城市={city['name']}{retry_text} (已运行 {elapsed:.0f}s)"
            )

            if retry_count:
                wait_seconds = CRAWL_CONFIG["combo_retry_delay_sec"]
                self.log(f"待补采任务将在 {wait_seconds} 秒后恢复")
                time.sleep(wait_seconds)

            try:
                result = self.run_one_combo(city, keyword, retry_count=retry_count)
            except Exception as e:
                self.log(f">>> ✗ 组合失败: {e}")
                progress = load_progress(self.conn, city["code"], keyword) or {}
                raw_unique_count = int(progress.get("raw_unique_count") or 0)
                parsed_count = int(progress.get("total_collected") or 0)
                result = {
                    "city": city["name"],
                    "city_code": city["code"],
                    "keyword": keyword,
                    "job_name": get_job_display_name(keyword),
                    "status": "combo_failed",
                    "status_label": VALIDATION_STATUS_LABELS["combo_failed"],
                    "passed": False,
                    "raw_item_count": raw_unique_count,
                    "raw_unique_count": raw_unique_count,
                    "parsed_count": parsed_count,
                    **classify_city_sample(
                        parsed_count,
                        CRAWL_CONFIG["city_descriptive_min"],
                        CRAWL_CONFIG["city_analysis_min"],
                    ),
                    "source_total_count": "",
                    "stop_reason": f"exception: {e}",
                    "last_page_attempted": int(progress.get("last_page") or 0),
                    "last_successful_page": int(progress.get("last_page") or 0),
                    "retryable": True,
                    "retry_count": retry_count,
                    "checkpoint_file": progress.get("checkpoint_file", ""),
                    "csv_file": find_latest_combo_csv(
                        get_job_display_name(keyword), city["name"]
                    ),
                }
                import traceback
                traceback.print_exc()

            results_by_combo[combo_key] = result
            self.log(
                f">>> 本组合状态: CSV有效 {result['parsed_count']} 条 | "
                f"{result.get('sample_label', '样本状态未知')} | {result['status_label']}"
            )

            if result.get("retryable"):
                if retry_count < CRAWL_CONFIG["max_combo_retries"]:
                    pending_queue.append((city, keyword, retry_count + 1))
                    self.log(
                        f">>> 已加入待补采队列：{city['name']} · "
                        f"{get_job_display_name(keyword)}"
                    )
                else:
                    result["status_label"] += "（重试次数已耗尽，检查点已保留）"
                    progress = load_progress(self.conn, city["code"], keyword) or {}
                    save_progress(
                        self.conn,
                        city["code"],
                        keyword,
                        int(progress.get("last_page") or 0),
                        int(progress.get("total_collected") or 0),
                        raw_unique_count=int(progress.get("raw_unique_count") or 0),
                        next_page=int(progress.get("next_page") or 1),
                        status="retry_exhausted",
                        checkpoint_file=progress.get("checkpoint_file", ""),
                        retry_count=retry_count,
                        last_error=result.get("stop_reason", ""),
                    )

            if pending_queue:
                time.sleep(10)

        self.combo_results = list(results_by_combo.values())

        quality_summary_file, role_summaries = self.save_quality_summary()
        analysis_file = self.save_analysis_dataset()
        completed_count = sum(1 for result in self.combo_results if result.get("passed"))
        interrupted_count = len(self.combo_results) - completed_count
        role_target_count = sum(1 for item in role_summaries if item["target_met"])
        total_elapsed = (datetime.now() - self.batch_start).total_seconds()
        self.log(f"\n{'='*60}")
        self.log(f"全部采集完成!")
        self.log(f"总计采集: {self.total_collected} 条")
        self.log(f"正常结束组合: {completed_count}/{len(self.combo_results)}")
        self.log(f"待补采或中断组合: {interrupted_count}")
        self.log(f"岗位总体目标达成: {role_target_count}/{len(role_summaries)}")
        self.log(f"爬取质量汇总: {quality_summary_file}")
        if analysis_file:
            self.log(f"岗位分析数据: {analysis_file}")
        self.log(f"总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
        self.log(f"数据目录: {DATA_ROOT}")
        self.log("="*60)


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="前程无忧批量采集")
    parser.add_argument("--city", default="", help="限定城市名（逗号分隔），留空=全部")
    parser.add_argument("--keyword", default="", help="限定岗位关键词（逗号分隔），留空=全部")
    parser.add_argument(
        "--max-per-combo",
        type=int,
        default=0,
        help="可选的城市×岗位有效数据上限，0=不设硬上限",
    )
    parser.add_argument(
        "--min-raw-per-combo",
        type=int,
        default=0,
        help="已停用，仅兼容旧命令；原始数据量不再作为达标条件",
    )
    parser.add_argument(
        "--role-target",
        type=int,
        default=500,
        help="每个岗位跨所选城市的有效数据总体目标",
    )
    parser.add_argument(
        "--min-relevance-pages",
        type=int,
        default=5,
        help="启用零有效页停止前至少读取的页数",
    )
    parser.add_argument(
        "--max-zero-valid-pages",
        type=int,
        default=5,
        help="连续多少页没有新增有效岗位后停止当前组合",
    )
    parser.add_argument("--max-page-retries", type=int, default=3, help="单页失败重试次数")
    parser.add_argument("--max-combo-retries", type=int, default=2, help="待补采组合重试次数")
    parser.add_argument("--retry-backoff", type=int, default=20, help="单页重试退避秒数")
    parser.add_argument("--combo-retry-delay", type=int, default=60, help="组合补采等待秒数")
    parser.add_argument(
        "--restart-completed",
        action="store_true",
        help="忽略已完成进度并重新采集终态组合",
    )
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    if args.max_per_combo < 0 or args.min_raw_per_combo < 0:
        parser.error("采集上限不能为负数")
    if args.role_target <= 0:
        parser.error("--role-target 必须为正整数")
    if args.min_relevance_pages <= 0 or args.max_zero_valid_pages <= 0:
        parser.error("自适应停止页数必须为正整数")
    if min(
        args.max_page_retries,
        args.max_combo_retries,
        args.retry_backoff,
        args.combo_retry_delay,
    ) < 0:
        parser.error("重试次数和等待秒数不能为负数")

    if args.headful:
        CRAWL_CONFIG["headless"] = False
    CRAWL_CONFIG["max_jobs_per_combo"] = args.max_per_combo
    CRAWL_CONFIG["min_raw_jobs_per_combo"] = 0
    CRAWL_CONFIG["role_valid_target"] = args.role_target
    CRAWL_CONFIG["min_pages_before_relevance_stop"] = args.min_relevance_pages
    CRAWL_CONFIG["max_consecutive_zero_valid_pages"] = args.max_zero_valid_pages
    CRAWL_CONFIG["max_page_retries"] = args.max_page_retries
    CRAWL_CONFIG["max_combo_retries"] = args.max_combo_retries
    CRAWL_CONFIG["retry_backoff_sec"] = args.retry_backoff
    CRAWL_CONFIG["combo_retry_delay_sec"] = args.combo_retry_delay
    CRAWL_CONFIG["resume_completed"] = not args.restart_completed
    if args.min_raw_per_combo:
        print("提示: --min-raw-per-combo 已停用，原始数据量仅作为审计指标。")

    # 筛选城市
    city_list = CITIES
    if args.city:
        names = set(x.strip() for x in args.city.split(","))
        city_list = [c for c in CITIES if c["name"] in names]
        print(f"限定城市: {[c['name'] for c in city_list]}")

    # 筛选岗位
    job_list = CORE_JOBS
    if args.keyword:
        job_list = [x.strip() for x in args.keyword.split(",")]
        print(f"限定岗位: {job_list}")

    # 初始化
    conn = init_fingerprint_db()
    fps, cfps = load_seen_fingerprints(conn)
    print(f"指纹库: {len(fps)} 条source指纹, {len(cfps)} 条内容指纹")

    try:
        crawler = Batch51JobCrawler(conn)
        crawler.seen_fps = fps
        crawler.seen_cfps = cfps

        # 覆盖城市和岗位列表
        crawler.cities = city_list
        crawler.jobs = job_list

        crawler.run_all()
    except KeyboardInterrupt:
        print("\n采集已由用户中断；当前检查点已保留，重新执行同一命令可继续采集。")
        raise SystemExit(130)
    finally:
        conn.close()
        print("\n指纹库已保存，下次运行将自动跳过已采集的岗位。")
