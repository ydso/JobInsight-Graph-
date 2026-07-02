from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


BASE = Path(__file__).resolve().parents[1]

SOURCES = {
    "前程无忧": BASE / "前程无忧" / "爬取数据",
    "国家大学生就业服务平台": BASE / "国家大学生就业服务平台" / "国家大学生就业服务平台_爬取数据_独立版",
}

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

CITIES = ["重庆", "北京", "上海", "广州", "深圳", "杭州", "南京", "武汉", "成都", "西安"]


def norm_role(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def read_combo_files(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    combos: list[dict[str, object]] = []
    for file in sorted(root.rglob("*_jobs.csv")):
        try:
            df = pd.read_csv(file, encoding="utf-8-sig", dtype=str)
        except Exception as exc:  # pragma: no cover - reporting helper
            print(f"读取失败: {file} | {exc}")
            continue

        match = re.match(r"(.+?)_(.+?)_\d{8}_\d{6}.*_jobs$", file.stem)
        job_from_file = match.group(1) if match else ""
        city_from_file = match.group(2) if match else ""

        if "搜索岗位" not in df.columns:
            df["搜索岗位"] = job_from_file
        if "搜索城市" not in df.columns:
            df["搜索城市"] = city_from_file

        combo_job = norm_role(job_from_file or (df["搜索岗位"].iloc[0] if len(df) else ""))
        combo_city = city_from_file or (df["搜索城市"].iloc[0] if len(df) else "")
        combos.append(
            {
                "标准岗位": combo_job,
                "城市": combo_city,
                "行数": int(len(df)),
                "文件": str(file),
            }
        )

        if len(df):
            df["_文件"] = str(file)
            frames.append(df)

    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return data, pd.DataFrame(combos)


def split_tokens(series: pd.Series, limit: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in series.dropna().astype(str):
        for token in re.split(r"[|,，、;；\s]+", value):
            token = token.strip()
            if not token or token.lower() in {"nan", "未说明", "not_specified"}:
                continue
            counts[token] = counts.get(token, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def top_values(df: pd.DataFrame, column: str, limit: int = 4) -> str:
    if column not in df.columns or df.empty:
        return "样本不足"
    values = df[column].fillna("").replace("", "未说明").value_counts().head(limit)
    return "、".join(f"{idx}({int(count)})" for idx, count in values.items())


def format_money(value: object) -> str:
    if pd.isna(value):
        return "样本不足"
    return f"{float(value):,.0f}元/月"


def format_tokens(items: list[tuple[str, int]], limit: int = 8) -> str:
    if not items:
        return "样本不足"
    return "、".join(token for token, _ in items[:limit])


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    headers = [str(column) for column in df.columns]
    rows = [[str(value) if not pd.isna(value) else "" for value in row] for row in df.to_numpy()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        escaped = [cell.replace("\n", "<br>").replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines)


def main() -> None:
    data_frames: list[pd.DataFrame] = []
    combo_frames: list[pd.DataFrame] = []

    for source, root in SOURCES.items():
        data, combos = read_combo_files(root)
        combos["平台"] = source
        combo_frames.append(combos)

        if not data.empty:
            data["平台"] = source
            data["标准岗位"] = data["搜索岗位"].map(norm_role)
            data["搜索城市"] = data["搜索城市"].fillna("")
            for column in ["最低月薪", "最高月薪", "年薪月数", "最低经验年限", "最高经验年限"]:
                if column in data.columns:
                    data[column] = pd.to_numeric(data[column], errors="coerce")
            if {"最低月薪", "最高月薪"}.issubset(data.columns):
                data["月薪中位估算"] = data[["最低月薪", "最高月薪"]].mean(axis=1, skipna=True)
            data_frames.append(data)

    all_data = pd.concat(data_frames, ignore_index=True)
    combos_all = pd.concat(combo_frames, ignore_index=True)

    coverage_rows: list[dict[str, object]] = []
    for source in SOURCES:
        source_combos = combos_all[combos_all["平台"] == source]
        combo_map = {
            (row.标准岗位, row.城市): int(row.行数)
            for row in source_combos.itertuples(index=False)
        }
        for role in ROLES:
            for city in CITIES:
                rows = combo_map.get((role, city))
                if rows is None:
                    status = "缺文件"
                elif rows == 0:
                    status = "0条"
                else:
                    status = "有数据"
                coverage_rows.append({"平台": source, "标准岗位": role, "城市": city, "状态": status, "行数": rows})
    coverage = pd.DataFrame(coverage_rows)

    salary = all_data.dropna(subset=["月薪中位估算"]).copy()
    salary = salary[(salary["月薪中位估算"] > 0) & (salary["月薪中位估算"] < 200000)]
    salary_median = (
        salary.groupby(["标准岗位", "平台"])["月薪中位估算"]
        .median()
        .unstack("平台")
        .reindex(ROLES)
    )
    salary_count = (
        salary.groupby(["标准岗位", "平台"])["月薪中位估算"]
        .count()
        .unstack("平台")
        .reindex(ROLES)
        .fillna(0)
        .astype(int)
    )
    role_counts = (
        all_data.groupby(["标准岗位", "平台"])
        .size()
        .unstack("平台")
        .reindex(ROLES)
        .fillna(0)
        .astype(int)
    )

    platform_combo_summary = (
        combos_all.groupby("平台")
        .agg(文件数=("文件", "count"), 有效记录数=("行数", "sum"), 零记录组合数=("行数", lambda s: int((s == 0).sum())))
        .reset_index()
    )

    role_rows: list[dict[str, object]] = []
    for role in ROLES:
        role_df = all_data[all_data["标准岗位"] == role]
        fw = role_df[role_df["平台"] == "前程无忧"]
        ncss = role_df[role_df["平台"] == "国家大学生就业服务平台"]
        role_rows.append(
            {
                "岗位": role,
                "前程无忧样本": int(role_counts.loc[role].get("前程无忧", 0)) if role in role_counts.index else 0,
                "NCSS样本": int(role_counts.loc[role].get("国家大学生就业服务平台", 0)) if role in role_counts.index else 0,
                "前程无忧薪资中位": format_money(salary_median.loc[role].get("前程无忧") if role in salary_median.index else None),
                "NCSS薪资中位": format_money(salary_median.loc[role].get("国家大学生就业服务平台") if role in salary_median.index else None),
                "核心技能": format_tokens(split_tokens(role_df.get("确认技能候选", pd.Series(dtype=str)), 10)),
                "常见专业": format_tokens(split_tokens(role_df.get("正文确认标准专业", pd.Series(dtype=str)), 6), 6),
            }
        )
    role_table = pd.DataFrame(role_rows)

    # Narrative facts
    total_fw = int(all_data[all_data["平台"] == "前程无忧"].shape[0])
    total_ncss = int(all_data[all_data["平台"] == "国家大学生就业服务平台"].shape[0])
    fw_top_role = role_counts["前程无忧"].idxmax()
    ncss_top_role = role_counts["国家大学生就业服务平台"].idxmax()

    missing_ncss = coverage[(coverage["平台"] == "国家大学生就业服务平台") & (coverage["状态"] != "有数据")]
    ncss_missing_by_role = (
        missing_ncss.groupby("标准岗位")
        .apply(lambda df: "、".join(f"{row.城市}({row.状态})" for row in df.itertuples(index=False)))
        .reindex(ROLES)
        .dropna()
    )

    lines: list[str] = []
    lines.append("# 岗位总结：前程无忧与国家大学生就业服务平台")
    lines.append("")
    lines.append("## 数据口径")
    lines.append("")
    lines.append("- 分析对象：8 个数据与算法相关岗位，覆盖重庆、北京、上海、广州、深圳、杭州、南京、武汉、成都、西安 10 个城市。")
    lines.append(f"- 前程无忧：读取分城市岗位 CSV {int(platform_combo_summary.loc[platform_combo_summary['平台']=='前程无忧','文件数'].iloc[0])} 个，有效岗位记录 {total_fw} 条。")
    lines.append(f"- 国家大学生就业服务平台：读取分城市岗位 CSV {int(platform_combo_summary.loc[platform_combo_summary['平台']=='国家大学生就业服务平台','文件数'].iloc[0])} 个，有效岗位记录 {total_ncss} 条；该平台仍存在部分 0 条或缺文件组合，适合作为校招/实习侧补充样本。")
    lines.append("- 薪资口径：以“最低月薪”和“最高月薪”的均值作为单条岗位的月薪估算，再取岗位维度中位数；缺薪资岗位不参与薪资统计。")
    lines.append("")
    lines.append("## 总体结论")
    lines.append("")
    lines.append(f"1. 市场侧需求主要集中在 `{fw_top_role}`、`数据开发工程师`、`数据分析师` 等岗位；校招平台样本最集中的是 `{ncss_top_role}`，说明 NCSS 对“算法”类宽口径关键词覆盖更强。")
    lines.append("2. 前程无忧样本量更完整，适合作为总体就业市场的主分析来源；国家大学生就业服务平台岗位更偏校招、实习和应届生，适合补充学历、专业、应届岗位画像。")
    lines.append("3. 技能结构呈现明显分层：数据分析强调 SQL、Python、Excel 和 BI 工具；数据开发/数仓强调 SQL、ETL、Hive、Spark、Flink；算法/机器学习强调 Python、机器学习、深度学习、PyTorch/TensorFlow、NLP/CV。")
    lines.append("4. 学历要求以本科为主体，算法、机器学习和部分高端数据开发岗位对硕士更友好；NCSS 中实习/校招岗位薪资缺失较多，薪资结论更应依赖前程无忧。")
    lines.append("5. 城市分布上，北京、上海、深圳、杭州承担更多算法和数据平台岗位；成都、武汉、西安、重庆样本更适合作为区域性补充，岗位数量和薪资水平整体弱于一线及新一线核心城市。")
    lines.append("")
    lines.append("## 岗位对比总表")
    lines.append("")
    lines.append(dataframe_to_markdown(role_table))
    lines.append("")
    lines.append("## 分岗位总结")
    lines.append("")

    for role in ROLES:
        role_df = all_data[all_data["标准岗位"] == role]
        fw = role_df[role_df["平台"] == "前程无忧"]
        ncss = role_df[role_df["平台"] == "国家大学生就业服务平台"]
        fw_count = int(role_counts.loc[role].get("前程无忧", 0))
        ncss_count = int(role_counts.loc[role].get("国家大学生就业服务平台", 0))
        skills = format_tokens(split_tokens(role_df.get("确认技能候选", pd.Series(dtype=str)), 12), 10)
        majors = format_tokens(split_tokens(role_df.get("正文确认标准专业", pd.Series(dtype=str)), 8), 6)
        fw_city = top_values(fw, "搜索城市", 5)
        ncss_city = top_values(ncss, "搜索城市", 5)
        edu_text = f"前程无忧：{top_values(fw, '学历要求', 4)}；NCSS：{top_values(ncss, '学历要求', 4)}"
        exp_text = f"前程无忧：{top_values(fw, '经验要求类型', 4)}；NCSS：{top_values(ncss, '经验要求类型', 4)}"
        fw_salary = format_money(salary_median.loc[role].get("前程无忧") if role in salary_median.index else None)
        ncss_salary = format_money(salary_median.loc[role].get("国家大学生就业服务平台") if role in salary_median.index else None)

        lines.append(f"### {role}")
        lines.append("")
        lines.append(f"- 样本量：前程无忧 {fw_count} 条，NCSS {ncss_count} 条。")
        lines.append(f"- 薪资中位估算：前程无忧 {fw_salary}，NCSS {ncss_salary}。")
        lines.append(f"- 城市集中度：前程无忧 Top 城市为 {fw_city}；NCSS Top 城市为 {ncss_city}。")
        lines.append(f"- 学历画像：{edu_text}。")
        lines.append(f"- 经验画像：{exp_text}。")
        lines.append(f"- 高频技能：{skills}。")
        lines.append(f"- 常见专业背景：{majors}。")

        if role == "数据分析师":
            lines.append("- 总结：该岗位是数据类岗位中最通用的一类，入门门槛相对低，强调业务理解、指标分析、SQL/Python/Excel 和可视化表达，适合本科应届生作为数据方向切入口。")
        elif role == "BI分析师":
            lines.append("- 总结：BI 更偏报表、指标体系和经营分析，前程无忧有一定市场样本，但 NCSS 当前 10 城均无有效数据，说明校招平台对该岗位命名覆盖不足，可与数据分析师合并讨论。")
        elif role == "数据开发工程师":
            lines.append("- 总结：该岗位连接业务数据需求和数据平台建设，要求 SQL、Python/Java、ETL、数仓和调度能力，是数据工程方向的核心岗位。")
        elif role == "大数据开发工程师":
            lines.append("- 总结：该岗位更偏平台和分布式计算，技能关键词集中在 Spark、Hive、Flink、Hadoop 等生态，岗位门槛高于普通数据开发，适合有工程项目经历的学生。")
        elif role == "数据仓库工程师":
            lines.append("- 总结：该岗位强调建模、ETL、指标口径、数据治理和稳定性，业务理解与工程规范同样重要，常与数据开发岗位存在交叉。")
        elif role == "Python开发工程师":
            lines.append("- 总结：岗位分布横跨后端、爬虫、自动化和数据处理，技能要求比纯数据岗更偏编程实现，Python、SQL、Linux、Web 框架是常见组合。")
        elif role == "机器学习工程师":
            lines.append("- 总结：该岗位更强调算法基础、建模能力和深度学习框架，样本量低于算法工程师但要求更聚焦，硕士及项目/竞赛经历优势更明显。")
        elif role == "算法工程师":
            lines.append("- 总结：该岗位是样本量和薪资表现最突出的高技术岗位之一，覆盖推荐、搜索、NLP、CV、机器学习等方向，对数学、计算机基础和工程落地都有较高要求。")
        lines.append("")

    lines.append("## 平台差异与使用建议")
    lines.append("")
    lines.append("- 前程无忧适合承担论文中的“市场招聘需求主体分析”：样本覆盖完整、岗位和城市组合齐全、薪资字段更可用。")
    lines.append("- 国家大学生就业服务平台适合承担“应届生/校招岗位补充分析”：它更能体现实习、校招、学历和专业要求，但当前部分岗位组合缺失或为 0 条。")
    lines.append("- 对毕业设计建模或可视化而言，建议将前程无忧作为主数据源，NCSS 作为辅助数据源；在图表中单独标注平台来源，避免把社会招聘和校招岗位直接混为同一口径。")
    lines.append("- 岗位归并建议：`BI分析师` 可并入 `数据分析师` 大类讨论；`大数据开发工程师`、`数据仓库工程师` 可并入 `数据工程` 大类对比；`机器学习工程师` 可作为 `算法工程师` 的细分方向。")
    lines.append("")
    lines.append("## NCSS 当前无有效数据组合")
    lines.append("")
    if ncss_missing_by_role.empty:
        lines.append("NCSS 当前 80 个组合均已有有效数据。")
    else:
        for role, cities in ncss_missing_by_role.items():
            lines.append(f"- {role}：{cities}")
    lines.append("")
    lines.append("## 附：按平台与岗位的样本量")
    lines.append("")
    count_table = role_counts.reset_index().rename(columns={"标准岗位": "岗位"})
    lines.append(dataframe_to_markdown(count_table))
    lines.append("")
    lines.append("## 附：薪资统计样本数")
    lines.append("")
    salary_count_table = salary_count.reset_index().rename(columns={"标准岗位": "岗位"})
    lines.append(dataframe_to_markdown(salary_count_table))
    lines.append("")

    output_path = BASE / "岗位总结_前程无忧_NCSS_20260624.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")

    stats_path = BASE / ".runtime-temp" / "job_summary_stats.json"
    stats = {
        "platform_combo_summary": platform_combo_summary.to_dict("records"),
        "role_counts": count_table.to_dict("records"),
        "salary_median": salary_median.reset_index().where(pd.notnull(salary_median.reset_index()), None).to_dict("records"),
        "salary_count": salary_count_table.to_dict("records"),
        "ncss_missing_by_role": ncss_missing_by_role.to_dict(),
        "output_path": str(output_path),
    }
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(output_path)
    print(platform_combo_summary.to_string(index=False))
    print(count_table.to_string(index=False))


if __name__ == "__main__":
    main()
