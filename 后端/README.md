# 后端接口层说明

该目录提供基于 FastAPI 的知识图谱后端接口，当前通过 Neo4j Query API 访问本机 Neo4j，不需要额外安装 `neo4j` Python 驱动。

## 1. 配置

复制 `.env.example` 为 `.env`，并把 `NEO4J_PASSWORD` 改成 Neo4j 的实际密码：

```powershell
Copy-Item .env.example .env
notepad .env
```

默认连接：

- Neo4j HTTP: `http://127.0.0.1:7474`
- database: `neo4j`
- user: `neo4j`

## 2. 启动

先确认 Neo4j 已启动：

```powershell
powershell -ExecutionPolicy Bypass -File ..\数据\图谱\start_neo4j_short_path.ps1
```

然后启动后端：

```powershell
cd backend
powershell -ExecutionPolicy Bypass -File .\start_backend.ps1
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

健康检查：

```text
http://127.0.0.1:8000/health
```

## 3. 核心接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/overview` | 图谱总览，返回节点数、关系数、热门岗位、热门技能 |
| `GET /api/job-roles` | 岗位类型列表 |
| `GET /api/skills` | 技能列表 |
| `GET /api/cities` | 城市列表 |
| `GET /api/majors` | 专业列表 |
| `GET /api/job-roles/{role}/skills` | 查询某岗位的热门技能 |
| `GET /api/skills/{skill}/job-roles` | 查询某技能关联的岗位 |
| `GET /api/cities/{city}/skills` | 查询某城市热门技能 |
| `GET /api/job-roles/{role}/cities` | 查询某岗位的城市分布 |
| `GET /api/majors/{major}/job-roles` | 查询某专业关联岗位 |
| `GET /api/graphs/job-role/{role}` | 返回岗位-技能-专业局部图，适合前端 ECharts graph |
| `GET /api/graphs/skill` | 返回城市-岗位-技能-专业综合图，可按岗位、城市、技能筛选 |
| `GET /api/search?q=Python` | 搜索岗位、技能、城市、专业 |

## 4. 示例

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/overview"
Invoke-RestMethod "http://127.0.0.1:8000/api/job-roles/算法工程师/skills?limit=10"
Invoke-RestMethod "http://127.0.0.1:8000/api/skills/Python/job-roles"
Invoke-RestMethod "http://127.0.0.1:8000/api/cities/上海/skills?limit=10"
```
