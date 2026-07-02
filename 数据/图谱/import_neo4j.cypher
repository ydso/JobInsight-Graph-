// Neo4j 知识图谱导入脚本
// 使用方式：先把 export_graph_csv.py 生成的 CSV 放入 Neo4j 当前 DBMS 的 import 目录，
// 然后在 Neo4j Desktop 左侧 Query 中执行本文件内容。

CREATE CONSTRAINT job_posting_id IF NOT EXISTS
FOR (n:JobPosting) REQUIRE n.record_id IS UNIQUE;

CREATE CONSTRAINT job_role_name IF NOT EXISTS
FOR (n:JobRole) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT skill_name IF NOT EXISTS
FOR (n:Skill) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT city_name IF NOT EXISTS
FOR (n:City) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT company_name IF NOT EXISTS
FOR (n:Company) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT major_name IF NOT EXISTS
FOR (n:Major) REQUIRE n.name IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///nodes_job_role.csv' AS row
MERGE (n:JobRole {name: row.name})
SET n.job_count = CASE row.job_count WHEN '' THEN null ELSE toInteger(row.job_count) END,
    n.top_source = row.top_source;

LOAD CSV WITH HEADERS FROM 'file:///nodes_skill.csv' AS row
MERGE (n:Skill {name: row.name})
SET n.job_count = CASE row.job_count WHEN '' THEN null ELSE toInteger(row.job_count) END;

LOAD CSV WITH HEADERS FROM 'file:///nodes_city.csv' AS row
MERGE (n:City {name: row.name})
SET n.job_count = CASE row.job_count WHEN '' THEN null ELSE toInteger(row.job_count) END;

LOAD CSV WITH HEADERS FROM 'file:///nodes_company.csv' AS row
MERGE (n:Company {name: row.name})
SET n.job_count = CASE row.job_count WHEN '' THEN null ELSE toInteger(row.job_count) END;

LOAD CSV WITH HEADERS FROM 'file:///nodes_major.csv' AS row
MERGE (n:Major {name: row.name})
SET n.job_count = CASE row.job_count WHEN '' THEN null ELSE toInteger(row.job_count) END;

LOAD CSV WITH HEADERS FROM 'file:///nodes_job_posting.csv' AS row
MERGE (n:JobPosting {record_id: row.record_id})
SET n.title = row.title,
    n.standard_job = row.standard_job,
    n.standard_city = row.standard_city,
    n.company_name = row.company_name,
    n.source_platform = row.source_platform,
    n.source_url = row.source_url,
    n.salary_text = row.salary_text,
    n.salary_min = CASE row.salary_min WHEN '' THEN null ELSE toFloat(row.salary_min) END,
    n.salary_max = CASE row.salary_max WHEN '' THEN null ELSE toFloat(row.salary_max) END,
    n.salary_mid = CASE row.salary_mid WHEN '' THEN null ELSE toFloat(row.salary_mid) END,
    n.annual_salary_estimated = CASE row.annual_salary_estimated WHEN '' THEN null ELSE toFloat(row.annual_salary_estimated) END,
    n.education = row.education,
    n.experience = row.experience,
    n.job_type = row.job_type,
    n.industry = row.industry,
    n.company_size = row.company_size,
    n.company_type = row.company_type,
    n.publish_date = CASE row.publish_date WHEN '' THEN null ELSE date(row.publish_date) END,
    n.crawl_time = row.crawl_time,
    n.quality_flag = row.quality_flag,
    n.description_short = row.description_short;

LOAD CSV WITH HEADERS FROM 'file:///rel_belongs_to_role.csv' AS row
MATCH (j:JobPosting {record_id: row.record_id})
MATCH (r:JobRole {name: row.role_name})
MERGE (j)-[:BELONGS_TO_ROLE]->(r);

LOAD CSV WITH HEADERS FROM 'file:///rel_located_in.csv' AS row
MATCH (j:JobPosting {record_id: row.record_id})
MATCH (c:City {name: row.city_name})
MERGE (j)-[:LOCATED_IN]->(c);

LOAD CSV WITH HEADERS FROM 'file:///rel_posted_by.csv' AS row
MATCH (j:JobPosting {record_id: row.record_id})
MATCH (c:Company {name: row.company_name})
MERGE (j)-[:POSTED_BY]->(c);

LOAD CSV WITH HEADERS FROM 'file:///rel_requires.csv' AS row
MATCH (j:JobPosting {record_id: row.record_id})
MATCH (s:Skill {name: row.skill_name})
MERGE (j)-[rel:REQUIRES]->(s)
SET rel.source_platform = row.source_platform,
    rel.standard_job = row.standard_job,
    rel.standard_city = row.standard_city;

LOAD CSV WITH HEADERS FROM 'file:///rel_related_to_major.csv' AS row
MATCH (j:JobPosting {record_id: row.record_id})
MATCH (m:Major {name: row.major_name})
MERGE (j)-[rel:RELATED_TO_MAJOR]->(m)
SET rel.major_category = row.major_category,
    rel.major_requirement_level = row.major_requirement_level;

LOAD CSV WITH HEADERS FROM 'file:///rel_role_requires_skill.csv' AS row
MATCH (r:JobRole {name: row.role_name})
MATCH (s:Skill {name: row.skill_name})
MERGE (r)-[rel:ROLE_REQUIRES_SKILL]->(s)
SET rel.job_count = CASE row.job_count WHEN '' THEN null ELSE toInteger(row.job_count) END,
    rel.skill_ratio = CASE row.skill_ratio WHEN '' THEN null ELSE toFloat(row.skill_ratio) END;

MATCH (n) RETURN labels(n) AS labels, count(n) AS count ORDER BY labels;
