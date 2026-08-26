<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiClientError } from '../../api/client'

const props = defineProps<{ src: string }>()
const frameState = ref<'loading' | 'success' | 'empty' | 'error'>('loading')
const errorMessage = ref('')
const frameKey = ref(0)

const userMessage = computed(() => {
  if (frameState.value === 'loading') return '正在加载图谱…'
  if (frameState.value === 'empty') return '图谱暂不可用：当前没有可展示的图谱数据。'
  return errorMessage.value || '图谱暂不可用，请稍后重试。'
})

async function loadGraph() {
  frameState.value = 'loading'
  errorMessage.value = ''
  try {
    const response = await fetch(props.src, { headers: { Accept: 'text/html' } })
    if (!response.ok) {
      if (response.status >= 500) errorMessage.value = '图谱服务暂不可用。'
      else if (response.status === 404) errorMessage.value = '图谱数据不存在或尚未生成。'
      else errorMessage.value = '图谱请求无法完成，请检查筛选条件。'
      frameState.value = 'error'
      return
    }
    const html = await response.text()
    if (!html.trim()) {
      frameState.value = 'empty'
      return
    }
    frameState.value = 'success'
    frameKey.value += 1
  } catch (cause) {
    frameState.value = 'error'
    errorMessage.value = cause instanceof ApiClientError ? cause.message : '网络连接失败，请确认服务可用后重试。'
  }
}

watch(() => props.src, () => { void loadGraph() }, { immediate: true })
</script>

<template>
  <div class="native-graph-frame">
    <div v-if="frameState !== 'success'" class="graph-state" :class="`graph-state--${frameState}`"><strong>{{ frameState === 'loading' ? '加载图谱' : '图谱暂不可用' }}</strong><p>{{ userMessage }}</p><button v-if="frameState === 'error' || frameState === 'empty'" @click="loadGraph">重试</button></div>
    <iframe v-else :key="frameKey" :src="src" title="LightRAG 原生知识图谱" loading="eager" />
    <div v-if="frameState === 'success'" class="native-graph-note mono">LightRAG native · read only</div>
  </div>
</template>

<style scoped>
.native-graph-frame { position: relative; min-height: 700px; overflow: hidden; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: #fff; }
iframe { display: block; width: 100%; height: 700px; border: 0; background: #fff; }
.graph-state { min-height: 700px; display: grid; place-content: center; gap: 10px; padding: 24px; text-align: center; color: var(--color-muted); background: #fbfcfd; } .graph-state strong { color: var(--color-ink); font-size: 18px; } .graph-state p { max-width: 440px; margin: 0; line-height: 1.7; } .graph-state button { justify-self: center; min-height: 36px; padding: 0 14px; border: 1px solid var(--color-line); border-radius: var(--radius-control); color: var(--color-cobalt); background: white; font-weight: 700; } .graph-state--error { background: #fff0ee; } .graph-state--empty { background: var(--color-amber-soft); }
.native-graph-note { position: absolute; right: 14px; bottom: 12px; z-index: 2; padding: 5px 8px; border-radius: 5px; color: var(--color-muted); background: rgba(255,255,255,.9); font-size: 10px; }
</style>
