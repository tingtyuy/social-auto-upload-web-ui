<template>
  <div class="music-select">
    <el-select
      v-model="selectedMusicId"
      placeholder="选择音乐"
      clearable
      filterable
      no-data-text=" "
      @change="handleChange"
      style="width: 100%"
    >
      <template #header>
        <div class="search-input-wrapper">
          <el-input
            v-model="searchKeyword"
            placeholder="输入关键词后按回车搜索"
            clearable
            @keyup.enter="handleSearch"
            @clear="handleClear"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
        <div v-if="loading" class="loading-indicator">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>加载中...</span>
        </div>
      </template>

      <!-- Fixed options (no search needed) -->
      <div class="fixed-music-label">常用音乐（无需搜索）</div>
      <el-option
        v-for="music in fixedMusicList"
        :key="music.title"
        :label="music.title"
        :value="music.title"
      >
        <div class="music-option">
          <div class="music-info">
            <div class="music-title">{{ music.title }}</div>
            <div class="music-meta">
              <span class="music-author">{{ music.author }}</span>
            </div>
          </div>
        </div>
      </el-option>

      <!-- Search results -->
      <div v-if="musicList.length > 0" class="fixed-music-label">搜索结果</div>
      <el-option
        v-for="music in musicList"
        :key="music.musicId"
        :label="`${music.title} - ${music.author || '未知作者'}`"
        :value="music.musicId"
      >
        <div class="music-option">
          <img
            v-if="music.cover"
            :src="music.cover"
            :alt="music.title"
            class="music-cover"
            @error="onImageError"
          />
          <div class="music-info">
            <div class="music-title">{{ music.title }}</div>
            <div class="music-meta">
              <span class="music-author">{{ music.author || '未知作者' }}</span>
              <span class="music-duration">{{ formatDuration(music.duration) }}</span>
            </div>
          </div>
        </div>
      </el-option>
    </el-select>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Search, Loading } from '@element-plus/icons-vue'
import { kuaishouImageApi } from '@/api/kuaishouImage'

const props = defineProps({
  accountId: { type: [String, Number], default: '' },
  modelValue: { type: String, default: '' },
  data: { type: Object, default: null },
})

const emit = defineEmits(['update:modelValue', 'change'])

const loading = ref(false)
const musicList = ref([])
const selectedMusicId = ref(props.modelValue || '')
const searchKeyword = ref('')

// Fixed frequently used music list
const fixedMusicList = ref([
  { title: '默认原声', author: '系统' },
  { title: '轻快背景音乐', author: '推荐' },
  { title: '热门BGM', author: '推荐' },
  { title: '温柔纯音乐', author: '推荐' },
  { title: '节奏感强', author: '推荐' },
])

watch(() => props.modelValue, (val) => {
  selectedMusicId.value = val || ''
  if (val && !musicList.value.find(m => m.musicId === val) && !fixedMusicList.value.find(m => m.title === val)) {
    if (props.data && props.data.musicId === val) {
      musicList.value.unshift(props.data)
    } else {
      musicList.value.unshift({ musicId: val, title: val, author: '', duration: 0, cover: '' })
    }
  }
}, { immediate: true })

async function handleSearch() {
  const keyword = searchKeyword.value?.trim()
  if (!keyword) { musicList.value = []; return }
  loading.value = true
  try {
    const resp = await kuaishouImageApi.searchMusic(props.accountId || '', keyword, 0, 50)
    if (resp.code === 200) {
      musicList.value = resp.data?.musicList || []
    }
  } catch (e) {
    console.error('搜索音乐失败:', e)
  } finally {
    loading.value = false
  }
}

function handleClear() {
  searchKeyword.value = ''
  musicList.value = []
}

function handleChange(val) {
  if (val) {
    const music = musicList.value.find(m => m.musicId === val) || fixedMusicList.value.find(m => m.title === val)
    emit('update:modelValue', val)
    emit('change', { ...music, _searchKeyword: searchKeyword.value })
  } else {
    emit('update:modelValue', null)
    emit('change', null)
  }
}

function formatDuration(seconds) {
  if (!seconds) return '00:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

function onImageError(e) {
  e.target.style.display = 'none'
}
</script>

<style scoped lang="scss">
.music-select { width: 100%; }
.search-input-wrapper { padding: 8px 12px; }
.loading-indicator {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 8px 12px; color: #94A3B8; font-size: 13px;
  .is-loading { animation: rotating 1s linear infinite; }
  @keyframes rotating { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
}
.fixed-music-label {
  padding: 6px 12px; font-size: 12px; color: #94A3B8;
  background: #1e293b; border-top: 1px solid #334155; cursor: default;
  &:first-child { border-top: none; }
}
.music-option { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
.music-cover { width: 40px; height: 40px; border-radius: 4px; object-fit: cover; flex-shrink: 0; }
.music-info { flex: 1; min-width: 0; }
.music-title { font-size: 14px; color: #F8FAFC; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.music-meta { display: flex; gap: 12px; margin-top: 4px; font-size: 12px; color: #94A3B8; }
</style>
