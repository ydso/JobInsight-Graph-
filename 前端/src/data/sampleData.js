export const ALL_CITIES = "__all_cities__";

export const sampleRoles = [
  { name: "算法工程师", job_count: 1254, top_source: "前程无忧" },
  { name: "数据分析师", job_count: 345, top_source: "国家大学生就业服务平台" },
  { name: "Python开发工程师", job_count: 106, top_source: "前程无忧" },
  { name: "大数据开发工程师", job_count: 58, top_source: "智联招聘" },
  { name: "BI分析师", job_count: 52, top_source: "前程无忧" },
  { name: "数据仓库工程师", job_count: 41, top_source: "国家大学生就业服务平台" }
];

export const sampleCities = [
  { name: "上海", job_count: 1069 },
  { name: "北京", job_count: 971 },
  { name: "深圳", job_count: 960 },
  { name: "广州", job_count: 595 },
  { name: "杭州", job_count: 519 },
  { name: "南京", job_count: 421 },
  { name: "武汉", job_count: 364 },
  { name: "成都", job_count: 331 },
  { name: "重庆", job_count: 226 },
  { name: "西安", job_count: 208 }
];

export const sampleSkills = [
  { name: "Python", job_count: 1069 },
  { name: "机器学习", job_count: 866 },
  { name: "SQL", job_count: 481 },
  { name: "PyTorch", job_count: 603 },
  { name: "Spark", job_count: 322 },
  { name: "Linux", job_count: 376 },
  { name: "Hive", job_count: 188 },
  { name: "Tableau", job_count: 126 }
];

export const skillsByRole = {
  算法工程师: [
    { skill: "Python", job_count: 426, ratio: 0.34 },
    { skill: "机器学习", job_count: 385, ratio: 0.307 },
    { skill: "PyTorch", job_count: 302, ratio: 0.241 },
    { skill: "TensorFlow", job_count: 188, ratio: 0.15 },
    { skill: "SQL", job_count: 156, ratio: 0.124 },
    { skill: "Linux", job_count: 142, ratio: 0.113 }
  ],
  数据分析师: [
    { skill: "Python", job_count: 109, ratio: 0.316 },
    { skill: "SQL", job_count: 96, ratio: 0.278 },
    { skill: "Excel", job_count: 82, ratio: 0.238 },
    { skill: "Spark", job_count: 41, ratio: 0.119 },
    { skill: "机器学习", job_count: 34, ratio: 0.099 },
    { skill: "Tableau", job_count: 29, ratio: 0.084 }
  ],
  Python开发工程师: [
    { skill: "Python", job_count: 88, ratio: 0.83 },
    { skill: "Django", job_count: 38, ratio: 0.358 },
    { skill: "MySQL", job_count: 36, ratio: 0.339 },
    { skill: "Linux", job_count: 31, ratio: 0.292 },
    { skill: "Redis", job_count: 27, ratio: 0.255 },
    { skill: "Docker", job_count: 19, ratio: 0.179 }
  ],
  大数据开发工程师: [
    { skill: "Spark", job_count: 44, ratio: 0.759 },
    { skill: "Hadoop", job_count: 39, ratio: 0.672 },
    { skill: "Hive", job_count: 34, ratio: 0.586 },
    { skill: "SQL", job_count: 31, ratio: 0.534 },
    { skill: "Kafka", job_count: 26, ratio: 0.448 },
    { skill: "Flink", job_count: 22, ratio: 0.379 }
  ],
  BI分析师: [
    { skill: "SQL", job_count: 41, ratio: 0.788 },
    { skill: "Power BI", job_count: 29, ratio: 0.558 },
    { skill: "Tableau", job_count: 24, ratio: 0.462 },
    { skill: "Excel", job_count: 23, ratio: 0.442 },
    { skill: "Python", job_count: 18, ratio: 0.346 },
    { skill: "FineBI", job_count: 15, ratio: 0.288 }
  ],
  数据仓库工程师: [
    { skill: "Hive", job_count: 33, ratio: 0.805 },
    { skill: "SQL", job_count: 30, ratio: 0.732 },
    { skill: "Spark", job_count: 26, ratio: 0.634 },
    { skill: "ETL", job_count: 22, ratio: 0.537 },
    { skill: "Hadoop", job_count: 19, ratio: 0.463 },
    { skill: "Kafka", job_count: 14, ratio: 0.341 }
  ]
};

export const evidenceBySkill = {
  Python: [
    "任职要求中出现 Python、SQL，并要求能完成数据清洗和自动化分析。",
    "岗位职责提到使用 Python 构建报表脚本，配合业务完成指标监控。",
    "加分项包含 pandas、爬虫经验和数据可视化能力。"
  ],
  SQL: [
    "岗位描述要求熟悉 SQL 查询优化，能独立完成多表数据分析。",
    "任职要求中出现 MySQL、Hive SQL 和数据仓库建模经验。",
    "职责包含提取业务数据并进行口径校验。"
  ],
  Spark: [
    "岗位职责要求使用 Spark 完成离线数据处理和批量统计。",
    "任职要求中提到 PySpark、Spark SQL 和大数据平台经验。",
    "候选人需理解分布式计算和常见性能调优方法。"
  ],
  机器学习: [
    "岗位描述提到分类、回归、聚类模型的训练与评估。",
    "任职要求中出现特征工程、模型调参和业务指标解释。",
    "加分项包含推荐系统、时间序列和模型部署经验。"
  ],
  default: [
    "岗位文本中出现该技能词，并能回溯到任职要求段落。",
    "技能抽取结果由词库匹配和上下文规则共同确认。",
    "证据片段保留原文语义，便于人工复核。"
  ]
};

export const trendMonths = ["1月", "2月", "3月", "4月", "5月", "6月"];

export const trendBySkill = {
  Python: [42, 45, 48, 51, 49, 55],
  SQL: [37, 39, 41, 44, 42, 46],
  Spark: [21, 22, 25, 27, 29, 31],
  机器学习: [18, 20, 23, 24, 28, 30],
  default: [16, 18, 21, 22, 24, 25]
};

export const sampleJobPostings = [
  {
    record_id: "sample-1",
    title: "数据分析师",
    role: "数据分析师",
    city: "上海",
    company: "某互联网数据中心",
    source: "样例数据",
    source_url: "https://example.com/jobs/sample-1",
    salary: "12-18K",
    salary_mid: 15000,
    education: "本科",
    experience: "1-3年",
    industry: "互联网",
    company_type: "民营",
    company_size: "150-500人",
    publish_date: "2026-06",
    description: "负责业务数据指标体系、SQL 数据提取、Python 自动化分析和可视化报表建设。",
    skills: ["Python", "SQL", "Excel", "Tableau"],
    majors: ["统计学", "数据科学与大数据技术", "计算机科学与技术"]
  },
  {
    record_id: "sample-2",
    title: "算法工程师",
    role: "算法工程师",
    city: "北京",
    company: "智能科技实验室",
    source: "样例数据",
    source_url: "https://example.com/jobs/sample-2",
    salary: "20-35K",
    salary_mid: 27500,
    education: "硕士",
    experience: "1-3年",
    industry: "人工智能",
    company_type: "民营",
    company_size: "50-150人",
    publish_date: "2026-06",
    description: "参与机器学习模型训练、特征工程、PyTorch 算法验证和线上效果评估。",
    skills: ["Python", "机器学习", "PyTorch", "Linux"],
    majors: ["人工智能", "软件工程", "计算机科学与技术"]
  },
  {
    record_id: "sample-3",
    title: "大数据开发工程师",
    role: "大数据开发工程师",
    city: "深圳",
    company: "制造业数据平台",
    source: "样例数据",
    source_url: "https://example.com/jobs/sample-3",
    salary: "16-26K",
    salary_mid: 21000,
    education: "本科",
    experience: "3-5年",
    industry: "智能制造",
    company_type: "上市公司",
    company_size: "500-1000人",
    publish_date: "2026-06",
    description: "建设离线数仓任务，使用 Spark、Hive、Kafka 完成数据处理与质量校验。",
    skills: ["Spark", "Hive", "Kafka", "SQL"],
    majors: ["数据科学与大数据技术", "软件工程"]
  }
];

export const sampleGraph = {
  metrics: {
    total_jobs: 5857,
    node_count: 22,
    link_count: 27,
    top_role: "算法工程师",
    top_skill: "Python",
    top_city: "上海",
    top_major: "计算机科学与技术",
    avg_salary_mid: 16840,
    avg_annual_salary: 202080,
    salary_sample_count: 4620
  },
  top_cities: sampleCities.slice(0, 5).map((city) => ({ city: city.name, job_count: city.job_count })),
  top_roles: sampleRoles.slice(0, 5).map((role) => ({ role: role.name, job_count: role.job_count })),
  top_skills: sampleSkills.slice(0, 6).map((skill) => ({ skill: skill.name, job_count: skill.job_count })),
  top_majors: [
    { major: "计算机科学与技术", job_count: 742 },
    { major: "软件工程", job_count: 618 },
    { major: "数据科学与大数据技术", job_count: 491 },
    { major: "统计学", job_count: 284 }
  ],
  graph: {
    categories: [{ name: "City" }, { name: "JobRole" }, { name: "Skill" }, { name: "Major" }],
    nodes: [
      { id: "city:上海", name: "上海", category: "City", value: 1069 },
      { id: "city:北京", name: "北京", category: "City", value: 971 },
      { id: "city:深圳", name: "深圳", category: "City", value: 960 },
      { id: "role:算法工程师", name: "算法工程师", category: "JobRole", value: 1254 },
      { id: "role:数据分析师", name: "数据分析师", category: "JobRole", value: 345 },
      { id: "role:Python开发工程师", name: "Python开发工程师", category: "JobRole", value: 106 },
      { id: "skill:Python", name: "Python", category: "Skill", value: 1069 },
      { id: "skill:机器学习", name: "机器学习", category: "Skill", value: 866 },
      { id: "skill:PyTorch", name: "PyTorch", category: "Skill", value: 603 },
      { id: "skill:SQL", name: "SQL", category: "Skill", value: 481 },
      { id: "skill:Spark", name: "Spark", category: "Skill", value: 322 },
      { id: "major:计算机科学与技术", name: "计算机科学与技术", category: "Major", value: 742 },
      { id: "major:软件工程", name: "软件工程", category: "Major", value: 618 }
    ],
    links: [
      { source: "city:上海", target: "role:算法工程师", name: "CITY_HAS_ROLE", value: 246, ratio: 0.042 },
      { source: "city:北京", target: "role:算法工程师", name: "CITY_HAS_ROLE", value: 228, ratio: 0.039 },
      { source: "city:深圳", target: "role:算法工程师", name: "CITY_HAS_ROLE", value: 214, ratio: 0.036 },
      { source: "role:算法工程师", target: "skill:Python", name: "ROLE_REQUIRES_SKILL", value: 426, ratio: 0.34 },
      { source: "role:算法工程师", target: "skill:机器学习", name: "ROLE_REQUIRES_SKILL", value: 385, ratio: 0.307 },
      { source: "role:算法工程师", target: "skill:PyTorch", name: "ROLE_REQUIRES_SKILL", value: 302, ratio: 0.241 },
      { source: "role:数据分析师", target: "skill:Python", name: "ROLE_REQUIRES_SKILL", value: 109, ratio: 0.316 },
      { source: "role:数据分析师", target: "skill:SQL", name: "ROLE_REQUIRES_SKILL", value: 96, ratio: 0.278 },
      { source: "role:Python开发工程师", target: "skill:Python", name: "ROLE_REQUIRES_SKILL", value: 88, ratio: 0.83 },
      { source: "role:算法工程师", target: "major:计算机科学与技术", name: "ROLE_REQUIRES_MAJOR", value: 742, ratio: 0.592 },
      { source: "role:算法工程师", target: "major:软件工程", name: "ROLE_REQUIRES_MAJOR", value: 618, ratio: 0.493 }
    ]
  }
};

export const sampleCityReports = [
  {
    name: "上海",
    total_jobs: 1069,
    avg_salary_mid: 18600,
    avg_annual_salary: 223200,
    salary_sample_count: 928,
    top_role: "算法工程师",
    top_skill: "Python",
    top_major: "计算机类",
    top_roles: [
      { role: "算法工程师", job_count: 246 },
      { role: "数据分析师", job_count: 138 },
      { role: "Python开发工程师", job_count: 82 }
    ],
    top_skills: [
      { skill: "Python", job_count: 318 },
      { skill: "SQL", job_count: 186 },
      { skill: "机器学习", job_count: 174 },
      { skill: "Spark", job_count: 119 }
    ]
  },
  {
    name: "北京",
    total_jobs: 971,
    avg_salary_mid: 19300,
    avg_annual_salary: 231600,
    salary_sample_count: 846,
    top_role: "算法工程师",
    top_skill: "机器学习",
    top_major: "计算机类",
    top_roles: [
      { role: "算法工程师", job_count: 228 },
      { role: "数据开发工程师", job_count: 121 },
      { role: "数据分析师", job_count: 104 }
    ],
    top_skills: [
      { skill: "机器学习", job_count: 292 },
      { skill: "Python", job_count: 274 },
      { skill: "PyTorch", job_count: 158 },
      { skill: "SQL", job_count: 145 }
    ]
  },
  {
    name: "深圳",
    total_jobs: 960,
    avg_salary_mid: 17850,
    avg_annual_salary: 214200,
    salary_sample_count: 813,
    top_role: "算法工程师",
    top_skill: "Python",
    top_major: "电子信息类",
    top_roles: [
      { role: "算法工程师", job_count: 214 },
      { role: "Python开发工程师", job_count: 132 },
      { role: "大数据开发工程师", job_count: 95 }
    ],
    top_skills: [
      { skill: "Python", job_count: 281 },
      { skill: "Linux", job_count: 154 },
      { skill: "C++", job_count: 141 },
      { skill: "Spark", job_count: 128 }
    ]
  }
];

export const forecastRows = [
  { month: "1月", actual: 42, predicted: null },
  { month: "2月", actual: 45, predicted: null },
  { month: "3月", actual: 48, predicted: null },
  { month: "4月", actual: 51, predicted: null },
  { month: "5月", actual: 49, predicted: null },
  { month: "6月", actual: 55, predicted: 55 },
  { month: "7月", actual: null, predicted: 58 },
  { month: "8月", actual: null, predicted: 61 },
  { month: "9月", actual: null, predicted: 63 }
];

export const qualityRows = [
  { name: "岗位名称", completeness: 0.98, valid: 0.96 },
  { name: "城市", completeness: 0.95, valid: 0.93 },
  { name: "岗位描述", completeness: 0.91, valid: 0.88 },
  { name: "薪资", completeness: 0.79, valid: 0.73 },
  { name: "发布时间", completeness: 0.84, valid: 0.8 },
  { name: "技能抽取", completeness: 0.89, valid: 0.86 }
];

export const sourceRows = [
  { name: "前程无忧", value: 3420 },
  { name: "国家大学生就业服务平台", value: 1590 },
  { name: "智联招聘", value: 847 }
];
