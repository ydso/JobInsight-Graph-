# -*- coding: utf-8 -*-
"""
Merge, clean, deduplicate, and report quality for the three crawler datasets.

Default input root:
    D:/桌面/毕业设计/数据/爬虫

Default output directory:
    D:/桌面/毕业设计/数据/爬虫/总爬虫数据分析/分析结果

Run:
    python merge_clean_job_data.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


SOURCE_DIRS = ("前程无忧", "国家大学生就业服务平台", "智联招聘")

SOURCE_PRIORITY = {
    "国家大学生就业服务平台": 3,
    "前程无忧": 2,
    "智联招聘": 1,
}

JOB_ALIASES = {
    "BI 分析师": "BI分析师",
    "BI工程师": "BI分析师",
    "Python 开发工程师": "Python开发工程师",
    "python开发工程师": "Python开发工程师",
    "大数据工程师": "大数据开发工程师",
    "数据仓库": "数据仓库工程师",
    "数仓工程师": "数据仓库工程师",
    "机器学习": "机器学习工程师",
    "算法": "算法工程师",
}

CORE_FIELDS = [
    "岗位名称",
    "公司名称",
    "标准城市",
    "薪资原文",
    "最低月薪",
    "最高月薪",
    "学历要求",
    "经验要求",
    "岗位描述清洗文本",
    "技能列表",
    "内容指纹",
    "来源岗位指纹",
]

BASE_COLUMNS = [
    "记录ID",
    "来源平台",
    "数据源",
    "来源目录",
    "源文件",
    "源文件行号",
    "搜索岗位",
    "标准岗位",
    "搜索城市",
    "标准城市",
    "来源岗位ID",
    "来源链接",
    "采集时间",
    "来源岗位指纹",
    "内容指纹",
    "岗位名称",
    "公司名称",
    "实际城市",
    "区县",
    "薪资原文",
    "最低月薪",
    "最高月薪",
    "薪资中位数",
    "年薪月数",
    "学历要求",
    "学历原文",
    "经验要求",
    "经验原文",
    "最低经验年限",
    "最高经验年限",
    "经验要求类型",
    "岗位类型",
    "行业",
    "公司规模",
    "公司性质",
    "发布日期",
    "发布日期原文",
    "经度",
    "纬度",
    "正文确认标准专业",
    "专业类别",
    "专业要求级别",
    "岗位描述清洗文本",
    "任职要求文本",
    "岗位职责文本",
    "确认技能候选",
    "技能列表",
    "技能数量",
    "技能来源字段",
    "技能类别",
    "技能证据",
    "技能提取范围",
    "数据质量标记",
    "去重键",
    "完整度评分",
]


@dataclass
class FileStat:
    source: str
    job_folder: str
    file_path: str
    encoding: str = ""
    parsed_rows: int = 0
    valid_rows: int = 0
    blank_rows: int = 0
    error: str = ""


@dataclass
class RunResult:
    rows_before_dedup: list[dict[str, str]] = field(default_factory=list)
    rows_after_dedup: list[dict[str, str]] = field(default_factory=list)
    duplicate_rows: list[dict[str, str]] = field(default_factory=list)
    file_stats: list[FileStat] = field(default_factory=list)
    fieldnames_seen: set[str] = field(default_factory=set)


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", clean_cell(value)).lower()


def normalize_job(value: str) -> str:
    text = clean_cell(value)
    if not text:
        return ""
    text = text.replace(" ", "")
    for alias, standard in JOB_ALIASES.items():
        if alias.replace(" ", "").lower() in text.lower():
            return standard
    return text


def normalize_city(actual_city: str, search_city: str) -> str:
    city = clean_cell(actual_city) or clean_cell(search_city)
    city = re.sub(r"[市区县]+$", "", city)
    return city


def parse_number(value: str) -> float | None:
    text = clean_cell(value)
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    return float(match.group(0))


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def split_skills(value: str) -> list[str]:
    text = clean_cell(value)
    if not text:
        return []
    parts = re.split(r"[|,，;；、/]+", text)
    skills: list[str] = []
    seen: set[str] = set()
    for part in parts:
        skill = clean_cell(part)
        if not skill:
            continue
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        skills.append(skill)
    return skills


def first_non_empty(row: dict[str, str], fields: Iterable[str]) -> tuple[str, str]:
    for field_name in fields:
        value = clean_cell(row.get(field_name, ""))
        if value:
            return value, field_name
    return "", ""


def make_hash(*parts: str, length: int = 16) -> str:
    joined = "||".join(clean_cell(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def detect_source(path: Path, input_root: Path) -> str:
    try:
        relative_parts = path.relative_to(input_root).parts
    except ValueError:
        relative_parts = path.parts
    for source in SOURCE_DIRS:
        if source in relative_parts:
            return source
    return relative_parts[0] if relative_parts else "未知来源"


def iter_csv_rows(path: Path) -> tuple[str, list[str], list[dict[str, str]]]:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as file_obj:
                reader = csv.DictReader(file_obj)
                rows = [dict(row) for row in reader]
                return encoding, reader.fieldnames or [], rows
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise UnicodeDecodeError("csv", b"", 0, 1, "无法用 utf-8-sig/utf-8/gb18030 解码: " + " | ".join(errors))


def is_blank_row(row: dict[str, str]) -> bool:
    return not any(clean_cell(value) for value in row.values())


def quality_score(row: dict[str, str]) -> int:
    score = 0
    weighted_fields = {
        "岗位名称": 5,
        "公司名称": 5,
        "标准城市": 5,
        "薪资原文": 3,
        "最低月薪": 3,
        "最高月薪": 3,
        "学历要求": 3,
        "经验要求": 3,
        "岗位描述清洗文本": 8,
        "技能列表": 8,
        "来源链接": 2,
        "发布日期": 2,
    }
    for field_name, weight in weighted_fields.items():
        if clean_cell(row.get(field_name, "")):
            score += weight
    score += min(int(row.get("技能数量", "0") or "0"), 10)
    score += SOURCE_PRIORITY.get(row.get("来源平台", ""), 0)
    return score


def normalize_row(raw: dict[str, str], source: str, source_file: Path, line_no: int, input_root: Path) -> dict[str, str]:
    row = {field_name: clean_cell(raw.get(field_name, "")) for field_name in raw.keys()}

    search_job = row.get("搜索岗位", "")
    title = row.get("岗位名称", "")
    standard_job = normalize_job(search_job) or normalize_job(title)

    standard_city = normalize_city(row.get("实际城市", ""), row.get("搜索城市", ""))

    salary_min = parse_number(row.get("最低月薪", ""))
    salary_max = parse_number(row.get("最高月薪", ""))
    salary_mid: float | None = None
    if salary_min is not None and salary_max is not None:
        salary_mid = (salary_min + salary_max) / 2
    elif salary_min is not None:
        salary_mid = salary_min
    elif salary_max is not None:
        salary_mid = salary_max

    skills_text, skills_source = first_non_empty(
        row,
        ("确认技能候选", "任职要求技能候选", "描述技能候选"),
    )
    skills = split_skills(skills_text)

    description = row.get("岗位描述清洗文本", "") or row.get("岗位描述原文", "")
    dedup_key = row.get("内容指纹", "")
    if not dedup_key:
        dedup_key = make_hash(
            title,
            row.get("公司名称", ""),
            standard_city,
            description,
            row.get("来源链接", ""),
        )

    record_id = make_hash(source, row.get("来源岗位ID", ""), row.get("来源链接", ""), dedup_key, str(line_no))
    relative_file = str(source_file.relative_to(input_root)) if source_file.is_relative_to(input_root) else str(source_file)

    normalized = {column: "" for column in BASE_COLUMNS}
    normalized.update(
        {
            "记录ID": record_id,
            "来源平台": source,
            "数据源": row.get("数据源", source),
            "来源目录": source_file.parent.name,
            "源文件": relative_file,
            "源文件行号": str(line_no),
            "搜索岗位": search_job,
            "标准岗位": standard_job,
            "搜索城市": row.get("搜索城市", ""),
            "标准城市": standard_city,
            "来源岗位ID": row.get("来源岗位ID", ""),
            "来源链接": row.get("来源链接", ""),
            "采集时间": row.get("采集时间", ""),
            "来源岗位指纹": row.get("来源岗位指纹", ""),
            "内容指纹": row.get("内容指纹", ""),
            "岗位名称": title,
            "公司名称": row.get("公司名称", ""),
            "实际城市": row.get("实际城市", ""),
            "区县": row.get("区县", ""),
            "薪资原文": row.get("薪资原文", ""),
            "最低月薪": format_number(salary_min),
            "最高月薪": format_number(salary_max),
            "薪资中位数": format_number(salary_mid),
            "年薪月数": row.get("年薪月数", ""),
            "学历要求": row.get("学历要求", ""),
            "学历原文": row.get("学历原文", ""),
            "经验要求": row.get("经验要求", ""),
            "经验原文": row.get("经验原文", ""),
            "最低经验年限": row.get("最低经验年限", ""),
            "最高经验年限": row.get("最高经验年限", ""),
            "经验要求类型": row.get("经验要求类型", ""),
            "岗位类型": row.get("岗位类型", ""),
            "行业": row.get("行业", ""),
            "公司规模": row.get("公司规模", ""),
            "公司性质": row.get("公司性质", ""),
            "发布日期": row.get("发布日期", ""),
            "发布日期原文": row.get("发布日期原文", ""),
            "经度": row.get("经度", ""),
            "纬度": row.get("纬度", ""),
            "正文确认标准专业": row.get("正文确认标准专业", ""),
            "专业类别": row.get("专业类别", ""),
            "专业要求级别": row.get("专业要求级别", ""),
            "岗位描述清洗文本": description,
            "任职要求文本": row.get("任职要求文本", ""),
            "岗位职责文本": row.get("岗位职责文本", ""),
            "确认技能候选": row.get("确认技能候选", ""),
            "技能列表": "|".join(skills),
            "技能数量": str(len(skills)),
            "技能来源字段": skills_source,
            "技能类别": row.get("技能类别", ""),
            "技能证据": row.get("技能证据", ""),
            "技能提取范围": row.get("技能提取范围", ""),
            "数据质量标记": row.get("数据质量标记", ""),
            "去重键": dedup_key,
        }
    )
    normalized["完整度评分"] = str(quality_score(normalized))
    return normalized


def collect_rows(input_root: Path) -> RunResult:
    result = RunResult()
    csv_files = sorted(
        path for path in input_root.rglob("*_jobs.csv")
        if "总爬虫数据分析" not in path.parts
    )

    for path in csv_files:
        source = detect_source(path, input_root)
        stat = FileStat(source=source, job_folder=path.parent.name, file_path=str(path))
        try:
            encoding, fieldnames, raw_rows = iter_csv_rows(path)
            stat.encoding = encoding
            result.fieldnames_seen.update(fieldnames)
            for index, raw_row in enumerate(raw_rows, start=2):
                stat.parsed_rows += 1
                if is_blank_row(raw_row):
                    stat.blank_rows += 1
                    continue
                stat.valid_rows += 1
                result.rows_before_dedup.append(normalize_row(raw_row, source, path, index, input_root))
        except Exception as exc:  # Keep the batch running and surface errors in the report.
            stat.error = f"{type(exc).__name__}: {exc}"
        result.file_stats.append(stat)
    return result


def deduplicate_rows(result: RunResult) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in result.rows_before_dedup:
        grouped[row["去重键"]].append(row)

    kept_rows: list[dict[str, str]] = []
    duplicate_rows: list[dict[str, str]] = []

    for key, rows in grouped.items():
        sorted_rows = sorted(
            rows,
            key=lambda item: (
                int(item.get("完整度评分", "0") or "0"),
                SOURCE_PRIORITY.get(item.get("来源平台", ""), 0),
                item.get("采集时间", ""),
            ),
            reverse=True,
        )
        kept = sorted_rows[0]
        kept_rows.append(kept)
        for duplicate in sorted_rows[1:]:
            record = dict(duplicate)
            record["保留记录ID"] = kept["记录ID"]
            record["重复原因"] = "去重键相同"
            duplicate_rows.append(record)

    result.rows_after_dedup = sorted(
        kept_rows,
        key=lambda item: (
            item.get("来源平台", ""),
            item.get("标准岗位", ""),
            item.get("标准城市", ""),
            item.get("公司名称", ""),
            item.get("岗位名称", ""),
        ),
    )
    result.duplicate_rows = sorted(
        duplicate_rows,
        key=lambda item: (item.get("去重键", ""), item.get("来源平台", ""), item.get("记录ID", "")),
    )


def pct(numerator: int | float, denominator: int | float) -> str:
    if not denominator:
        return "0.0%"
    return f"{numerator * 100 / denominator:.1f}%"


def count_missing(rows: list[dict[str, str]], field_name: str) -> int:
    return sum(1 for row in rows if not clean_cell(row.get(field_name, "")))


def summarize_by_source(result: RunResult) -> list[dict[str, str]]:
    before_by_source = Counter(row["来源平台"] for row in result.rows_before_dedup)
    after_by_source = Counter(row["来源平台"] for row in result.rows_after_dedup)
    duplicate_by_source = Counter(row["来源平台"] for row in result.duplicate_rows)
    files_by_source = Counter(stat.source for stat in result.file_stats)
    blank_by_source = Counter()
    valid_by_source = Counter()
    error_by_source = Counter()
    for stat in result.file_stats:
        blank_by_source[stat.source] += stat.blank_rows
        valid_by_source[stat.source] += stat.valid_rows
        if stat.error:
            error_by_source[stat.source] += 1

    rows: list[dict[str, str]] = []
    for source in sorted(set(before_by_source) | set(files_by_source)):
        source_rows = [row for row in result.rows_after_dedup if row["来源平台"] == source]
        total = len(source_rows)
        rows.append(
            {
                "来源平台": source,
                "文件数": str(files_by_source[source]),
                "有效行数_去重前": str(before_by_source[source]),
                "有效行数_去重后": str(after_by_source[source]),
                "移除重复行数": str(duplicate_by_source[source]),
                "空白行数": str(blank_by_source[source]),
                "读取错误文件数": str(error_by_source[source]),
                "技能覆盖率": pct(total - count_missing(source_rows, "技能列表"), total),
                "薪资解析覆盖率": pct(total - count_missing(source_rows, "薪资中位数"), total),
                "描述覆盖率": pct(total - count_missing(source_rows, "岗位描述清洗文本"), total),
                "岗位名缺失率": pct(count_missing(source_rows, "岗位名称"), total),
                "公司名缺失率": pct(count_missing(source_rows, "公司名称"), total),
                "城市缺失率": pct(count_missing(source_rows, "标准城市"), total),
                "学历缺失率": pct(count_missing(source_rows, "学历要求"), total),
                "经验缺失率": pct(count_missing(source_rows, "经验要求"), total),
            }
        )
    return rows


def summarize_by_source_city_job(result: RunResult) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    before = Counter((row["来源平台"], row["标准城市"], row["标准岗位"]) for row in result.rows_before_dedup)
    removed = Counter((row["来源平台"], row["标准城市"], row["标准岗位"]) for row in result.duplicate_rows)
    for row in result.rows_after_dedup:
        grouped[(row["来源平台"], row["标准城市"], row["标准岗位"])].append(row)

    summary_rows: list[dict[str, str]] = []
    for (source, city, job), rows in sorted(grouped.items()):
        total = len(rows)
        summary_rows.append(
            {
                "来源平台": source,
                "标准城市": city,
                "标准岗位": job,
                "有效行数_去重前": str(before[(source, city, job)]),
                "有效行数_去重后": str(total),
                "移除重复行数": str(removed[(source, city, job)]),
                "技能覆盖率": pct(total - count_missing(rows, "技能列表"), total),
                "薪资解析覆盖率": pct(total - count_missing(rows, "薪资中位数"), total),
                "描述覆盖率": pct(total - count_missing(rows, "岗位描述清洗文本"), total),
            }
        )
    return summary_rows


def summarize_files(result: RunResult, input_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for stat in sorted(result.file_stats, key=lambda item: item.file_path):
        path = Path(stat.file_path)
        relative = str(path.relative_to(input_root)) if path.is_relative_to(input_root) else stat.file_path
        rows.append(
            {
                "来源平台": stat.source,
                "岗位目录": stat.job_folder,
                "文件": relative,
                "编码": stat.encoding,
                "解析行数": str(stat.parsed_rows),
                "有效行数": str(stat.valid_rows),
                "空白行数": str(stat.blank_rows),
                "是否空文件": "是" if stat.valid_rows == 0 else "否",
                "错误": stat.error,
            }
        )
    return rows


def summarize_skill_frequency(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    total_by_group: Counter[tuple[str, str]] = Counter()
    for row in rows:
        job = row.get("标准岗位", "")
        city = row.get("标准城市", "")
        total_by_group[(job, city)] += 1
        for skill in split_skills(row.get("技能列表", "")):
            counter[(job, city, skill)] += 1
    result_rows: list[dict[str, str]] = []
    for (job, city, skill), count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        total = total_by_group[(job, city)]
        result_rows.append(
            {
                "标准岗位": job,
                "标准城市": city,
                "技能": skill,
                "出现岗位数": str(count),
                "岗位内出现率": pct(count, total),
            }
        )
    return result_rows


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def top_counter(rows: list[dict[str, str]], field_name: str, limit: int = 10) -> list[tuple[str, int]]:
    counter = Counter(row.get(field_name, "") or "未标注" for row in rows)
    return counter.most_common(limit)


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def build_report(
    result: RunResult,
    source_summary: list[dict[str, str]],
    city_job_summary: list[dict[str, str]],
    input_root: Path,
    output_dir: Path,
    generated_at: datetime,
) -> str:
    total_files = len(result.file_stats)
    error_files = sum(1 for stat in result.file_stats if stat.error)
    empty_files = sum(1 for stat in result.file_stats if stat.valid_rows == 0)
    parsed_rows = sum(stat.parsed_rows for stat in result.file_stats)
    blank_rows = sum(stat.blank_rows for stat in result.file_stats)
    before_rows = len(result.rows_before_dedup)
    after_rows = len(result.rows_after_dedup)
    duplicate_rows = len(result.duplicate_rows)

    lines = [
        "# 三源爬虫数据融合清洗质量报告",
        "",
        f"- 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 输入目录：`{input_root}`",
        f"- 输出目录：`{output_dir}`",
        "",
        "## 1. 总体结果",
        "",
        markdown_table(
            ["指标", "数值"],
            [
                ["读取 CSV 文件数", str(total_files)],
                ["读取错误文件数", str(error_files)],
                ["有效行数为 0 的文件数", str(empty_files)],
                ["CSV 解析行数", str(parsed_rows)],
                ["全空行数", str(blank_rows)],
                ["有效岗位行数（去重前）", str(before_rows)],
                ["移除重复岗位行数", str(duplicate_rows)],
                ["统一总表行数（去重后）", str(after_rows)],
                ["全局技能覆盖率", pct(after_rows - count_missing(result.rows_after_dedup, "技能列表"), after_rows)],
                ["全局薪资解析覆盖率", pct(after_rows - count_missing(result.rows_after_dedup, "薪资中位数"), after_rows)],
                ["全局岗位描述覆盖率", pct(after_rows - count_missing(result.rows_after_dedup, "岗位描述清洗文本"), after_rows)],
            ],
        ),
        "",
        "## 2. 分来源质量概览",
        "",
        markdown_table(
            [
                "来源平台",
                "文件数",
                "去重前",
                "去重后",
                "重复行",
                "技能覆盖率",
                "薪资覆盖率",
                "描述覆盖率",
                "学历缺失率",
                "经验缺失率",
            ],
            [
                [
                    row["来源平台"],
                    row["文件数"],
                    row["有效行数_去重前"],
                    row["有效行数_去重后"],
                    row["移除重复行数"],
                    row["技能覆盖率"],
                    row["薪资解析覆盖率"],
                    row["描述覆盖率"],
                    row["学历缺失率"],
                    row["经验缺失率"],
                ]
                for row in source_summary
            ],
        ),
        "",
        "## 3. 岗位与城市分布",
        "",
        "### 3.1 标准岗位 Top 10",
        "",
        markdown_table(["标准岗位", "岗位数"], [[name, str(count)] for name, count in top_counter(result.rows_after_dedup, "标准岗位")]),
        "",
        "### 3.2 标准城市 Top 10",
        "",
        markdown_table(["标准城市", "岗位数"], [[name, str(count)] for name, count in top_counter(result.rows_after_dedup, "标准城市")]),
        "",
        "## 4. 主要字段缺失情况",
        "",
        markdown_table(
            ["字段", "缺失行数", "缺失率"],
            [
                [
                    field_name,
                    str(count_missing(result.rows_after_dedup, field_name)),
                    pct(count_missing(result.rows_after_dedup, field_name), after_rows),
                ]
                for field_name in CORE_FIELDS
            ],
        ),
        "",
        "## 5. 输出文件说明",
        "",
        markdown_table(
            ["文件", "说明"],
            [
                ["unified_jobs_clean.csv", "三源合并、字段标准化、按去重键保留最完整记录后的统一岗位总表"],
                ["duplicates_removed.csv", "被去重移除的记录及其对应保留记录 ID"],
                ["quality_by_source.csv", "按数据来源汇总的数据质量指标"],
                ["quality_by_source_city_job.csv", "按来源、城市、岗位汇总的数据量和覆盖率"],
                ["file_inventory.csv", "每个原始 CSV 的读取、空行、错误情况"],
                ["skill_frequency_by_job_city.csv", "按标准岗位和城市统计的技能频次，可用于后续可视化或图谱边权重"],
                ["run_summary.json", "本次运行的机器可读摘要"],
            ],
        ),
        "",
        "## 6. 处理规则摘要",
        "",
        "- 全空 CSV 行不进入统一总表，但会计入文件清单。",
        "- 岗位名统一处理了空格差异和常见别名，例如 `BI 分析师` 归一为 `BI分析师`。",
        "- 城市优先使用 `实际城市`，缺失时回退到 `搜索城市`，并去掉末尾的 `市/区/县`。",
        "- 技能优先使用 `确认技能候选`；缺失时回退到 `任职要求技能候选` 或 `描述技能候选`。",
        "- 去重优先使用 `内容指纹`；若缺失，则用岗位名、公司、城市、描述、来源链接生成兜底签名。",
        "- 重复记录保留完整度评分更高的行；评分考虑岗位、公司、城市、薪资、学历、经验、描述、技能、链接等字段。",
        "",
    ]

    if error_files:
        error_rows = [
            [Path(stat.file_path).name, stat.source, stat.error]
            for stat in result.file_stats
            if stat.error
        ][:20]
        lines.extend(
            [
                "## 7. 读取错误文件",
                "",
                markdown_table(["文件", "来源平台", "错误"], error_rows),
                "",
            ]
        )

    city_job_top = sorted(
        city_job_summary,
        key=lambda row: int(row["有效行数_去重后"]),
        reverse=True,
    )[:20]
    lines.extend(
        [
            "## 7. 城市-岗位样本量 Top 20",
            "",
            markdown_table(
                ["来源平台", "标准城市", "标准岗位", "去重后行数", "技能覆盖率"],
                [
                    [
                        row["来源平台"],
                        row["标准城市"],
                        row["标准岗位"],
                        row["有效行数_去重后"],
                        row["技能覆盖率"],
                    ]
                    for row in city_job_top
                ],
            ),
            "",
            "## 8. 后续建议",
            "",
            "1. 用 `unified_jobs_clean.csv` 作为 Silver 层岗位宽表，继续拆分岗位-技能关系表。",
            "2. 用 `skill_frequency_by_job_city.csv` 生成技能热度图、岗位画像和知识图谱边权重。",
            "3. 趋势预测需要多批次采集或外部历史数据；当前三源数据更适合支撑横截面的需求分析。",
            "",
        ]
    )

    return "\n".join(lines)


def write_outputs(result: RunResult, input_root: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now()

    source_summary = summarize_by_source(result)
    city_job_summary = summarize_by_source_city_job(result)
    file_inventory = summarize_files(result, input_root)
    skill_frequency = summarize_skill_frequency(result.rows_after_dedup)

    write_csv(output_dir / "unified_jobs_clean.csv", result.rows_after_dedup, BASE_COLUMNS)
    duplicate_columns = BASE_COLUMNS + ["保留记录ID", "重复原因"]
    write_csv(output_dir / "duplicates_removed.csv", result.duplicate_rows, duplicate_columns)
    write_csv(output_dir / "quality_by_source.csv", source_summary, list(source_summary[0].keys()) if source_summary else ["来源平台"])
    write_csv(
        output_dir / "quality_by_source_city_job.csv",
        city_job_summary,
        list(city_job_summary[0].keys()) if city_job_summary else ["来源平台", "标准城市", "标准岗位"],
    )
    write_csv(output_dir / "file_inventory.csv", file_inventory, list(file_inventory[0].keys()) if file_inventory else ["文件"])
    write_csv(
        output_dir / "skill_frequency_by_job_city.csv",
        skill_frequency,
        ["标准岗位", "标准城市", "技能", "出现岗位数", "岗位内出现率"],
    )

    report = build_report(result, source_summary, city_job_summary, input_root, output_dir, generated_at)
    (output_dir / "data_quality_report.md").write_text(report, encoding="utf-8")

    summary = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "input_root": str(input_root),
        "output_dir": str(output_dir),
        "csv_files": len(result.file_stats),
        "error_files": sum(1 for stat in result.file_stats if stat.error),
        "empty_valid_files": sum(1 for stat in result.file_stats if stat.valid_rows == 0),
        "parsed_rows": sum(stat.parsed_rows for stat in result.file_stats),
        "blank_rows": sum(stat.blank_rows for stat in result.file_stats),
        "valid_rows_before_dedup": len(result.rows_before_dedup),
        "duplicates_removed": len(result.duplicate_rows),
        "rows_after_dedup": len(result.rows_after_dedup),
        "source_summary": source_summary,
    }
    write_json(output_dir / "run_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_input_root = script_dir.parent
    default_output_dir = script_dir / "分析结果"
    parser = argparse.ArgumentParser(description="三源招聘爬虫数据融合清洗与质量报告生成脚本")
    parser.add_argument("--input-root", type=Path, default=default_input_root, help="爬虫数据根目录，默认是脚本所在目录的上一级")
    parser.add_argument("--output-dir", type=Path, default=default_output_dir, help="输出目录，默认写入脚本目录下的 分析结果")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = args.input_root.resolve()
    output_dir = args.output_dir.resolve()

    if not input_root.exists():
        raise FileNotFoundError(f"输入目录不存在：{input_root}")

    result = collect_rows(input_root)
    deduplicate_rows(result)
    summary = write_outputs(result, input_root, output_dir)

    print("三源爬虫数据融合清洗完成")
    print(f"输入目录: {input_root}")
    print(f"输出目录: {output_dir}")
    print(f"CSV 文件数: {summary['csv_files']}")
    print(f"去重前有效行数: {summary['valid_rows_before_dedup']}")
    print(f"移除重复行数: {summary['duplicates_removed']}")
    print(f"统一总表行数: {summary['rows_after_dedup']}")
    print(f"质量报告: {output_dir / 'data_quality_report.md'}")


if __name__ == "__main__":
    main()
