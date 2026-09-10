<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import EvaluationComparison from '../../components/admin/EvaluationComparison.vue'
import EvaluationQuestionTable from '../../components/admin/EvaluationQuestionTable.vue'
import EvaluationSummary from '../../components/admin/EvaluationSummary.vue'
import { useEvaluation } from '../../composables/useEvaluation'

const { dashboard, loading, error, kbId, load } = useEvaluation()
const selectedGeneration = shallowRef('')
const selectedComparison = shallowRef('')
const generations = computed(() => dashboard.value?.available_generations ?? [])
async function refresh() { await load(selectedGeneration.value || undefined, selectedComparison.value || undefined) }
</script>

<template>
  <section class="evaluation-page">
    <header class="heading"><div><span class="eyebrow">ADMIN / EVALUATION</span><h1>评估中心</h1><p>只读查看正式 canonical validation；不在页面触发新的评估。</p></div><button class="refresh" :disabled="loading || !kbId" @click="refresh">{{ loading ? '读取中…' : '刷新' }}</button></header>
    <div v-if="error" class="state error" role="alert"><strong>评估结果读取失败</strong><p>{{ error }}</p><button class="retry" @click="refresh">重试</button></div>
    <div v-else-if="!kbId" class="state">请先选择知识库。</div>
    <div v-else-if="loading && !dashboard" class="state">正在读取评估结果…</div>
    <template v-else-if="dashboard">
      <div class="toolbar"><label>当前评估<select v-model="selectedGeneration" @change="refresh"><option value="">Active Generation</option><option v-for="generation in generations" :key="generation.id" :value="generation.id">{{ generation.generation }} · {{ generation.status }}</option></select></label><label>对比 Generation<select v-model="selectedComparison" @change="refresh"><option value="">上一代正式 validation</option><option v-for="generation in generations" :key="generation.id" :value="generation.id">{{ generation.generation }} · {{ generation.status }}</option></select></label><span class="identity">KB · {{ dashboard.knowledge_base.name }}</span></div>
      <EvaluationSummary :result="dashboard.current" />
      <EvaluationComparison :current="dashboard.current" :current-generation="dashboard.active_generation" :comparison-generation="dashboard.comparison_generation" :comparison="dashboard.comparison" />
      <EvaluationQuestionTable :result="dashboard.current" :comparison="dashboard.comparison" />
    </template>
  </section>
</template>

<style scoped>
.evaluation-page{max-width:1180px;margin:0 auto;display:grid;gap:16px}.heading{display:flex;justify-content:space-between;align-items:end;gap:20px}.eyebrow{color:var(--color-amber);font:11px Bahnschrift,monospace;letter-spacing:.12em}h1{margin:9px 0 7px;font-size:38px;letter-spacing:-.05em}.heading p{margin:0;color:var(--color-muted)}.refresh,.retry{min-height:40px;padding:0 13px;border:1px solid var(--color-line);border-radius:var(--radius-control);color:var(--color-cobalt);background:white;font-weight:700}.refresh:disabled{opacity:.55}.toolbar{display:flex;align-items:end;gap:14px;flex-wrap:wrap;padding:14px 16px;border:1px solid var(--color-line);background:#f5f8fb}.toolbar label{display:grid;gap:5px;color:var(--color-muted);font-size:11px}.toolbar select{min-height:36px;min-width:220px;padding:0 10px;border:1px solid var(--color-line);border-radius:6px;background:white;color:var(--color-ink)}.identity{margin-left:auto;color:var(--color-muted);font:11px Bahnschrift,monospace}.state{padding:20px;border:1px solid var(--color-line);background:white;color:var(--color-muted)}.error{color:var(--color-danger)}.error p{margin:8px 0 14px}@media(max-width:700px){.heading{align-items:start;flex-direction:column}.identity{margin-left:0}.toolbar select{min-width:180px}}
</style>
