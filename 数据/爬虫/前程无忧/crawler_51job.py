"""
前程无忧(51job) 招聘数据爬取脚本
====================================
功能：爬取前程无忧指定城市、指定岗位的招聘数据
城市：重庆 (060000)
岗位：数据分析师
范围：20条测试数据

技术方案：
  1. Playwright 启动浏览器，绕过阿里云WAF验证
  2. 通过页面拦截搜索API响应，获取包含完整岗位描述的数据
  3. 清洗文本并保存为适合后续 Spark/NLP 处理的完整 CSV

合规原则：
  - 仅采集公开可访问的岗位信息
  - 单线程串行采集，请求间隔 ≥ 5秒
  - 仅用于毕业设计学术研究
  - 不绕过验证码、登录等安全机制

日期：2026-06-18
"""

import argparse
import csv
import html
import json
import re
import os
import time
import hashlib
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ============================================================
# 配置参数
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DICTIONARY_FILE = os.path.join(BASE_DIR, "skill_dictionary.csv")
MAJOR_DICTIONARY_FILE = os.path.join(BASE_DIR, "major_dictionary.csv")
CRAWLER_VERSION = "2026-06-18.7"
CSV_SCHEMA_VERSION = "51job-csv-v6"


CONFIG = {
    # 目标城市（重庆）
    "city_code": "060000",
    "city_name": "重庆",

    # 目标岗位关键词
    "keyword": "数据分析师",

    # 采集数量
    "max_jobs": 20,           # 本次采集目标：20条
    "page_size": 20,          # 官网当前搜索API每页返回数
    "max_pages": 100,
    "max_consecutive_empty_pages": 3,

    # 频率控制（秒）
    "request_interval": 8,    # 翻页间隔
    "page_load_timeout": 30,  # 页面加载超时
    "data_wait_timeout": 15,  # 等待数据加载超时

    # 输出目录
    "output_dir": os.path.join(BASE_DIR, "爬取数据"),

    # 搜索参数
    "search_type": 2,         # 2 = 精确搜索

    # 标题质量控制。每个关键词应配置对应的核心词，避免搜索结果混入会计、物流等岗位。
    "title_include_keywords": None,  # None时由keyword自动推导，也可通过命令行覆盖
    "filter_irrelevant_titles": True,

    # 浏览器设置
    "headless": True,         # 无头模式
    "viewport_width": 1920,
    "viewport_height": 1080,
}


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
            canonical_name = (row.get("canonical_name") or "").strip()
            aliases = [x.strip() for x in (row.get("aliases") or "").split("|") if x.strip()]
            category = (row.get("category") or "").strip()
            version = (row.get("version") or "").strip()
            if not canonical_name or not aliases or not version:
                raise ValueError(f"词典存在不完整记录: {path}")
            entries.append({
                "canonical_name": canonical_name,
                "aliases": aliases,
                "category": category,
                "pattern": _alias_pattern(aliases),
            })
            versions.add(version)

    if len(versions) != 1:
        raise ValueError(f"词典版本不一致: {path}")
    return entries, versions.pop()


SKILL_ENTRIES, SKILL_DICTIONARY_VERSION = _load_alias_dictionary(SKILL_DICTIONARY_FILE)
MAJOR_ENTRIES, MAJOR_DICTIONARY_VERSION = _load_alias_dictionary(MAJOR_DICTIONARY_FILE)
SKILL_PATTERNS = [(x["canonical_name"], x["pattern"]) for x in SKILL_ENTRIES]
MAJOR_PATTERNS = [(x["canonical_name"], x["pattern"]) for x in MAJOR_ENTRIES]
SKILL_CATEGORY_BY_NAME = {x["canonical_name"]: x["category"] for x in SKILL_ENTRIES}
MAJOR_CATEGORY_BY_NAME = {x["canonical_name"]: x["category"] for x in MAJOR_ENTRIES}
MAJOR_ALIAS_TO_CANONICAL = {
    alias.casefold(): entry["canonical_name"]
    for entry in MAJOR_ENTRIES
    for alias in entry["aliases"]
}


REQUIREMENT_HEADINGS = [
    "任职要求", "任职资格", "岗位要求", "职位要求", "工作要求",
    "任职条件", "招聘要求", "岗位基本需求", "基本要求",
]
RESPONSIBILITY_HEADINGS = [
    "岗位职责", "工作职责", "职位描述", "工作内容", "职责与工作内容", "岗位介绍", "核心岗位职责",
]
OTHER_SECTION_HEADINGS = [
    "福利", "福利待遇", "薪资福利", "薪酬福利", "岗位亮点", "工作地址",
    "上班地址", "公司地址", "公司简介", "联系方式", "作息安排",
]

# ============================================================
# 工具函数
# ============================================================

def compute_fingerprint(job_title, company_name, city, job_id):
    """计算同一数据源内的岗位标识；优先使用稳定的 source_job_id。"""
    if job_id:
        raw = f"51job|{str(job_id).strip()}"
    else:
        raw = f"{job_title.strip()}|{company_name.strip()}|{city.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_content_fingerprint(job_title, company_name, description):
    """计算内容指纹，用于识别换了岗位 ID 的重复发布。"""
    normalized = re.sub(r"\s+", "", normalize_job_text(description)).lower()
    # 不纳入标题：平台经常只改标题中的福利后缀并重新生成岗位 ID。
    raw = f"{company_name.strip()}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ensure_output_dir(path):
    """确保输出目录存在"""
    os.makedirs(path, exist_ok=True)


def sanitize_filename(text):
    """清理文件名中的非法字符"""
    return re.sub(r'[<>:"/\\|?*]', '_', text)


def canonicalize_job_url(url):
    """移除搜索会话参数和锚点，保留稳定的岗位详情URL。"""
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def source_has_more_pages(page_num, page_size, total_count, source_item_count):
    """根据官网原始结果数判断是否还有下一页，不受本地标题过滤影响。"""
    if source_item_count <= 0:
        return False
    if isinstance(total_count, int) and total_count >= 0:
        return page_num * page_size < total_count
    return source_item_count >= page_size


def normalize_job_text(text):
    """解码 HTML 实体并统一换行、空白，保留段落结构供后续 NLP 使用。"""
    if not text:
        return ""
    value = html.unescape(str(text)).replace("\u00a0", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    cleaned = []
    previous_blank = False
    for line in lines:
        if line:
            cleaned.append(line)
            previous_blank = False
        elif cleaned and not previous_blank:
            cleaned.append("")
            previous_blank = True
    return "\n".join(cleaned).strip()


def _heading_pattern(headings):
    names = "|".join(sorted((re.escape(x) for x in headings), key=len, reverse=True))
    enumeration = r"(?:(?:[一二三四五六七八九十]+|\d+)[、.．)）]\s*)?"
    return re.compile(
        rf"^[ \t]*{enumeration}[【\[]?[ \t]*(?:{names})[ \t]*[】\]]?[ \t]*[：:]?[ \t]*",
        re.MULTILINE | re.IGNORECASE,
    )


def _extract_section(description, target_headings):
    text = normalize_job_text(description)
    if not text:
        return ""

    target_pattern = _heading_pattern(target_headings)
    start_match = target_pattern.search(text)
    if not start_match:
        return ""

    all_headings = REQUIREMENT_HEADINGS + RESPONSIBILITY_HEADINGS + OTHER_SECTION_HEADINGS
    boundary_pattern = _heading_pattern(all_headings)
    end_match = boundary_pattern.search(text, start_match.end())
    end = end_match.start() if end_match else len(text)
    return text[start_match.end():end].strip()


def _evidence_snippet(text, start, end, max_length=140):
    left_candidates = [text.rfind(mark, 0, start) for mark in "\n。；;！!?？"]
    left = max(left_candidates) + 1
    right_candidates = [text.find(mark, end) for mark in "\n。；;！!?？"]
    right_candidates = [pos for pos in right_candidates if pos >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    snippet = text[left:right].strip()
    if len(snippet) > max_length:
        snippet = snippet[:max_length].rstrip() + "…"
    return snippet


def _is_negated_skill_mention(text, start, end):
    prefix = text[max(0, start - 16):start]
    suffix = text[end:min(len(text), end + 16)]
    prefix_pattern = re.compile(
        r"(?:不要求|不需要|不必|无需|无须)(?:具备|掌握|熟悉|了解|使用|会)?[\s、，,:：]*$"
    )
    suffix_pattern = re.compile(r"^[\s、，,:：]*(?:不是必需|非必需|不作要求|不做要求)")
    return bool(prefix_pattern.search(prefix) or suffix_pattern.search(suffix))


def extract_skill_candidates(description):
    """从原文生成高精度技能候选及证据，不把平台标签直接当作技能。"""
    text = normalize_job_text(description)
    skills = []
    evidence = {}
    for canonical_name, pattern in SKILL_PATTERNS:
        valid_match = None
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if not _is_negated_skill_mention(text, match.start(), match.end()):
                valid_match = match
                break
        if not valid_match:
            continue
        skills.append(canonical_name)
        evidence[canonical_name] = _evidence_snippet(
            text,
            valid_match.start(),
            valid_match.end(),
        )
    return skills, evidence


def extract_platform_major_tags(api_major1="", api_major2=""):
    """保留平台搜索API返回的隐藏专业标签，不将其视为岗位专业要求。"""
    tags = []
    for value in (api_major1, api_major2):
        value = normalize_job_text(value)
        if value and value not in tags:
            tags.append(value)
    return tags


def extract_major_candidates(description):
    """只从岗位正文中提取有学历/专业语境证据的专业名称。"""
    candidates = []
    evidence_text = extract_major_evidence(description)
    for _, pattern in MAJOR_PATTERNS:
        match = re.search(pattern, evidence_text, re.IGNORECASE)
        if match and match.group(0) not in candidates:
            candidates.append(match.group(0))
    return candidates


def normalize_major_candidates(candidates):
    """将原始专业名称映射到可统计的标准名称，无法映射时保留原值。"""
    normalized = []
    for value in candidates:
        value = normalize_job_text(value)
        canonical = MAJOR_ALIAS_TO_CANONICAL.get(value.casefold(), value)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _is_major_requirement_line(line):
    if "专业" not in line:
        return False
    context_words = (
        "学历", "本科", "大专", "硕士", "博士", "毕业", "相关专业",
        "专业背景", "专业方向", "专业类别", "类专业", "专业不限", "不限专业", "优先",
    )
    return any(word in line for word in context_words)


def extract_major_evidence(description):
    """只保留学历/毕业背景语境中的专业要求，排除“专业服务”等普通用法。"""
    text = normalize_job_text(description)
    lines = []
    for line in re.split(r"[\n。；;]", text):
        line = line.strip()
        if _is_major_requirement_line(line) and line not in lines:
            lines.append(line)
    return " || ".join(lines)


def classify_major_requirement(description):
    evidence = extract_major_evidence(description)
    if not evidence:
        return "未说明"
    is_unlimited = "专业不限" in evidence or "不限专业" in evidence
    is_preferred = "优先" in evidence
    if is_unlimited and is_preferred:
        return "不限_相关专业优先"
    if is_unlimited:
        return "不限"
    if is_preferred:
        return "优先"
    return "要求"


def derive_title_keywords(keyword):
    """从搜索词推导保守的标题核心词，可由命令行显式覆盖。"""
    value = re.sub(r"\s+", "", keyword or "")
    for suffix in ("高级工程师", "工程师", "专员", "主管", "经理", "师", "员"):
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            value = value[:-len(suffix)]
            break
    return [value] if value else []


def is_title_relevant(title, include_keywords):
    if not include_keywords:
        return True
    normalized = re.sub(r"\s+", "", title or "").lower()
    return any(re.sub(r"\s+", "", keyword).lower() in normalized for keyword in include_keywords)


def parse_salary(salary_text):
    """
    解析薪资格式 -> (min, max) 月薪(元)
    支持格式：
      - "6-7千" -> (6000, 7000)
      - "9千-1.4万" -> (9000, 14000)
      - "10-20万/年" -> (8333, 16667) 折算月薪
      - "150元/天" -> (3300, 3300) 按22天/月折算
      - "薪资面议" -> (None, None)
    """
    if not salary_text or salary_text == "薪资面议":
        return None, None

    text = salary_text.strip()
    if "小时" in text or "/时" in text:
        # 不擅自假设每日工时和每月工作天数，保留原文交给后续统一清洗。
        return None, None
    is_year = "年" in text
    is_day = "天" in text or "日" in text

    # 清理文本
    text_clean = text.replace("/年", "").replace("/月", "").replace("/天", "").replace("/日", "")
    text_clean = re.sub(r"[·・]?\d+\s*薪", "", text_clean)

    # 处理混合单位格式: "9千-1.4万" -> 拆分处理
    # 策略: 分别解析"-"前后的值，按各自单位换算

    has_wan = "万" in text
    has_qian = "千" in text

    if "-" in text_clean and (has_wan or has_qian):
        try:
            parts = text_clean.split("-")
            vals = []
            for part in parts:
                part = part.strip()
                num_str = re.findall(r'[\d.]+', part)
                if not num_str:
                    continue
                num = float(num_str[0])
                if "万" in part:
                    num *= 10000
                elif "千" in part:
                    num *= 1000
                elif "百" in part:
                    num *= 100
                elif "万" in text_clean:
                    # "10-20万" 中 "10" 的 "万" 是共享的
                    num *= 10000
                elif "千" in text_clean:
                    num *= 1000
                elif num < 100 and not is_day:
                    num *= 1000
                vals.append(num)
            if len(vals) == 2:
                min_val, max_val = min(vals), max(vals)
            elif len(vals) == 1:
                min_val = max_val = vals[0]
            else:
                min_val = max_val = 0
        except (ValueError, TypeError):
            min_val = max_val = 0
    else:
        # 统一处理单位
        text_clean = text_clean.replace("万", "").replace("千", "").replace(",", "")

        numbers = re.findall(r'[\d.]+', text_clean)
        if not numbers:
            return None, None

        try:
            nums = [float(n) for n in numbers]
            min_val = min(nums)
            max_val = max(nums) if len(nums) >= 2 else min_val

            # 万元单位
            if has_wan:
                min_val *= 10000
                max_val *= 10000
            # 千元单位
            elif has_qian:
                min_val *= 1000
                max_val *= 1000
            # 没有明确单位但数字较小（可能是千元）
            elif min_val < 100 and not is_day:
                min_val *= 1000
                max_val *= 1000
        except (ValueError, TypeError):
            return None, None

    # 年薪 -> 月薪（÷12）
    if is_year:
        min_val = round(min_val / 12)
        max_val = round(max_val / 12)

    # 日薪 -> 月薪（×22工作日）
    if is_day:
        min_val = round(min_val * 22)
        max_val = round(max_val * 22)

    return int(min_val), int(max_val)


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


def parse_education_details(text):
    """返回(最低学历, 优先学历)，避免把“硕士优先”误当最低要求。"""
    if not text:
        return "", ""
    value = text.strip()
    if "学历不限" in value or value in {"不限", "无学历要求"}:
        return "学历不限", ""

    aliases = [
        ("高中", "高中"),
        ("中专", "中专"),
        ("大专", "大专"),
        ("本科", "本科"),
        ("研究生", "硕士"),
        ("硕士", "硕士"),
        ("博士", "博士"),
    ]
    rank = {"高中": 0, "中专": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}
    alias_map = dict(aliases)
    token_pattern = "|".join(re.escape(token) for token, _ in sorted(aliases, key=lambda x: len(x[0]), reverse=True))
    preferred_pattern = re.compile(
        rf"(?P<degree>{token_pattern})(?:学历)?(?:及以上|以上)?[^，。；;]{{0,6}}优先"
    )
    preferred = [alias_map[match.group("degree")] for match in preferred_pattern.finditer(value)]
    minimum_text = preferred_pattern.sub("", value)
    minimum_candidates = [
        alias_map[match.group(0)]
        for match in re.finditer(token_pattern, minimum_text)
    ]

    minimum = min(minimum_candidates, key=rank.get) if minimum_candidates else ""
    preferred_value = max(preferred, key=rank.get) if preferred else ""
    if not minimum and preferred_value:
        minimum = preferred_value
        preferred_value = ""
    return minimum or value, preferred_value


def parse_education(text):
    """返回最低学历要求。"""
    return parse_education_details(text)[0]


def extract_requirement_from_desc(description):
    """兼容带括号、编号以及无冒号标题的任职要求段落。"""
    return _extract_section(description, REQUIREMENT_HEADINGS)


def extract_responsibility_from_desc(description):
    """兼容常见标题格式，从完整描述中分离岗位职责。"""
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


# ============================================================
# 主爬取逻辑
# ============================================================

class Job51Crawler:
    """前程无忧爬虫"""

    def __init__(self, config):
        self.config = dict(config)
        if not self.config.get("title_include_keywords"):
            self.config["title_include_keywords"] = derive_title_keywords(self.config.get("keyword", ""))
        self.api_responses = []      # 拦截到的API响应
        self.collected_jobs = []     # 已采集岗位
        self.seen_fingerprints = set()  # 去重指纹集合
        self.seen_content_fingerprints = set()  # 识别换ID重复发布
        self.last_source_item_count = 0
        self.last_total_count = None
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(self.config["output_dir"], self.timestamp)

    def log(self, msg):
        """带时间戳的日志"""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}")

    def setup_response_interception(self, page):
        """设置API响应拦截器"""
        def handle_response(response):
            url = response.url
            if "api/job/search-pc" in url and response.status == 200:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = response.body()
                        text = body.decode("utf-8", errors="ignore")
                        # 确保是有效JSON
                        data = json.loads(text)
                        if "resultbody" in data and "job" in data.get("resultbody", {}):
                            self.api_responses.append({
                                "url": url,
                                "data": data,
                                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds")
                            })
                            self.log(f"  拦截到API响应: {len(data['resultbody']['job']['items'])} 条岗位")
                except Exception as exc:
                    self.log(f"  ⚠ API响应解析失败: {exc}")

        page.on("response", handle_response)

    def build_search_url(self, page_num=1):
        """构造搜索页URL"""
        params = {
            "keyword": self.config["keyword"],
            "searchType": self.config["search_type"],
            "jobArea": self.config["city_code"],
            "page": page_num,
        }
        return f"https://we.51job.com/pc/search?{urlencode(params)}"

    def crawl_page(self, page, page_num):
        """爬取单页数据"""
        url = self.build_search_url(page_num)
        self.log(f"\n{'='*60}")
        self.log(f"正在加载第 {page_num} 页...")
        self.log(f"URL: {url}")

        try:
            self.api_responses.clear()
            self.last_source_item_count = 0
            self.last_total_count = None

            # 导航到搜索页
            page.goto(url, timeout=self.config["page_load_timeout"] * 1000, wait_until="domcontentloaded")

            # 等待数据加载（WAF验证 + API调用 + 页面渲染）
            self.log("  等待WAF验证和数据加载...")
            page.wait_for_timeout(self.config["data_wait_timeout"] * 1000)

            # 同时检查页面是否被重定向到验证页
            if "验证" in page.title() or "waf" in page.url.lower():
                self.log("  ⚠ 页面触发了验证码，跳过此页")
                return []

            # 优先使用API响应中的数据（包含完整jobDescribe）
            jobs = []
            page_responses = self.api_responses
            if page_responses:
                api_data = page_responses[-1]["data"]  # 只使用本页最新响应
                source_job_data = api_data.get("resultbody", {}).get("job", {})
                items = source_job_data.get("items", [])
                self.last_source_item_count = len(items)
                total_count = source_job_data.get("totalCount", source_job_data.get("totalcount"))
                try:
                    self.last_total_count = int(total_count) if total_count not in (None, "") else None
                except (TypeError, ValueError):
                    self.last_total_count = None
                self.log(f"  API数据: {len(items)} 条岗位")

                for item in items:
                    job = self._parse_api_job(item)
                    if job:
                        jobs.append(job)
            else:
                # DOM摘要不含完整岗位描述，不满足专业/技能抽取要求，因此不写入结果。
                self.log("  ⚠ 未拦截到含完整描述的API响应，本页不保存")

            return jobs

        except PlaywrightTimeoutError:
            self.log(f"  ✗ 页面加载超时")
            return []
        except Exception as e:
            self.log(f"  ✗ 爬取出错: {e}")
            return []

    def _parse_api_job(self, item):
        """解析API返回的单条岗位数据"""
        fp = None
        try:
            # 基础信息
            job_id = item.get("jobId", "")
            job_title = item.get("jobName", "")
            company_name = item.get("fullCompanyName", "") or item.get("companyName", "")

            # 同一来源岗位ID去重
            fp = compute_fingerprint(job_title, company_name, self.config["city_name"], job_id)
            if fp in self.seen_fingerprints:
                return None
            self.seen_fingerprints.add(fp)

            # 优先使用API数值薪资；缺失时再解析展示文本。
            salary_text = item.get("provideSalaryString", "")
            raw_salary_min = item.get("jobSalaryMin")
            raw_salary_max = item.get("jobSalaryMax")
            salary_from_api = False
            try:
                salary_min = int(float(raw_salary_min)) if raw_salary_min not in (None, "") else None
                salary_max = int(float(raw_salary_max)) if raw_salary_max not in (None, "") else None
                salary_from_api = salary_min is not None and salary_max is not None
            except (TypeError, ValueError):
                salary_min = salary_max = None
            if salary_min is None or salary_max is None:
                salary_min, salary_max = parse_salary(salary_text)
            salary_month_match = re.search(r"(\d+)\s*薪", salary_text)
            salary_months = int(salary_month_match.group(1)) if salary_month_match else 12
            if "面议" in salary_text or not salary_text:
                salary_period = "面议或未说明"
                salary_conversion_method = "未转换"
                salary_months = None
            elif "小时" in salary_text or "/时" in salary_text:
                salary_period = "小时"
                salary_conversion_method = "保留原文_未折算"
                salary_months = None
            elif "/年" in salary_text or "年薪" in salary_text:
                salary_period = "年"
                salary_conversion_method = "年薪除以12"
            elif "/天" in salary_text or "/日" in salary_text:
                salary_period = "日"
                salary_conversion_method = "日薪乘以22"
            else:
                salary_period = "月"
                salary_conversion_method = "API月薪数值" if salary_from_api else "薪资文本解析"

            # 工作地点 - API中 jobAreaString 通常有值, workAreaString 可能为null
            work_area = item.get("jobAreaString", "") or item.get("workAreaString", "")
            # 还可以从jobAreaLevelDetail中获取更详细的城市信息
            area_detail = item.get("jobAreaLevelDetail", {})
            city = ""
            district = ""
            if area_detail:
                city = area_detail.get("cityString", "") or area_detail.get("provinceString", "")
                district = area_detail.get("districtString", "")
            if not city and work_area:
                # 解析 "重庆·南岸区" -> city, district
                if "·" in work_area:
                    parts = work_area.split("·", 1)
                    city = parts[0].strip()
                    district = parts[1].strip() if len(parts) > 1 else ""
                else:
                    city = work_area.strip()
                    district = ""

            # 经验 & 学历
            experience_raw = item.get("workYearString", "")
            experience, experience_min_years, experience_max_years, experience_type = (
                parse_experience_details(experience_raw)
            )
            education_raw = item.get("degreeString", "")
            education, education_preferred = parse_education_details(education_raw)

            # 公司信息
            company_size = item.get("companySizeString", "")
            industry = item.get("companyIndustryType1Str", "")
            company_type = item.get("companyTypeString", "")

            # 岗位类型
            term_str = item.get("termStr", "全职")
            job_type = term_str if term_str else "全职"

            # 发布日期
            issue_date = item.get("issueDateString", "")
            # 标准化日期格式
            if issue_date and len(issue_date) == 19:  # "2026-06-05 15:09:19"
                publish_date = issue_date[:10]  # "2026-06-05"
            else:
                publish_date = issue_date

            # 完整描述：原文与清洗文本同时保留，CSV支持带换行字段。
            job_description_raw = item.get("jobDescribe", "") or ""
            job_description = normalize_job_text(job_description_raw)

            # 分离职责/要求，并生成可追溯的专业与技能候选。
            requirement_text = extract_requirement_from_desc(job_description)
            responsibility_text = extract_responsibility_from_desc(job_description)
            major1_raw = item.get("major1Str", "") or ""
            major2_raw = item.get("major2Str", "") or ""
            platform_major_tags = extract_platform_major_tags(major1_raw, major2_raw)
            platform_major_normalized = normalize_major_candidates(platform_major_tags)
            major_candidates_raw = extract_major_candidates(job_description)
            major_candidates = normalize_major_candidates(major_candidates_raw)
            major_categories = {
                major: MAJOR_CATEGORY_BY_NAME.get(major, "未映射")
                for major in major_candidates
            }
            major_evidence = extract_major_evidence(job_description)
            major_requirement_level = classify_major_requirement(job_description)
            if major_candidates:
                major_source = "description"
                overlap = set(platform_major_normalized) & set(major_candidates)
                if not platform_major_tags:
                    platform_major_validation = "not_provided"
                elif set(platform_major_normalized).issubset(set(major_candidates)):
                    platform_major_validation = "confirmed_by_description"
                elif overlap:
                    platform_major_validation = "partially_confirmed"
                else:
                    platform_major_validation = "not_supported_by_description"
                major_decision_note = "仅依据岗位正文中的专业要求及证据生成标准专业候选"
            else:
                major_source = "not_specified"
                platform_major_validation = (
                    "unverified_api_only" if platform_major_tags else "not_provided"
                )
                major_decision_note = "岗位正文未明确专业要求，平台隐藏专业标签不纳入标准专业候选"

            description_skill_candidates, description_skill_evidence = extract_skill_candidates(job_description)
            requirement_skill_candidates, requirement_skill_evidence = extract_skill_candidates(requirement_text)
            if requirement_text:
                skill_candidates = requirement_skill_candidates
                skill_evidence = requirement_skill_evidence
                skill_extraction_scope = "requirement_text"
            else:
                skill_candidates = description_skill_candidates
                skill_evidence = description_skill_evidence
                skill_extraction_scope = "job_description_fallback"
            job_tags = item.get("jobTags", [])
            platform_skill_candidates, _ = extract_skill_candidates(
                "\n".join(str(tag) for tag in job_tags)
            )
            skill_categories = {
                skill: SKILL_CATEGORY_BY_NAME.get(skill, "未分类")
                for skill in skill_candidates
            }

            title_relevant = is_title_relevant(
                job_title,
                self.config.get("title_include_keywords", []),
            )

            # 岗位URL
            job_url_raw = item.get("jobHref", "") or ""
            job_url = canonicalize_job_url(job_url_raw)

            # 经纬度
            lon = item.get("lon", "")
            lat = item.get("lat", "")

            # 构建结构化数据
            job_record = {
                # 元数据
                "source": "51job",
                "processing_stage": "raw_enriched",
                "crawler_version": CRAWLER_VERSION,
                "schema_version": CSV_SCHEMA_VERSION,
                "skill_dictionary_version": SKILL_DICTIONARY_VERSION,
                "major_dictionary_version": MAJOR_DICTIONARY_VERSION,
                "source_job_id": job_id,
                "source_url": job_url,
                "source_url_raw": job_url_raw,
                "crawl_time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "fingerprint": fp,

                # 基础字段
                "job_title": job_title,
                "company_name": company_name,
                "city": city,
                "district": district,
                "salary_text": salary_text,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_months": salary_months,
                "salary_period": salary_period,
                "salary_conversion_method": salary_conversion_method,
                "education": education,
                "education_raw": education_raw,
                "education_preferred": education_preferred,
                "experience": experience,
                "experience_raw": experience_raw,
                "experience_min_years": experience_min_years,
                "experience_max_years": experience_max_years,
                "experience_type": experience_type,
                "job_type": job_type,

                # 公司信息
                "industry": industry,
                "company_size": company_size,
                "company_type": company_type,

                # 日期
                "publish_date": publish_date,
                "publish_date_raw": issue_date,

                # 地理位置
                "longitude": lon,
                "latitude": lat,

                # 专业要求
                "major1_raw": major1_raw,
                "major2_raw": major2_raw,
                "platform_major_tags": platform_major_tags,
                "platform_major_normalized": platform_major_normalized,
                "platform_major_validation": platform_major_validation,
                "major_candidates_raw": major_candidates_raw,
                "major_candidates": major_candidates,
                "major_categories": major_categories,
                "major_requirement_level": major_requirement_level,
                "major_evidence": major_evidence,
                "major_source": major_source,
                "major_decision_note": major_decision_note,

                # 文本内容与技能候选
                "job_description_raw": job_description_raw,
                "job_description": job_description,
                "requirement_text": requirement_text,
                "responsibility_text": responsibility_text,
                "job_tags": job_tags,
                "platform_skill_candidates": platform_skill_candidates,
                "description_skill_candidates": description_skill_candidates,
                "requirement_skill_candidates": requirement_skill_candidates,
                "skill_candidates": skill_candidates,
                "skill_categories": skill_categories,
                "skill_evidence": skill_evidence,
                "skill_extraction_scope": skill_extraction_scope,
                "is_title_relevant": title_relevant,
            }

            content_fp = compute_content_fingerprint(job_title, company_name, job_description)
            job_record["content_fingerprint"] = content_fp

            quality_flags = []
            if not requirement_text:
                quality_flags.append("requirement_section_missing")
            if not major_candidates:
                quality_flags.append("major_not_specified")
            if platform_major_validation == "unverified_api_only":
                quality_flags.append("platform_major_unverified")
            elif platform_major_validation == "not_supported_by_description":
                quality_flags.append("platform_major_not_supported")
            elif platform_major_validation == "partially_confirmed":
                quality_flags.append("platform_major_partially_confirmed")
            if not skill_candidates:
                quality_flags.append("skill_candidate_missing")
            if not education:
                quality_flags.append("education_missing")
            if not district:
                quality_flags.append("district_missing")
            if not title_relevant:
                quality_flags.append("title_irrelevant")
            job_record["quality_flags"] = quality_flags

            # 排除无效或无关岗位；被过滤的指纹允许后续同ID的有效响应重新判断。
            if self._is_irrelevant(job_record):
                self.seen_fingerprints.discard(fp)
                return None

            if content_fp in self.seen_content_fingerprints:
                self.seen_fingerprints.discard(fp)
                return None
            self.seen_content_fingerprints.add(content_fp)

            return job_record

        except Exception as e:
            if fp:
                self.seen_fingerprints.discard(fp)
            self.log(f"  ✗ 解析岗位出错: {e}")
            return None

    def _is_irrelevant(self, job):
        """过滤培训广告、缺少正文以及偏离目标关键词的岗位。"""
        title = job.get("job_title", "")
        description = job.get("job_description", "")

        # 过滤培训/招生类
        spam_keywords = ["培训", "包就业", "学费", "招生", "零基础", "实训",
                        "招生老师", "课程顾问", "电话销售", "保险", "房产"]

        for kw in spam_keywords:
            if kw in title:
                return True

        # 过滤描述过短的（可能是无效数据）
        if len(description) < 20:
            return True

        if self.config.get("filter_irrelevant_titles", True) and not job.get("is_title_relevant", False):
            return True

        return False

    def run(self):
        """执行爬取"""
        self.log("="*60)
        self.log("前程无忧招聘数据爬取 - 开始")
        self.log(f"目标城市: {self.config['city_name']} ({self.config['city_code']})")
        self.log(f"目标岗位: {self.config['keyword']}")
        self.log(f"目标数量: {self.config['max_jobs']} 条")
        self.log(f"输出目录: {self.output_dir}")
        self.log("="*60)

        # 创建输出目录
        ensure_output_dir(self.output_dir)

        # 启动Playwright
        with sync_playwright() as p:
            self.log("\n正在启动浏览器...")
            browser = p.chromium.launch(
                headless=self.config["headless"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={
                    "width": self.config["viewport_width"],
                    "height": self.config["viewport_height"],
                },
                locale="zh-CN",
            )

            # 添加反检测脚本
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            """)

            # 设置响应拦截
            self.setup_response_interception(page)

            # 逐页爬取
            page_num = 1
            max_pages = self.config.get("max_pages", 100)
            max_empty_pages = self.config.get("max_consecutive_empty_pages", 3)
            consecutive_empty_pages = 0

            while len(self.collected_jobs) < self.config["max_jobs"] and page_num <= max_pages:
                # 爬取当前页
                page_jobs = self.crawl_page(page, page_num)

                # 添加到结果集（控制在目标数量内）
                remaining = self.config["max_jobs"] - len(self.collected_jobs)
                new_jobs = page_jobs[:remaining]
                self.collected_jobs.extend(new_jobs)

                self.log(f"  本页获取: {len(new_jobs)} 条新岗位")
                self.log(f"  累计: {len(self.collected_jobs)}/{self.config['max_jobs']}")

                # 仅按官网原始结果判断空页；本地标题过滤为0不代表官网没有下一页。
                if self.last_source_item_count == 0:
                    consecutive_empty_pages += 1
                    self.log("  官网本页无原始岗位数据，尝试下一页...")
                    if consecutive_empty_pages >= max_empty_pages:
                        self.log(f"  连续 {consecutive_empty_pages} 页无官网原始数据，停止翻页")
                        break
                else:
                    consecutive_empty_pages = 0

                # 已收集足够，停止翻页
                if len(self.collected_jobs) >= self.config["max_jobs"]:
                    break

                if not source_has_more_pages(
                    page_num,
                    self.config["page_size"],
                    self.last_total_count,
                    self.last_source_item_count,
                ):
                    self.log("  已到达官网搜索结果末页，停止翻页")
                    break

                page_num += 1

                # 翻页间隔
                if len(self.collected_jobs) < self.config["max_jobs"]:
                    self.log(f"  等待 {self.config['request_interval']} 秒后翻页...")
                    time.sleep(self.config['request_interval'])

            # 关闭浏览器
            browser.close()

        # 保存结果
        self.save_results()

        # 输出统计
        self.log(f"\n{'='*60}")
        self.log(f"爬取完成！")
        self.log(f"总计采集: {len(self.collected_jobs)} 条岗位")
        self.log(f"数据保存在: {self.output_dir}")
        self.log("="*60)

        return self.collected_jobs

    def save_results(self):
        """仅保存完整 CSV 和运行日志，不生成 JSON 数据文件。"""
        ensure_output_dir(self.output_dir)

        timestamp = self.timestamp
        keyword = sanitize_filename(self.config["keyword"])
        city = sanitize_filename(self.config["city_name"])
        prefix = f"{keyword}_{city}_{timestamp}"

        csv_file = os.path.join(self.output_dir, f"{prefix}_jobs.csv")
        field_specs = [
            ("record_no", "序号"),
            ("source", "数据源"),
            ("processing_stage", "数据处理阶段"),
            ("crawler_version", "爬虫版本"),
            ("schema_version", "CSV结构版本"),
            ("skill_dictionary_version", "技能词典版本"),
            ("major_dictionary_version", "专业词典版本"),
            ("search_keyword", "搜索关键词"),
            ("search_city_name", "搜索城市"),
            ("search_city_code", "搜索城市编码"),
            ("source_job_id", "来源岗位ID"),
            ("source_url", "规范来源链接"),
            ("source_url_raw", "来源链接原文"),
            ("crawl_time", "爬取时间"),
            ("fingerprint", "来源岗位指纹"),
            ("content_fingerprint", "内容指纹"),
            ("job_title", "岗位名称"),
            ("company_name", "公司名称"),
            ("city", "城市"),
            ("district", "区县"),
            ("salary_text", "薪资原文"),
            ("salary_min", "最低月薪"),
            ("salary_max", "最高月薪"),
            ("salary_months", "年薪月数"),
            ("salary_period", "薪资周期"),
            ("salary_conversion_method", "薪资换算方式"),
            ("education", "学历要求"),
            ("education_raw", "学历原文"),
            ("education_preferred", "优先学历"),
            ("experience", "经验要求"),
            ("experience_raw", "经验原文"),
            ("experience_min_years", "最低经验年限"),
            ("experience_max_years", "最高经验年限"),
            ("experience_type", "经验要求类型"),
            ("job_type", "岗位类型"),
            ("industry", "行业"),
            ("company_size", "公司规模"),
            ("company_type", "公司性质"),
            ("publish_date", "发布日期"),
            ("publish_date_raw", "发布日期原文"),
            ("longitude", "经度"),
            ("latitude", "纬度"),
            ("major1_raw", "平台隐藏专业标签1"),
            ("major2_raw", "平台隐藏专业标签2"),
            ("platform_major_tags", "平台隐藏专业标签汇总"),
            ("platform_major_normalized", "平台隐藏专业标签标准化"),
            ("platform_major_validation", "平台专业标签核验状态"),
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
            ("platform_skill_candidates", "官网标签技能候选"),
            ("description_skill_candidates", "描述技能候选"),
            ("requirement_skill_candidates", "任职要求技能候选"),
            ("skill_candidates", "技能候选"),
            ("skill_categories", "技能类别"),
            ("skill_evidence", "技能证据"),
            ("skill_extraction_scope", "技能提取范围"),
            ("is_title_relevant", "标题是否相关"),
            ("quality_flags", "数据质量标记"),
        ]
        fieldnames = [label for _, label in field_specs]

        def flatten(value):
            if value is None:
                return ""
            if isinstance(value, bool):
                return "1" if value else "0"
            if isinstance(value, (list, tuple, set)):
                return "|".join(str(item).replace("|", "/") for item in value)
            if isinstance(value, dict):
                return " || ".join(
                    f"{key}=>{str(item).replace('||', '/').strip()}"
                    for key, item in value.items()
                )
            return value

        with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for i, job in enumerate(self.collected_jobs, 1):
                row = {
                    label: flatten(job.get(internal_name, ""))
                    for internal_name, label in field_specs
                }
                row.update({
                    "序号": i,
                    "搜索关键词": self.config["keyword"],
                    "搜索城市": self.config["city_name"],
                    "搜索城市编码": self.config["city_code"],
                })
                writer.writerow(row)
        self.log(f"  完整CSV数据: {csv_file}")

        log_file = os.path.join(self.output_dir, f"{prefix}_run_log.txt")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"爬取时间: {timestamp}\n")
            f.write(f"数据源: 前程无忧 51job\n")
            f.write(f"城市: {self.config['city_name']} ({self.config['city_code']})\n")
            f.write(f"岗位关键词: {self.config['keyword']}\n")
            f.write(f"爬虫版本: {CRAWLER_VERSION}\n")
            f.write(f"CSV结构版本: {CSV_SCHEMA_VERSION}\n")
            f.write(f"技能词典版本: {SKILL_DICTIONARY_VERSION}\n")
            f.write(f"专业词典版本: {MAJOR_DICTIONARY_VERSION}\n")
            f.write(f"采集数量: {len(self.collected_jobs)} 条\n")
            written_fingerprints = {job.get("fingerprint") for job in self.collected_jobs if job.get("fingerprint")}
            f.write(f"写入岗位指纹数量: {len(written_fingerprints)}\n")
            f.write(f"\n输出文件:\n")
            f.write(f"  - 完整CSV数据: {os.path.basename(csv_file)}\n")
            f.write(f"\n爬取日志（简要）:\n")
            for i, job in enumerate(self.collected_jobs, 1):
                f.write(f"  {i}. [{job['job_title']}] @ {job['company_name']} | "
                       f"{job['salary_text']} | {job['district']} | "
                       f"专业候选: {len(job.get('major_candidates', []))} | "
                       f"技能候选: {len(job.get('skill_candidates', []))}\n")
        self.log(f"  运行日志: {log_file}")


# ============================================================
# 主入口
# ============================================================

def build_config_from_args(argv=None):
    parser = argparse.ArgumentParser(description="前程无忧岗位数据采集器（完整CSV输出）")
    parser.add_argument("--keyword", default=CONFIG["keyword"], help="岗位搜索关键词")
    parser.add_argument("--city-name", default=CONFIG["city_name"], help="城市名称")
    parser.add_argument("--city-code", default=CONFIG["city_code"], help="前程无忧城市编码")
    parser.add_argument("--max-jobs", type=int, default=CONFIG["max_jobs"], help="目标有效岗位数")
    parser.add_argument("--max-pages", type=int, default=CONFIG["max_pages"], help="最大翻页数")
    parser.add_argument("--output-dir", default=CONFIG["output_dir"], help="输出根目录")
    parser.add_argument(
        "--title-keywords",
        default=None,
        help="逗号分隔的岗位标题核心词；不填写时从keyword自动推导",
    )
    parser.add_argument(
        "--keep-irrelevant-titles",
        action="store_true",
        help="保留标题不匹配的搜索结果，供后续统一清洗",
    )
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    args = parser.parse_args(argv)

    config = dict(CONFIG)
    config.update({
        "keyword": args.keyword,
        "city_name": args.city_name,
        "city_code": args.city_code,
        "max_jobs": args.max_jobs,
        "max_pages": args.max_pages,
        "output_dir": args.output_dir,
        "headless": not args.headful,
        "filter_irrelevant_titles": not args.keep_irrelevant_titles,
        "title_include_keywords": (
            [x.strip() for x in args.title_keywords.split(",") if x.strip()]
            if args.title_keywords is not None
            else None
        ),
    })
    return config

if __name__ == "__main__":
    crawler = Job51Crawler(build_config_from_args())
    jobs = crawler.run()

    # 输出简要汇总
    print(f"\n{'='*60}")
    print(f"爬取结果汇总")
    print(f"{'='*60}")
    for i, job in enumerate(jobs, 1):
        skills = job.get("skill_candidates", [])
        skill_str = ", ".join(skills[:8])
        if len(skills) > 8:
            skill_str += f" ... (+{len(skills)-8})"

        salary_display = f"{job['salary_text']}"
        if job['salary_min']:
            salary_display += f" (约{job['salary_min']}-{job['salary_max']}元/月)"
        print(f"\n{i:2d}. {job['job_title']}")
        print(f"    公司: {job['company_name']}")
        print(f"    薪资: {salary_display}")
        print(f"    地点: {job['city']}·{job['district']}")
        print(f"    学历: {job['education']} | 经验: {job['experience']}")
        print(f"    发布日期: {job['publish_date']}")
        print(f"    专业候选: {', '.join(job.get('major_candidates', []))}")
        print(f"    技能候选: {skill_str}")
        desc = job.get('job_description', '')
        print(f"    描述预览: {desc[:80]}..." if len(desc) > 80 else f"    描述: {desc}")
