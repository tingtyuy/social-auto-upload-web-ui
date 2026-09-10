<template>
  <div class="scheduled-page">
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">定时图集发布</span>
          <div class="header-actions">
            <el-button :loading="testingZr" @click="onTestZr">
              <el-icon><Connection /></el-icon>&nbsp;测试 ZR 连接
            </el-button>
            <el-button :loading="testingSmtp" @click="onTestSmtp">
              <el-icon><Promotion /></el-icon>&nbsp;测试邮件
            </el-button>
            <el-button type="primary" @click="openCreate">
              <el-icon><Plus /></el-icon>&nbsp;新建规则
            </el-button>
          </div>
        </div>
      </template>

      <el-tabs v-model="activeTab">
        <el-tab-pane label="发布规则" name="rules">
          <el-table :data="rules" v-loading="loadingRules" empty-text="暂无规则，点击右上角「新建规则」">
            <el-table-column label="规则" min-width="140">
              <template #default="{ row }">
                <div class="rule-name">{{ row.name }}</div>
                <div class="rule-sub">
                  <span class="cron-tag">cron</span>
                  {{ row.cron_expr || '（未设置）' }}
                  · 上限 {{ row.image_count || '不限' }} 张
                  · {{ (row.publish_mode || 'single') === 'merge' ? '合并' : '单任务' }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="来源" min-width="150">
              <template #default="{ row }">
                <div class="rule-sub">{{ row.zr_base_url || '（全局设置）' }}</div>
                <div class="rule-sub">状态过滤: {{ row.fetch_status }}</div>
              </template>
            </el-table-column>
            <el-table-column label="目标账号" width="90">
              <template #default="{ row }">{{ (row.accounts || []).length }} 个</template>
            </el-table-column>
            <el-table-column label="启用" width="80" align="center">
              <template #default="{ row }">
                <el-switch :model-value="!!row.enabled" @change="(v) => onToggle(row, v)" />
              </template>
            </el-table-column>
            <el-table-column label="最近运行" width="160">
              <template #default="{ row }">
                <template v-if="row.last_run">
                  <el-tag :type="statusTag(row.last_run.status)" size="small">{{ statusText(row.last_run.status) }}</el-tag>
                  <div class="rule-sub">{{ formatRunTime(row.last_run.started_at) }}</div>
                </template>
                <span v-else class="rule-sub">尚未运行</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="250" fixed="right">
              <template #default="{ row }">
                <el-button size="small" @click="onRun(row)">运行</el-button>
                <el-button size="small" @click="onPreview(row)">预览</el-button>
                <el-button size="small" type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="运行记录" name="runs">
          <div class="runs-toolbar">
            <el-select v-model="runStatusFilter" placeholder="状态" clearable style="width: 130px" @change="onRunFilterChange">
              <el-option label="成功" value="success" />
              <el-option label="失败" value="failed" />
              <el-option label="发布中" value="running" />
              <el-option label="等待中" value="pending" />
            </el-select>
            <el-select v-model="runTopicFilter" placeholder="主题" clearable filterable style="width: 180px" @change="onRunFilterChange">
              <el-option v-for="t in runTopicOptions" :key="t" :label="t" :value="t" />
            </el-select>
            <el-select v-model="runDaysFilter" placeholder="时间范围" clearable style="width: 140px" @change="onRunFilterChange">
              <el-option label="今天" :value="1" />
              <el-option label="近 7 天" :value="7" />
              <el-option label="近 30 天" :value="30" />
            </el-select>
            <el-button size="small" :loading="loadingRuns" @click="loadRuns">
              <el-icon><Refresh /></el-icon>&nbsp;刷新
            </el-button>
            <div class="runs-toolbar-spacer"></div>
            <el-button size="small" type="danger" plain :disabled="!runs.length" :loading="clearingRuns" @click="onClearRuns">
              <el-icon><Delete /></el-icon>&nbsp;清空全部
            </el-button>
          </div>
          <el-table :data="runs" v-loading="loadingRuns" empty-text="暂无运行记录">
            <el-table-column label="规则名称" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.rule_name || row.rule_id || '—' }}</template>
            </el-table-column>
            <el-table-column label="主题" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.topic || '—' }}</template>
            </el-table-column>
            <el-table-column label="账号/图集" width="110" align="center">
              <template #default="{ row }">
                <span>{{ row.account_count ?? 0 }} 账号</span>
                <div class="rule-sub">{{ row.album_count ?? 0 }} 个图集</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="成功/失败" width="100" align="center">
              <template #default="{ row }">{{ row.success_count }} / {{ row.failed_count }}</template>
            </el-table-column>
            <el-table-column label="开始时间" width="170">
              <template #default="{ row }">{{ formatRunTime(row.started_at) }}</template>
            </el-table-column>
            <el-table-column label="明细" width="80" align="center">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="onShowItems(row)">明细</el-button>
              </template>
            </el-table-column>
            <el-table-column label="错误" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">{{ row.error_message }}</template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="center">
              <template #default="{ row }">
                <el-button size="small" link type="danger" @click="onDeleteRun(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="全局设置" name="settings">
          <el-alert type="info" :closable="false" class="mb"
            title="全局配置对所有规则生效，无需在每条规则里重复填写。测试按钮会读取这里的配置。" />
          <el-card shadow="never" class="mb">
            <template #header>
              <div class="card-header">
                <span>ZR.Admin.NET 服务</span>
                <el-button :loading="testingZr" size="small" @click="onTestZr">
                  <el-icon><Connection /></el-icon>&nbsp;测试 ZR 连接
                </el-button>
              </div>
            </template>
            <el-form label-width="140px">
              <el-form-item label="ZR 服务地址">
                <el-input v-model="globalConfig.zr_base_url" placeholder="例如 http://127.0.0.1:8888（ZR.Admin.NET）" />
              </el-form-item>
            </el-form>
          </el-card>
          <el-card shadow="never" class="mb">
            <template #header>
              <div class="card-header">
                <span>SMTP 邮件通知</span>
                <el-button :loading="testingSmtp" size="small" @click="onTestSmtp">
                  <el-icon><Promotion /></el-icon>&nbsp;测试邮件
                </el-button>
              </div>
            </template>
            <el-form label-width="140px">
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="SMTP 服务器">
                    <el-input v-model="globalConfig.smtp.host" placeholder="smtp.example.com" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="端口">
                    <el-input-number v-model="globalConfig.smtp.port" :min="1" :max="65535" style="width:100%" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="用户名">
                    <el-input v-model="globalConfig.smtp.user" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="密码">
                    <el-input v-model="globalConfig.smtp.password" type="password" show-password />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="发件人">
                    <el-input v-model="globalConfig.smtp.from" placeholder="留空则用用户名" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="接收人">
                    <el-input v-model="globalConfig.smtp.to" placeholder="多个用逗号分隔" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-form-item label="SSL">
                <el-switch v-model="globalConfig.smtp.ssl" />
                <span class="hint">true=SMTP_SSL，false=SMTP(+starttls)</span>
              </el-form-item>
            </el-form>
          </el-card>
          <el-card shadow="never" class="mb">
            <template #header>
              <div class="card-header">
                <span>风控设置（行为风控）</span>
              </div>
            </template>
            <el-form label-width="140px">
              <el-form-item label="同账号发布间隔（秒）">
                <el-input-number v-model="globalConfig.risk.publishIntervalSeconds" :min="0" :max="86400" style="width:220px" />
                <span class="hint" style="margin-left:8px;">同一账号两次发布的强制最小间隔。默认 60 秒。</span>
              </el-form-item>
              <el-form-item label="全局发布间隔（秒）">
                <el-input-number v-model="globalConfig.risk.globalIntervalSeconds" :min="0" :max="86400" style="width:220px" />
                <span class="hint" style="margin-left:8px;">任意两次发布之间的最小间隔，防止多账号连续发布。默认 30 秒。</span>
              </el-form-item>
              <el-form-item label="间隔随机抖动（秒）">
                <el-input-number v-model="globalConfig.risk.jitterSeconds" :min="0" :max="3600" style="width:220px" />
                <span class="hint" style="margin-left:8px;">实际等待 = 基础间隔 + 0~抖动秒 随机值，避免固定间隔暴露机器特征。默认 20 秒。</span>
              </el-form-item>
              <el-form-item label="每账号每日上限（次）">
                <el-input-number v-model="globalConfig.risk.dailyAccountLimit" :min="0" :max="10000" style="width:220px" />
                <span class="hint" style="margin-left:8px;">单账号一天最多发布次数，超限本次跳过。0 = 不限。默认 0。</span>
              </el-form-item>
              <el-form-item label="全局每日上限（次）">
                <el-input-number v-model="globalConfig.risk.dailyGlobalLimit" :min="0" :max="100000" style="width:220px" />
                <span class="hint" style="margin-left:8px;">所有账号一天合计发布上限，超限本次跳过。0 = 不限。默认 0。</span>
              </el-form-item>
              <el-form-item label="连续失败熔断（次）">
                <el-input-number v-model="globalConfig.risk.failBreakCount" :min="0" :max="1000" style="width:220px" />
                <span class="hint" style="margin-left:8px;">账号连续失败达到该次数后暂停发布。0 = 关闭。默认 3 次。</span>
              </el-form-item>
              <el-form-item label="熔断时长（秒）">
                <el-input-number v-model="globalConfig.risk.failBreakSeconds" :min="0" :max="604800" style="width:220px" />
                <span class="hint" style="margin-left:8px;">熔断后暂停多久恢复。默认 600 秒（10 分钟）。</span>
              </el-form-item>
            </el-form>
          </el-card>
          <el-button type="primary" :loading="savingSettings" @click="onSaveSettings">保存全局设置</el-button>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="formVisible" :title="form.id ? '编辑规则' : '新建规则'" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="120px" label-position="right">
        <el-form-item label="规则名称" required>
          <el-input v-model="form.name" placeholder="例如：小红书每日图集" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="任务状态过滤">
          <el-select v-model="form.fetch_status">
            <el-option label="success" value="success" />
            <el-option label="failed" value="failed" />
            <el-option label="running" value="running" />
            <el-option label="pending" value="pending" />
          </el-select>
        </el-form-item>
        <el-form-item label="主题过滤">
          <el-select v-model="form.zr_topic" placeholder="选择 AI 任务工厂主题（留空 = 全部任务）" clearable filterable class="full">
            <el-option v-for="tp in zrTopicOptions" :key="tp" :label="tp" :value="tp" />
          </el-select>
          <span class="hint" :class="taskCountHintClass">{{ taskCountText }}</span>
        </el-form-item>
        <el-form-item label="图片数量上限">
          <el-input-number v-model="form.image_count" :min="0" :max="50" />
          <span class="hint">每个任务最多取几张；0 = 不限制</span>
        </el-form-item>
        <el-form-item label="发布模式">
          <el-radio-group v-model="form.publish_mode">
            <el-radio value="single">单个任务</el-radio>
            <el-radio value="merge">多任务合并</el-radio>
          </el-radio-group>
          <span class="hint">单个任务：任务与账号按顺序配对，一个任务的全部图片发给一个账号，任务多于账号时多余任务本次不发布；多任务合并：每批 N 个任务合成一篇，第 j 个账号取每个任务的第 j 张</span>
        </el-form-item>
        <el-form-item v-if="form.publish_mode === 'merge'" label="每批任务数">
          <el-input-number v-model="form.batch_size" :min="0" :max="50" />
          <span class="hint">每批 N 个任务合成一篇（N ≥ 2，或 0 = 全部候选合并为一组）；候选任务/图片不足时整批跳过，不会退化为单任务</span>
        </el-form-item>
        <el-form-item label="标题模板">
          <el-input v-model="form.title_template" type="textarea" :rows="2"
            placeholder="支持 {{变量Label}} 占位符；留空 = 默认取任务描述（原文）前 15 字" />
        </el-form-item>
        <el-form-item label="描述模板">
          <el-input v-model="form.desc_template" type="textarea" :rows="3"
            placeholder="支持 {{变量Label}} 占位符；留空 = 默认取任务描述（原文）全文" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="多个标签用英文逗号分隔" />
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="form.platform" class="full" @change="onPlatformChange">
            <el-option v-for="p in platformOptions" :key="String(p.value)" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标账号">
          <el-select v-model="form.accounts" multiple filterable placeholder="选择要发布的账号（留空 = 全部账号逐一发布）" class="full">
            <el-option v-for="acc in filteredAccountOptions" :key="acc.id" :label="acc.label" :value="acc.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行计划" required>
          <div class="cron-row">
            <el-input v-model="form.cron_expr" clearable placeholder="必填，例如 */30 * * * *" class="full">
              <template #prepend>cron</template>
            </el-input>
            <el-select v-model="cronPreset" placeholder="常用模板" clearable class="cron-preset" @change="onCronPreset">
              <el-option v-for="p in cronPresets" :key="p.expr" :label="p.label" :value="p.expr" />
            </el-select>
          </div>
          <div class="cron-meta">
            <span v-if="cronPreview.status === 'ok'" class="cron-ok">
              <template v-if="cronPreview.desc">{{ cronPreview.desc }} · 下次执行 {{ cronPreview.text }}</template>
              <template v-else>下次执行：{{ cronPreview.text }}</template>
            </span>
            <span v-else-if="cronPreview.status === 'error'" class="cron-err">{{ cronPreview.text }}</span>
            <span v-else class="cron-hint">5 段：分 时 日 月 周；支持 * 、*/n 、a-b 、a,b（0/7 = 周日）</span>
          </div>
        </el-form-item>
        <el-form-item label="邮件通知">
          <el-select v-model="form.notify_on">
            <el-option label="仅失败时通知" value="fail" />
            <el-option label="每次运行都通知" value="always" />
            <el-option label="不通知" value="never" />
          </el-select>
        </el-form-item>
        <el-collapse v-model="activeCollapse" class="adv-collapse">
          <el-collapse-item title="高级选项（一般无需修改）" name="adv">
            <el-form-item label="ZR 地址覆盖">
              <el-input v-model="form.zr_base_url" placeholder="留空则使用「全局设置」中的 ZR 地址" />
            </el-form-item>
            <el-form-item label="工作流 ID">
              <el-input v-model="form.workflow_id" placeholder="留空则使用任务自带的工作流 ID" />
            </el-form-item>
            <el-form-item label="功能类型">
              <el-input v-model="form.func_type" placeholder="func_type 过滤（可留空）" />
            </el-form-item>
            <el-form-item label="Prompt 过滤">
              <el-input v-model="form.prompt" placeholder="按 prompt 关键字过滤（可留空）" />
            </el-form-item>
            <el-form-item label="平台附加参数">
              <el-input v-model="extraKwargsText" type="textarea" :rows="3"
                placeholder="可选。JSON 对象，合并进平台 publish_image 参数" />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewVisible" title="预览（按账号分层）" width="720px">
      <div v-loading="previewLoading">
        <template v-if="preview && preview.task_count">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="任务">{{ preview.task_name || '（无）' }}</el-descriptions-item>
            <el-descriptions-item label="数量">任务 {{ preview.task_count }} 个 / 账号 {{ preview.account_count }} 个</el-descriptions-item>
            <el-descriptions-item label="标题">{{ preview.title || '（空）' }}</el-descriptions-item>
            <el-descriptions-item label="描述">{{ preview.desc || '（空）' }}</el-descriptions-item>
            <el-descriptions-item label="标签">{{ (preview.tags || []).join('，') || '（空）' }}</el-descriptions-item>
          </el-descriptions>
          <div class="preview-account-note">
            {{ preview.mode === 'paired'
              ? '任务与账号按顺序配对：账号 1 ← 第 1 个任务全部图片、账号 2 ← 第 2 个任务…（账号用完轮转）。下图为账号 1 的图集'
              : '下图为第 1 个账号的图集：每个任务的第 1 张，按任务顺序拼接' }}
          </div>
          <div class="preview-images">
            <el-image v-for="(img, i) in (preview.images || [])" :key="i" :src="img"
              :preview-src-list="preview.images" fit="cover" class="preview-img" />
          </div>
        </template>
        <el-empty v-else-if="!previewLoading"
          :description="preview ? '暂无待发布任务（任务发布后会标记为 published，不再进入预览）' : '无候选任务'" />
      </div>
    </el-dialog>

    <el-dialog v-model="itemsVisible" title="运行明细（按账号聚合）" width="680px">
      <el-table :data="itemsGrouped" size="small" empty-text="暂无明细">
        <el-table-column label="账号" min-width="140" prop="account_name" />
        <el-table-column label="平台" width="100">
          <template #default="{ row }">{{ platformIdToName[row.platform_id] || row.platform_id }}</template>
        </el-table-column>
        <el-table-column label="发布图集" width="90" align="center">
          <template #default="{ row }">{{ row.count }} 个</template>
        </el-table-column>
        <el-table-column label="成功" width="70" align="center">
          <template #default="{ row }">{{ row.success }}</template>
        </el-table-column>
        <el-table-column label="失败" width="70" align="center">
          <template #default="{ row }">{{ row.failed }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.error_message }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Connection, Promotion, Refresh, Delete } from '@element-plus/icons-vue'
import { scheduledPublishApi } from '@/api/scheduledPublish'
import { accountApi } from '@/api/account'
import { settingsApi } from '@/api/v2'
import { platformIdToName, platformList } from '@/config/platforms'

const activeTab = ref('rules')

const rules = ref([])
const loadingRules = ref(false)
const runs = ref([])
const loadingRuns = ref(false)
const clearingRuns = ref(false)
// 运行记录查询：状态 + 主题 + 时间范围
const runStatusFilter = ref('')
const runTopicFilter = ref('')
const runTopicOptions = ref([])
const runDaysFilter = ref('')
const accountOptions = ref([])

const emptyForm = () => ({
  id: '', name: '', enabled: true, zr_base_url: '', workflow_id: '',
  fetch_status: 'success', func_type: '', prompt: '', zr_topic: '', image_count: 0, batch_size: 1, publish_mode: 'single',
  title_template: '', desc_template: '', tags: '', accounts: [], platform: 3,
  cron_expr: '', notify_on: 'fail',
})

const form = ref(emptyForm())
const formVisible = ref(false)
const saving = ref(false)
const extraKwargsText = ref('{}')

// Cron 调度：常用模板 + 下次执行时间预览（由后端 croniter 计算，单一实现）
const cronPresets = [
  { label: '每 30 分钟', expr: '*/30 * * * *' },
  { label: '每小时整点', expr: '0 * * * *' },
  { label: '每 2 小时整点', expr: '0 */2 * * *' },
  { label: '每天 09:00', expr: '0 9 * * *' },
  { label: '每天 09:00 和 18:00', expr: '0 9,18 * * *' },
  { label: '每周一 08:30', expr: '30 8 * * 1' },
  { label: '工作日 08:00', expr: '0 8 * * 1-5' },
  { label: '周末 10:00', expr: '0 10 * * 0,6' },
  { label: '每月 1 日 08:00', expr: '0 8 1 * *' },
  { label: '工作日 09-18 点每 30 分钟', expr: '*/30 9-18 * * 1-5' },
]
const cronPreset = ref('')
const cronPreview = ref({ status: 'idle', text: '', desc: '' })

function onCronPreset(expr) {
  if (expr) form.value.cron_expr = expr
}

let cronPreviewTimer = null
watch(() => form.value.cron_expr, () => {
  clearTimeout(cronPreviewTimer)
  cronPreviewTimer = setTimeout(refreshCronPreview, 400)
})

async function refreshCronPreview() {
  const expr = (form.value.cron_expr || '').trim()
  if (!expr) {
    cronPreview.value = { status: 'idle', text: '' }
    return
  }
  if (expr.split(/\s+/).length !== 5) {
    cronPreview.value = { status: 'error', text: '应为 5 段：分 时 日 月 周（例如 */30 * * * *）' }
    return
  }
  try {
    const res = await scheduledPublishApi.cronNext(expr)
    if (res.code === 200 && res.data) {
      if (res.data.valid) cronPreview.value = { status: 'ok', text: res.data.next, desc: res.data.desc || '' }
      else cronPreview.value = { status: 'error', text: res.data.error || '表达式无效' }
    }
  } catch (e) { /* 拦截器已提示 */ }
}

const platformOptions = computed(() => [
  { label: '全部平台', value: '' },
  ...platformList.map(p => ({ label: p.name, value: p.id })),
])

const filteredAccountOptions = computed(() => {
  const p = form.value.platform
  if (p === '' || p === null || p === undefined) return accountOptions.value
  return accountOptions.value.filter(a => a.platform === p)
})

function onPlatformChange(p) {
  if (p === '' || p === null || p === undefined) return
  form.value.accounts = form.value.accounts.filter(id => {
    const acc = accountOptions.value.find(a => a.id === id)
    return acc && acc.platform === p
  })
}

const preview = ref(null)
const previewVisible = ref(false)
const previewLoading = ref(false)
const items = ref([])
// 运行明细按账号聚合：每个账号发布的图集数、成功/失败次数
const itemsGrouped = computed(() => {
  const map = new Map()
  for (const it of items.value || []) {
    const key = it.account_id ?? it.account_name ?? 'unknown'
    if (!map.has(key)) {
      map.set(key, {
        account_name: it.account_name || `账号 ${it.account_id}`,
        platform_id: it.platform_id,
        count: 0, success: 0, failed: 0, error_message: '',
      })
    }
    const g = map.get(key)
    g.count++
    if (it.status === 'success') g.success++
    else if (it.status === 'failed') g.failed++
    if (it.error_message && !g.error_message) g.error_message = it.error_message
  }
  return [...map.values()]
})
const itemsVisible = ref(false)
const testingZr = ref(false)
const testingSmtp = ref(false)

// 主题过滤（从 ZR 拉取）+ 当前筛选匹配任务数实时提示
const zrTopicOptions = ref([])
const zrTopicLoading = ref(false)
const taskCount = ref(null)
const taskCountLoading = ref(false)
const taskCountHintClass = computed(() => (taskCount.value === 0 ? 'hint-warn' : ''))
const taskCountText = computed(() => {
  if (!effectiveZrBaseUrl()) return '未配置 ZR 地址，请先在「全局设置」中配置'
  if (taskCountLoading.value) return '正在统计匹配任务数…'
  if (taskCount.value === null) return '选择主题或修改筛选后，实时显示匹配任务数'
  return taskCount.value > 0
    ? `当前筛选将匹配约 ${taskCount.value} 个 ZR 任务`
    : '当前筛选下没有匹配的 ZR 任务（检查状态/主题/功能类型）'
})
function effectiveZrBaseUrl() {
  return (form.value.zr_base_url || globalConfig.value.zr_base_url || '').trim()
}
async function loadZrTopics() {
  const base = effectiveZrBaseUrl()
  if (!base) { zrTopicOptions.value = []; return }
  zrTopicLoading.value = true
  try {
    const res = await scheduledPublishApi.zrTopics(base)
    zrTopicOptions.value = (res.code === 200 && Array.isArray(res.data)) ? res.data : []
  } catch (e) { zrTopicOptions.value = [] }
  zrTopicLoading.value = false
}
let countTimer = null
async function refreshTaskCount() {
  const base = effectiveZrBaseUrl()
  if (!base) { taskCount.value = null; return }
  taskCountLoading.value = true
  try {
    const res = await scheduledPublishApi.zrTaskCount({
      zr_base_url: base,
      topic: form.value.zr_topic || '',
      status: form.value.fetch_status || '',
      func_type: form.value.func_type || '',
      prompt: form.value.prompt || '',
    })
    taskCount.value = (res.code === 200 && res.data) ? (res.data.count ?? null) : null
  } catch (e) { taskCount.value = null }
  taskCountLoading.value = false
}
// 筛选条件变化 → 防抖实时统计
watch(() => [form.value.zr_topic, form.value.fetch_status, form.value.func_type, form.value.prompt, form.value.zr_base_url], () => {
  clearTimeout(countTimer)
  countTimer = setTimeout(refreshTaskCount, 500)
})
const activeCollapse = ref([])
const savingSettings = ref(false)
const DEFAULT_ZR_URL = 'http://192.168.3.8:8888'
const globalConfig = ref({
  zr_base_url: DEFAULT_ZR_URL,
  smtp: { host: 'smtp.163.com', port: 465, user: '', password: '', from: '', to: '', ssl: true },
  risk: { publishIntervalSeconds: 60, globalIntervalSeconds: 30, jitterSeconds: 20, dailyAccountLimit: 0, dailyGlobalLimit: 0, failBreakCount: 3, failBreakSeconds: 600 },
})

async function loadSettings() {
  try {
    const res = await settingsApi.getSettings()
    const s = (res && res.data) || {}
    globalConfig.value.zr_base_url = s.zr_base_url || DEFAULT_ZR_URL
    let smtp = {}
    if (typeof s.smtp === 'string' && s.smtp) {
      try { smtp = JSON.parse(s.smtp) } catch (e) { smtp = {} }
    } else if (s.smtp && typeof s.smtp === 'object') {
      smtp = s.smtp
    }
    globalConfig.value.smtp = {
      host: smtp.host || 'smtp.163.com',
      port: smtp.port || 465,
      user: smtp.user || '',
      password: smtp.password || '',
      from: smtp.from || '',
      to: smtp.to || '',
      ssl: smtp.ssl !== false,
    }
  } catch (e) { /* ignore */ }
  // 读取风控配置
  try {
    const rc = await settingsApi.getRiskConfig()
    const rd = (rc && rc.data) || {}
    globalConfig.value.risk = {
      publishIntervalSeconds: rd.publish_interval_seconds ?? 60,
      globalIntervalSeconds: rd.global_interval_seconds ?? 30,
      jitterSeconds: rd.jitter_seconds ?? 20,
      dailyAccountLimit: rd.daily_account_limit ?? 0,
      dailyGlobalLimit: rd.daily_global_limit ?? 0,
      failBreakCount: rd.fail_break_count ?? 3,
      failBreakSeconds: rd.fail_break_seconds ?? 600,
    }
  } catch (e) { /* ignore */ }
}

async function onSaveSettings() {
  savingSettings.value = true
  try {
    await settingsApi.updateSettings({
      zr_base_url: globalConfig.value.zr_base_url,
      smtp: {
        host: globalConfig.value.smtp.host,
        port: globalConfig.value.smtp.port,
        user: globalConfig.value.smtp.user,
        password: globalConfig.value.smtp.password,
        from: globalConfig.value.smtp.from,
        to: globalConfig.value.smtp.to,
        ssl: globalConfig.value.smtp.ssl,
      },
    })
    await settingsApi.setRiskConfig({
      publish_interval_seconds: globalConfig.value.risk.publishIntervalSeconds,
      global_interval_seconds: globalConfig.value.risk.globalIntervalSeconds,
      jitter_seconds: globalConfig.value.risk.jitterSeconds,
      daily_account_limit: globalConfig.value.risk.dailyAccountLimit,
      daily_global_limit: globalConfig.value.risk.dailyGlobalLimit,
      fail_break_count: globalConfig.value.risk.failBreakCount,
      fail_break_seconds: globalConfig.value.risk.failBreakSeconds,
    })
    ElMessage.success('全局设置已保存')
  } catch (e) { /* 拦截器已提示 */ }
  savingSettings.value = false
}

function statusText(s) {
  return { success: '成功', failed: '失败', partial: '部分成功', running: '运行中', pending: '待处理' }[s] || s
}
function statusTag(s) {
  return { success: 'success', failed: 'danger', partial: 'warning', running: 'info', pending: 'info' }[s] || 'info'
}

function buildPayload() {
  let extra = {}
  if (extraKwargsText.value && extraKwargsText.value.trim()) {
    try { extra = JSON.parse(extraKwargsText.value) } catch (e) { extra = {} }
  }
  return {
    name: form.value.name,
    enabled: form.value.enabled,
    zr_base_url: form.value.zr_base_url || '',
    workflow_id: form.value.workflow_id || '',
    fetch_status: form.value.fetch_status,
    func_type: form.value.func_type || '',
    prompt: form.value.prompt || '',
    zr_topic: form.value.zr_topic || '',
    image_count: form.value.image_count || 0,
    batch_size: form.value.batch_size || 0,
    publish_mode: form.value.publish_mode || 'single',
    title_template: form.value.title_template || '',
    desc_template: form.value.desc_template || '',
    tags: (form.value.tags || '').split(',').map(t => t.trim()).filter(Boolean),
    accounts: form.value.accounts,
    cron_expr: (form.value.cron_expr || '').trim(),
    notify_on: form.value.notify_on,
    extra_kwargs: extra,
  }
}
async function loadRules() {
  loadingRules.value = true
  try {
    const res = await scheduledPublishApi.listRules()
    if (res.code === 200) rules.value = res.data || []
  } catch (e) { /* ignore */ }
  loadingRules.value = false
}

async function loadRuns() {
  loadingRuns.value = true
  try {
    const params = {}
    if (runStatusFilter.value) params.status = runStatusFilter.value
    if (runTopicFilter.value) params.topic = runTopicFilter.value
    if (runDaysFilter.value) params.days = runDaysFilter.value
    const res = await scheduledPublishApi.listRuns(params)
    if (res.code === 200) runs.value = res.data || []
  } catch (e) { /* ignore */ }
  loadingRuns.value = false
}

async function loadRunTopics() {
  try {
    const res = await scheduledPublishApi.listRunTopics()
    if (res.code === 200) runTopicOptions.value = res.data || []
  } catch (e) { /* ignore */ }
}

function onRunFilterChange() {
  loadRuns()
}

async function onDeleteRun(row) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.task_name || row.id}」的运行记录？此操作不可恢复。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    const res = await scheduledPublishApi.deleteRun(row.id)
    if (res.code === 200) {
      ElMessage.success('已删除')
      runs.value = runs.value.filter(r => r.id !== row.id)
    }
  } catch (e) { /* 拦截器已提示 */ }
}

async function onClearRuns() {
  if (!runs.value.length) return
  try {
    await ElMessageBox.confirm(
      `确认清空全部 ${runs.value.length} 条运行记录？明细将一并删除，此操作不可恢复。`,
      '清空运行记录',
      { confirmButtonText: '清空', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  clearingRuns.value = true
  try {
    const res = await scheduledPublishApi.clearRuns()
    if (res.code === 200) {
      ElMessage.success('运行记录已清空')
      runs.value = []
    }
  } catch (e) { /* 拦截器已提示 */ }
  clearingRuns.value = false
}

// 开始时间格式化：ISO 字符串 → 用户易读的「YYYY-MM-DD HH:mm」
function formatRunTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return String(iso)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function loadAccounts() {
  try {
    const res = await accountApi.getAccounts()
    const list = res.data || []
    accountOptions.value = list.map(a => {
      // /getAccounts 返回的是「位置数组」：[id, type, filePath, userName, status, ...]
      const id = Array.isArray(a) ? a[0] : a.id
      const type = Array.isArray(a) ? a[1] : a.type
      const userName = Array.isArray(a) ? a[3] : (a.userName || a.name)
      return {
        id,
        platform: type,
        label: `${userName || id}（${platformIdToName[type] || type}）`,
      }
    })
  } catch (e) { /* ignore */ }
}

function openCreate() {
  form.value = emptyForm()
  extraKwargsText.value = '{}'
  cronPreset.value = ''
  cronPreview.value = { status: 'idle', text: '' }
  formVisible.value = true
  loadZrTopics()
  refreshTaskCount()
}

function openEdit(row) {
  const accs = row.accounts || []
  const plats = [...new Set(
    accs.map(id => accountOptions.value.find(a => a.id === id)?.platform).filter(Boolean)
  )]
  const platform = plats.length === 1 ? plats[0] : ''
  form.value = {
    ...emptyForm(),
    ...row,
    publish_mode: row.publish_mode || (row.batch_size > 1 ? 'merge' : 'single'),
    platform,
    tags: (row.tags || []).join(', '),
    accounts: accs,
  }
  extraKwargsText.value = JSON.stringify(row.extra_kwargs || {})
  cronPreset.value = ''
  refreshCronPreview()
  formVisible.value = true
  loadZrTopics()
  refreshTaskCount()
}

async function onSave() {
  if (!form.value.name) { ElMessage.warning('请输入规则名称'); return }
  const cronExpr = (form.value.cron_expr || '').trim()
  if (!cronExpr) { ElMessage.warning('请填写 Cron 表达式'); return }
  if (cronExpr.split(/\s+/).length !== 5) { ElMessage.warning('Cron 表达式应为 5 段：分 时 日 月 周'); return }
  saving.value = true
  try {
    const payload = buildPayload()
    const isEdit = !!form.value.id
    const res = isEdit
      ? await scheduledPublishApi.updateRule(form.value.id, payload)
      : await scheduledPublishApi.createRule(payload)
    if (res.code === 200) {
      ElMessage.success(isEdit ? '规则已更新' : '规则已创建')
      formVisible.value = false
      await loadRules()
    }
  } catch (e) { /* 拦截器已提示 */ }
  saving.value = false
}

async function onToggle(row, v) {
  try {
    const res = await scheduledPublishApi.updateRule(row.id, { enabled: v })
    if (res.code === 200) { row.enabled = v; ElMessage.success(v ? '已启用' : '已停用') }
  } catch (e) { /* 拦截器已提示 */ }
}

async function onRun(row) {
  try { await ElMessageBox.confirm(`立即执行规则「${row.name}」？`, '提示', { type: 'warning' }) } catch (e) { return }
  try {
    const res = await scheduledPublishApi.runRule(row.id)
    if (res.code === 200) {
      ElMessage.success(res.data?.message || '运行已触发')
      await loadRules()
      activeTab.value = 'runs'
      await loadRuns()
    }
  } catch (e) { /* 拦截器已提示 */ }
}

async function onPreview(row) {
  previewVisible.value = true
  previewLoading.value = true
  preview.value = null
  try {
    const res = await scheduledPublishApi.preview(row.id)
    if (res.code === 200) preview.value = res.data
  } catch (e) { /* ignore */ }
  previewLoading.value = false
}

async function onDelete(row) {
  try { await ElMessageBox.confirm(`删除规则「${row.name}」？`, '提示', { type: 'warning' }) } catch (e) { return }
  try {
    const res = await scheduledPublishApi.deleteRule(row.id)
    if (res.code === 200) { ElMessage.success('已删除'); await loadRules() }
  } catch (e) { /* 拦截器已提示 */ }
}

async function onShowItems(row) {
  try {
    const res = await scheduledPublishApi.getRun(row.id)
    if (res.code === 200) items.value = res.data?.items || []
  } catch (e) { /* 拦截器已提示 */ }
  itemsVisible.value = true
}

async function onTestZr() {
  testingZr.value = true
  try {
    // 用页面上填写的地址测试（未保存也生效），避免测到旧的已存配置
    const url = (globalConfig.value.zr_base_url || '').trim()
    const res = await scheduledPublishApi.testZr({ zr_base_url: url || undefined })
    if (res.code === 200 && res.data?.ok) {
      ElMessage.success(`ZR 连接正常，当前可见 ${res.data.count ?? 0} 个候选任务`)
    } else {
      ElMessage.error(res.msg || res.message || 'ZR 连接失败')
    }
  } catch (e) { /* 拦截器已提示 */ }
  testingZr.value = false
}

async function onTestSmtp() {
  testingSmtp.value = true
  try {
    const res = await scheduledPublishApi.testSmtp()
    if (res.code === 200) ElMessage.success(res.data?.message || '邮件发送成功')
    else ElMessage.error(res.msg || res.message || '邮件发送失败')
  } catch (e) { /* 拦截器已提示 */ }
  testingSmtp.value = false
}

onMounted(() => {
  loadRules()
  loadRuns()
  loadRunTopics()
  loadAccounts()
  loadSettings()
})
</script>

<style scoped>
.scheduled-page { padding: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-title { font-size: 16px; font-weight: 600; }
.header-actions { display: flex; gap: 8px; }
.rule-name { font-weight: 600; }
.rule-sub { font-size: 12px; color: #909399; }
.mb { margin-bottom: 12px; }
.hint { margin-left: 12px; font-size: 12px; color: #909399; }
.hint-warn { color: #E6A23C; }
.full { width: 100%; }
.preview-images { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.preview-img { width: 96px; height: 96px; border-radius: 4px; }
.preview-account-note { margin-top: 12px; font-size: 12px; color: #909399; }
.cron-row { display: flex; gap: 8px; width: 100%; }
.cron-preset { width: 230px; }
.cron-meta { margin-top: 4px; font-size: 12px; line-height: 1.5; }
.cron-ok { color: #67c23a; }
.cron-err { color: #f56c6c; }
.cron-hint { color: #909399; }
.cron-tag {
  display: inline-block; margin-right: 4px; padding: 0 4px;
  border-radius: 3px; background: #ecf5ff; color: #409eff; font-size: 11px;
}
.runs-toolbar {
  display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
}
.runs-toolbar-spacer { flex: 1; }
</style>