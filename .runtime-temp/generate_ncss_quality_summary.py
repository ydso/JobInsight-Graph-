from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parents[1]
DATA_ROOT = BASE / "国家大学生就业服务平台" / "国家大学生就业服务平台_爬取数据_独立版"

ROLES = [
    "数据分析师",
    "BI分析师",
    "数据开发工程师",
    "大数据开发工程师",
    "数据仓库工程师",
    "Python开发工程师",
    "机器学习工程师",
    "算法工程师",
]
CITIES = [
    ("重庆", "50"),
    ("北京", "11"),
    ("上海", "31"),
    ("广州", "440100"),
    ("深圳", "440300"),
    ("杭州", "330100"),
    ("南京", "320100"),
    ("武汉", "420100"),
    ("成都", "510100"),
    ("西安", "610100"),
]

FIELDS = [
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

STATUS_LABELS = {
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
TERMINAL_STATUSES = {
    "source_exhausted",
    "max_pages_reached",
    "optional_cap_reached",
    "relevance_exhausted",
    "no_result",
}


def classify_city_sample(count: int) -> dict[str, Any]:
    if count < 30:
        return {"sample_level": "descriptive_only", "sample_label": "样本较少，仅作描述性分析"}
    if count < 50:
        return {"sample_level": "limited", "sample_label": "可以分析，但需说明样本量限制"}
    return {"sample_level": "sufficient", "sample_label": "适合一般城市特征分析"}


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def latest_file(job: str, city: str, suffix: str) -> Path | None:
    folder = DATA_ROOT / job
    files = sorted(folder.glob(f"{job}_{city}_*_{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def parse_log(path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not path or not path.exists():
        return result

    patterns = {
        "valid_count": r"CSV有效数量:\s*(\d+)\s*条",
        "raw_item_count": r"API原始返回数量:\s*(\d+)\s*条",
        "raw_unique_count": r"原始唯一岗位数量:\s*(\d+)\s*条",
        "source_total_count": r"官网报告总数:\s*(\d+)",
        "duplicate_count": r"跳过重复:\s*(\d+)\s*条",
        "irrelevant_title_count": r"标题不相关过滤:\s*(\d+)\s*条",
        "invalid_record_count": r"无效记录过滤:\s*(\d+)\s*条",
        "zero_valid_streak": r"连续零有效页:\s*(\d+)\s*页",
        "parse_error_count": r"解析错误:\s*(\d+)\s*条",
    }
    text = path.read_text(encoding="utf-8", errors="replace")
    status_match = re.search(r"状态:\s*(.*?)\s*\(([^()]+)\)", text)
    if status_match:
        result["status_label"] = status_match.group(1).strip()
        result["status"] = status_match.group(2).strip()
    stop_match = re.search(r"停止原因:\s*(.+)", text)
    if stop_match:
        result["stop_reason"] = stop_match.group(1).strip()
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            result[key] = int(match.group(1))
    last_try = re.search(r"最后尝试页:\s*(\d+)", text)
    if last_try:
        result["last_page_attempted"] = int(last_try.group(1))
    last_success = re.search(r"最后成功页:\s*(\d+)", text)
    if last_success:
        result["last_successful_page"] = int(last_success.group(1))
    return result


def parse_checkpoint(job: str, city: str) -> dict[str, Any]:
    path = DATA_ROOT / job / f"_{job}_{city}_ncss_checkpoint.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"checkpoint_file": str(path)}
    data["checkpoint_file"] = str(path)
    return data


def parse_raw_pages(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    pages = data.get("items") if isinstance(data, dict) else data
    if not isinstance(pages, list):
        return {}
    page_numbers: list[int] = []
    ids: set[str] = set()
    raw_count = 0
    for page in pages:
        if not isinstance(page, dict):
            continue
        try:
            page_numbers.append(int(page.get("page") or 0))
        except Exception:
            pass
        items = page.get("items") or []
        if isinstance(items, list):
            raw_count += len(items)
            for item in items:
                if not isinstance(item, dict):
                    continue
                job_id = str(item.get("source_job_id") or item.get("detail_url") or "").strip()
                if job_id:
                    ids.add(job_id)
    max_page = max(page_numbers) if page_numbers else ""
    return {
        "last_page_attempted": max_page,
        "last_successful_page": max_page,
        "raw_item_count": raw_count,
        "raw_unique_count": len(ids),
    }


def build_city_role_row(job: str, city: str, city_code: str) -> dict[str, Any]:
    csv_file = latest_file(job, city, "ncss_jobs.csv")
    log_file = latest_file(job, city, "ncss_run_log.txt")
    raw_file = latest_file(job, city, "ncss_raw_items.json")
    log_data = parse_log(log_file)
    raw_data = parse_raw_pages(raw_file)
    checkpoint = parse_checkpoint(job, city)

    valid_count = count_csv_rows(csv_file) if csv_file else 0
    status = log_data.get("status")
    if not status:
        status = "source_exhausted" if valid_count > 0 else "no_result"
    checkpoint_file = checkpoint.get("checkpoint_file", "")
    if checkpoint.get("last_error") in STATUS_LABELS:
        status = checkpoint["last_error"]

    passed = status in TERMINAL_STATUSES
    retryable = (not passed) or bool(checkpoint_file)
    sample = classify_city_sample(valid_count)

    last_attempted = (
        log_data.get("last_page_attempted")
        or checkpoint.get("last_page_attempted")
        or raw_data.get("last_page_attempted")
        or ""
    )
    last_success = (
        log_data.get("last_successful_page")
        or checkpoint.get("last_successful_page")
        or raw_data.get("last_successful_page")
        or ""
    )
    if not last_attempted:
        last_attempted = last_success or ""
    if not last_success:
        last_success = last_attempted or ""

    return {
        "record_type": "city_role",
        "job_name": job,
        "keyword": job,
        "city": city,
        "city_code": city_code,
        "status": status,
        "status_label": STATUS_LABELS.get(status, log_data.get("status_label", status)),
        "passed": bool_cn(passed),
        "valid_count": valid_count,
        "raw_item_count": log_data.get("raw_item_count", raw_data.get("raw_item_count", valid_count)),
        "raw_unique_count": log_data.get("raw_unique_count", raw_data.get("raw_unique_count", valid_count)),
        "sample_level": sample["sample_level"],
        "sample_label": sample["sample_label"],
        "source_total_count": log_data.get("source_total_count", ""),
        "stop_reason": log_data.get("stop_reason", checkpoint.get("last_error", "")),
        "last_page_attempted": last_attempted,
        "last_successful_page": last_success,
        "zero_valid_streak": log_data.get("zero_valid_streak", checkpoint.get("zero_valid_streak", 0)),
        "retryable": bool_cn(retryable),
        "retry_count": checkpoint.get("retry_count", 0),
        "checkpoint_file": checkpoint_file if retryable else "",
        "duplicate_count": log_data.get("duplicate_count", checkpoint.get("total_skipped_dup", 0)),
        "irrelevant_title_count": log_data.get("irrelevant_title_count", checkpoint.get("total_filtered_irrelevant", 0)),
        "invalid_record_count": log_data.get("invalid_record_count", checkpoint.get("total_filtered_invalid", 0)),
        "parse_error_count": log_data.get("parse_error_count", checkpoint.get("total_parse_errors", 0)),
    }


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = DATA_ROOT / f"NCSS_爬取质量汇总_{timestamp}.csv"

    rows: list[dict[str, Any]] = []
    city_counts_by_role: dict[str, dict[str, int]] = defaultdict(dict)

    for job in ROLES:
        for city, city_code in CITIES:
            row = build_city_role_row(job, city, city_code)
            rows.append(row)
            city_counts_by_role[job][city] = int(row["valid_count"] or 0)

    target = 500
    for job in ROLES:
        city_counts = city_counts_by_role[job]
        total = sum(city_counts.values())
        target_met = total >= target
        rows.append(
            {
                "record_type": "role_total",
                "job_name": job,
                "keyword": job,
                "status": "role_target_met" if target_met else "role_target_shortfall",
                "status_label": "岗位总体样本已达标" if target_met else "岗位总体样本不足",
                "passed": bool_cn(target_met),
                "valid_count": total,
                "role_target": target,
                "role_target_met": bool_cn(target_met),
                "role_shortfall": max(0, target - total),
                "city_count": sum(1 for value in city_counts.values() if value > 0),
                "city_counts": json.dumps(city_counts, ensure_ascii=False, sort_keys=True),
            }
        )

    fieldnames = [label for _, label in FIELDS]
    key_to_label = dict(FIELDS)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key_to_label[key]: row.get(key, "") for key, _ in FIELDS})

    # Keep only the newly generated quality summary.
    for old in DATA_ROOT.glob("NCSS_爬取质量汇总_*.csv"):
        if old.resolve() != output.resolve():
            old.unlink()

    print(output)
    print(f"rows={len(rows)}")
    print(f"city_role_rows={sum(1 for row in rows if row.get('record_type') == 'city_role')}")
    print(f"role_total_rows={sum(1 for row in rows if row.get('record_type') == 'role_total')}")


if __name__ == "__main__":
    main()
