from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ConfigError, get_settings
from .graph_repository import GraphRepository
from .neo4j_query_api import Neo4jQueryClient, Neo4jQueryError


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API for querying the job-skill Neo4j knowledge graph.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache
def get_repository() -> GraphRepository:
    client = Neo4jQueryClient(get_settings())
    return GraphRepository(client)


def graph_repo() -> GraphRepository:
    return get_repository()


def _safe_limit(value: int) -> int:
    return max(1, min(value, 200))


def _safe_offset(value: int) -> int:
    return max(0, value)


@app.exception_handler(ConfigError)
async def config_error_handler(_, exc: ConfigError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(Neo4jQueryError)
async def neo4j_error_handler(_, exc: Neo4jQueryError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/", tags=["meta"])
def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["meta"])
def health(repo: GraphRepository = Depends(graph_repo)):
    try:
        ok = repo.ping()
    except (ConfigError, Neo4jQueryError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "status": "ok" if ok else "failed",
        "neo4j": ok,
        "database": settings.neo4j_database,
    }


@app.get("/api/overview", tags=["overview"])
def overview(repo: GraphRepository = Depends(graph_repo)):
    return repo.overview()


@app.get("/api/job-roles", tags=["dictionary"])
def job_roles(limit: int = Query(100, ge=1, le=200), repo: GraphRepository = Depends(graph_repo)):
    return {"items": repo.job_roles(_safe_limit(limit))}


@app.get("/api/job-roles/{role}/detail", tags=["analysis"])
def role_detail(role: str, repo: GraphRepository = Depends(graph_repo)):
    detail = repo.role_detail(role)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job role not found")
    return {"role": role, **detail}


@app.get("/api/job-postings", tags=["job-postings"])
def job_postings(
    role: str = Query("", description="标准岗位名称"),
    city: str = Query("", description="标准城市名称"),
    skill: str = Query("", description="技能名称"),
    q: str = Query("", description="岗位、公司或描述关键词"),
    education: str = Query("", description="学历要求"),
    experience: str = Query("", description="工作经验"),
    company_size: str = Query("", description="公司规模"),
    industry: str = Query("", description="行业"),
    salary_min_wan: float = Query(0, ge=0, description="最低年薪，单位万元"),
    salary_max_wan: float = Query(0, ge=0, description="最高年薪，单位万元"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repo: GraphRepository = Depends(graph_repo),
):
    return repo.job_postings(
        role=role,
        city=city,
        skill=skill,
        keyword=q,
        education=education,
        experience=experience,
        company_size=company_size,
        industry=industry,
        salary_min_wan=salary_min_wan,
        salary_max_wan=salary_max_wan,
        limit=_safe_limit(limit),
        offset=_safe_offset(offset),
    )


@app.get("/api/job-postings/{record_id}", tags=["job-postings"])
def job_posting_detail(record_id: str, repo: GraphRepository = Depends(graph_repo)):
    detail = repo.job_posting_detail(record_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job posting not found")
    return detail


@app.get("/api/skills", tags=["dictionary"])
def skills(limit: int = Query(100, ge=1, le=200), repo: GraphRepository = Depends(graph_repo)):
    return {"items": repo.skills(_safe_limit(limit))}


@app.get("/api/cities", tags=["dictionary"])
def cities(limit: int = Query(100, ge=1, le=200), repo: GraphRepository = Depends(graph_repo)):
    return {"items": repo.cities(_safe_limit(limit))}


@app.get("/api/majors", tags=["dictionary"])
def majors(limit: int = Query(100, ge=1, le=200), repo: GraphRepository = Depends(graph_repo)):
    return {"items": repo.majors(_safe_limit(limit))}


@app.get("/api/job-roles/{role}/skills", tags=["analysis"])
def role_skills(
    role: str,
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"role": role, "items": repo.role_skills(role, _safe_limit(limit))}


@app.get("/api/skills/{skill}/job-roles", tags=["analysis"])
def skill_roles(
    skill: str,
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"skill": skill, "items": repo.skill_roles(skill, _safe_limit(limit))}


@app.get("/api/skill-job-roles", tags=["analysis"])
def skill_roles_by_query(
    skill: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"skill": skill, "items": repo.skill_roles(skill, _safe_limit(limit))}


@app.get("/api/cities/{city}/skills", tags=["analysis"])
def city_skills(
    city: str,
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"city": city, "items": repo.city_skills(city, _safe_limit(limit))}


@app.get("/api/job-roles/{role}/cities", tags=["analysis"])
def role_cities(
    role: str,
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"role": role, "items": repo.role_cities(role, _safe_limit(limit))}


@app.get("/api/job-roles/{role}/cities/{city}/skills", tags=["analysis"])
def role_city_skills(
    role: str,
    city: str,
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"role": role, "city": city, "items": repo.role_city_skills(role, city, _safe_limit(limit))}


@app.get("/api/majors/{major}/job-roles", tags=["analysis"])
def major_roles(
    major: str,
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"major": major, "items": repo.major_roles(major, _safe_limit(limit))}


@app.get("/api/graphs/job-role/{role}", tags=["graph"])
def role_graph(
    role: str,
    limit: int = Query(30, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"role": role, "graph": repo.role_graph(role, _safe_limit(limit))}


@app.get("/api/graphs/skill", tags=["graph"])
def skill_graph(
    role: str = Query("", description="标准岗位名称"),
    city: str = Query("", description="标准城市名称"),
    skill: str = Query("", description="技能名称；用于查看共现技能图谱"),
    limit: int = Query(60, ge=1, le=120),
    repo: GraphRepository = Depends(graph_repo),
):
    return repo.skill_graph(role=role, city=city, skill=skill, limit=_safe_limit(limit))


@app.get("/api/search", tags=["search"])
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    repo: GraphRepository = Depends(graph_repo),
):
    return {"keyword": q, "items": repo.search(q, _safe_limit(limit))}
