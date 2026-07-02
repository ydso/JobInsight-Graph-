"""前程无忧CSV的Spark读取入口。

岗位描述包含合法的CSV多行字段，必须启用multiLine，否则一条岗位会被拆成多行。
该模块只负责无损读取，不执行清洗或技能归一化。
"""


def read_51job_csv(spark, path):
    """使用与crawler_51job.py输出格式匹配的参数读取CSV。"""
    return (
        spark.read
        .option("header", True)
        .option("multiLine", True)
        .option("quote", '"')
        .option("escape", '"')
        .option("encoding", "UTF-8")
        .option("mode", "PERMISSIVE")
        .csv(path)
    )
