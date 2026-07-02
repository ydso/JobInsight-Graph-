# Neo4j 知识图谱导入说明

## 1. 导出 CSV

默认输出到 `数据/图谱/输出`：

```powershell
python 数据\图谱\export_graph_csv.py
```

推荐直接输出到 Neo4j Desktop 当前实例的 `import` 目录：

```powershell
python 数据\图谱\export_graph_csv.py --output-dir "C:\Users\杨欣瑞\.Neo4jDesktop2\Data\dbmss\dbms-a3fb0f08-fe8e-4ceb-8f2a-228522586a6f\import"
```

## 2. 启动 Neo4j

在 Neo4j Desktop 中打开 `job_skill_graph`，点击启动按钮。截图里当前状态是 `STOPPED`，需要先启动到 `RUNNING`。

如果 Neo4j Desktop 只短暂显示 `RUNNING` 后又变成 `STOPPED`，查看日志后常见原因是：

- Windows 下 Neo4j Desktop 安装目录过长，触发 `CreateProcess error=206`。
- 当前实例是 Enterprise Edition，尚未接受 Neo4j 评估许可。

可使用短路径脚本绕过 Desktop 启动：

```powershell
# 仅当你同意 Neo4j Enterprise 评估许可时运行
powershell -ExecutionPolicy Bypass -File 数据\图谱\accept_neo4j_evaluation_license.ps1

# 启动 Neo4j，启动后打开 http://localhost:7474
powershell -ExecutionPolicy Bypass -File 数据\图谱\start_neo4j_short_path.ps1
```

停止：

```powershell
powershell -ExecutionPolicy Bypass -File 数据\图谱\stop_neo4j_short_path.ps1
```

## 3. 执行导入

启动后进入左侧 `Query`，复制并执行：

```text
数据/图谱/import_neo4j.cypher
```

导入成功后最后会返回每类节点数量。

## 4. 常用查询

导入后可以执行：

```text
数据/图谱/graph_queries.cypher
```

建议先跑：

```cypher
MATCH (n)
RETURN labels(n) AS labels, count(n) AS count
ORDER BY labels;
```

## 5. 预期图谱结构

- `JobPosting`：岗位记录
- `JobRole`：标准岗位
- `Skill`：技能
- `City`：城市
- `Company`：公司
- `Major`：专业

主要关系：

- `(:JobPosting)-[:BELONGS_TO_ROLE]->(:JobRole)`
- `(:JobPosting)-[:REQUIRES]->(:Skill)`
- `(:JobPosting)-[:LOCATED_IN]->(:City)`
- `(:JobPosting)-[:POSTED_BY]->(:Company)`
- `(:JobPosting)-[:RELATED_TO_MAJOR]->(:Major)`
- `(:JobRole)-[:ROLE_REQUIRES_SKILL]->(:Skill)`
