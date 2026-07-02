from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


CSV_PATH = Path(r"C:\Users\杨欣瑞\Downloads\NCSS_岗位分析数据_合并去重_重新编号.csv")
OUT_PATH = Path(__file__).with_name("ncss_merged_dedup_check.json")


def main() -> None:
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype=str)
    source_id_col = "来源岗位ID"
    record_no_col = "序号"

    ids = df[source_id_col].fillna("").astype(str).str.strip()
    non_empty = ids[ids != ""]
    duplicate_mask = non_empty.duplicated(keep=False)
    duplicate_ids = non_empty[duplicate_mask]

    sequence_info = {}
    if record_no_col in df.columns:
        numbers = pd.to_numeric(df[record_no_col], errors="coerce")
        expected = list(range(1, len(df) + 1))
        actual = numbers.fillna(-1).astype(int).tolist()
        bad_positions = [
            {"row_position": i + 2, "expected": expected[i], "actual": actual[i]}
            for i in range(len(actual))
            if actual[i] != expected[i]
        ][:20]
        sequence_info = {
            "continuous_from_1": bool(len(bad_positions) == 0 and numbers.notna().all()),
            "first_number": int(numbers.iloc[0]) if len(numbers) and pd.notna(numbers.iloc[0]) else None,
            "last_number": int(numbers.iloc[-1]) if len(numbers) and pd.notna(numbers.iloc[-1]) else None,
            "bad_position_count": sum(1 for i in range(len(actual)) if actual[i] != expected[i]),
            "bad_position_examples": bad_positions,
        }

    duplicates_detail = []
    if len(duplicate_ids):
        dup_groups = df.loc[ids.isin(set(duplicate_ids)), :].copy()
        for source_id, group in dup_groups.groupby(source_id_col, dropna=False):
            duplicates_detail.append(
                {
                    "source_job_id": str(source_id),
                    "count": int(len(group)),
                    "record_nos": group.get(record_no_col, pd.Series(dtype=str)).astype(str).tolist()[:20],
                    "titles": group.get("岗位名称", pd.Series(dtype=str)).astype(str).dropna().unique().tolist()[:10],
                    "companies": group.get("公司名称", pd.Series(dtype=str)).astype(str).dropna().unique().tolist()[:10],
                }
            )
        duplicates_detail = sorted(duplicates_detail, key=lambda item: (-item["count"], item["source_job_id"]))[:50]

    def top_counts(column: str, limit: int = 20) -> list[dict[str, object]]:
        if column not in df.columns:
            return []
        counts = df[column].fillna("未说明").replace("", "未说明").value_counts().head(limit)
        return [{"name": str(k), "count": int(v)} for k, v in counts.items()]

    by_role_city = []
    if {"搜索岗位", "搜索城市"}.issubset(df.columns):
        grouped = df.groupby(["搜索岗位", "搜索城市"]).size().reset_index(name="count")
        grouped = grouped.sort_values(["搜索岗位", "搜索城市"])
        by_role_city = [
            {"job": str(row["搜索岗位"]), "city": str(row["搜索城市"]), "count": int(row["count"])}
            for _, row in grouped.iterrows()
        ]

    result = {
        "file": str(CSV_PATH),
        "file_size_bytes": CSV_PATH.stat().st_size,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": df.columns.tolist(),
        "source_job_id": {
            "total_rows": int(len(df)),
            "non_empty_count": int(len(non_empty)),
            "empty_count": int((ids == "").sum()),
            "unique_non_empty_count": int(non_empty.nunique()),
            "duplicate_row_count": int(len(duplicate_ids)),
            "duplicate_id_count": int(duplicate_ids.nunique()),
            "duplicates_detail": duplicates_detail,
        },
        "record_no": sequence_info,
        "top_search_jobs": top_counts("搜索岗位"),
        "top_search_cities": top_counts("搜索城市"),
        "top_actual_cities": top_counts("实际城市"),
        "by_role_city": by_role_city,
    }

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_PATH)


if __name__ == "__main__":
    main()
