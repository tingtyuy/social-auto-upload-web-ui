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
                <div class="rule-sub">每 {{ row.interval_minutes }} 分钟 · 上限 {{ row.image_count || '不限' }} 张</div>
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
                  <div class="rule-sub">{{ row.last_run.started_at }}</div>
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
          <el-button size="small" class="mb" @click="loadRuns">刷新</el-button>
          <el-table :data="runs" v-loading="loadingRuns" empty-text="暂无运行记录">
            <el-table-column label="任务" min-width="150">
              <template #default="{ row }">
                <div>{{ row.task_name || '（无任务名）' }}</div>
                <div class="rule-sub">task_id: {{ row.task_id }}</div>
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
              <template #default="{ row }">{{ row.started_at }}</template>
            </el-table-column>
            <el-table-column label="明细" width="80" align="center">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="onShowItems(row)">明细</el-button>
              </template>
            </el-table-column>
            <el-table-column label="错误" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">{{ row.error_message }}</template>
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
        <el-form-item label="图片数量上限">
          <el-input-number v-model="form.image_count" :min="0" :max="50" />
          <span class="hint">0 表示不限制</span>
        </el-form-item>
        <el-form-item label="每批任务数">
          <el-input-number v-model="form.batch_size" :min="0" :max="50" />
          <span class="hint">几个 ZR 任务合成一个发布内容；0 = 全部候选任务合成一个</span>
        </el-form-item>
        <el-form-item label="标题模板">
          <el-input v-model="form.title_template" type="textarea" :rows="2"
            placeholder="支持 {{变量Label}} 占位符，未匹配的原样保留" />
        </el-form-item>
        <el-form-item label="描述模板">
          <el-input v-model="form.desc_template" type="textarea" :rows="3"
            placeholder="支持 {{变量Label}} 占位符，未匹配的原样保留" />
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
        <el-form-item label="执行间隔(分钟)">
          <el-input-number v-model="form.interval_minutes" :min="1" :max="1440" />
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
          <div class="preview-account-note">下图为第 1 个账号的图集：每个任务的第 1 张，按任务顺序拼接</div>
          <div class="preview-images">
            <el-image v-for="(img, i) in (preview.images || [])" :key="i" :src="img"
              :preview-src-list="preview.images" fit="cover" class="preview-img" />
          </div>
        </template>
        <el-empty v-else-if="!previewLoading"
          :description="preview ? '暂无待发布任务（任务发布后会标记为 published，不再进入预览）' : '无候选任务'" />
      </div>
    </el-dialog>

    <el-dialog v-model="itemsVisible" title="运行明细" width="640px">
      <el-table :data="items" size="small" empty-text="暂无明细">
        <el-table-column label="账号" min-width="140" prop="account_name" />
        <el-table-column label="平台" width="100" prop="platform_id" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="160" show-overflow-tooltip prop="error_message" />
      </el-table>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Connection, Promotion } from '@element-plus/icons-vue'
import { scheduledPublishApi } from '@/api/scheduledPublish'
import { accountApi } from '@/api/account'
import { settingsApi } from '@/api/v2'
import { platformIdToName, platformList } from '@/config/platforms'

const activeTab = ref('rules')

const rules = ref([])
const loadingRules = ref(false)
const runs = ref([])
const loadingRuns = ref(false)
const accountOptions = ref([])

const emptyForm = () => ({
  id: '', name: '', enabled: true, zr_base_url: '', workflow_id: '',
  fetch_status: 'success', func_type: '', prompt: '', image_count: 0, batch_size: 0,
  title_template: '', desc_template: '', tags: '', accounts: [], platform: 3,
  interval_minutes: 30, notify_on: 'fail',
})

const form = ref(emptyForm())
const formVisible = ref(false)
const saving = ref(false)
const extraKwargsText = ref('{}')

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
const itemsVisible = ref(false)
const testingZr = ref(false)
const testingSmtp = ref(false)

const activeCollapse = ref([])
const savingSettings = ref(false)
const DEFAULT_ZR_URL = 'http://192.168.3.8:8888'
const globalConfig = ref({
  zr_base_url: DEFAULT_ZR_URL,
  smtp: { host: 'smtp.163.com', port: 465, user: '', password: '', from: '', to: '', ssl: true },
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
    zr_base_url: form.value.zr_base_url || null,
    workflow_id: form.value.workflow_id || null,
    fetch_status: form.value.fetch_status,
    func_type: form.value.func_type || null,
    prompt: form.value.prompt || null,
    image_count: form.value.image_count || 0,
    batch_size: form.value.batch_size || 0,
    title_template: form.value.title_template || null,
    desc_template: form.value.desc_template || null,
    tags: (form.value.tags || '').split(',').map(t => t.trim()).filter(Boolean),
    accounts: form.value.accounts,
    interval_minutes: form.value.interval_minutes,
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
    const res = await scheduledPublishApi.listRuns()
    if (res.code === 200) runs.value = res.data || []
  } catch (e) { /* ignore */ }
  loadingRuns.value = false
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
  formVisible.value = true
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
    platform,
    tags: (row.tags || []).join(', '),
    accounts: accs,
  }
  extraKwargsText.value = JSON.stringify(row.extra_kwargs || {})
  formVisible.value = true
}

async function onSave() {
  if (!form.value.name) { ElMessage.warning('请输入规则名称'); return }
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
    const res = await scheduledPublishApi.testZr()
    if (res.code === 200) ElMessage.success(res.data?.message || 'ZR 连接正常')
    else ElMessage.error(res.msg || res.message || '连接失败')
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
.full { width: 100%; }
.preview-images { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.preview-img { width: 96px; height: 96px; border-radius: 4px; }
.preview-account-note { margin-top: 12px; font-size: 12px; color: #909399; }
</style>