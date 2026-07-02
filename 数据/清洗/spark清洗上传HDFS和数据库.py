# -*- coding: utf-8 -*-
"""
Spark 清洗、HDFS 入湖与 MySQL 落库脚本。

默认输入:
    D:/桌面/毕业设计/数据/爬虫/总爬虫数据分析/分析结果/总爬取数据.csv

默认 HDFS 输出:
    /数据/就业技能需求分析/

默认数据库:
    MySQL job_skill

直接运行:
    python spark_clean_to_hdfs_db.py

运行前只需要确认下方“默认运行配置”中的 DEFAULT_DB_PASSWORD 已改为本机 MySQL 密码。

仅验证 HDFS/本地 Parquet 输出:
    python spark_clean_to_hdfs_db.py --skip-db --hdfs-root file:///D:/桌面/毕业设计/数据/清洗/Spark清洗输出

说明:
    在 Windows 中文路径下，python 启动 PySpark 通常比 spark-submit 更稳定。
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T
from pyspark.sql.window import Window


# 默认运行配置：一般只需要修改 DEFAULT_DB_PASSWORD。
DEFAULT_HDFS_ROOT = "hdfs://localhost:9000/数据/就业技能需求分析"
DEFAULT_JDBC_JAR = r"D:\bigdata\downloads\mysql-connector-j-8.4.0.jar"
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = "3306"
DEFAULT_DB_NAME = "job_skill"
DEFAULT_DB_USER = "root"
DEFAULT_DB_PASSWORD = "123456yXr!"
DEFAULT_JDBC_PARAMS = "useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=Asia/Shanghai&characterEncoding=utf8"


CN_TO_EN = {
    "记录ID": "record_id",
    "来源平台": "source_platform",
    "数据源": "source_code",
    "来源目录": "source_folder",
    "源文件": "source_file",
    "源文件行号": "source_line_no",
    "搜索岗位": "search_job",
    "标准岗位": "standard_job",
    "搜索城市": "search_city",
    "标准城市": "standard_city",
    "来源岗位ID": "source_job_id",
    "来源链接": "source_url",
    "采集时间": "crawl_time",
    "来源岗位指纹": "source_job_fingerprint",
    "内容指纹": "content_fingerprint",
    "岗位名称": "job_title",
    "公司名称": "company_name",
    "实际城市": "actual_city",
    "区县": "district",
    "薪资原文": "salary_text",
    "最低月薪": "salary_min",
    "最高月薪": "salary_max",
    "薪资中位数": "salary_mid",
    "年薪月数": "salary_months",
    "学历要求": "education",
    "学历原文": "education_raw",
    "经验要求": "experience",
    "经验原文": "experience_raw",
    "最低经验年限": "experience_min",
    "最高经验年限": "experience_max",
    "经验要求类型": "experience_type",
    "岗位类型": "job_type",
    "行业": "industry",
    "公司规模": "company_size",
    "公司性质": "company_type",
    "发布日期": "publish_date",
    "发布日期原文": "publish_date_raw",
    "正文确认标准专业": "major_list_raw",
    "专业类别": "major_category_raw",
    "专业要求级别": "major_requirement_level",
    "岗位描述清洗文本": "job_description",
    "任职要求文本": "requirement_text",
    "岗位职责文本": "responsibility_text",
    "确认技能候选": "confirmed_skills",
    "技能列表": "skill_list",
    "技能数量": "skill_count",
    "技能来源字段": "skill_source_field",
    "技能类别": "skill_category_raw",
    "技能证据": "skill_evidence",
    "技能提取范围": "skill_extract_scope",
    "数据质量标记": "quality_flag",
    "去重键": "dedup_key",
    "完整度评分": "completeness_score",
}

SOURCE_PRIORITY = {
    "国家大学生就业服务平台": 3,
    "前程无忧": 2,
    "智联招聘": 1,
}

DEFAULT_JDBC_JAR_CANDIDATES = [
    DEFAULT_JDBC_JAR,
    r"D:\Bank-Management-System\BM_System\libs\mysql-connector-java-8.0.22.jar",
]

MYSQL_TABLE_COMMENTS = {
    "job_posting_clean": "岗位清洗宽表：一条记录代表一个清洗后的招聘岗位，用于保存标准岗位、城市、学历、经验、薪资年薪等核心字段",
    "job_posting_detail": "岗位文本详情表：保存岗位描述、任职要求、岗位职责、技能证据等长文本字段，用于后续文本分析",
    "job_skill_relation": "岗位技能关系表：保存岗位与技能的明细关系，一条记录代表一个岗位命中的一个技能",
    "skill_frequency": "技能热度统计表：按岗位和技能统计出现次数、覆盖岗位数等指标，用于分析热门技能需求",
    "job_major_relation": "岗位专业关系表：保存岗位与相关专业的匹配关系，用于分析不同专业对应的就业岗位方向",
    "job_demand_summary": "岗位需求汇总表：按标准岗位汇总招聘数量、薪资水平、城市覆盖等指标，用于岗位需求总览",
    "city_job_salary": "城市岗位薪资表：按城市和岗位汇总招聘数量及年薪统计，用于分析城市间岗位薪资差异",
    "education_experience_summary": "学历经验统计表：按学历和经验要求汇总岗位数量及薪资，用于分析招聘门槛和薪资关系",
}


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_input = script_dir.parent / "爬虫" / "总爬虫数据分析" / "分析结果" / "总爬取数据.csv"

    parser = argparse.ArgumentParser(description="Spark 清洗招聘岗位数据，写入 HDFS 并落库 MySQL")
    parser.add_argument("--input-csv", type=Path, default=default_input, help="统一爬取数据 CSV 路径")
    parser.add_argument("--input-encoding", default=os.getenv("JOB_SKILL_INPUT_ENCODING", "GB18030"), help="输入 CSV 编码，当前总爬取数据.csv 默认为 GB18030")
    parser.add_argument("--hdfs-root", default=os.getenv("JOB_SKILL_HDFS_ROOT", DEFAULT_HDFS_ROOT), help="HDFS 或 file:// 输出根目录")
    parser.add_argument("--batch-date", default=None, help="批次日期，默认从当前日期生成 yyyy-MM-dd")
    parser.add_argument("--jdbc-jar", default=os.getenv("MYSQL_JDBC_JAR", DEFAULT_JDBC_JAR), help="MySQL JDBC 驱动 jar 路径")
    parser.add_argument("--db-host", default=os.getenv("JOB_SKILL_DB_HOST", DEFAULT_DB_HOST), help="MySQL 主机")
    parser.add_argument("--db-port", default=os.getenv("JOB_SKILL_DB_PORT", DEFAULT_DB_PORT), help="MySQL 端口")
    parser.add_argument("--db-name", default=os.getenv("JOB_SKILL_DB_NAME", DEFAULT_DB_NAME), help="数据库名")
    parser.add_argument("--db-user", default=os.getenv("JOB_SKILL_DB_USER", DEFAULT_DB_USER), help="数据库用户名")
    parser.add_argument("--db-password", default=os.getenv("JOB_SKILL_DB_PASSWORD", DEFAULT_DB_PASSWORD), help="数据库密码")
    parser.add_argument("--jdbc-params", default=os.getenv("JOB_SKILL_JDBC_PARAMS", DEFAULT_JDBC_PARAMS), help="JDBC URL 参数")
    parser.add_argument("--write-mode", choices=["overwrite", "append"], default="overwrite", help="数据库写入模式")
    parser.add_argument("--skip-hdfs", action="store_true", help="跳过 HDFS/Parquet 输出")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库创建和落库")
    return parser.parse_args()


def detect_jdbc_jar(explicit_path: str) -> str:
    if explicit_path and Path(explicit_path).exists():
        return str(Path(explicit_path).resolve())
    for candidate in DEFAULT_JDBC_JAR_CANDIDATES:
        if Path(candidate).exists():
            return str(Path(candidate).resolve())
    return explicit_path


def local_file_uri(path: Path) -> str:
    resolved = path.resolve()
    # Spark on Windows handles raw UTF-8 file URIs with Chinese characters better
    # than percent-encoded URIs produced by Path.as_uri().
    return "file:///" + resolved.as_posix()


def prepare_spark_input_csv(input_csv: Path, input_encoding: str) -> tuple[Path, str]:
    if not input_csv.exists():
        fallback = input_csv.parent / "unified_jobs_clean.csv"
        if fallback.exists():
            input_csv = fallback
        else:
            raise FileNotFoundError(f"输入文件不存在：{input_csv}")

    normalized_encoding = input_encoding.replace("-", "").lower()
    if normalized_encoding in {"utf8", "utf8sig"}:
        return input_csv, "UTF-8"

    temp_dir = Path(__file__).resolve().parent / "_spark_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_csv = temp_dir / "spark_input_utf8.csv"
    with input_csv.open("r", encoding=input_encoding, newline="") as src, temp_csv.open("w", encoding="utf-8", newline="") as dst:
        shutil.copyfileobj(src, dst)
    return temp_csv, "UTF-8"


def build_spark(jdbc_jar: str) -> SparkSession:
    builder = (
        SparkSession.builder
        .appName("job-skill-clean-to-hdfs-db")
        .config("spark.sql.session.timeZone", "Asia/Shanghai")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .config("spark.sql.codegen.wholeStage", "false")
        .config("spark.sql.shuffle.partitions", "16")
    )
    if jdbc_jar:
        builder = (
            builder
            .config("spark.jars", jdbc_jar)
            .config("spark.driver.extraClassPath", jdbc_jar)
            .config("spark.executor.extraClassPath", jdbc_jar)
        )
    return builder.getOrCreate()


def c(name: str) -> F.Column:
    return F.col(name)


def clean_text_col(name: str) -> F.Column:
    return F.trim(F.regexp_replace(F.coalesce(c(name).cast("string"), F.lit("")), r"\s+", " "))


def to_double_col(name: str) -> F.Column:
    cleaned = F.regexp_replace(clean_text_col(name), ",", "")
    return F.when(cleaned == "", F.lit(None).cast("double")).otherwise(cleaned.cast("double"))


def to_int_col(name: str) -> F.Column:
    cleaned = F.regexp_replace(clean_text_col(name), ",", "")
    return F.when(cleaned == "", F.lit(None).cast("int")).otherwise(cleaned.cast("int"))


def normalize_columns(df: DataFrame) -> DataFrame:
    cleaned_columns = [column.replace("\ufeff", "").strip() for column in df.columns]
    df = df.toDF(*cleaned_columns)
    for cn_name, en_name in CN_TO_EN.items():
        if cn_name in df.columns:
            df = df.withColumnRenamed(cn_name, en_name)
    return df


def ensure_columns(df: DataFrame, columns: list[str]) -> DataFrame:
    for column in columns:
        if column not in df.columns:
            df = df.withColumn(column, F.lit(""))
    return df


def standardize_education(column: F.Column) -> F.Column:
    text = F.lower(F.coalesce(column, F.lit("")))
    return (
        F.when((text == "") | text.contains("不限") | text.contains("无要求"), F.lit("不限"))
        .when(text.contains("博士"), F.lit("博士"))
        .when(text.contains("硕士") | text.contains("研究生"), F.lit("硕士"))
        .when(text.contains("本科"), F.lit("本科"))
        .when(text.contains("大专") | text.contains("专科"), F.lit("大专"))
        .when(text.contains("中专") | text.contains("高中"), F.lit("中专/高中"))
        .otherwise(column)
    )


def standardize_experience_text(column: F.Column) -> F.Column:
    text = F.coalesce(column, F.lit(""))
    return (
        F.when(text == "", F.lit("未知"))
        .when(text.contains("不限") | text.contains("经验不限"), F.lit("不限"))
        .when(text.contains("应届") | text.contains("在校"), F.lit("应届"))
        .otherwise(text)
    )


def standardize_experience_type(column: F.Column, experience: F.Column) -> F.Column:
    text = F.coalesce(column, F.lit(""))
    exp = F.coalesce(experience, F.lit(""))
    return (
        F.when(exp == "不限", F.lit("不限"))
        .when(exp == "应届", F.lit("应届"))
        .when(text != "", text)
        .when(exp == "未知", F.lit("未知"))
        .otherwise(F.lit("区间"))
    )


@F.udf(returnType=T.StringType())
def pick_skill_category(raw, skill):
    if not raw or not skill:
        return ""
    target = skill.strip().lower()
    for item in raw.split("||"):
        if "=>" not in item:
            continue
        name, category = item.split("=>", 1)
        if name.strip().lower() == target:
            return category.strip()
    return ""


@F.udf(returnType=T.StringType())
def pick_major_category(raw, major):
    if not raw or not major:
        return ""
    target = major.strip().lower()
    for item in raw.split("||"):
        if "=>" not in item:
            continue
        name, category = item.split("=>", 1)
        if name.strip().lower() == target:
            return category.strip()
    return ""


def read_input(spark: SparkSession, input_csv: Path, batch_date: str, input_encoding: str) -> DataFrame:
    input_csv, spark_encoding = prepare_spark_input_csv(input_csv, input_encoding)
    input_uri = local_file_uri(input_csv)
    df = (
        spark.read
        .option("header", "true")
        .option("multiLine", "true")
        .option("escape", '"')
        .option("quote", '"')
        .option("encoding", spark_encoding)
        .csv(input_uri)
    )
    df = normalize_columns(df)
    df = ensure_columns(df, list(CN_TO_EN.values()))
    return df.withColumn("batch_date", F.lit(batch_date).cast("date"))


def build_bronze_df(raw_df: DataFrame) -> DataFrame:
    # Bronze 层保留已映射字段，后续可追溯来源文件、行号、指纹等字段。
    return raw_df


def build_clean_df(raw_df: DataFrame) -> DataFrame:
    df = raw_df
    for column in df.columns:
        if dict(df.dtypes).get(column) == "string":
            df = df.withColumn(column, clean_text_col(column))

    salary_min_raw = to_double_col("salary_min")
    salary_max_raw = to_double_col("salary_max")
    salary_mid_raw = to_double_col("salary_mid")
    salary_months_raw = to_int_col("salary_months")

    salary_min_valid = F.when((salary_min_raw >= 1000) & (salary_min_raw <= 200000), salary_min_raw)
    salary_max_valid = F.when((salary_max_raw >= 1000) & (salary_max_raw <= 200000), salary_max_raw)

    salary_min_final = (
        F.when(salary_min_valid.isNotNull() & salary_max_valid.isNotNull(), F.least(salary_min_valid, salary_max_valid))
        .otherwise(salary_min_valid)
    )
    salary_max_final = (
        F.when(salary_min_valid.isNotNull() & salary_max_valid.isNotNull(), F.greatest(salary_min_valid, salary_max_valid))
        .otherwise(salary_max_valid)
    )
    salary_mid_final = (
        F.when(salary_min_final.isNotNull() & salary_max_final.isNotNull(), (salary_min_final + salary_max_final) / F.lit(2.0))
        .when(salary_min_final.isNotNull(), salary_min_final)
        .when(salary_max_final.isNotNull(), salary_max_final)
        .when((salary_mid_raw >= 1000) & (salary_mid_raw <= 200000), salary_mid_raw)
        .otherwise(F.lit(None).cast("double"))
    )

    salary_months_final = (
        F.when((salary_months_raw >= 1) & (salary_months_raw <= 24), salary_months_raw)
        .otherwise(F.lit(12))
    )

    source_priority_expr = F.create_map(
        *[item for kv in SOURCE_PRIORITY.items() for item in (F.lit(kv[0]), F.lit(kv[1]))]
    )

    cleaned = (
        df
        .withColumn("record_id", F.when(c("record_id") != "", c("record_id")).otherwise(F.sha2(F.concat_ws("||", c("source_platform"), c("source_job_id"), c("source_url"), c("dedup_key")), 256)))
        .withColumn("source_platform", F.when(c("source_platform") == "", F.lit("未知来源")).otherwise(c("source_platform")))
        .withColumn("standard_job", F.when(c("standard_job") != "", c("standard_job")).otherwise(c("search_job")))
        .withColumn("standard_city", F.regexp_replace(F.when(c("standard_city") != "", c("standard_city")).otherwise(c("search_city")), r"[市区县]+$", ""))
        .withColumn("district", F.when(c("district") == "", F.lit("未知")).otherwise(c("district")))
        .withColumn("salary_text", F.when(c("salary_text") == "", F.lit("面议")).otherwise(c("salary_text")))
        .withColumn("salary_min", salary_min_final.cast("double"))
        .withColumn("salary_max", salary_max_final.cast("double"))
        .withColumn("salary_mid", salary_mid_final.cast("double"))
        .withColumn("salary_months", salary_months_final.cast("int"))
        .withColumn("annual_salary_estimated", F.when(c("salary_mid").isNotNull(), c("salary_mid") * c("salary_months")).cast("double"))
        .withColumn("education", standardize_education(c("education")))
        .withColumn("experience", standardize_experience_text(c("experience")))
        .withColumn("experience_min", to_double_col("experience_min"))
        .withColumn("experience_max", to_double_col("experience_max"))
        .withColumn("experience_type", standardize_experience_type(c("experience_type"), c("experience")))
        .withColumn("job_type", F.when(c("job_type") == "", F.lit("未知")).otherwise(c("job_type")))
        .withColumn("industry", F.when(c("industry") == "", F.lit("未知")).otherwise(c("industry")))
        .withColumn("company_size", F.when(c("company_size") == "", F.lit("未知")).otherwise(c("company_size")))
        .withColumn("company_type", F.when(c("company_type") == "", F.lit("未知")).otherwise(c("company_type")))
        .withColumn("publish_date", F.expr("try_cast(regexp_replace(publish_date, '/', '-') as date)"))
        .withColumn("crawl_time", F.expr("try_cast(crawl_time as timestamp)"))
        .withColumn("skill_count", to_int_col("skill_count"))
        .withColumn("quality_flag", F.when(c("quality_flag") == "", F.lit("normal")).otherwise(c("quality_flag")))
        .withColumn("source_line_no", to_int_col("source_line_no"))
        .withColumn("completeness_score", to_int_col("completeness_score"))
        .withColumn("source_priority", F.coalesce(source_priority_expr[c("source_platform")], F.lit(0)))
        .filter((c("job_title") != "") & (c("company_name") != "") & (c("standard_city") != ""))
    )

    window = Window.partitionBy("dedup_key").orderBy(
        F.desc_nulls_last("completeness_score"),
        F.desc("source_priority"),
        F.desc_nulls_last("crawl_time"),
    )

    clean_columns = [
        "record_id",
        "source_platform",
        "standard_job",
        "job_title",
        "company_name",
        "standard_city",
        "district",
        "salary_min",
        "salary_max",
        "salary_mid",
        "salary_months",
        "annual_salary_estimated",
        "education",
        "experience",
        "experience_min",
        "experience_max",
        "experience_type",
        "job_type",
        "industry",
        "company_size",
        "company_type",
        "publish_date",
        "crawl_time",
        "skill_list",
        "skill_count",
        "quality_flag",
        "batch_date",
        "source_url",
        "source_file",
        "source_line_no",
        "salary_text",
        "job_description",
        "requirement_text",
        "responsibility_text",
        "skill_evidence",
        "skill_category_raw",
        "major_list_raw",
        "major_category_raw",
        "major_requirement_level",
        "dedup_key",
        "content_fingerprint",
    ]

    return (
        cleaned
        .withColumn("rn", F.row_number().over(window))
        .filter(c("rn") == 1)
        .select(*clean_columns)
    )


def build_job_posting_clean(clean_df: DataFrame) -> DataFrame:
    return clean_df.select(
        "record_id",
        "source_platform",
        "standard_job",
        "job_title",
        "company_name",
        "standard_city",
        "district",
        "salary_min",
        "salary_max",
        "salary_mid",
        "salary_months",
        "annual_salary_estimated",
        "education",
        "experience",
        "experience_min",
        "experience_max",
        "experience_type",
        "job_type",
        "industry",
        "company_size",
        "company_type",
        "publish_date",
        "crawl_time",
        "skill_count",
        "quality_flag",
        "batch_date",
    )


def build_job_text_detail(clean_df: DataFrame) -> DataFrame:
    return clean_df.select(
        "record_id",
        "source_url",
        "source_file",
        "source_line_no",
        "salary_text",
        "job_description",
        "requirement_text",
        "responsibility_text",
        "skill_evidence",
    )


def build_skill_relation(clean_df: DataFrame) -> DataFrame:
    exploded = (
        clean_df
        .withColumn("skill_name", F.explode(F.split(F.coalesce(c("skill_list"), F.lit("")), r"\|")))
        .withColumn("skill_name", F.trim(c("skill_name")))
        .filter(c("skill_name") != "")
        .dropDuplicates(["record_id", "skill_name"])
        .withColumn("skill_category", c("skill_category_raw"))
    )
    return exploded.select(
        "record_id",
        "standard_job",
        "standard_city",
        "skill_name",
        "skill_category",
        "source_platform",
        "publish_date",
        "batch_date",
    )


def build_major_relation(clean_df: DataFrame) -> DataFrame:
    exploded = (
        clean_df
        .withColumn("major_name", F.explode(F.split(F.coalesce(c("major_list_raw"), F.lit("")), r"\|")))
        .withColumn("major_name", F.trim(c("major_name")))
        .filter(c("major_name") != "")
        .dropDuplicates(["record_id", "major_name"])
        .withColumn("major_category", c("major_category_raw"))
    )
    return exploded.select(
        "record_id",
        "standard_job",
        "standard_city",
        "major_name",
        "major_category",
        "major_requirement_level",
        "source_platform",
        "publish_date",
        "batch_date",
    )


def build_skill_frequency(clean_df: DataFrame, skill_relation: DataFrame) -> DataFrame:
    total = clean_df.groupBy("standard_job", "standard_city", "batch_date").agg(F.countDistinct("record_id").alias("total_job_count"))
    freq = skill_relation.groupBy("standard_job", "standard_city", "skill_name", "batch_date").agg(F.countDistinct("record_id").alias("job_count"))
    return (
        freq
        .join(total, ["standard_job", "standard_city", "batch_date"], "left")
        .withColumn("skill_ratio", F.round(c("job_count") / c("total_job_count"), 6))
        .select("standard_job", "standard_city", "skill_name", "job_count", "total_job_count", "skill_ratio", "batch_date")
    )


def build_city_job_salary(clean_df: DataFrame) -> DataFrame:
    return clean_df.groupBy("standard_city", "standard_job", "batch_date").agg(
        F.countDistinct("record_id").alias("job_count"),
        F.round(F.avg("salary_min"), 2).alias("salary_min_avg"),
        F.round(F.avg("salary_max"), 2).alias("salary_max_avg"),
        F.round(F.avg("salary_mid"), 2).alias("salary_mid_avg"),
        F.round(F.expr("percentile_approx(salary_mid, 0.5)"), 2).alias("salary_mid_median"),
        F.round(F.avg("annual_salary_estimated"), 2).alias("annual_salary_avg"),
    )


def build_job_demand_summary(clean_df: DataFrame) -> DataFrame:
    return clean_df.groupBy("standard_city", "standard_job", "source_platform", "batch_date").agg(
        F.countDistinct("record_id").alias("job_count")
    )


def build_education_experience_summary(clean_df: DataFrame) -> DataFrame:
    return clean_df.groupBy("standard_job", "standard_city", "education", "experience", "batch_date").agg(
        F.countDistinct("record_id").alias("job_count")
    )


def hdfs_path(root: str, *parts: str) -> str:
    return "/".join([root.rstrip("/"), *[part.strip("/") for part in parts]])


def write_parquet_outputs(root: str, tables: dict[str, DataFrame]) -> None:
    tables["bronze_unified_jobs_raw"].write.mode("overwrite").partitionBy("batch_date").parquet(
        hdfs_path(root, "01_原始数据层", "融合原始岗位表")
    )
    tables["job_posting_clean"].write.mode("overwrite").partitionBy("batch_date", "standard_city").parquet(
        hdfs_path(root, "02_清洗标准层", "岗位清洗宽表")
    )
    tables["job_text_detail"].write.mode("overwrite").parquet(
        hdfs_path(root, "02_清洗标准层", "岗位文本详情表")
    )
    tables["job_skill_relation"].write.mode("overwrite").partitionBy("batch_date", "standard_job").parquet(
        hdfs_path(root, "03_分析应用层", "岗位技能关系表")
    )
    tables["skill_frequency"].write.mode("overwrite").partitionBy("batch_date", "standard_job").parquet(
        hdfs_path(root, "03_分析应用层", "技能热度统计表")
    )
    tables["job_major_relation"].write.mode("overwrite").partitionBy("batch_date", "major_name").parquet(
        hdfs_path(root, "03_分析应用层", "岗位专业关系表")
    )
    tables["job_demand_summary"].write.mode("overwrite").partitionBy("batch_date").parquet(
        hdfs_path(root, "03_分析应用层", "岗位需求汇总表")
    )
    tables["city_job_salary"].write.mode("overwrite").partitionBy("batch_date", "standard_city").parquet(
        hdfs_path(root, "03_分析应用层", "城市岗位薪资表")
    )
    tables["education_experience_summary"].write.mode("overwrite").partitionBy("batch_date").parquet(
        hdfs_path(root, "03_分析应用层", "学历经验统计表")
    )


def mysql_url(host: str, port: str, db_name: str | None, params: str) -> str:
    database_part = f"/{db_name}" if db_name else "/"
    separator = "&" if "?" in database_part else "?"
    return f"jdbc:mysql://{host}:{port}{database_part}{separator}{params}"


def create_mysql_database_if_needed(spark: SparkSession, args: argparse.Namespace) -> None:
    spark.sparkContext._jvm.java.lang.Class.forName("com.mysql.cj.jdbc.Driver")
    url = mysql_url(args.db_host, args.db_port, None, args.jdbc_params)
    props = spark.sparkContext._jvm.java.util.Properties()
    props.setProperty("user", args.db_user)
    props.setProperty("password", args.db_password)
    conn = spark.sparkContext._jvm.java.sql.DriverManager.getConnection(url, props)
    try:
        stmt = conn.createStatement()
        stmt.execute(
            f"CREATE DATABASE IF NOT EXISTS `{args.db_name}` "
            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        stmt.close()
    finally:
        conn.close()


def mysql_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def mysql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def apply_mysql_table_comments(spark: SparkSession, args: argparse.Namespace) -> None:
    spark.sparkContext._jvm.java.lang.Class.forName("com.mysql.cj.jdbc.Driver")
    url = mysql_url(args.db_host, args.db_port, args.db_name, args.jdbc_params)
    props = spark.sparkContext._jvm.java.util.Properties()
    props.setProperty("user", args.db_user)
    props.setProperty("password", args.db_password)
    conn = spark.sparkContext._jvm.java.sql.DriverManager.getConnection(url, props)
    try:
        stmt = conn.createStatement()
        try:
            for table_name, comment in MYSQL_TABLE_COMMENTS.items():
                stmt.execute(f"ALTER TABLE {mysql_identifier(table_name)} COMMENT = {mysql_string(comment)}")
        finally:
            stmt.close()
    finally:
        conn.close()


def write_jdbc_table(df: DataFrame, url: str, table_name: str, args: argparse.Namespace, column_types: str | None = None) -> None:
    writer = (
        df.coalesce(4)
        .write
        .format("jdbc")
        .option("url", url)
        .option("dbtable", table_name)
        .option("user", args.db_user)
        .option("password", args.db_password)
        .option("driver", "com.mysql.cj.jdbc.Driver")
        .option("batchsize", "1000")
        .mode(args.write_mode)
    )
    if column_types:
        writer = writer.option("createTableColumnTypes", column_types)
    writer.save()


def write_mysql_outputs(spark: SparkSession, tables: dict[str, DataFrame], args: argparse.Namespace) -> None:
    create_mysql_database_if_needed(spark, args)
    url = mysql_url(args.db_host, args.db_port, args.db_name, args.jdbc_params)
    write_jdbc_table(tables["job_posting_clean"], url, "job_posting_clean", args)
    write_jdbc_table(
        tables["job_text_detail"],
        url,
        "job_posting_detail",
        args,
    )
    write_jdbc_table(tables["job_skill_relation"], url, "job_skill_relation", args)
    write_jdbc_table(tables["skill_frequency"], url, "skill_frequency", args)
    write_jdbc_table(tables["job_major_relation"], url, "job_major_relation", args)
    write_jdbc_table(tables["job_demand_summary"], url, "job_demand_summary", args)
    write_jdbc_table(tables["city_job_salary"], url, "city_job_salary", args)
    write_jdbc_table(tables["education_experience_summary"], url, "education_experience_summary", args)
    apply_mysql_table_comments(spark, args)


def collect_summary(tables: dict[str, DataFrame]) -> dict[str, int]:
    return {
        "bronze_unified_jobs_raw": tables["bronze_unified_jobs_raw"].count(),
        "job_posting_clean": tables["job_posting_clean"].count(),
        "job_text_detail": tables["job_text_detail"].count(),
        "job_skill_relation": tables["job_skill_relation"].count(),
        "skill_frequency": tables["skill_frequency"].count(),
        "job_major_relation": tables["job_major_relation"].count(),
        "job_demand_summary": tables["job_demand_summary"].count(),
        "city_job_salary": tables["city_job_salary"].count(),
        "education_experience_summary": tables["education_experience_summary"].count(),
    }


def main() -> None:
    args = parse_args()
    jdbc_jar = "" if args.skip_db else detect_jdbc_jar(args.jdbc_jar)
    if not args.skip_db and not jdbc_jar:
        raise FileNotFoundError("未找到 MySQL JDBC 驱动 jar。请使用 --jdbc-jar 指定 mysql-connector-j-*.jar，或设置 MYSQL_JDBC_JAR。")
    if not args.skip_db and args.db_password == "你的密码":
        raise ValueError("请先在脚本顶部将 DEFAULT_DB_PASSWORD 改为本机 MySQL 密码，或使用 --db-password/环境变量 JOB_SKILL_DB_PASSWORD 覆盖。")

    spark = build_spark(jdbc_jar)
    batch_date = args.batch_date or spark.sql("select current_date() as d").first()["d"].isoformat()

    raw_df = read_input(spark, args.input_csv, batch_date, args.input_encoding).cache()
    bronze_df = build_bronze_df(raw_df)
    clean_df = build_clean_df(raw_df).cache()

    job_posting_clean = build_job_posting_clean(clean_df).cache()
    job_text_detail = build_job_text_detail(clean_df).cache()
    job_skill_relation = build_skill_relation(clean_df).cache()
    job_major_relation = build_major_relation(clean_df).cache()
    skill_frequency = build_skill_frequency(clean_df, job_skill_relation).cache()
    city_job_salary = build_city_job_salary(clean_df).cache()
    job_demand_summary = build_job_demand_summary(clean_df).cache()
    education_experience_summary = build_education_experience_summary(clean_df).cache()

    tables = {
        "bronze_unified_jobs_raw": bronze_df,
        "job_posting_clean": job_posting_clean,
        "job_text_detail": job_text_detail,
        "job_skill_relation": job_skill_relation,
        "skill_frequency": skill_frequency,
        "job_major_relation": job_major_relation,
        "job_demand_summary": job_demand_summary,
        "city_job_salary": city_job_salary,
        "education_experience_summary": education_experience_summary,
    }

    summary = collect_summary(tables)
    print("清洗后表行数：")
    for table_name, row_count in summary.items():
        print(f"  {table_name}: {row_count}")

    if not args.skip_hdfs:
        write_parquet_outputs(args.hdfs_root, tables)
        print(f"已写入 HDFS/文件系统根目录：{args.hdfs_root}")

    if not args.skip_db:
        write_mysql_outputs(spark, tables, args)
        print(f"已写入 MySQL 数据库：{args.db_name}")

    spark.stop()


if __name__ == "__main__":
    main()
