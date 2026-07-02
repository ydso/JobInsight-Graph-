// 1. 查看图谱规模
MATCH (n)
RETURN labels(n) AS labels, count(n) AS count
ORDER BY labels;

// 2. 某岗位最常见技能
MATCH (:JobRole {name: '数据分析师'})-[r:ROLE_REQUIRES_SKILL]->(s:Skill)
RETURN s.name AS skill, r.job_count AS job_count, r.skill_ratio AS ratio
ORDER BY job_count DESC
LIMIT 20;

// 3. 某技能关联的岗位类型
MATCH (role:JobRole)-[r:ROLE_REQUIRES_SKILL]->(:Skill {name: 'Python'})
RETURN role.name AS job_role, r.job_count AS job_count, r.skill_ratio AS ratio
ORDER BY job_count DESC;

// 4. 某城市热门技能
MATCH (j:JobPosting)-[:LOCATED_IN]->(:City {name: '上海'})
MATCH (j)-[:REQUIRES]->(s:Skill)
RETURN s.name AS skill, count(DISTINCT j) AS job_count
ORDER BY job_count DESC
LIMIT 20;

// 5. 某岗位类型的城市分布
MATCH (j:JobPosting)-[:BELONGS_TO_ROLE]->(:JobRole {name: '算法工程师'})
MATCH (j)-[:LOCATED_IN]->(c:City)
RETURN c.name AS city, count(DISTINCT j) AS job_count
ORDER BY job_count DESC;

// 6. 岗位-技能局部图，可直接可视化
MATCH p = (:JobRole {name: '数据开发工程师'})-[r:ROLE_REQUIRES_SKILL]->(:Skill)
RETURN p
ORDER BY r.job_count DESC
LIMIT 30;
