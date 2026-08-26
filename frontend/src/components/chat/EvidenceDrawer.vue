<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount, onMounted } from 'vue'
import { ApiClientError, apiClient } from '../../api/client'
import { useSessionStore } from '../../app/stores/session'
import type { DocumentSource, Evidence } from '../../types/api'

const props = defineProps<{ visible: boolean; evidence: Evidence[]; selectedCitationId: string | null }>()
const emit = defineEmits<{ close: []; select: [citationId: string] }>()
const session = useSessionStore()
const selected = computed(() => props.evidence.find((item) => item.citation_id === props.selectedCitationId) ?? props.evidence[0] ?? null)
const sourceState = ref<'empty' | 'loading' | 'success' | 'error'>('empty')
const source = ref<DocumentSource | null>(null)
const sourceError = ref('')
const contextSegments = computed(() => {
  const context = source.value?.page_context ?? ''
  const excerpt = selected.value?.excerpt?.trim() ?? ''
  if (!context || !excerpt) return context ? [{ text: context, highlighted: false }] : []
  const segments: Array<{ text: string; highlighted: boolean }> = []
  let cursor = 0
  while (cursor < context.length) {
    const matchIndex = context.indexOf(excerpt, cursor)
    if (matchIndex < 0) {
      segments.push({ text: context.slice(cursor), highlighted: false })
      break
    }
    if (matchIndex > cursor) segments.push({ text: context.slice(cursor, matchIndex), highlighted: false })
    segments.push({ text: excerpt, highlighted: true })
    cursor = matchIndex + excerpt.length
  }
  return segments
})
function handleKeydown(event: KeyboardEvent) { if (event.key === 'Escape' && props.visible) emit('close') }
const canLookupSource = computed(() => Boolean(session.activeKnowledgeBaseId && selected.value?.document_id && selected.value?.page))
async function loadSource() {
  source.value = null
  sourceError.value = ''
  if (!props.visible || !selected.value) { sourceState.value = 'empty'; return }
  if (!canLookupSource.value || !session.activeKnowledgeBaseId || !selected.value.document_id) {
    sourceState.value = 'error'
    sourceError.value = '当前无法打开原文，已保留引用摘录供核验。'
    return
  }
  sourceState.value = 'loading'
  try {
    source.value = await apiClient.getDocumentSource({
      kbId: session.activeKnowledgeBaseId,
      documentId: selected.value.document_id,
      page: selected.value.page,
      generationId: selected.value.generation_id,
      evidenceId: selected.value.evidence_id,
      excerpt: selected.value.excerpt,
    })
    sourceState.value = 'success'
  } catch (cause) {
    sourceState.value = 'error'
    if (cause instanceof ApiClientError) {
      if (cause.code === 'SOURCE_DOCUMENT_NOT_FOUND') sourceError.value = '当前无法打开原文：文档不存在或已不可用。'
      else if (cause.code === 'SOURCE_FORBIDDEN') sourceError.value = '当前无法打开原文：你没有权限查看此版本，或引用不属于当前知识库版本。'
      else if (cause.code === 'SOURCE_PAGE_NOT_FOUND' || cause.code === 'SOURCE_PAGE_INVALID') sourceError.value = '当前无法打开原文：页码不存在。'
      else sourceError.value = cause.message
    } else {
      sourceError.value = '当前无法打开原文，已保留引用摘录供核验。'
    }
  }
}
watch(() => [props.visible, props.selectedCitationId, selected.value?.evidence_id, session.activeKnowledgeBaseId], () => { void loadSource() }, { immediate: true })
onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="drawer-layer"><button class="drawer-backdrop" aria-label="关闭证据抽屉" @click="emit('close')" /><aside class="evidence-drawer" aria-label="证据抽屉" role="dialog"><div class="page-strip"><span>MANUAL</span><strong>{{ selected?.page ?? '—' }}</strong><span>PAGE</span></div><div class="drawer-main"><header><div><span class="drawer-kicker">EVIDENCE CHECK</span><h2>核验原文</h2></div><button class="close" aria-label="关闭" @click="emit('close')">×</button></header><div v-if="selected" class="evidence-content"><span class="relevance">{{ selected.relevance_label || '核心依据' }}</span><h3>{{ selected.document_name }}</h3><p class="page-label">第 {{ selected.page }} 页<span v-if="selected.section_path?.length"> · {{ selected.section_path.join(' / ') }}</span></p><p class="page-label" v-if="source?.generation_id || source?.document_version">版本 {{ source?.generation_id || selected.generation_id || '当前' }} · 文档版本 {{ source?.document_version ?? '—' }}</p><blockquote>{{ selected.excerpt || '当前引用没有可展示的片段。' }}</blockquote><div class="source-panel" :class="`source-panel--${sourceState}`"><div v-if="sourceState === 'loading'">正在打开原文上下文…</div><div v-else-if="sourceState === 'success' && source"><strong>{{ source.source_available ? '原文可打开' : '当前无法打开原文' }}</strong><p v-if="source.page_context" class="page-context" aria-label="页面上下文"><template v-for="(segment, index) in contextSegments" :key="`${segment.text}-${index}`"><mark v-if="segment.highlighted" class="source-highlight">{{ segment.text }}</mark><span v-else>{{ segment.text }}</span></template></p><p v-else-if="source.unavailable_reason">{{ source.unavailable_reason }}</p><p v-else>当前页面没有可展示的前后文，已保留引用摘录。</p><a v-if="source.source_available && source.source_url" class="context-button" :href="`${source.source_url}#page=${source.page}`" target="_blank" rel="noreferrer">打开原始 PDF <span>第 {{ source.page }} 页</span></a></div><div v-else><strong>当前无法打开原文</strong><p>{{ sourceError || '已保留引用摘录供核验。' }}</p><button class="context-button" @click="loadSource">重试</button></div></div><div v-if="evidence.length > 1" class="evidence-switch"><span>同一回答中的依据</span><button v-for="item in evidence" :key="item.evidence_id" :class="{ active: item.evidence_id === selected?.evidence_id }" @click="emit('select', item.citation_id || '')">{{ item.citation_id }}</button></div></div><div v-else class="drawer-empty"><strong>没有可展示的证据</strong><p>这条回答没有绑定可核验片段。请换一种问法，或查看高频问题。</p></div></div></aside></div>
  </Teleport>
</template>

<style scoped>
.drawer-layer { position: fixed; z-index: 25; inset: 0; display: flex; justify-content: flex-end; } .drawer-backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(23,33,43,.22); } .evidence-drawer { position: relative; display: grid; grid-template-columns: 58px minmax(0, 1fr); width: min(470px, 100%); height: 100%; border-left: 1px solid var(--color-line); background: var(--color-surface); box-shadow: -16px 0 40px rgba(23,33,43,.12); } .page-strip { display: flex; flex-direction: column; align-items: center; gap: 8px; padding-top: 26px; border-right: 1px solid var(--color-line); color: var(--color-cobalt); background: #f4f7fb; font: 9px Bahnschrift, monospace; letter-spacing: .09em; writing-mode: vertical-rl; } .page-strip strong { writing-mode: horizontal-tb; font-size: 23px; } .drawer-main { min-width: 0; overflow: auto; padding: 26px 24px; } header { display: flex; justify-content: space-between; align-items: start; } .drawer-kicker { color: var(--color-cobalt); font: 10px Bahnschrift, monospace; letter-spacing: .11em; } h2 { margin: 7px 0 0; font-size: 22px; } .close { min-width: 44px; border: 0; color: var(--color-muted); background: transparent; font-size: 26px; line-height: 1; } .evidence-content { margin-top: 32px; } .relevance { display: inline-block; padding: 5px 8px; border-radius: 5px; color: var(--color-success); background: #e8f7f1; font-size: 11px; font-weight: 700; } h3 { margin: 16px 0 5px; font-size: 18px; line-height: 1.4; } .page-label { margin: 0; color: var(--color-muted); font-size: 12px; } blockquote { margin: 24px 0; padding: 16px; border-left: 3px solid var(--color-cobalt); color: var(--color-ink); background: #f6f8fa; font-size: 15px; line-height: 1.8; } .source-panel { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--color-line); border-radius: 7px; background: #fbfcfd; font-size: 13px; line-height: 1.7; } .source-panel--error { border-color: #f4c5bf; background: #fff0ee; } .source-panel p { margin: 0; color: var(--color-muted); } .page-context { white-space: pre-wrap; } .source-highlight { padding: 1px 3px; border-radius: 3px; color: var(--color-ink); background: #ffe08a; box-shadow: 0 0 0 1px #f1c94a; } .context-button { display: inline-flex; align-items: center; justify-content: center; width: fit-content; min-height: 40px; padding: 0 12px; border: 1px solid var(--color-line); border-radius: var(--radius-control); color: var(--color-cobalt); background: white; text-decoration: none; font-weight: 700; } .context-button span { margin-left: 6px; font-size: 10px; } .evidence-switch { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 34px; padding-top: 18px; border-top: 1px solid var(--color-line); color: var(--color-muted); font-size: 12px; } .evidence-switch span { width: 100%; } .evidence-switch button { min-width: 34px; min-height: 32px; border: 1px solid var(--color-line); border-radius: 6px; color: var(--color-cobalt); background: white; } .evidence-switch button.active { color: white; border-color: var(--color-cobalt); background: var(--color-cobalt); } .drawer-empty { margin-top: 40px; padding: 16px; border-left: 3px solid var(--color-amber); background: var(--color-amber-soft); line-height: 1.7; } .drawer-empty p { color: var(--color-muted); font-size: 13px; }
@media (max-width: 1199px) { .evidence-drawer { width: min(420px, 100%); } } @media (max-width: 640px) { .drawer-layer { align-items: end; } .evidence-drawer { grid-template-columns: 46px 1fr; width: 100%; height: min(78vh, 620px); border-top: 1px solid var(--color-line); border-left: 0; border-radius: 16px 16px 0 0; } .drawer-main { padding: 20px 16px; } }
</style>
