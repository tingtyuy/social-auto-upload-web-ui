<template>
  <el-dialog title="从AI系统导入结果图" :model-value="modelValue" width="960px" @open="onOpen" @update:model-value="$emit('update:modelValue', $event)">
    <div style="display: flex; align-items: center; margin-bottom: 16px; gap: 12px;">
      <el-input v-model="keyword" placeholder="按任务名称搜索..." clearable style="width: 300px;" @keyup.enter.native="searchAndReset">
        <el-button slot="append" icon="el-icon-search" @click="searchAndReset" :loading="loading"></el-button>
      </el-input>
      <el-button v-if="keyword" size="small" @click="clearSearch">清除搜索</el-button>
      <span style="color: #909399; font-size: 13px;">共 {{ totalNum }} 个结果</span>
      <span v-if="selectedIds.length" style="color: #409EFF; font-weight: bold; margin-left: auto;">已选 {{ selectedIds.length }} 张</span>
    </div>

    <div v-loading="loading" style="min-height: 300px; max-height: 480px; overflow-y: auto;">
      <div v-if="taskList.length === 0 && !loading" style="text-align: center; color: #909399; padding: 60px 0;">
        <i class="el-icon-picture-outline" style="font-size: 48px; display: block; margin-bottom: 12px;"></i>
        暂无已完成的任务
      </div>
      <div v-for="task in taskList" :key="task.id" class="task-item" :class="{ selected: selectedIds.includes(task.id) }" @click="toggleSelect(task)">
        <el-checkbox :value="selectedIds.includes(task.id)" @click.stop @change="toggleSelect(task)" class="task-checkbox"></el-checkbox>
        <div class="task-thumb-wrap">
          <img :src="getProxyUrl(task.outputImagePath)" class="task-thumb" @error="onImgError($event)" />
        </div>
        <div class="task-info">
          <div class="task-prompt">{{ task.taskName || '未命名' }}</div>
          <div class="task-meta" style="color:#606266">提示词: {{ task.prompt || '-' }}</div>
          <div class="task-meta">ID: {{ task.id }} | {{ formatTime(task.createTime) }}</div>
        </div>
      </div>
    </div>

    <div style="margin-top: 16px; display: flex; justify-content: space-between; align-items: center;">
      <span style="font-size: 12px; color: #909399;">第 {{ currentPage }} / {{ totalPages }} 页</span>
      <el-pagination layout="prev, pager, next" :total="totalNum" :page-size="pageSize" :current-page="currentPage" @current-change="handlePageChange" />
    </div>

    <template #footer>
      <el-button @click="closeDialog">取消</el-button>
      <el-button type="primary" :disabled="selectedIds.length === 0" :loading="importing" @click="handleImport">
        {{ importing ? '上传中...' : `添加选中的 ${selectedIds.length} 张图片到发布列表` }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import axios from 'axios'
import { materialsApi } from '@/api/materials'
import { ElMessage } from 'element-plus'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'imported'])

const loading = ref(false)
const importing = ref(false)
const keyword = ref('')
const taskList = ref([])
const totalNum = ref(0)
const totalPages = ref(1)
const currentPage = ref(1)
const pageSize = ref(12)
const selectedIds = ref([])

watch(totalNum, (val) => { totalPages.value = Math.ceil(val / pageSize.value) || 1 })
watch(() => props.modelValue, (val) => { if (val) onOpen() })

function onOpen() {
  currentPage.value = 1
  keyword.value = ''
  selectedIds.value = []
  loadTasks()
}

function loadTasks() {
  loading.value = true
  axios.get('/ai/task/result-images', {
    params: { pageNum: currentPage.value, pageSize: pageSize.value, keyword: keyword.value || undefined }
  }).then(res => {
    if (res.data.code === 200) {
      taskList.value = res.data.data.result || []
      totalNum.value = res.data.data.totalNum || 0
    }
  }).catch(err => { console.error('获取失败:', err) })
    .finally(() => { loading.value = false })
}

function searchAndReset() { currentPage.value = 1; loadTasks() }
function clearSearch() { keyword.value = ''; currentPage.value = 1; loadTasks() }
function handlePageChange(page) { currentPage.value = page; loadTasks() }

function toggleSelect(task) {
  const idx = selectedIds.value.indexOf(task.id)
  if (idx >= 0) selectedIds.value.splice(idx, 1)
  else selectedIds.value.push(task.id)
}

function getProxyUrl(url) {
  if (!url) return ''
  const m = url.match(/\/storage\/.+/)
  return m ? m[0] : url
}

function parseTags(s) { return s ? s.split(',').map(t => t.trim()).filter(Boolean) : [] }
function formatTime(t) { return t ? t.substring(0, 16) : '' }
function onImgError(e) { e.target.style.display = 'none' }
function closeDialog() { emit('update:modelValue', false) }

async function handleImport() {
  if (selectedIds.value.length === 0) return
  importing.value = true

  try {
    const selected = taskList.value.filter(t => selectedIds.value.includes(t.id))
    const uploadedImages = []

    for (const task of selected) {
      const imageUrl = getProxyUrl(task.outputImagePath)
      if (!imageUrl) continue

      try {
        // 下载图片为 blob
        const response = await fetch(imageUrl)
        const blob = await response.blob()

        // 创建 FormData 上传到 materials 系统
        const formData = new FormData()
        const fileName = `AI_${task.id}.png`
        formData.append('file', blob, fileName)

        const resp = await materialsApi.upload(formData)
        if (resp.code === 200) {
          uploadedImages.push({
            id: resp.data.id,
            name: resp.data.original_filename,
            url: resp.data.stored_path ? `/api/materials/file/${resp.data.stored_path}` : imageUrl,
            stored_path: resp.data.stored_path,
            size: resp.data.file_size,
            type: resp.data.mime_type,
          })
        }
      } catch (e) {
        console.error('上传图片失败:', task.id, e)
      }
    }

    if (uploadedImages.length > 0) {
      emit('imported', uploadedImages)
      ElMessage.success(`成功导入 ${uploadedImages.length} 张图片`)
    } else {
      ElMessage.warning('没有成功导入任何图片')
    }
    emit('update:modelValue', false)
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.task-item { display: flex; align-items: center; padding: 12px 14px; border: 2px solid #ebeef5; border-radius: 8px; margin-bottom: 10px; cursor: pointer; transition: all 0.2s; }
.task-item:hover { border-color: #409EFF; background: #f5f7fa; }
.task-item.selected { border-color: #409EFF; background: #ecf5ff; box-shadow: 0 2px 8px rgba(64, 158, 255, 0.15); }
.task-checkbox { margin-right: 8px; }
.task-thumb-wrap { width: 100px; height: 100px; border-radius: 6px; overflow: hidden; margin-right: 16px; flex-shrink: 0; background: #f5f7fa; display: flex; align-items: center; justify-content: center; }
.task-thumb { width: 100%; height: 100%; object-fit: cover; }
.task-info { flex: 1; min-width: 0; }
.task-prompt { font-size: 15px; font-weight: 600; color: #303133; line-height: 1.5; margin-bottom: 6px; }
.task-tags { margin-bottom: 6px; }
.task-meta { font-size: 12px; color: #909399; }
</style>
