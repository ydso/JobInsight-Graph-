# -*- coding: utf-8 -*-
"""
Export the cleaned recruitment dataset to Neo4j LOAD CSV files.

Run from the project root:
    python 数据/图谱/export_graph_csv.py

Export directly to a Neo4j Desktop import directory:
    python 数据/图谱/export_graph_csv.py --output-dir "C:/path/to/dbms/import"
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "graph_config.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出 Neo4j 知识图谱节点和关系 CSV")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="图谱导出配置 JSON")
    parser.add_argument("--input-csv", type=Path, default=None, help="覆盖输入 CSV 路径")
    parser.add_argument("--input-encoding", default=None, help="覆盖输入 CSV 编码")
    parser.add_argument("--output-dir", type=Path, default=None, help="覆盖输出目录")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_path(path_value: object, base_dir: Path) -> Path:
    path = Path(str(path_value))
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").replace("\x00", " ").strip()
    return re.sub(r"\s+", " ", text)


def stable_id(*parts: str, prefix: str = "") -> str:
    raw = "||".join(clean_cell(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def split_multi(value: str) -> list[str]:
    text = clean_cell(value)
    if not text:
        return []
    parts = re.split(r"[|,，;；、]+", text)
    ignored = {"", "无", "暂无", "不限", "未说明", "未知", "null", "None", "none"}
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        item = clean_cell(part)
        if item in ignored or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def number_text(value: str) -> str:
    text = clean_cell(value).replace(",", "")
    if not text:
        return ""
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return match.group(0) if match else ""


def int_text(value: str) -> str:
    number = number_text(value)
    return str(int(float(number))) if number else ""


def estimate_annual_salary(row: dict[str, str]) -> str:
    salary_mid = number_text(row.get("薪资中位数", ""))
    if not salary_mid:
        return ""
    months = int_text(row.get("年薪月数", "")) or "12"
    return f"{float(salary_mid) * int(months):.2f}"


def normalize_date(value: str) -> str:
    text = clean_cell(value)
    if not text:
        return ""
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-").replace(".", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return ""


def trim_text(value: str, max_len: int) -> str:
    text = clean_cell(value)
    if max_len <= 0 or len(text) <= max_len:
        return text
    return text[:max_len]


def read_rows(input_csv: Path, encoding: str) -> list[dict[str, str]]:
    with input_csv.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        return [{clean_cell(k): clean_cell(v) for k, v in row.items()} for row in reader]


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: clean_cell(row.get(key, "")) for key in fieldnames})
            count += 1
    return count


def build_graph_rows(rows: list[dict[str, str]], max_text_length: int, min_role_skill_count: int) -> dict[str, list[dict[str, object]]]:
    job_nodes: dict[str, dict[str, object]] = {}
    role_nodes: dict[str, dict[str, object]] = {}
    skill_nodes: dict[str, dict[str, object]] = {}
    city_nodes: dict[str, dict[str, object]] = {}
    company_nodes: dict[str, dict[str, object]] = {}
    major_nodes: dict[str, dict[str, object]] = {}

    rel_belongs: set[tuple[str, str]] = set()
    rel_located: set[tuple[str, str]] = set()
    rel_company: set[tuple[str, str]] = set()
    rel_requires: set[tuple[str, str, str, str, str]] = set()
    rel_major: set[tuple[str, str, str, str]] = set()

    role_job_ids: defaultdict[str, set[str]] = defaultdict(set)
    role_skill_job_ids: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    skill_job_ids: defaultdict[str, set[str]] = defaultdict(set)
    city_job_ids: defaultdict[str, set[str]] = defaultdict(set)
    company_job_ids: defaultdict[str, set[str]] = defaultdict(set)
    major_job_ids: defaultdict[str, set[str]] = defaultdict(set)
    source_counter_by_role: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for index, row in enumerate(rows, start=1):
        record_id = clean_cell(row.get("记录ID", "")) or stable_id(
            row.get("岗位名称", ""),
            row.get("公司名称", ""),
            row.get("标准城市", ""),
            row.get("来源链接", ""),
            str(index),
            prefix="job_",
        )
        role_name = clean_cell(row.get("标准岗位", "")) or clean_cell(row.get("搜索岗位", ""))
        city_name = clean_cell(row.get("标准城市", "")) or clean_cell(row.get("搜索城市", ""))
        company_name = clean_cell(row.get("公司名称", ""))
        source_platform = clean_cell(row.get("来源平台", ""))

        job_nodes[record_id] = {
            "record_id": record_id,
            "title": row.get("岗位名称", ""),
            "standard_job": role_name,
            "standard_city": city_name,
            "company_name": company_name,
            "source_platform": source_platform,
            "source_url": row.get("来源链接", ""),
            "salary_text": row.get("薪资原文", ""),
            "salary_min": number_text(row.get("最低月薪", "")),
            "salary_max": number_text(row.get("最高月薪", "")),
            "salary_mid": number_text(row.get("薪资中位数", "")),
            "annual_salary_estimated": estimate_annual_salary(row),
            "education": row.get("学历要求", ""),
            "experience": row.get("经验要求", ""),
            "job_type": row.get("岗位类型", ""),
            "industry": row.get("行业", ""),
            "company_size": row.get("公司规模", ""),
            "company_type": row.get("公司性质", ""),
            "publish_date": normalize_date(row.get("发布日期", "")),
            "crawl_time": row.get("采集时间", ""),
            "quality_flag": row.get("数据质量标记", ""),
            "description_short": trim_text(row.get("岗位描述清洗文本", ""), max_text_length),
        }

        if role_name:
            role_nodes.setdefault(role_name, {"name": role_name})
            role_job_ids[role_name].add(record_id)
            source_counter_by_role[role_name][source_platform] += 1
            rel_belongs.add((record_id, role_name))

        if city_name:
            city_nodes.setdefault(city_name, {"name": city_name})
            city_job_ids[city_name].add(record_id)
            rel_located.add((record_id, city_name))

        if company_name:
            company_nodes.setdefault(company_name, {"name": company_name})
            company_job_ids[company_name].add(record_id)
            rel_company.add((record_id, company_name))

        skills = split_multi(row.get("技能列表", "")) or split_multi(row.get("确认技能候选", ""))
        for skill_name in skills:
            skill_nodes.setdefault(skill_name, {"name": skill_name})
            skill_job_ids[skill_name].add(record_id)
            if role_name:
                role_skill_job_ids[(role_name, skill_name)].add(record_id)
            rel_requires.add((record_id, skill_name, source_platform, role_name, city_name))

        major_category = row.get("专业类别", "")
        major_level = row.get("专业要求级别", "")
        for major_name in split_multi(row.get("正文确认标准专业", "")):
            major_nodes.setdefault(major_name, {"name": major_name})
            major_job_ids[major_name].add(record_id)
            rel_major.add((record_id, major_name, major_category, major_level))

    for role_name, node in role_nodes.items():
        source_counter = source_counter_by_role[role_name]
        node["job_count"] = len(role_job_ids[role_name])
        node["top_source"] = source_counter.most_common(1)[0][0] if source_counter else ""

    for skill_name, node in skill_nodes.items():
        node["job_count"] = len(skill_job_ids[skill_name])

    for city_name, node in city_nodes.items():
        node["job_count"] = len(city_job_ids[city_name])

    for company_name, node in company_nodes.items():
        node["job_count"] = len(company_job_ids[company_name])

    for major_name, node in major_nodes.items():
        node["job_count"] = len(major_job_ids[major_name])

    rel_role_skill = []
    for (role_name, skill_name), job_ids in role_skill_job_ids.items():
        job_count = len(job_ids)
        if job_count < min_role_skill_count:
            continue
        total = len(role_job_ids[role_name]) or 1
        rel_role_skill.append({
            "role_name": role_name,
            "skill_name": skill_name,
            "job_count": job_count,
            "skill_ratio": f"{job_count / total:.6f}",
        })

    return {
        "nodes_job_posting": sorted(job_nodes.values(), key=lambda item: str(item["record_id"])),
        "nodes_job_role": sorted(role_nodes.values(), key=lambda item: str(item["name"])),
        "nodes_skill": sorted(skill_nodes.values(), key=lambda item: (-int(item["job_count"]), str(item["name"]))),
        "nodes_city": sorted(city_nodes.values(), key=lambda item: str(item["name"])),
        "nodes_company": sorted(company_nodes.values(), key=lambda item: str(item["name"])),
        "nodes_major": sorted(major_nodes.values(), key=lambda item: str(item["name"])),
        "rel_belongs_to_role": [
            {"record_id": record_id, "role_name": role_name}
            for record_id, role_name in sorted(rel_belongs)
        ],
        "rel_located_in": [
            {"record_id": record_id, "city_name": city_name}
            for record_id, city_name in sorted(rel_located)
        ],
        "rel_posted_by": [
            {"record_id": record_id, "company_name": company_name}
            for record_id, company_name in sorted(rel_company)
        ],
        "rel_requires": [
            {
                "record_id": record_id,
                "skill_name": skill_name,
                "source_platform": source_platform,
                "standard_job": role_name,
                "standard_city": city_name,
            }
            for record_id, skill_name, source_platform, role_name, city_name in sorted(rel_requires)
        ],
        "rel_related_to_major": [
            {
                "record_id": record_id,
                "major_name": major_name,
                "major_category": major_category,
                "major_requirement_level": major_level,
            }
            for record_id, major_name, major_category, major_level in sorted(rel_major)
        ],
        "rel_role_requires_skill": sorted(
            rel_role_skill,
            key=lambda item: (str(item["role_name"]), -int(item["job_count"]), str(item["skill_name"])),
        ),
    }


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    config_dir = args.config.resolve().parent

    input_csv = args.input_csv or resolve_path(config["input_csv"], config_dir)
    fallback_csv = resolve_path(config["fallback_input_csv"], config_dir)
    if not input_csv.exists() and fallback_csv.exists():
        input_csv = fallback_csv
    if not input_csv.exists():
        raise FileNotFoundError(f"输入 CSV 不存在：{input_csv}")

    output_dir = args.output_dir or resolve_path(config["output_dir"], config_dir)
    encoding = args.input_encoding or str(config.get("input_encoding") or "utf-8-sig")
    max_text_length = int(config.get("max_text_length") or 300)
    min_role_skill_count = int(config.get("min_role_skill_count") or 1)

    rows = read_rows(input_csv, encoding)
    graph_rows = build_graph_rows(rows, max_text_length, min_role_skill_count)

    schemas = {
        "nodes_job_posting": [
            "record_id", "title", "standard_job", "standard_city", "company_name",
            "source_platform", "source_url", "salary_text", "salary_min", "salary_max",
            "salary_mid", "annual_salary_estimated", "education", "experience", "job_type",
            "industry", "company_size", "company_type", "publish_date", "crawl_time",
            "quality_flag", "description_short",
        ],
        "nodes_job_role": ["name", "job_count", "top_source"],
        "nodes_skill": ["name", "job_count"],
        "nodes_city": ["name", "job_count"],
        "nodes_company": ["name", "job_count"],
        "nodes_major": ["name", "job_count"],
        "rel_belongs_to_role": ["record_id", "role_name"],
        "rel_located_in": ["record_id", "city_name"],
        "rel_posted_by": ["record_id", "company_name"],
        "rel_requires": ["record_id", "skill_name", "source_platform", "standard_job", "standard_city"],
        "rel_related_to_major": ["record_id", "major_name", "major_category", "major_requirement_level"],
        "rel_role_requires_skill": ["role_name", "skill_name", "job_count", "skill_ratio"],
    }

    print(f"输入文件：{input_csv}")
    print(f"输出目录：{output_dir}")
    for name, fieldnames in schemas.items():
        count = write_csv(output_dir / f"{name}.csv", fieldnames, graph_rows[name])
        print(f"{name}.csv: {count}")


if __name__ == "__main__":
    main()
