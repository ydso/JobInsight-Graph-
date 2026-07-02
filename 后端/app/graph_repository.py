from __future__ import annotations

from typing import Any

from .neo4j_query_api import Neo4jQueryClient


class GraphRepository:
    def __init__(self, client: Neo4jQueryClient):
        self._client = client

    def ping(self) -> bool:
        rows = self._client.run("RETURN 1 AS ok")
        return bool(rows and rows[0].get("ok") == 1)

    def overview(self) -> dict[str, Any]:
        node_counts = self._client.run(
            """
            MATCH (n)
            RETURN labels(n)[0] AS label, count(n) AS count
            ORDER BY label
            """
        )
        relationship_counts = self._client.run(
            """
            MATCH ()-[r]->()
            RETURN type(r) AS type, count(r) AS count
            ORDER BY type
            """
        )
        totals = self._client.run(
            """
            MATCH (n)
            WITH count(n) AS nodes
            MATCH ()-[r]->()
            RETURN nodes, count(r) AS relationships
            """
        )
        top_roles = self.job_roles(limit=8)
        top_skills = self.skills(limit=10)

        return {
            "totals": totals[0] if totals else {"nodes": 0, "relationships": 0},
            "node_counts": node_counts,
            "relationship_counts": relationship_counts,
            "top_roles": top_roles,
            "top_skills": top_skills,
        }

    def job_roles(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (r:JobRole)
            RETURN r.name AS name, r.job_count AS job_count, r.top_source AS top_source
            ORDER BY coalesce(r.job_count, 0) DESC, name
            LIMIT $limit
            """,
            {"limit": limit},
        )

    def skills(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (s:Skill)
            RETURN s.name AS name, s.job_count AS job_count
            ORDER BY coalesce(s.job_count, 0) DESC, name
            LIMIT $limit
            """,
            {"limit": limit},
        )

    def cities(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (c:City)
            RETURN c.name AS name, c.job_count AS job_count
            ORDER BY coalesce(c.job_count, 0) DESC, name
            LIMIT $limit
            """,
            {"limit": limit},
        )

    def majors(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (m:Major)
            RETURN m.name AS name, m.job_count AS job_count
            ORDER BY coalesce(m.job_count, 0) DESC, name
            LIMIT $limit
            """,
            {"limit": limit},
        )

    def role_detail(self, role: str) -> dict[str, Any] | None:
        summary = self._client.run(
            """
            MATCH (r:JobRole {name: $role})
            OPTIONAL MATCH (r)<-[:BELONGS_TO_ROLE]-(j:JobPosting)
            WITH r,
                 count(DISTINCT j) AS actual_count,
                 avg(j.salary_mid) AS avg_salary_mid,
                 avg(j.annual_salary_estimated) AS avg_annual_salary
            RETURN r.name AS name,
                   coalesce(r.job_count, actual_count) AS job_count,
                   r.top_source AS top_source,
                   round(avg_salary_mid * 100) / 100 AS avg_salary_mid,
                   round(avg_annual_salary * 100) / 100 AS avg_annual_salary
            """,
            {"role": role},
        )
        if not summary:
            return None

        sources = self._client.run(
            """
            MATCH (:JobRole {name: $role})<-[:BELONGS_TO_ROLE]-(j:JobPosting)
            WITH coalesce(j.source_platform, '未标注') AS name, count(DISTINCT j) AS job_count
            RETURN name, job_count
            ORDER BY job_count DESC, name
            LIMIT 5
            """,
            {"role": role},
        )
        educations = self._client.run(
            """
            MATCH (:JobRole {name: $role})<-[:BELONGS_TO_ROLE]-(j:JobPosting)
            WHERE coalesce(j.education, '') <> ''
            RETURN j.education AS name, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, name
            LIMIT 5
            """,
            {"role": role},
        )
        experiences = self._client.run(
            """
            MATCH (:JobRole {name: $role})<-[:BELONGS_TO_ROLE]-(j:JobPosting)
            WHERE coalesce(j.experience, '') <> ''
            RETURN j.experience AS name, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, name
            LIMIT 5
            """,
            {"role": role},
        )

        return {
            "summary": summary[0],
            "top_cities": self.role_cities(role, limit=5),
            "top_skills": self.role_skills(role, limit=6),
            "source_distribution": sources,
            "education_distribution": educations,
            "experience_distribution": experiences,
        }

    def job_postings(
        self,
        role: str = "",
        city: str = "",
        skill: str = "",
        keyword: str = "",
        education: str = "",
        experience: str = "",
        company_size: str = "",
        industry: str = "",
        salary_min_wan: float = 0,
        salary_max_wan: float = 0,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        parameters = {
            "role": role.strip(),
            "city": city.strip(),
            "skill": skill.strip(),
            "keyword": keyword.strip(),
            "education": education.strip(),
            "experience": experience.strip(),
            "company_size": company_size.strip(),
            "industry": industry.strip(),
            "salary_min": max(0, float(salary_min_wan or 0)) * 10000,
            "salary_max": max(0, float(salary_max_wan or 0)) * 10000,
            "limit": limit,
            "offset": offset,
        }
        filter_clause = """
            ($role = '' OR j.standard_job = $role)
            AND ($city = '' OR j.standard_city = $city)
            AND (
              $education = ''
              OR j.education = $education
              OR ($education = '学历不限' AND j.education = '不限')
              OR ($education = '不限' AND j.education = '学历不限')
            )
            AND ($experience = '' OR j.experience = $experience)
            AND ($company_size = '' OR j.company_size = $company_size)
            AND ($industry = '' OR j.industry = $industry)
            AND ($salary_min = 0 OR coalesce(j.annual_salary_estimated, j.salary_mid * 12, 0) >= $salary_min)
            AND ($salary_max = 0 OR coalesce(j.annual_salary_estimated, j.salary_mid * 12, 0) <= $salary_max)
            AND (
              $keyword = ''
              OR toLower(coalesce(j.title, '')) CONTAINS toLower($keyword)
              OR toLower(coalesce(j.company_name, '')) CONTAINS toLower($keyword)
              OR toLower(coalesce(j.description_short, '')) CONTAINS toLower($keyword)
              OR EXISTS {
                MATCH (j)-[:REQUIRES]->(keyword_skill:Skill)
                WHERE toLower(keyword_skill.name) CONTAINS toLower($keyword)
              }
            )
            AND (
              $skill = ''
              OR EXISTS {
                MATCH (j)-[:REQUIRES]->(:Skill {name: $skill})
              }
            )
        """
        count_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            RETURN count(DISTINCT j) AS total
            """,
            parameters,
        )
        rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            WITH DISTINCT j, coalesce(toString(j.publish_date), '') AS publish_date
            RETURN j.record_id AS record_id,
                   j.title AS title,
                   j.standard_job AS role,
                   j.standard_city AS city,
                   j.company_name AS company,
                   j.source_platform AS source,
                   j.source_url AS source_url,
                   j.salary_text AS salary,
                   j.salary_mid AS salary_mid,
                   j.annual_salary_estimated AS annual_salary_estimated,
                   j.education AS education,
                   j.experience AS experience,
                   j.industry AS industry,
                   j.company_size AS company_size,
                   j.company_type AS company_type,
                   j.job_type AS job_type,
                   publish_date,
                   j.description_short AS description
            ORDER BY publish_date DESC, coalesce(j.salary_mid, 0) DESC, title
            SKIP $offset
            LIMIT $limit
            """,
            parameters,
        )
        return {
            "total": count_rows[0]["total"] if count_rows else 0,
            "limit": limit,
            "offset": offset,
            "items": rows,
        }

    def job_posting_detail(self, record_id: str) -> dict[str, Any] | None:
        rows = self._client.run(
            """
            MATCH (j:JobPosting {record_id: $record_id})
            OPTIONAL MATCH (j)-[:REQUIRES]->(s:Skill)
            WITH j, collect(DISTINCT s.name) AS skills
            OPTIONAL MATCH (j)-[:RELATED_TO_MAJOR]->(m:Major)
            WITH j, skills, collect(DISTINCT m.name) AS majors
            RETURN j.record_id AS record_id,
                   j.title AS title,
                   j.standard_job AS role,
                   j.standard_city AS city,
                   j.company_name AS company,
                   j.source_platform AS source,
                   j.source_url AS source_url,
                   j.salary_text AS salary,
                   j.salary_min AS salary_min,
                   j.salary_max AS salary_max,
                   j.salary_mid AS salary_mid,
                   j.annual_salary_estimated AS annual_salary_estimated,
                   j.education AS education,
                   j.experience AS experience,
                   j.job_type AS job_type,
                   j.industry AS industry,
                   j.company_size AS company_size,
                   j.company_type AS company_type,
                   coalesce(toString(j.publish_date), '') AS publish_date,
                   j.quality_flag AS quality_flag,
                   j.description_short AS description,
                   [skill IN skills WHERE skill IS NOT NULL] AS skills,
                   [major IN majors WHERE major IS NOT NULL] AS majors
            """,
            {"record_id": record_id},
        )
        return rows[0] if rows else None

    def role_skills(self, role: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (:JobRole {name: $role})-[r:ROLE_REQUIRES_SKILL]->(s:Skill)
            RETURN s.name AS skill, r.job_count AS job_count, r.skill_ratio AS ratio
            ORDER BY job_count DESC, skill
            LIMIT $limit
            """,
            {"role": role, "limit": limit},
        )

    def role_city_skills(self, role: str, city: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (j:JobPosting)-[:BELONGS_TO_ROLE]->(:JobRole {name: $role})
            MATCH (j)-[:LOCATED_IN]->(:City {name: $city})
            WITH collect(DISTINCT j) AS jobs
            WITH jobs, size(jobs) AS total
            UNWIND jobs AS job
            MATCH (job)-[:REQUIRES]->(s:Skill)
            WITH s.name AS skill, count(DISTINCT job) AS job_count, total
            RETURN skill,
                   job_count,
                   CASE WHEN total = 0 THEN 0.0 ELSE toFloat(job_count) / total END AS ratio
            ORDER BY job_count DESC, skill
            LIMIT $limit
            """,
            {"role": role, "city": city, "limit": limit},
        )

    def skill_roles(self, skill: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (role:JobRole)-[r:ROLE_REQUIRES_SKILL]->(:Skill {name: $skill})
            RETURN role.name AS role, r.job_count AS job_count, r.skill_ratio AS ratio
            ORDER BY job_count DESC, role
            LIMIT $limit
            """,
            {"skill": skill, "limit": limit},
        )

    def city_skills(self, city: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (j:JobPosting)-[:LOCATED_IN]->(:City {name: $city})
            MATCH (j)-[:REQUIRES]->(s:Skill)
            RETURN s.name AS skill, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, skill
            LIMIT $limit
            """,
            {"city": city, "limit": limit},
        )

    def role_cities(self, role: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (j:JobPosting)-[:BELONGS_TO_ROLE]->(:JobRole {name: $role})
            MATCH (j)-[:LOCATED_IN]->(c:City)
            RETURN c.name AS city, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, city
            LIMIT $limit
            """,
            {"role": role, "limit": limit},
        )

    def major_roles(self, major: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._client.run(
            """
            MATCH (:Major {name: $major})<-[:RELATED_TO_MAJOR]-(j:JobPosting)-[:BELONGS_TO_ROLE]->(r:JobRole)
            RETURN r.name AS role, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, role
            LIMIT $limit
            """,
            {"major": major, "limit": limit},
        )

    def role_graph(self, role: str, limit: int = 30) -> dict[str, Any]:
        skill_rows = self._client.run(
            """
            MATCH (role:JobRole {name: $role})-[r:ROLE_REQUIRES_SKILL]->(skill:Skill)
            RETURN role.name AS role, skill.name AS skill, r.job_count AS job_count, r.skill_ratio AS ratio
            ORDER BY job_count DESC, skill
            LIMIT $limit
            """,
            {"role": role, "limit": limit},
        )
        major_rows = self._client.run(
            """
            MATCH (j:JobPosting)-[:BELONGS_TO_ROLE]->(role:JobRole {name: $role})
            WITH role, collect(DISTINCT j) AS jobs
            WITH role, jobs, size(jobs) AS total
            UNWIND jobs AS job
            MATCH (job)-[:RELATED_TO_MAJOR]->(major:Major)
            WITH role.name AS role, major.name AS major, count(DISTINCT job) AS job_count, total
            RETURN role,
                   major,
                   job_count,
                   CASE WHEN total = 0 THEN 0.0 ELSE toFloat(job_count) / total END AS ratio
            ORDER BY job_count DESC, major
            LIMIT $limit
            """,
            {"role": role, "limit": limit},
        )

        if not skill_rows and not major_rows:
            return {"nodes": [], "links": [], "categories": [{"name": "JobRole"}, {"name": "Skill"}, {"name": "Major"}]}

        role_name = (skill_rows[0] if skill_rows else major_rows[0])["role"]
        nodes = [
            {
                "id": f"role:{role_name}",
                "name": role_name,
                "category": "JobRole",
                "symbolSize": 64,
                "value": max(len(skill_rows), len(major_rows)),
            }
        ]
        links = []
        for row in skill_rows:
            skill = row["skill"]
            job_count = row.get("job_count") or 0
            ratio = row.get("ratio") or 0
            nodes.append(
                {
                    "id": f"skill:{skill}",
                    "name": skill,
                    "category": "Skill",
                    "symbolSize": max(24, min(56, 20 + job_count / 60)),
                    "value": job_count,
                }
            )
            links.append(
                {
                    "source": f"role:{role_name}",
                    "target": f"skill:{skill}",
                    "name": "ROLE_REQUIRES_SKILL",
                    "value": job_count,
                    "ratio": ratio,
                }
            )
        for row in major_rows:
            major = row["major"]
            job_count = row.get("job_count") or 0
            ratio = row.get("ratio") or 0
            nodes.append(
                {
                    "id": f"major:{major}",
                    "name": major,
                    "category": "Major",
                    "symbolSize": max(24, min(56, 20 + job_count / 60)),
                    "value": job_count,
                }
            )
            links.append(
                {
                    "source": f"role:{role_name}",
                    "target": f"major:{major}",
                    "name": "ROLE_REQUIRES_MAJOR",
                    "value": job_count,
                    "ratio": ratio,
                }
            )

        return {
            "nodes": nodes,
            "links": links,
            "categories": [{"name": "JobRole"}, {"name": "Skill"}, {"name": "Major"}],
        }

    def skill_graph(self, role: str = "", city: str = "", skill: str = "", limit: int = 60) -> dict[str, Any]:
        parameters = {
            "role": role.strip(),
            "city": city.strip(),
            "skill": skill.strip(),
            "limit": limit,
            "city_limit": min(max(limit // 2, 8), 40),
            "major_limit": min(max(limit // 2, 8), 40),
            "bucket_limit": 16,
        }
        filter_clause = """
            ($role = '' OR j.standard_job = $role)
            AND ($city = '' OR j.standard_city = $city)
            AND (
              $skill = ''
              OR EXISTS {
                MATCH (j)-[:REQUIRES]->(:Skill {name: $skill})
              }
            )
        """

        total_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            RETURN count(DISTINCT j) AS total_jobs
            """,
            parameters,
        )
        total_jobs = total_rows[0]["total_jobs"] if total_rows else 0
        parameters["total_jobs"] = total_jobs

        salary_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            RETURN round(avg(j.salary_mid) * 100) / 100 AS avg_salary_mid,
                   round(avg(j.annual_salary_estimated) * 100) / 100 AS avg_annual_salary,
                   sum(CASE WHEN j.salary_mid IS NULL THEN 0 ELSE 1 END) AS salary_sample_count
            """,
            parameters,
        )
        salary_metrics = salary_rows[0] if salary_rows else {}

        role_skill_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:BELONGS_TO_ROLE]->(role:JobRole)
            MATCH (j)-[:REQUIRES]->(skill:Skill)
            WITH role.name AS role, skill.name AS skill, count(DISTINCT j) AS job_count
            RETURN role,
                   skill,
                   job_count,
                   CASE WHEN $total_jobs = 0 THEN 0.0 ELSE toFloat(job_count) / $total_jobs END AS ratio
            ORDER BY job_count DESC, role, skill
            LIMIT $limit
            """,
            parameters,
        )
        city_role_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:LOCATED_IN]->(city:City)
            MATCH (j)-[:BELONGS_TO_ROLE]->(role:JobRole)
            RETURN city.name AS city, role.name AS role, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, city, role
            LIMIT $city_limit
            """,
            parameters,
        )
        role_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:BELONGS_TO_ROLE]->(role:JobRole)
            RETURN role.name AS role, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, role
            LIMIT $bucket_limit
            """,
            parameters,
        )
        skill_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:REQUIRES]->(skill:Skill)
            RETURN skill.name AS skill, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, skill
            LIMIT $bucket_limit
            """,
            parameters,
        )
        role_major_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:BELONGS_TO_ROLE]->(role:JobRole)
            MATCH (j)-[:RELATED_TO_MAJOR]->(major:Major)
            WITH role.name AS role, major.name AS major, count(DISTINCT j) AS job_count
            RETURN role,
                   major,
                   job_count,
                   CASE WHEN $total_jobs = 0 THEN 0.0 ELSE toFloat(job_count) / $total_jobs END AS ratio
            ORDER BY job_count DESC, role, major
            LIMIT $major_limit
            """,
            parameters,
        )
        major_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:RELATED_TO_MAJOR]->(major:Major)
            RETURN major.name AS major, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, major
            LIMIT $bucket_limit
            """,
            parameters,
        )
        city_rows = self._client.run(
            f"""
            MATCH (j:JobPosting)
            WHERE {filter_clause}
            MATCH (j)-[:LOCATED_IN]->(city:City)
            RETURN city.name AS city, count(DISTINCT j) AS job_count
            ORDER BY job_count DESC, city
            LIMIT 8
            """,
            parameters,
        )

        nodes_by_id: dict[str, dict[str, Any]] = {}

        def add_node(node_id: str, name: str, category: str, value: int) -> None:
            if not name:
                return
            current = nodes_by_id.get(node_id)
            if current is None or value > (current.get("value") or 0):
                nodes_by_id[node_id] = {
                    "id": node_id,
                    "name": name,
                    "category": category,
                    "value": value,
                }

        for row in city_rows:
            add_node(f"city:{row['city']}", row["city"], "City", row.get("job_count") or 0)
        for row in role_rows:
            add_node(f"role:{row['role']}", row["role"], "JobRole", row.get("job_count") or 0)
        for row in skill_rows:
            add_node(f"skill:{row['skill']}", row["skill"], "Skill", row.get("job_count") or 0)
        for row in major_rows:
            add_node(f"major:{row['major']}", row["major"], "Major", row.get("job_count") or 0)

        links: list[dict[str, Any]] = []
        for row in city_role_rows:
            city_name = row["city"]
            role_name = row["role"]
            job_count = row.get("job_count") or 0
            add_node(f"city:{city_name}", city_name, "City", job_count)
            add_node(f"role:{role_name}", role_name, "JobRole", job_count)
            links.append(
                {
                    "source": f"city:{city_name}",
                    "target": f"role:{role_name}",
                    "name": "CITY_HAS_ROLE",
                    "value": job_count,
                    "ratio": 0 if total_jobs == 0 else job_count / total_jobs,
                }
            )

        for row in role_skill_rows:
            role_name = row["role"]
            skill_name = row["skill"]
            job_count = row.get("job_count") or 0
            add_node(f"role:{role_name}", role_name, "JobRole", job_count)
            add_node(f"skill:{skill_name}", skill_name, "Skill", job_count)
            links.append(
                {
                    "source": f"role:{role_name}",
                    "target": f"skill:{skill_name}",
                    "name": "ROLE_REQUIRES_SKILL",
                    "value": job_count,
                    "ratio": row.get("ratio") or 0,
                }
            )

        for row in role_major_rows:
            role_name = row["role"]
            major_name = row["major"]
            job_count = row.get("job_count") or 0
            add_node(f"role:{role_name}", role_name, "JobRole", job_count)
            add_node(f"major:{major_name}", major_name, "Major", job_count)
            links.append(
                {
                    "source": f"role:{role_name}",
                    "target": f"major:{major_name}",
                    "name": "ROLE_REQUIRES_MAJOR",
                    "value": job_count,
                    "ratio": row.get("ratio") or 0,
                }
            )

        top_role = role_rows[0] if role_rows else None
        top_skill = skill_rows[0] if skill_rows else None
        top_city = city_rows[0] if city_rows else None
        top_major = major_rows[0] if major_rows else None
        metrics = {
            "total_jobs": total_jobs,
            "node_count": len(nodes_by_id),
            "link_count": len(links),
            "top_role": top_role["role"] if top_role else "",
            "top_skill": top_skill["skill"] if top_skill else "",
            "top_city": top_city["city"] if top_city else "",
            "top_major": top_major["major"] if top_major else "",
            "avg_salary_mid": salary_metrics.get("avg_salary_mid") or 0,
            "avg_annual_salary": salary_metrics.get("avg_annual_salary") or 0,
            "salary_sample_count": salary_metrics.get("salary_sample_count") or 0,
        }

        return {
            "filters": {
                "role": parameters["role"],
                "city": parameters["city"],
                "skill": parameters["skill"],
                "limit": limit,
            },
            "metrics": metrics,
            "top_roles": role_rows,
            "top_skills": skill_rows,
            "top_cities": city_rows,
            "top_majors": major_rows,
            "graph": {
                "nodes": list(nodes_by_id.values()),
                "links": links,
                "categories": [{"name": "City"}, {"name": "JobRole"}, {"name": "Skill"}, {"name": "Major"}],
            },
        }

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        keyword = keyword.strip()
        if not keyword:
            return []

        per_label_limit = max(1, limit)
        return self._client.run(
            """
            CALL {
              MATCH (n:JobRole)
              WHERE toLower(n.name) CONTAINS toLower($keyword)
              RETURN 'JobRole' AS label, n.name AS name, n.job_count AS job_count
              ORDER BY coalesce(n.job_count, 0) DESC, name
              LIMIT $per_label_limit
              UNION
              MATCH (n:Skill)
              WHERE toLower(n.name) CONTAINS toLower($keyword)
              RETURN 'Skill' AS label, n.name AS name, n.job_count AS job_count
              ORDER BY coalesce(n.job_count, 0) DESC, name
              LIMIT $per_label_limit
              UNION
              MATCH (n:City)
              WHERE toLower(n.name) CONTAINS toLower($keyword)
              RETURN 'City' AS label, n.name AS name, n.job_count AS job_count
              ORDER BY coalesce(n.job_count, 0) DESC, name
              LIMIT $per_label_limit
              UNION
              MATCH (n:Major)
              WHERE toLower(n.name) CONTAINS toLower($keyword)
              RETURN 'Major' AS label, n.name AS name, n.job_count AS job_count
              ORDER BY coalesce(n.job_count, 0) DESC, name
              LIMIT $per_label_limit
            }
            RETURN label, name, job_count
            ORDER BY label, coalesce(job_count, 0) DESC, name
            LIMIT $limit
            """,
            {"keyword": keyword, "limit": limit, "per_label_limit": per_label_limit},
        )
