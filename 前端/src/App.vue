<template>
  <div class="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">技</span>
        <div>
          <strong>就业技能需求</strong>
          <span>Vue + ECharts</span>
        </div>
      </div>

      <nav class="nav-list">
        <button
          v-for="item in navItems"
          :key="item.key"
          class="nav-item"
          :class="{ active: activeTab === item.key }"
          type="button"
          @click="activeTab = item.key"
        >
          <span aria-hidden="true">{{ item.mark }}</span>
          {{ item.label }}
        </button>
      </nav>

      <section class="source-panel">
        <p class="panel-kicker">数据服务</p>
        <strong>{{ apiConnected ? "FastAPI 已连接" : "样例数据模式" }}</strong>
        <span>{{ apiConnected ? "图谱与岗位接口实时查询" : "后端不可用时用于演示" }}</span>
      </section>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div>
          <p class="crumb">{{ activeMeta.kicker }}</p>
          <h1>{{ activeMeta.title }}</h1>
          <p class="intro">{{ activeMeta.description }}</p>
        </div>

        <div class="api-state" :class="{ connected: apiConnected }">
          <span class="state-label">接口状态</span>
          <strong>{{ apiStatusText }}</strong>
        </div>
      </header>

      <section v-if="activeTab !== 'jobs'" class="filters" aria-label="筛选条件">
        <label class="control">
          <span>城市</span>
          <select v-model="selectedCity" @change="refreshByGlobalFilter">
            <option :value="ALL_CITIES">全部城市</option>
            <option v-for="city in cityOptions" :key="city.name" :value="city.name">{{ city.name }}</option>
          </select>
        </label>
        <label class="control">
          <span>岗位</span>
          <select v-model="selectedRole" @change="refreshByRole">
            <option v-for="role in roleOptions" :key="role.name" :value="role.name">{{ role.name }}</option>
          </select>
        </label>
        <label class="control">
          <span>技能</span>
          <select v-model="selectedSkill" @change="refreshBySkill">
            <option v-for="skill in skillOptions" :key="skill.skill || skill.name" :value="skill.skill || skill.name">
              {{ skill.skill || skill.name }}
            </option>
          </select>
        </label>
        <label class="control control-wide">
          <span>搜索</span>
          <input v-model.trim="jobKeyword" type="search" placeholder="搜索岗位、公司或描述" @keyup.enter="loadJobs(true)" />
        </label>
        <button class="refresh-button" type="button" @click="refreshAll">刷新</button>
      </section>

      <section v-if="activeTab === 'overview'" class="page-stack">
        <section class="role-detail-bar" aria-label="岗位类型概览">
          <div class="role-detail-copy">
            <span>当前岗位类型概览</span>
            <strong>{{ selectedRole }}</strong>
            <p>{{ roleSummaryText }}</p>
          </div>
          <dl class="role-metrics">
            <div>
              <dt>岗位样本</dt>
              <dd>{{ formatNumber(roleSummary.job_count) }}</dd>
            </div>
            <div>
              <dt>主要来源</dt>
              <dd>{{ compactText(roleSummary.top_source, "样例数据") }}</dd>
            </div>
            <div>
              <dt>月薪均值</dt>
              <dd>{{ salaryText(roleSummary.avg_salary_mid) }}</dd>
            </div>
            <div>
              <dt>热门城市</dt>
              <dd>{{ topCityName }}</dd>
            </div>
          </dl>
        </section>

        <section class="summary-strip" aria-label="核心指标">
          <article v-for="metric in overviewMetrics" :key="metric.label" class="summary-item">
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
          </article>
        </section>

        <section class="main-grid">
          <article class="panel graph-panel">
            <div class="panel-header">
              <div>
                <h2>岗位技能图谱</h2>
                <p>当前岗位与高频技能、专业的关系概览。</p>
              </div>
              <div class="chip-group">
                <button
                  v-for="role in roleOptions.slice(0, 5)"
                  :key="role.name"
                  class="chip"
                  :class="{ active: role.name === selectedRole }"
                  type="button"
                  @click="setRole(role.name)"
                >
                  {{ role.name }}
                </button>
              </div>
            </div>
            <div class="chart-wrap chart-large">
              <EChart :option="overviewGraphOption" aria-label="岗位技能关系图" @chart-click="handleGraphClick" />
            </div>
          </article>

          <aside class="panel insight-panel">
            <div class="panel-header compact">
              <div>
                <h2>{{ selectedSkill }}</h2>
                <p>技能详情</p>
              </div>
              <span class="sample-tag">{{ apiConnected ? "接口数据" : "样例数据" }}</span>
            </div>

            <div class="skill-meter">
              <span>需求占比</span>
              <strong>{{ ratioText(selectedSkillRow.ratio) }}</strong>
              <div class="inline-meter" aria-hidden="true">
                <span :style="{ width: meterWidth(selectedSkillRow.ratio) }"></span>
              </div>
            </div>

            <section class="detail-block">
              <h3>关联岗位</h3>
              <div class="role-list">
                <button
                  v-for="role in relatedRoles"
                  :key="role.role"
                  type="button"
                  @click="setRole(role.role)"
                >
                  <strong>{{ role.role }}</strong>
                  <span>{{ formatNumber(role.job_count) }} 条 · {{ ratioText(role.ratio) }}</span>
                </button>
              </div>
            </section>

            <section class="detail-block">
              <h3>证据片段</h3>
              <div class="evidence-list">
                <p v-for="item in evidenceList" :key="item">{{ item }}</p>
              </div>
            </section>
          </aside>

          <article class="panel trend-panel">
            <div class="panel-header compact">
              <div>
                <h2>需求趋势</h2>
                <p>{{ selectedSkill }} 近 6 个月热度变化。</p>
              </div>
            </div>
            <div class="chart-wrap chart-small">
              <EChart :option="trendOption" aria-label="技能需求趋势折线图" />
            </div>
          </article>

          <article class="panel city-panel">
            <div class="panel-header compact">
              <div>
                <h2>城市分布</h2>
                <p>{{ selectedRole }} 在核心城市的岗位规模。</p>
              </div>
            </div>
            <div class="chart-wrap chart-small">
              <EChart :option="cityDistributionOption" aria-label="城市岗位分布柱状图" />
            </div>
          </article>

          <article class="panel rank-panel">
            <div class="panel-header compact">
              <div>
                <h2>热门技能</h2>
                <p>按岗位样本数排序。</p>
              </div>
            </div>
            <div class="chart-wrap chart-small">
              <EChart :option="skillRankOption" aria-label="热门技能排行图" />
            </div>
          </article>
        </section>
      </section>

      <section v-else-if="activeTab === 'jobs'" class="jobs-table-page">
        <header class="job-table-title">
          <div>
            <span aria-hidden="true"></span>
            <h2>岗位列表</h2>
            <p>共 {{ formatNumber(jobTotal) }} 条</p>
          </div>
        </header>

        <section class="job-table-panel" aria-label="岗位列表">
          <div class="job-filter-grid">
            <label class="job-search-control">
              <span>关键词</span>
              <input v-model.trim="jobKeyword" type="search" placeholder="请输入关键词（岗位/公司/技能）" @keyup.enter="loadJobs(true)" />
            </label>
            <label class="job-filter-control">
              <span>城市</span>
              <select v-model="selectedCity" @change="loadJobs(true)">
                <option :value="ALL_CITIES">全部</option>
                <option v-for="city in allCityOptions" :key="city.name" :value="city.name">{{ city.name }}</option>
              </select>
            </label>
            <label class="job-filter-control">
              <span>工作经验</span>
              <select v-model="jobExperienceFilter" @change="loadJobs(true)">
                <option value="">全部</option>
                <option v-for="item in jobExperienceOptions" :key="item" :value="item">{{ item }}</option>
              </select>
            </label>
            <label class="job-filter-control">
              <span>学历要求</span>
              <select v-model="jobEducationFilter" @change="loadJobs(true)">
                <option value="">全部</option>
                <option v-for="item in jobEducationOptions" :key="item" :value="item">{{ item }}</option>
              </select>
            </label>
            <label class="job-filter-control">
              <span>公司规模</span>
              <select v-model="jobCompanySizeFilter" @change="loadJobs(true)">
                <option value="">全部</option>
                <option v-for="item in jobCompanySizeOptions" :key="item" :value="item">{{ item }}</option>
              </select>
            </label>
            <label class="job-filter-control">
              <span>行业</span>
              <select v-model="jobIndustryFilter" @change="loadJobs(true)">
                <option value="">全部</option>
                <option v-for="item in jobIndustryOptions" :key="item" :value="item">{{ item }}</option>
              </select>
            </label>
            <label class="job-salary-control">
              <span>年薪范围（万元）</span>
              <span class="salary-range-inputs">
                <input v-model.number="jobSalaryMinWan" type="number" min="0" placeholder="最低" @keyup.enter="loadJobs(true)" />
                <em>－</em>
                <input v-model.number="jobSalaryMaxWan" type="number" min="0" placeholder="最高" @keyup.enter="loadJobs(true)" />
              </span>
            </label>
            <button class="job-reset-button" type="button" @click="resetJobFilters">重置</button>
            <button class="job-search-button" type="button" @click="loadJobs(true)">搜索</button>
          </div>

          <div class="job-table-scroll">
            <table class="jobs-table">
              <thead>
                <tr>
                  <th>职位名称</th>
                  <th>公司名称</th>
                  <th>城市</th>
                  <th>薪资范围</th>
                  <th>学历要求</th>
                  <th>工作经验</th>
                  <th>公司类型</th>
                  <th>发布时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody v-if="jobs.length">
                <tr v-for="job in jobs" :key="job.record_id" :class="{ active: selectedJobId === job.record_id }">
                  <td>
                    <button class="job-name-link" type="button" @click="openJobDetail(job.record_id)">
                      {{ compactText(job.title, "未命名岗位") }}
                    </button>
                  </td>
                  <td>
                    <span class="table-ellipsis" :title="compactText(job.company, '--')">{{ compactText(job.company, "--") }}</span>
                  </td>
                  <td>{{ compactText(job.city, "--") }}</td>
                  <td class="salary-cell">{{ salaryRangeText(job) }}</td>
                  <td>{{ educationText(job.education, "--") }}</td>
                  <td>{{ compactText(job.experience, "--") }}</td>
                  <td>{{ compactText(job.company_type || job.source, "--") }}</td>
                  <td>{{ dateText(job.publish_date) }}</td>
                  <td>
                    <div class="job-row-actions">
                      <button type="button" @click="openJobDetail(job.record_id)">查看详情</button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-if="!jobs.length" class="empty-state">{{ apiConnected ? "暂无匹配岗位记录" : "后端不可用，当前显示样例记录" }}</p>
          </div>

          <footer class="job-table-footer">
            <span>第 {{ formatNumber(jobPage) }} / {{ formatNumber(jobPageCount) }} 页</span>
            <nav class="job-pagination" aria-label="岗位分页">
              <button type="button" :disabled="jobOffset <= 0" @click="changePage(-1)">上一页</button>
              <button
                v-for="page in jobVisiblePages"
                :key="page"
                type="button"
                :class="{ active: page === jobPage }"
                @click="goToJobPage(page)"
              >
                {{ page }}
              </button>
              <button type="button" :disabled="jobOffset + jobLimit >= jobTotal" @click="changePage(1)">下一页</button>
            </nav>
          </footer>
        </section>

        <div v-if="jobDetailOpen" class="job-detail-modal" role="dialog" aria-modal="true" aria-label="岗位详情" @click.self="closeJobDetail">
          <section class="job-detail-sheet">
            <div class="panel-header compact">
              <div>
                <h2>{{ activeJob ? compactText(activeJob.title, "岗位详情") : "岗位详情" }}</h2>
                <p>{{ activeJob ? [activeJob.company, activeJob.city, activeJob.source].filter(Boolean).join(" · ") : "选择岗位后显示详情" }}</p>
              </div>
              <div class="job-detail-actions">
                <a
                  v-if="activeJob && originalJobUrl(activeJob)"
                  class="source-link detail-source-link"
                  :href="originalJobUrl(activeJob)"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  前往岗位原网址
                </a>
                <button type="button" class="job-detail-close" @click="closeJobDetail">关闭</button>
              </div>
            </div>

            <template v-if="activeJob">
              <dl class="job-detail-grid">
                <div v-for="row in activeJobRows" :key="row.label">
                  <dt>{{ row.label }}</dt>
                  <dd>{{ row.value }}</dd>
                </div>
              </dl>

              <section class="job-detail-block">
                <h3>岗位描述</h3>
                <p>{{ compactText(activeJob.description, "暂无岗位描述") }}</p>
              </section>

              <section class="job-detail-block">
                <h3>技能与专业</h3>
                <div class="tag-cloud">
                  <span v-for="skill in activeJob.skills || []" :key="`skill-${skill}`">{{ skill }}</span>
                  <span v-for="major in activeJob.majors || []" :key="`major-${major}`" class="major-tag">{{ major }}</span>
                </div>
              </section>

              <section class="job-detail-block">
                <h3>该岗位局部图谱</h3>
                <div class="chart-wrap chart-medium">
                  <EChart :option="jobGraphOption" aria-label="岗位记录局部图谱" />
                </div>
              </section>
            </template>
          </section>
        </div>
      </section>

      <section v-else-if="activeTab === 'graph'" class="page-stack">
        <section class="summary-strip" aria-label="图谱指标">
          <article class="summary-item">
            <span>图谱岗位</span>
            <strong>{{ formatNumber(graphMetrics.total_jobs) }}</strong>
          </article>
          <article class="summary-item">
            <span>节点数量</span>
            <strong>{{ formatNumber(graphMetrics.node_count) }}</strong>
          </article>
          <article class="summary-item">
            <span>关系数量</span>
            <strong>{{ formatNumber(graphMetrics.link_count) }}</strong>
          </article>
          <article class="summary-item">
            <span>图谱焦点</span>
            <strong>{{ compactText(graphMetrics.top_skill || graphMetrics.top_role, "--") }}</strong>
          </article>
        </section>

        <section class="graph-analysis-grid">
          <article class="panel graph-main-panel">
            <div class="panel-header">
              <div>
                <h2>城市-岗位-技能-专业图谱</h2>
                <p>支持筛选后查看实体关系密度和核心连接。</p>
              </div>
            </div>
            <div class="chart-wrap graph-deep">
              <EChart :option="fullGraphOption" aria-label="综合技能图谱" @chart-click="handleGraphClick" />
            </div>

            <section class="graph-lower-grid" aria-label="图谱补充分析">
              <div class="graph-analysis-block">
                <div class="graph-analysis-heading">
                  <h3>关系类型分布</h3>
                  <span>{{ formatNumber(graphRelationRows.length) }} 类</span>
                </div>
                <div class="chart-wrap graph-mini-chart">
                  <EChart :option="graphRelationOption" aria-label="图谱关系类型分布图" />
                </div>
              </div>

              <div class="graph-analysis-block">
                <div class="graph-analysis-heading">
                  <h3>实体节点构成</h3>
                  <span>{{ formatNumber(graphNodeCompositionRows.length) }} 类</span>
                </div>
                <div class="chart-wrap graph-mini-chart">
                  <EChart :option="graphNodeCompositionOption" aria-label="图谱实体节点构成图" />
                </div>
              </div>

              <div class="graph-analysis-block">
                <div class="graph-analysis-heading">
                  <h3>连接强度排行</h3>
                  <span>Top {{ formatNumber(graphLinkRankRows.length) }}</span>
                </div>
                <div class="chart-wrap graph-mini-chart">
                  <EChart :option="graphLinkRankOption" aria-label="图谱连接强度排行图" />
                </div>
              </div>
            </section>
          </article>

          <aside class="panel">
            <div class="panel-header compact">
              <div>
                <h2>图谱桶</h2>
                <p>按实体类型聚合的前排节点。</p>
              </div>
            </div>
            <div class="bucket-list">
              <section v-for="bucket in graphBuckets" :key="bucket.label" class="bucket-section">
                <div class="bucket-section-header">
                  <h3>{{ bucket.label }}</h3>
                  <span>{{ formatNumber(bucket.rows.length) }} 项</span>
                </div>
                <div class="chart-wrap bucket-chart">
                  <EChart
                    :option="bucketChartOption(bucket)"
                    :aria-label="`${bucket.label}图谱桶排行图`"
                    @chart-click="handleBucketChartClick"
                  />
                </div>
                <div class="bucket-actions" aria-label="图谱桶节点列表">
                  <button v-for="row in bucket.rows.slice(0, 5)" :key="bucket.label + bucketName(row)" type="button" @click="applyBucket(bucket.type, row)">
                    <span>{{ bucketName(row) }}</span>
                    <strong>{{ formatNumber(row.job_count) }}</strong>
                  </button>
                </div>
              </section>
            </div>
          </aside>
        </section>
      </section>

      <section v-else-if="activeTab === 'city'" class="page-stack">
        <section class="filters subfilters" aria-label="城市对比条件">
          <label v-for="(_, index) in selectedCities" :key="index" class="control">
            <span>城市 {{ index + 1 }}</span>
            <select v-model="selectedCities[index]" @change="loadCityReports">
              <option v-for="city in allCityOptions" :key="city.name" :value="city.name">{{ city.name }}</option>
            </select>
          </label>
        </section>

        <section class="summary-strip" aria-label="城市对比指标">
          <article class="summary-item">
            <span>对比城市</span>
            <strong>{{ formatNumber(cityReports.length) }} 个</strong>
          </article>
          <article class="summary-item">
            <span>岗位合计</span>
            <strong>{{ formatNumber(cityJobTotal) }}</strong>
          </article>
          <article class="summary-item">
            <span>领先城市</span>
            <strong>{{ leadingCity?.name || "--" }}</strong>
          </article>
          <article class="summary-item">
            <span>平均月薪</span>
            <strong>{{ salaryText(avgCitySalary) }}</strong>
          </article>
        </section>

        <section class="chart-grid two-col">
          <article class="panel">
            <div class="panel-header compact">
              <div>
                <h2>岗位规模对比</h2>
                <p>城市样本量横向比较。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="cityScaleOption" aria-label="城市岗位规模图" />
            </div>
          </article>

          <article class="panel">
            <div class="panel-header compact">
              <div>
                <h2>薪资对比</h2>
                <p>月薪均值与年薪估算。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="salaryOption" aria-label="城市薪资对比图" />
            </div>
          </article>

          <article class="panel">
            <div class="panel-header compact">
              <div>
                <h2>技能热力</h2>
                <p>不同城市热门技能出现规模。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="skillHeatmapOption" aria-label="城市技能热力图" />
            </div>
          </article>

          <article class="panel">
            <div class="panel-header compact">
              <div>
                <h2>岗位结构</h2>
                <p>每个城市的岗位类别构成。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="roleMixOption" aria-label="城市岗位结构图" />
            </div>
          </article>
        </section>
      </section>

      <section v-else-if="activeTab === 'trend'" class="page-stack">
        <section class="summary-strip" aria-label="预测指标">
          <article class="summary-item">
            <span>预测对象</span>
            <strong>{{ selectedSkill }}</strong>
          </article>
          <article class="summary-item">
            <span>预测窗口</span>
            <strong>未来 3 月</strong>
          </article>
          <article class="summary-item">
            <span>基线 MAE</span>
            <strong>4.8</strong>
          </article>
          <article class="summary-item">
            <span>当前 MAE</span>
            <strong>3.1</strong>
          </article>
        </section>

        <section class="chart-grid two-col trend-grid">
          <article class="panel wide-panel">
            <div class="panel-header">
              <div>
                <h2>技能需求预测</h2>
                <p>历史实际值与未来 3 个月预测值分段展示。</p>
              </div>
            </div>
            <div class="chart-wrap chart-large">
              <EChart :option="forecastOption" aria-label="技能需求预测折线图" />
            </div>
          </article>

          <aside class="panel">
            <div class="panel-header compact">
              <div>
                <h2>预测解释</h2>
                <p>用于论文与答辩说明的可读摘要。</p>
              </div>
            </div>
            <div class="insight-stack">
              <p>系统使用月度需求序列构建预测样本，当前演示保留移动平均基线和趋势外推结果。</p>
              <p>生产环境可接入 Job-SDF 或自采多批次数据，统一展示 MAE、RMSE、SMAPE。</p>
              <p>技能需求占比比绝对岗位数更稳定，可减少采集规模波动造成的误判。</p>
            </div>
          </aside>
        </section>
      </section>

      <section v-else class="page-stack">
        <section class="summary-strip" aria-label="数据质量指标">
          <article class="summary-item">
            <span>字段完整率</span>
            <strong>89.3%</strong>
          </article>
          <article class="summary-item">
            <span>去重后样本</span>
            <strong>{{ formatNumber(5857) }}</strong>
          </article>
          <article class="summary-item">
            <span>技能覆盖率</span>
            <strong>90.8%</strong>
          </article>
          <article class="summary-item">
            <span>异常薪资</span>
            <strong>2.7%</strong>
          </article>
        </section>

        <section class="chart-grid two-col">
          <article class="panel">
            <div class="panel-header compact">
              <div>
                <h2>来源分布</h2>
                <p>多源招聘数据样本构成。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="sourceOption" aria-label="来源分布饼图" />
            </div>
          </article>

          <article class="panel">
            <div class="panel-header compact">
              <div>
                <h2>字段质量</h2>
                <p>完整率与有效率对比。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="qualityOption" aria-label="字段质量对比图" />
            </div>
          </article>

          <article class="panel wide-panel">
            <div class="panel-header compact">
              <div>
                <h2>质量雷达</h2>
                <p>核心字段对图谱与预测的支撑能力。</p>
              </div>
            </div>
            <div class="chart-wrap chart-medium">
              <EChart :option="qualityRadarOption" aria-label="质量雷达图" />
            </div>
          </article>
        </section>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import EChart from "./components/EChart.vue";
import { encodePath, fetchJson } from "./services/api";
import {
  ALL_CITIES,
  evidenceBySkill,
  forecastRows,
  qualityRows,
  sampleCities,
  sampleCityReports,
  sampleGraph,
  sampleJobPostings,
  sampleRoles,
  sampleSkills,
  skillsByRole,
  sourceRows,
  trendBySkill,
  trendMonths
} from "./data/sampleData";

const navItems = [
  { key: "overview", label: "总览", mark: "总", kicker: "岗位技能洞察", title: "就业技能需求可视化系统", description: "岗位、技能、城市、图谱与预测的一体化展示。" },
  { key: "jobs", label: "岗位查询", mark: "岗", kicker: "岗位明细", title: "岗位记录查询", description: "按岗位、城市、技能和关键词查询招聘记录。" },
  { key: "graph", label: "技能图谱", mark: "图", kicker: "知识图谱", title: "岗位-技能-专业关系图谱", description: "查看城市、岗位、技能、专业之间的图关系。" },
  { key: "city", label: "城市对比", mark: "城", kicker: "城市工作台", title: "城市岗位需求对比", description: "比较核心城市的岗位规模、技能热度和薪资水平。" },
  { key: "trend", label: "趋势预测", mark: "趋", kicker: "趋势预测", title: "技能需求趋势预测", description: "展示月度需求序列和未来 3 个月预测。" },
  { key: "quality", label: "数据质量", mark: "质", kicker: "质量报告", title: "采集与清洗质量监控", description: "展示字段完整性、来源构成和质量风险。" }
];

const activeTab = ref("overview");
const apiConnected = ref(false);
const apiStatusText = ref("检测中");
const overview = ref(null);
const roles = ref([...sampleRoles]);
const cities = ref([...sampleCities]);
const skills = ref([...sampleSkills]);
const selectedRole = ref(sampleRoles[0].name);
const selectedCity = ref(ALL_CITIES);
const selectedSkill = ref(skillsByRole[sampleRoles[0].name][0].skill);
const roleSkills = ref([...skillsByRole[sampleRoles[0].name]]);
const roleCities = ref([...sampleCities]);
const roleDetail = ref(null);
const roleGraph = ref(null);
const relatedRoles = ref([]);
const graphData = ref(sampleGraph);
const jobs = ref([...sampleJobPostings]);
const jobTotal = ref(sampleJobPostings.length);
const jobLimit = ref(10);
const jobOffset = ref(0);
const jobKeyword = ref("");
const jobExperienceFilter = ref("");
const jobEducationFilter = ref("");
const jobCompanySizeFilter = ref("");
const jobIndustryFilter = ref("");
const jobSalaryMinWan = ref("");
const jobSalaryMaxWan = ref("");
const selectedJobId = ref(sampleJobPostings[0].record_id);
const selectedJobDetail = ref(sampleJobPostings[0]);
const jobDetailOpen = ref(false);
const selectedCities = ref(["上海", "北京", "深圳"]);
const cityReports = ref([...sampleCityReports]);

const activeMeta = computed(() => navItems.find((item) => item.key === activeTab.value) || navItems[0]);
const roleOptions = computed(() => (roles.value.length ? roles.value : sampleRoles));
const allCityOptions = computed(() => normalizeCityOptions(cities.value.length ? cities.value : sampleCities));
const cityOptions = computed(() => normalizeCityOptions(roleCities.value.length ? roleCities.value : allCityOptions.value));
const skillOptions = computed(() => (roleSkills.value.length ? roleSkills.value : sampleSkills));

const roleSummary = computed(() => {
  return roleDetail.value?.summary || roleOptions.value.find((role) => role.name === selectedRole.value) || sampleRoles[0];
});

const roleSummaryText = computed(() => {
  const topSkills = roleSkills.value.slice(0, 3).map((item) => item.skill).join("、");
  return `${selectedRole.value} 当前高频技能集中在 ${topSkills || "Python、SQL"}，适合用于岗位能力画像和课程建设参考。`;
});

const topCityName = computed(() => roleDetail.value?.top_cities?.[0]?.city || roleCities.value[0]?.city || roleCities.value[0]?.name || "--");

const overviewMetrics = computed(() => {
  const nodeCounts = overview.value?.node_counts || [];
  const jobCount = countByLabel(nodeCounts, "JobPosting") || 5857;
  const skillCount = countByLabel(nodeCounts, "Skill") || 326;
  const links = overview.value?.totals?.relationships || 42180;

  return [
    { label: "有效岗位", value: formatNumber(jobCount) },
    { label: "技能节点", value: formatNumber(skillCount) },
    { label: "图谱关系", value: formatNumber(links) },
    { label: "技能覆盖率", value: "90.8%" }
  ];
});

const selectedSkillRow = computed(() => {
  return roleSkills.value.find((item) => item.skill === selectedSkill.value) || roleSkills.value[0] || { skill: selectedSkill.value, job_count: 0, ratio: 0 };
});

const evidenceList = computed(() => evidenceBySkill[selectedSkill.value] || evidenceBySkill.default);
const activeJob = computed(() => selectedJobDetail.value || jobs.value.find((job) => job.record_id === selectedJobId.value));

const activeJobRows = computed(() => {
  const job = activeJob.value || {};
  return [
    { label: "标准岗位", value: compactText(job.role) },
    { label: "城市", value: compactText(job.city) },
    { label: "薪资", value: compactText(job.salary) },
    { label: "月薪中位", value: salaryText(job.salary_mid) },
    { label: "学历", value: educationText(job.education) },
    { label: "经验", value: compactText(job.experience) },
    { label: "行业", value: compactText(job.industry) },
    { label: "公司类型", value: compactText(job.company_type) },
    { label: "公司规模", value: compactText(job.company_size) },
    { label: "发布日期", value: compactText(job.publish_date) }
  ];
});

const jobListCaption = computed(() => {
  const parts = [selectedRole.value, selectedCity.value === ALL_CITIES ? "全部城市" : selectedCity.value, selectedSkill.value].filter(Boolean);
  if (jobKeyword.value) parts.push(`"${jobKeyword.value}"`);
  return `${parts.join(" · ")}，共 ${formatNumber(jobTotal.value)} 条岗位记录。`;
});

const jobPageText = computed(() => {
  const start = jobTotal.value ? jobOffset.value + 1 : 0;
  const end = Math.min(jobOffset.value + jobLimit.value, jobTotal.value);
  return `${formatNumber(start)}-${formatNumber(end)} / ${formatNumber(jobTotal.value)}`;
});

const jobPage = computed(() => Math.floor(jobOffset.value / jobLimit.value) + 1);
const jobPageCount = computed(() => Math.max(1, Math.ceil(jobTotal.value / jobLimit.value)));
const jobVisiblePages = computed(() => {
  const count = jobPageCount.value;
  const current = jobPage.value;
  const start = Math.max(1, Math.min(current - 2, count - 4));
  const end = Math.min(count, start + 4);
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
});
const jobExperienceOptions = computed(() => jobOptionList("experience", ["经验不限", "1-3年", "3-5年", "5-10年"]));
const jobEducationOptions = computed(() => ["本科", "大专", "硕士", "博士", "学历不限"]);
const jobCompanySizeOptions = computed(() => jobOptionList("company_size", ["少于50人", "50-150人", "150-500人", "500-1000人", "1000-5000人"]));
const jobIndustryOptions = computed(() => jobOptionList("industry", ["互联网", "人工智能", "大数据", "软件服务", "智能制造"]));

const graphMetrics = computed(() => graphData.value?.metrics || sampleGraph.metrics);

const graphBuckets = computed(() => [
  { label: "城市", type: "city", rows: graphData.value?.top_cities || [] },
  { label: "岗位", type: "role", rows: graphData.value?.top_roles || [] },
  { label: "技能", type: "skill", rows: graphData.value?.top_skills || [] },
  { label: "专业", type: "major", rows: graphData.value?.top_majors || [] }
]);

const cityJobTotal = computed(() => cityReports.value.reduce((sum, city) => sum + (city.total_jobs || 0), 0));
const leadingCity = computed(() => [...cityReports.value].sort((a, b) => (b.total_jobs || 0) - (a.total_jobs || 0))[0]);
const avgCitySalary = computed(() => averageWeighted(cityReports.value, "avg_salary_mid", "salary_sample_count"));

const overviewGraphOption = computed(() => {
  const role = selectedRole.value;
  const roleNode = { id: `role:${role}`, name: role, category: "JobRole", value: roleSummary.value.job_count || roleSkills.value.length };
  const nodes = [roleNode];
  const links = [];

  roleSkills.value.slice(0, 10).forEach((item) => {
    nodes.push({ id: `skill:${item.skill}`, name: item.skill, category: "Skill", value: item.job_count });
    links.push({ source: roleNode.id, target: `skill:${item.skill}`, name: "ROLE_REQUIRES_SKILL", value: item.job_count, ratio: item.ratio });
  });

  if (roleDetail.value?.top_cities?.[0]?.city) {
    const city = roleDetail.value.top_cities[0];
    nodes.push({ id: `city:${city.city}`, name: city.city, category: "City", value: city.job_count });
    links.push({ source: `city:${city.city}`, target: roleNode.id, name: "CITY_HAS_ROLE", value: city.job_count });
  }

  return graphOptionFromRaw({ nodes, links, categories: [{ name: "City" }, { name: "JobRole" }, { name: "Skill" }] }, "force");
});

const trendOption = computed(() => {
  const values = trendBySkill[selectedSkill.value] || trendBySkill.default;
  return baseChartOption({
    tooltip: { trigger: "axis" },
    grid: { left: 42, right: 18, top: 26, bottom: 32 },
    xAxis: { type: "category", data: trendMonths, boundaryGap: false },
    yAxis: { type: "value", name: "热度" },
    series: [
      {
        name: selectedSkill.value,
        type: "line",
        smooth: true,
        symbolSize: 8,
        data: values,
        areaStyle: { opacity: 0.12 },
        lineStyle: { width: 3, color: "#167c69" },
        itemStyle: { color: "#167c69" }
      }
    ]
  });
});

const cityDistributionOption = computed(() => {
  const rows = roleCities.value.slice(0, 8);
  return baseChartOption({
    tooltip: { trigger: "axis" },
    grid: { left: 44, right: 20, top: 24, bottom: 36 },
    xAxis: { type: "category", data: rows.map(cityName) },
    yAxis: { type: "value" },
    series: [
      {
        type: "bar",
        data: rows.map((city) => city.job_count || 0),
        barWidth: 18,
        itemStyle: { color: "#315f8c", borderRadius: [4, 4, 0, 0] }
      }
    ]
  });
});

const skillRankOption = computed(() => {
  const rows = [...roleSkills.value].slice(0, 8).reverse();
  return baseChartOption({
    tooltip: { trigger: "axis" },
    grid: { left: 92, right: 24, top: 18, bottom: 24 },
    xAxis: { type: "value" },
    yAxis: { type: "category", data: rows.map((item) => item.skill) },
    series: [
      {
        type: "bar",
        data: rows.map((item) => item.job_count || 0),
        barWidth: 14,
        itemStyle: { color: "#8d4f70", borderRadius: [0, 4, 4, 0] }
      }
    ]
  });
});

const fullGraphOption = computed(() => graphOptionFromRaw(graphData.value?.graph || sampleGraph.graph, "force"));
const graphRelationRows = computed(() => relationRowsFromGraph());
const graphNodeCompositionRows = computed(() => nodeCompositionRowsFromGraph());
const graphLinkRankRows = computed(() => linkRankRowsFromGraph());

const graphRelationOption = computed(() => {
  const rows = graphRelationRows.value;
  return baseChartOption({
    color: chartPalette,
    tooltip: {
      trigger: "item",
      formatter: (params) => `${params.name}<br/>${formatNumber(params.value || 0)} 条 · ${params.percent || 0}%`
    },
    legend: {
      bottom: 0,
      type: "scroll",
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: "#536059", fontSize: 11 }
    },
    series: [
      {
        name: "关系类型",
        type: "pie",
        radius: ["48%", "72%"],
        center: ["50%", "43%"],
        data: rows,
        label: { color: "#38433d", fontSize: 11, formatter: "{b}\n{d}%" },
        itemStyle: { borderColor: "#ffffff", borderWidth: 2 }
      }
    ]
  });
});

const graphNodeCompositionOption = computed(() => {
  const rows = graphNodeCompositionRows.value;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params) => {
        const item = params?.[0]?.data || {};
        return `${item.name || params?.[0]?.name}<br/>${formatNumber(item.value || 0)} 个节点`;
      }
    },
    grid: { left: 34, right: 18, top: 24, bottom: 34 },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.name),
      axisTick: { show: false },
      axisLabel: { interval: 0, color: "#38433d", fontSize: 11 }
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#536059", fontSize: 11 },
      splitLine: { lineStyle: { color: "#e7eeeb" } }
    },
    series: [
      {
        name: "节点数量",
        type: "bar",
        barWidth: 20,
        data: rows.map((row, index) => ({
          name: row.name,
          value: row.value,
          itemStyle: { color: chartPalette[index % chartPalette.length], borderRadius: [4, 4, 0, 0] }
        })),
        label: {
          show: true,
          position: "top",
          color: "#536059",
          fontSize: 11,
          formatter: (params) => formatNumber(params.value || 0)
        }
      }
    ]
  });
});

const graphLinkRankOption = computed(() => {
  const rows = [...graphLinkRankRows.value].reverse();
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params) => {
        const item = params?.[0]?.data || {};
        return `${item.fullName || params?.[0]?.name}<br/>${item.relation || "关系"} · ${formatNumber(item.value || 0)} 条`;
      }
    },
    grid: { left: 90, right: 34, top: 18, bottom: 24 },
    xAxis: {
      type: "value",
      axisLabel: { show: false },
      splitLine: { lineStyle: { color: "#e7eeeb" } }
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => row.name),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: "#38433d", fontSize: 11 }
    },
    series: [
      {
        name: "连接强度",
        type: "bar",
        barWidth: 12,
        data: rows.map((row) => ({
          value: row.value,
          name: row.name,
          fullName: row.fullName,
          relation: row.relation
        })),
        label: {
          show: true,
          position: "right",
          color: "#536059",
          fontSize: 11,
          formatter: (params) => formatNumber(params.value || 0)
        },
        itemStyle: { color: "#167c69", borderRadius: [0, 4, 4, 0] }
      }
    ]
  });
});

function bucketChartOption(bucket) {
  const rows = normalizedBucketRows(bucket).reverse();
  const color = bucketColor(bucket.type);
  return baseChartOption({
    color: [color],
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params) => {
        const item = params?.[0]?.data || {};
        return `${item.rawName || item.name}<br/>${formatNumber(item.value || 0)} 条`;
      }
    },
    grid: { left: 78, right: 18, top: 10, bottom: 14 },
    xAxis: {
      type: "value",
      axisLabel: { show: false },
      splitLine: { lineStyle: { color: "#e7eeeb" } }
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => row.name),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: {
        color: "#38433d",
        fontSize: 12,
        formatter: shortAxisLabel
      }
    },
    series: [
      {
        name: bucket.label,
        type: "bar",
        barWidth: 10,
        data: rows.map((row) => ({
          value: row.value,
          name: row.name,
          rawName: row.rawName,
          bucketType: bucket.type
        })),
        label: {
          show: true,
          position: "right",
          color: "#536059",
          fontSize: 11,
          formatter: (params) => formatNumber(params.value || 0)
        },
        itemStyle: { color, borderRadius: [0, 4, 4, 0] }
      }
    ]
  });
}

const jobGraphOption = computed(() => {
  const job = activeJob.value;
  if (!job) return graphOptionFromRaw({ nodes: [], links: [], categories: [] });
  const center = { id: `job:${job.record_id}`, name: job.title || "岗位记录", category: "JobPosting", value: 1 };
  const nodes = [center];
  const links = [];
  const pushNode = (category, name, label, value = 1) => {
    if (!name) return;
    const id = `${category}:${name}`;
    nodes.push({ id, name, category, value, label });
    links.push({ source: center.id, target: id, value });
  };
  pushNode("JobRole", job.role, "标准岗位");
  pushNode("City", job.city, "城市");
  (job.skills || []).slice(0, 8).forEach((skill) => pushNode("Skill", skill, "技能"));
  (job.majors || []).slice(0, 5).forEach((major) => pushNode("Major", major, "专业"));
  return graphOptionFromRaw({ nodes, links, categories: [{ name: "JobPosting" }, { name: "JobRole" }, { name: "City" }, { name: "Skill" }, { name: "Major" }] }, "circular");
});

const cityScaleOption = computed(() => {
  return baseChartOption({
    tooltip: { trigger: "axis" },
    grid: { left: 48, right: 20, top: 28, bottom: 34 },
    xAxis: { type: "category", data: cityReports.value.map((city) => city.name) },
    yAxis: { type: "value" },
    series: [
      {
        type: "bar",
        data: cityReports.value.map((city) => city.total_jobs || 0),
        barWidth: 24,
        itemStyle: { color: "#167c69", borderRadius: [4, 4, 0, 0] }
      }
    ]
  });
});

const salaryOption = computed(() => {
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const cityName = params?.[0]?.axisValue || "";
        const rows = (params || []).map((item) => {
          const unit = item.seriesName === "年薪估算" ? "元/年" : "元/月";
          return `${item.marker}${item.seriesName}：${formatNumber(item.value || 0)} ${unit}`;
        });
        return [cityName, ...rows].join("<br/>");
      }
    },
    legend: { top: 0, right: 0 },
    grid: { left: 58, right: 76, top: 48, bottom: 34 },
    xAxis: { type: "category", data: cityReports.value.map((city) => city.name) },
    yAxis: [
      {
        type: "value",
        name: "元/月",
        axisLabel: { formatter: (value) => formatNumber(value) }
      },
      {
        type: "value",
        name: "元/年",
        axisLabel: { formatter: (value) => `${formatNumber(value / 10000)}万` },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: "月薪均值",
        type: "bar",
        yAxisIndex: 0,
        data: cityReports.value.map((city) => city.avg_salary_mid || 0),
        itemStyle: { color: "#315f8c", borderRadius: [4, 4, 0, 0] }
      },
      {
        name: "年薪估算",
        type: "line",
        yAxisIndex: 1,
        data: cityReports.value.map(annualSalaryValue),
        smooth: true,
        lineStyle: { width: 3, color: "#9a6a1e" },
        itemStyle: { color: "#9a6a1e" }
      }
    ]
  });
});

const skillHeatmapOption = computed(() => {
  const skillNames = topSkillRows();
  const cityNames = cityReports.value.map((city) => city.name);
  const data = [];
  cityReports.value.forEach((city, x) => {
    skillNames.forEach((skill, y) => {
      const row = (city.top_skills || []).find((item) => skillName(item) === skill);
      data.push([x, y, row?.job_count || 0]);
    });
  });

  return baseChartOption({
    tooltip: { position: "top" },
    grid: { left: 92, right: 28, top: 24, bottom: 34 },
    xAxis: { type: "category", data: cityNames, splitArea: { show: true } },
    yAxis: { type: "category", data: skillNames, splitArea: { show: true } },
    visualMap: {
      min: 0,
      max: Math.max(...data.map((item) => item[2]), 1),
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: ["#eef5f2", "#8fb9ad", "#167c69"] }
    },
    series: [{ type: "heatmap", data, label: { show: true }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,0.18)" } } }]
  });
});

const roleMixOption = computed(() => {
  const cityNames = cityReports.value.map((city) => city.name);
  const roleNames = [...new Set(cityReports.value.flatMap((city) => (city.top_roles || []).map(roleName)))].slice(0, 5);
  return baseChartOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, type: "scroll" },
    grid: { left: 46, right: 18, top: 50, bottom: 34 },
    xAxis: { type: "category", data: cityNames },
    yAxis: { type: "value" },
    series: roleNames.map((name, index) => ({
      name,
      type: "bar",
      stack: "role",
      data: cityReports.value.map((city) => (city.top_roles || []).find((item) => roleName(item) === name)?.job_count || 0),
      itemStyle: { color: chartPalette[index % chartPalette.length] }
    }))
  });
});

const forecastOption = computed(() => {
  return baseChartOption({
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 0 },
    grid: { left: 52, right: 28, top: 48, bottom: 42 },
    xAxis: { type: "category", data: forecastRows.map((row) => row.month), boundaryGap: false },
    yAxis: { type: "value", name: "需求热度" },
    series: [
      {
        name: "实际值",
        type: "line",
        data: forecastRows.map((row) => row.actual),
        smooth: true,
        symbolSize: 8,
        lineStyle: { width: 3, color: "#167c69" },
        itemStyle: { color: "#167c69" }
      },
      {
        name: "预测值",
        type: "line",
        data: forecastRows.map((row) => row.predicted),
        smooth: true,
        symbolSize: 8,
        lineStyle: { width: 3, type: "dashed", color: "#9a6a1e" },
        itemStyle: { color: "#9a6a1e" },
        areaStyle: { opacity: 0.1 }
      }
    ]
  });
});

const sourceOption = computed(() => {
  return baseChartOption({
    tooltip: { trigger: "item" },
    legend: { bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["46%", "70%"],
        center: ["50%", "45%"],
        data: sourceRows,
        label: { formatter: "{b}\n{d}%" },
        itemStyle: { borderColor: "#fff", borderWidth: 2 },
        color: chartPalette
      }
    ]
  });
});

const qualityOption = computed(() => {
  return baseChartOption({
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 0 },
    grid: { left: 86, right: 20, top: 44, bottom: 34 },
    xAxis: { type: "value", max: 1, axisLabel: { formatter: (value) => `${Math.round(value * 100)}%` } },
    yAxis: { type: "category", data: qualityRows.map((row) => row.name) },
    series: [
      {
        name: "完整率",
        type: "bar",
        data: qualityRows.map((row) => row.completeness),
        itemStyle: { color: "#167c69", borderRadius: [0, 4, 4, 0] }
      },
      {
        name: "有效率",
        type: "bar",
        data: qualityRows.map((row) => row.valid),
        itemStyle: { color: "#315f8c", borderRadius: [0, 4, 4, 0] }
      }
    ]
  });
});

const qualityRadarOption = computed(() => {
  return baseChartOption({
    tooltip: {},
    radar: {
      indicator: qualityRows.map((row) => ({ name: row.name, max: 1 })),
      radius: "64%",
      splitArea: { areaStyle: { color: ["#f7faf8", "#eef5f2"] } }
    },
    series: [
      {
        type: "radar",
        data: [
          {
            value: qualityRows.map((row) => row.completeness),
            name: "完整率",
            areaStyle: { opacity: 0.16 },
            lineStyle: { width: 3, color: "#167c69" },
            itemStyle: { color: "#167c69" }
          }
        ]
      }
    ]
  });
});

const chartPalette = ["#167c69", "#315f8c", "#9a6a1e", "#8d4f70", "#6f6aa7", "#d16645", "#4b8063"];

onMounted(async () => {
  await refreshAll();
});

async function refreshAll() {
  await loadDictionaries();
  await Promise.all([loadRoleData(), loadGraphData(), loadJobs(true), loadCityReports()]);
}

async function loadDictionaries() {
  apiStatusText.value = "检测中";
  try {
    const [overviewData, roleData, cityData, skillData] = await Promise.all([
      fetchJson("/api/overview"),
      fetchJson("/api/job-roles?limit=200"),
      fetchJson("/api/cities?limit=200"),
      fetchJson("/api/skills?limit=200")
    ]);

    overview.value = overviewData;
    roles.value = roleData.items?.length ? roleData.items : sampleRoles;
    cities.value = cityData.items?.length ? cityData.items : sampleCities;
    skills.value = skillData.items?.length ? skillData.items : sampleSkills;
    apiConnected.value = true;
    apiStatusText.value = "已连接后端";
    if (!roles.value.some((role) => role.name === selectedRole.value)) {
      selectedRole.value = roles.value[0]?.name || sampleRoles[0].name;
    }
  } catch {
    overview.value = null;
    roles.value = [...sampleRoles];
    cities.value = [...sampleCities];
    skills.value = [...sampleSkills];
    apiConnected.value = false;
    apiStatusText.value = "使用样例数据";
  }
}

async function loadRoleData() {
  ensureSelectedCity();

  if (!apiConnected.value) {
    loadSampleRoleData();
    return;
  }

  try {
    const role = encodePath(selectedRole.value);
    const cityFiltered = selectedCity.value !== ALL_CITIES;
    const skillsPath = cityFiltered
      ? `/api/job-roles/${role}/cities/${encodePath(selectedCity.value)}/skills?limit=12`
      : `/api/job-roles/${role}/skills?limit=12`;

    const [cityData, skillData, detailData, graphResponse] = await Promise.all([
      fetchJson(`/api/job-roles/${role}/cities?limit=12`),
      fetchJson(skillsPath),
      fetchJson(`/api/job-roles/${role}/detail`).catch(() => null),
      fetchJson(`/api/graphs/job-role/${role}?limit=18`).catch(() => null)
    ]);

    roleCities.value = cityData.items?.length ? cityData.items : cities.value;
    roleSkills.value = skillData.items?.length ? skillData.items : skillsByRole[selectedRole.value] || sampleSkills.map((item) => ({ skill: item.name, job_count: item.job_count, ratio: 0.1 }));
    roleDetail.value = detailData;
    roleGraph.value = graphResponse?.graph || null;

    if (!roleSkills.value.some((item) => item.skill === selectedSkill.value)) {
      selectedSkill.value = roleSkills.value[0]?.skill || skills.value[0]?.name || "Python";
    }

    await loadRelatedRoles();
  } catch {
    loadSampleRoleData();
  }
}

function loadSampleRoleData() {
  roleCities.value = [...sampleCities];
  roleSkills.value = skillsByRole[selectedRole.value] || skillsByRole[sampleRoles[0].name];
  roleDetail.value = {
    summary: roleOptions.value.find((role) => role.name === selectedRole.value) || sampleRoles[0],
    top_cities: sampleCities.slice(0, 5).map((city) => ({ city: city.name, job_count: city.job_count })),
    top_skills: roleSkills.value.slice(0, 6)
  };
  roleGraph.value = null;

  if (!roleSkills.value.some((item) => item.skill === selectedSkill.value)) {
    selectedSkill.value = roleSkills.value[0]?.skill || "Python";
  }
  relatedRoles.value = sampleRelatedRoles();
}

async function loadRelatedRoles() {
  if (!apiConnected.value) {
    relatedRoles.value = sampleRelatedRoles();
    return;
  }

  try {
    const data = await fetchJson(`/api/skill-job-roles?skill=${encodePath(selectedSkill.value)}&limit=6`);
    relatedRoles.value = data.items?.length ? data.items : sampleRelatedRoles();
  } catch {
    relatedRoles.value = sampleRelatedRoles();
  }
}

function sampleRelatedRoles() {
  return roleOptions.value.slice(0, 5).map((role, index) => ({
    role: role.name,
    job_count: Math.max(8, Math.round((role.job_count || 40) * (0.16 - index * 0.02))),
    ratio: Math.max(0.05, (selectedSkillRow.value.ratio || 0.24) - index * 0.025)
  }));
}

async function loadGraphData() {
  if (!apiConnected.value) {
    graphData.value = sampleGraph;
    return;
  }

  const params = new URLSearchParams({ limit: "80" });
  if (selectedRole.value) params.set("role", selectedRole.value);
  if (selectedCity.value !== ALL_CITIES) params.set("city", selectedCity.value);
  if (selectedSkill.value) params.set("skill", selectedSkill.value);

  try {
    graphData.value = await fetchJson(`/api/graphs/skill?${params.toString()}`);
  } catch {
    graphData.value = sampleGraph;
  }
}

async function loadJobs(reset = false) {
  if (reset) jobOffset.value = 0;

  if (!apiConnected.value) {
    const keyword = jobKeyword.value.toLowerCase();
    const filtered = sampleJobPostings.filter((job) => {
      const cityOk = selectedCity.value === ALL_CITIES || job.city === selectedCity.value;
      const experienceOk = !jobExperienceFilter.value || job.experience === jobExperienceFilter.value;
      const educationOk = educationMatches(job.education, jobEducationFilter.value);
      const companySizeOk = !jobCompanySizeFilter.value || job.company_size === jobCompanySizeFilter.value;
      const industryOk = !jobIndustryFilter.value || job.industry === jobIndustryFilter.value;
      const annualWan = annualSalaryValue(job) / 10000;
      const minOk = !Number(jobSalaryMinWan.value || 0) || annualWan >= Number(jobSalaryMinWan.value || 0);
      const maxOk = !Number(jobSalaryMaxWan.value || 0) || annualWan <= Number(jobSalaryMaxWan.value || 0);
      const keywordOk =
        !keyword ||
        [job.title, job.company, job.description, ...(job.skills || [])].some((value) => `${value || ""}`.toLowerCase().includes(keyword));
      return cityOk && experienceOk && educationOk && companySizeOk && industryOk && minOk && maxOk && keywordOk;
    });
    jobs.value = filtered.slice(jobOffset.value, jobOffset.value + jobLimit.value);
    jobTotal.value = filtered.length;
    if (!jobs.value.some((job) => job.record_id === selectedJobId.value)) {
      selectedJobId.value = "";
      selectedJobDetail.value = null;
      jobDetailOpen.value = false;
    }
    return;
  }

  const params = new URLSearchParams({
    limit: String(jobLimit.value),
    offset: String(jobOffset.value)
  });
  if (selectedCity.value !== ALL_CITIES) params.set("city", selectedCity.value);
  if (jobKeyword.value) params.set("q", jobKeyword.value);
  if (jobExperienceFilter.value) params.set("experience", jobExperienceFilter.value);
  if (jobEducationFilter.value) params.set("education", jobEducationFilter.value);
  if (jobCompanySizeFilter.value) params.set("company_size", jobCompanySizeFilter.value);
  if (jobIndustryFilter.value) params.set("industry", jobIndustryFilter.value);
  if (Number(jobSalaryMinWan.value || 0)) params.set("salary_min_wan", String(Number(jobSalaryMinWan.value)));
  if (Number(jobSalaryMaxWan.value || 0)) params.set("salary_max_wan", String(Number(jobSalaryMaxWan.value)));

  try {
    const data = await fetchJson(`/api/job-postings?${params.toString()}`);
    jobs.value = data.items || [];
    jobTotal.value = data.total || 0;
    if (!jobs.value.some((job) => job.record_id === selectedJobId.value)) {
      selectedJobId.value = "";
      selectedJobDetail.value = null;
      jobDetailOpen.value = false;
    }
  } catch {
    jobs.value = [];
    jobTotal.value = 0;
    selectedJobId.value = "";
    selectedJobDetail.value = null;
  }
}

async function selectJob(recordId) {
  selectedJobId.value = recordId;

  const sample = jobs.value.find((job) => job.record_id === recordId);
  if (!apiConnected.value) {
    selectedJobDetail.value = sample || null;
    return;
  }

  try {
    selectedJobDetail.value = await fetchJson(`/api/job-postings/${encodePath(recordId)}`);
  } catch {
    selectedJobDetail.value = sample || null;
  }
}

async function openJobDetail(recordId) {
  await selectJob(recordId);
  jobDetailOpen.value = true;
}

function closeJobDetail() {
  jobDetailOpen.value = false;
}

async function goToJobPage(page) {
  const nextPage = Math.max(1, Math.min(page, jobPageCount.value));
  jobOffset.value = (nextPage - 1) * jobLimit.value;
  await loadJobs(false);
}

async function resetJobFilters() {
  jobKeyword.value = "";
  selectedCity.value = ALL_CITIES;
  jobExperienceFilter.value = "";
  jobEducationFilter.value = "";
  jobCompanySizeFilter.value = "";
  jobIndustryFilter.value = "";
  jobSalaryMinWan.value = "";
  jobSalaryMaxWan.value = "";
  await loadJobs(true);
}

async function loadCityReports() {
  const uniqueCities = [...new Set(selectedCities.value.filter(Boolean))];
  if (!uniqueCities.length) {
    cityReports.value = [];
    return;
  }

  if (!apiConnected.value) {
    cityReports.value = uniqueCities.map(sampleCityReport);
    return;
  }

  const reports = await Promise.all(
    uniqueCities.map(async (city) => {
      const params = new URLSearchParams({ city, limit: "80" });
      if (selectedRole.value) params.set("role", selectedRole.value);
      try {
        const data = await fetchJson(`/api/graphs/skill?${params.toString()}`);
        return normalizeCityReport(city, data);
      } catch {
        return sampleCityReport(city);
      }
    })
  );
  cityReports.value = reports;
}

function normalizeCityReport(city, data) {
  const metrics = data.metrics || {};
  return {
    name: city,
    total_jobs: metrics.total_jobs || 0,
    avg_salary_mid: metrics.avg_salary_mid || 0,
    avg_annual_salary: metrics.avg_annual_salary || 0,
    salary_sample_count: metrics.salary_sample_count || 0,
    top_role: metrics.top_role || data.top_roles?.[0]?.role || "",
    top_skill: metrics.top_skill || data.top_skills?.[0]?.skill || "",
    top_major: metrics.top_major || data.top_majors?.[0]?.major || "",
    top_roles: data.top_roles || [],
    top_skills: data.top_skills || [],
    top_majors: data.top_majors || []
  };
}

function sampleCityReport(city) {
  return sampleCityReports.find((item) => item.name === city) || {
    ...sampleCityReports[0],
    name: city,
    total_jobs: Math.max(30, Math.round(sampleCityReports[0].total_jobs * 0.36))
  };
}

async function refreshByRole() {
  ensureSelectedCity();
  await Promise.all([loadRoleData(), loadGraphData(), loadJobs(true), loadCityReports()]);
}

async function refreshBySkill() {
  await Promise.all([loadRelatedRoles(), loadGraphData(), loadJobs(true)]);
}

async function refreshByGlobalFilter() {
  ensureSelectedCity();
  await Promise.all([loadRoleData(), loadGraphData(), loadJobs(true), loadCityReports()]);
}

async function setRole(role) {
  selectedRole.value = role;
  await refreshByRole();
}

function handleGraphClick(event) {
  const data = event?.data || {};
  if (data.categoryKey === "Skill" && data.displayName) {
    selectedSkill.value = data.displayName;
    refreshBySkill();
  }
  if (data.categoryKey === "JobRole" && data.displayName) {
    setRole(data.displayName);
  }
}

function handleBucketChartClick(event) {
  const data = event?.data || {};
  if (!data.bucketType || !data.rawName) return;
  applyBucket(data.bucketType, bucketRowFromChart(data.bucketType, data.rawName, data.value));
}

function applyBucket(type, row) {
  const name = bucketName(row);
  if (!name) return;
  if (type === "city") selectedCity.value = name;
  if (type === "role") selectedRole.value = name;
  if (type === "skill") selectedSkill.value = name;
  refreshByGlobalFilter();
}

async function changePage(direction) {
  const next = jobOffset.value + direction * jobLimit.value;
  jobOffset.value = Math.max(0, next);
  await loadJobs(false);
}

function currentGraphRaw() {
  return graphData.value?.graph || sampleGraph.graph || { nodes: [], links: [] };
}

function relationRowsFromGraph() {
  const totals = new Map();
  (currentGraphRaw().links || []).forEach((link) => {
    const name = relationLabel(link.name);
    totals.set(name, (totals.get(name) || 0) + Number(link.value || 0));
  });
  return sortedRowsFromMap(totals);
}

function nodeCompositionRowsFromGraph() {
  const totals = new Map();
  (currentGraphRaw().nodes || []).forEach((node) => {
    const name = categoryLabel(node.category);
    totals.set(name, (totals.get(name) || 0) + 1);
  });
  return sortedRowsFromMap(totals);
}

function linkRankRowsFromGraph() {
  const graph = currentGraphRaw();
  const nodeNames = new Map((graph.nodes || []).map((node) => [node.id || `${node.category}:${node.name}`, node.name]));
  return (graph.links || [])
    .map((link) => {
      const sourceName = nodeNames.get(link.source) || link.source;
      const targetName = nodeNames.get(link.target) || link.target;
      const fullName = `${sourceName} → ${targetName}`;
      return {
        name: shortGraphLabel(fullName),
        fullName,
        relation: relationLabel(link.name),
        value: Number(link.value || 0)
      };
    })
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value || a.fullName.localeCompare(b.fullName, "zh-CN"))
    .slice(0, 7);
}

function sortedRowsFromMap(map) {
  return [...map.entries()]
    .map(([name, value]) => ({ name, value }))
    .filter((row) => row.name && row.value > 0)
    .sort((a, b) => b.value - a.value || a.name.localeCompare(b.name, "zh-CN"));
}

function relationLabel(value) {
  return {
    CITY_HAS_ROLE: "城市-岗位",
    ROLE_REQUIRES_SKILL: "岗位-技能",
    ROLE_REQUIRES_MAJOR: "岗位-专业"
  }[value] || value || "关系";
}

function categoryLabel(value) {
  return {
    City: "城市",
    JobRole: "岗位",
    Skill: "技能",
    Major: "专业",
    JobPosting: "岗位记录"
  }[value] || value || "未分类";
}

function graphOptionFromRaw(rawGraph, layout = "force") {
  const categories = [
    { key: "City", name: "城市", color: "#9a6a1e" },
    { key: "JobRole", name: "岗位", color: "#167c69" },
    { key: "Skill", name: "技能", color: "#315f8c" },
    { key: "Major", name: "专业", color: "#8d4f70" },
    { key: "JobPosting", name: "岗位记录", color: "#6f6aa7" }
  ];
  const categoryIndex = new Map(categories.map((item, index) => [item.key, index]));
  const nodes = (rawGraph?.nodes || []).map((node) => ({
    id: node.id || `${node.category}:${node.name}`,
    name: node.id || `${node.category}:${node.name}`,
    displayName: node.name,
    category: categoryIndex.get(node.category) ?? 0,
    categoryKey: node.category,
    value: node.value || 1,
    symbolSize: node.symbolSize || Math.max(30, Math.min(72, 28 + Math.sqrt(node.value || 1) * 2.4)),
    label: { show: true }
  }));

  return baseChartOption({
    color: categories.map((item) => item.color),
    tooltip: {
      formatter: (params) => {
        if (params.dataType === "edge") {
          const data = params.data || {};
          return `${data.name || "关系"}<br/>${formatNumber(data.value || 0)} 条`;
        }
        return `${params.data?.displayName || params.name}<br/>${formatNumber(params.data?.value || 0)} 条`;
      }
    },
    legend: { top: 0, data: categories.map((item) => item.name) },
    series: [
      {
        type: "graph",
        layout,
        top: 34,
        bottom: 12,
        roam: true,
        draggable: true,
        categories: categories.map((item) => ({ name: item.name })),
        data: nodes,
        links: (rawGraph?.links || []).map((link) => ({
          source: link.source,
          target: link.target,
          name: link.name,
          value: link.value || 1,
          ratio: link.ratio
        })),
        force: { repulsion: 260, edgeLength: [88, 180], gravity: 0.08 },
        circular: { rotateLabel: false },
        label: {
          show: true,
          formatter: (params) => params.data.displayName,
          fontSize: 12,
          color: "#20302b"
        },
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 7,
        lineStyle: { color: "source", opacity: 0.45, width: 1.4, curveness: 0.08 },
        emphasis: { focus: "adjacency", lineStyle: { width: 3, opacity: 0.86 } }
      }
    ]
  });
}

function baseChartOption(option) {
  return {
    textStyle: {
      fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      color: "#1f2420"
    },
    aria: { enabled: true },
    ...option
  };
}

function countByLabel(items, label) {
  return items?.find((item) => item.label === label)?.count || 0;
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Math.round(Number(value || 0)));
}

function ratioText(value) {
  return `${((value || 0) * 100).toFixed(1)}%`;
}

function salaryText(value) {
  const number = Number(value || 0);
  return number ? `${formatNumber(number)} 元/月` : "--";
}

function annualSalaryValue(city) {
  const annual = Number(city?.avg_annual_salary || city?.annual_salary_estimated || 0);
  if (annual > 0) return Math.round(annual);
  return Math.round(Number(city?.avg_salary_mid || city?.salary_mid || 0) * 12);
}

function salaryRangeText(job) {
  const text = compactText(job?.salary, "");
  if (text) return text;
  const mid = Number(job?.salary_mid || 0);
  return mid ? salaryText(mid) : "--";
}

function dateText(value) {
  const text = `${value ?? ""}`.trim();
  return text ? text.slice(0, 10) : "--";
}

function jobOptionList(key, fallback = []) {
  const values = new Set(fallback);
  jobs.value.forEach((job) => {
    const value = `${job?.[key] || ""}`.trim();
    if (value) values.add(value);
  });
  return [...values].filter(Boolean).sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function normalizeEducation(value) {
  const text = `${value ?? ""}`.trim();
  return text === "不限" ? "学历不限" : text;
}

function educationText(value, fallback = "未标注") {
  return normalizeEducation(value) || fallback;
}

function educationMatches(jobEducation, filterEducation) {
  const filter = normalizeEducation(filterEducation);
  if (!filter) return true;
  return normalizeEducation(jobEducation) === filter;
}

function compactText(value, fallback = "未标注") {
  const text = `${value ?? ""}`.trim();
  return text || fallback;
}

function meterWidth(value) {
  return `${Math.max(6, Math.min(100, (value || 0) * 100))}%`;
}

function cityName(row) {
  return row?.city || row?.name || "";
}

function normalizeCityOptions(rows = []) {
  const seen = new Set();
  return rows
    .map((row) => ({ ...row, name: cityName(row).trim() }))
    .filter((row) => {
      if (!row.name || seen.has(row.name)) return false;
      seen.add(row.name);
      return true;
    });
}

function ensureSelectedCity() {
  const city = `${selectedCity.value ?? ""}`.trim();
  if (!city || city === ALL_CITIES) {
    selectedCity.value = ALL_CITIES;
    return;
  }
  if (!allCityOptions.value.some((item) => item.name === city)) {
    selectedCity.value = ALL_CITIES;
  }
}

function skillName(row) {
  return row?.skill || row?.name || "";
}

function roleName(row) {
  return row?.role || row?.name || "";
}

function bucketName(row) {
  return row?.city || row?.role || row?.skill || row?.major || row?.name || "";
}

function bucketRowFromChart(type, name, jobCount) {
  const keyByType = { city: "city", role: "role", skill: "skill", major: "major" };
  return {
    [keyByType[type] || "name"]: name,
    job_count: Number(jobCount || 0)
  };
}

function normalizedBucketRows(bucket) {
  return (bucket?.rows || [])
    .map((row) => {
      const rawName = bucketName(row);
      return {
        rawName,
        name: shortAxisLabel(rawName),
        value: row.job_count || 0
      };
    })
    .filter((row) => row.rawName)
    .slice(0, 7);
}

function bucketColor(type) {
  return {
    city: "#9a6a1e",
    role: "#167c69",
    skill: "#315f8c",
    major: "#8d4f70"
  }[type] || "#167c69";
}

function shortAxisLabel(value) {
  const text = `${value ?? ""}`;
  return text.length > 7 ? `${text.slice(0, 7)}…` : text;
}

function shortGraphLabel(value) {
  const text = `${value ?? ""}`;
  return text.length > 11 ? `${text.slice(0, 11)}…` : text;
}

function jobTags(job) {
  return [job.source, job.role, educationText(job.education, ""), job.experience].filter(Boolean).slice(0, 4);
}

function originalJobUrl(job) {
  const url = `${job?.source_url || job?.sourceUrl || ""}`.trim();
  return /^https?:\/\//i.test(url) ? url : "";
}

function averageWeighted(rows, key, weightKey) {
  const weighted = rows
    .map((row) => ({ value: Number(row[key] || 0), weight: Number(row[weightKey] || 0) }))
    .filter((row) => row.value > 0 && row.weight > 0);
  const totalWeight = weighted.reduce((sum, row) => sum + row.weight, 0);
  if (!totalWeight) return 0;
  return weighted.reduce((sum, row) => sum + row.value * row.weight, 0) / totalWeight;
}

function topSkillRows() {
  const totals = new Map();
  cityReports.value.forEach((city) => {
    (city.top_skills || []).forEach((skill) => {
      const name = skillName(skill);
      totals.set(name, (totals.get(name) || 0) + (skill.job_count || 0));
    });
  });
  return [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"))
    .slice(0, 8)
    .map(([name]) => name);
}
</script>
