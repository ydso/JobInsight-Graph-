CREATE DATABASE IF NOT EXISTS job_skill_dw
COMMENT '就业技能需求分析 Hive 数据仓库';

USE job_skill_dw;

CREATE EXTERNAL TABLE IF NOT EXISTS ods_fused_job_raw (
  record_id string,
  source_platform string,
  source_code string,
  source_folder string,
  source_file string,
  source_line_no string,
  search_job string,
  standard_job string,
  search_city string,
  standard_city string,
  source_job_id string,
  source_url string,
  crawl_time string,
  source_job_fingerprint string,
  content_fingerprint string,
  job_title string,
  company_name string,
  actual_city string,
  district string,
  salary_text string,
  salary_min string,
  salary_max string,
  salary_mid string,
  salary_months string,
  education string,
  education_raw string,
  experience string,
  experience_raw string,
  experience_min string,
  experience_max string,
  experience_type string,
  job_type string,
  industry string,
  company_size string,
  company_type string,
  publish_date string,
  publish_date_raw string,
  `经度` string,
  `纬度` string,
  major_list_raw string,
  major_category_raw string,
  major_requirement_level string,
  job_description string,
  requirement_text string,
  responsibility_text string,
  confirmed_skills string,
  skill_list string,
  skill_count string,
  skill_source_field string,
  skill_category_raw string,
  skill_evidence string,
  skill_extract_scope string,
  quality_flag string,
  dedup_key string,
  completeness_score string
)
COMMENT 'ODS 原始融合岗位表，映射 HDFS 原始数据层'
PARTITIONED BY (batch_date string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/01_原始数据层/融合原始岗位表';

CREATE EXTERNAL TABLE IF NOT EXISTS dwd_job_clean_wide (
  record_id string,
  source_platform string,
  standard_job string,
  job_title string,
  company_name string,
  district string,
  salary_min double,
  salary_max double,
  salary_mid double,
  salary_months int,
  annual_salary_estimated double,
  education string,
  experience string,
  experience_min double,
  experience_max double,
  experience_type string,
  job_type string,
  industry string,
  company_size string,
  company_type string,
  publish_date date,
  crawl_time timestamp,
  skill_count int,
  quality_flag string
)
COMMENT 'DWD 岗位清洗宽表'
PARTITIONED BY (batch_date string, standard_city string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/02_清洗标准层/岗位清洗宽表';

CREATE EXTERNAL TABLE IF NOT EXISTS dwd_job_text_detail (
  record_id string,
  source_url string,
  source_file string,
  source_line_no int,
  salary_text string,
  job_description string,
  requirement_text string,
  responsibility_text string,
  skill_evidence string
)
COMMENT 'DWD 岗位文本详情表'
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/02_清洗标准层/岗位文本详情表';

CREATE EXTERNAL TABLE IF NOT EXISTS ads_job_skill_relation (
  record_id string,
  standard_city string,
  skill_name string,
  skill_category string,
  source_platform string,
  publish_date date
)
COMMENT 'ADS 岗位技能关系表'
PARTITIONED BY (batch_date string, standard_job string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/03_分析应用层/岗位技能关系表';

CREATE EXTERNAL TABLE IF NOT EXISTS ads_skill_heat (
  standard_city string,
  skill_name string,
  job_count bigint,
  total_job_count bigint,
  skill_ratio double
)
COMMENT 'ADS 技能热度统计表'
PARTITIONED BY (batch_date string, standard_job string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/03_分析应用层/技能热度统计表';

CREATE EXTERNAL TABLE IF NOT EXISTS ads_job_major_relation (
  record_id string,
  standard_job string,
  standard_city string,
  major_category string,
  major_requirement_level string,
  source_platform string,
  publish_date date
)
COMMENT 'ADS 岗位专业关系表'
PARTITIONED BY (batch_date string, major_name string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/03_分析应用层/岗位专业关系表';

CREATE EXTERNAL TABLE IF NOT EXISTS ads_job_demand_summary (
  standard_city string,
  standard_job string,
  source_platform string,
  job_count bigint
)
COMMENT 'ADS 岗位需求汇总表'
PARTITIONED BY (batch_date string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/03_分析应用层/岗位需求汇总表';

CREATE EXTERNAL TABLE IF NOT EXISTS ads_city_job_salary (
  standard_job string,
  job_count bigint,
  salary_min_avg double,
  salary_max_avg double,
  salary_mid_avg double,
  salary_mid_median double,
  annual_salary_avg double
)
COMMENT 'ADS 城市岗位薪资表'
PARTITIONED BY (batch_date string, standard_city string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/03_分析应用层/城市岗位薪资表';

CREATE EXTERNAL TABLE IF NOT EXISTS ads_education_experience_summary (
  standard_job string,
  standard_city string,
  education string,
  experience string,
  job_count bigint
)
COMMENT 'ADS 学历经验统计表'
PARTITIONED BY (batch_date string)
STORED AS PARQUET
LOCATION '/数据/就业技能需求分析/03_分析应用层/学历经验统计表';

MSCK REPAIR TABLE ods_fused_job_raw;
MSCK REPAIR TABLE dwd_job_clean_wide;
MSCK REPAIR TABLE ads_job_skill_relation;
MSCK REPAIR TABLE ads_skill_heat;
MSCK REPAIR TABLE ads_job_major_relation;
MSCK REPAIR TABLE ads_job_demand_summary;
MSCK REPAIR TABLE ads_city_job_salary;
MSCK REPAIR TABLE ads_education_experience_summary;
