<script setup lang="ts">
import type { EvaluationResult } from '../../types/api'

const props = defineProps<{ result: EvaluationResult | null }>()
const cards = [
  { key: 'positive_match_count', label: 'Positive evidence hit', format: (v: unknown) => `${v ?? 0}/${props.result?.metrics.positive_question_count ?? 0}` },
  { key: 'negative_passed_count', label: 'Negative unsupported', format: (v: unknown) => `${v ?? 0}/${props.result?.metrics.negative_question_count ?? 0}` },
  { key: 'citation_gate', label: 'Citation gate', format: (v: unknown) => v ? 'PASS' : 'FAIL' },
  { key: 'trace_gate', label: 'Trace gate', format: (v: unknown) => v ? 'PASS' : 'FAIL' },
  { key: 'latency_ms', label: '平均延迟', format: (v: unknown) => `${v ?? 0} ms` },
]
</script>

<template>
  <section class="summary" aria-labelledby="summary-title">
    <div class="section-head"><div><span class="eyebrow">VALIDATION / SUMMARY</span><h2 id="summary-title">正式 canonical validation</h2></div><span v-if="result" class="status" :class="result.passed ? 'status--pass' : 'status--fail'">{{ result.passed ? 'PASS' : result.status === 'failed' ? 'FAIL' : 'AVAILABLE' }}</span></div>
    <div v-if="result" class="metric-grid">
      <article v-for="card in cards" :key="card.key" class="metric"><span>{{ card.label }}</span><strong>{{ card.format(result.metrics[card.key]) }}</strong></article>
    </div>
    <p v-if="result" class="source">{{ result.source?.artifact_name }} · {{ result.finished_at ? new Date(result.finished_at).toLocaleString('zh-CN') : '时间未知' }}</p>
    <div v-else class="empty">暂无正式评估结果</div>
  </section>
</template>

<style scoped>
.summary { padding: 22px; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: white; box-shadow: var(--shadow-panel); }
.section-head { display: flex; justify-content: space-between; gap: 16px; align-items: start; } .eyebrow { color: var(--color-amber); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } h2 { margin: 8px 0 0; font-size: 22px; } .status { padding: 6px 9px; border-radius: 6px; font-size: 11px; font-weight: 800; } .status--pass { color: var(--color-success); background: #e8f7f1; } .status--fail { color: var(--color-danger); background: #fff0ed; }
.metric-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-top: 22px; } .metric { min-height: 92px; padding: 14px; border-left: 3px solid var(--color-cobalt); background: #f5f8fb; } .metric span { display: block; color: var(--color-muted); font-size: 11px; } .metric strong { display: block; margin-top: 12px; font: 23px Bahnschrift, monospace; } .source { margin: 16px 0 0; color: var(--color-muted); font-size: 12px; } .empty { margin-top: 18px; padding: 18px; color: var(--color-muted); background: #f5f8fb; }
@media (max-width: 800px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
