<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import type { EvaluationComparison, EvaluationQuestion, EvaluationResult } from '../../types/api'

const props = defineProps<{ result: EvaluationResult | null; comparison: EvaluationComparison }>()
const filter = shallowRef<'all' | 'positive' | 'negative' | 'changed'>('all')
const expanded = shallowRef<string | null>(null)
const rows = computed(() => {
  const current = props.result?.questions ?? []
  const compareById = new Map(props.comparison.questions.map(row => [row.id, row]))
  return current.map(row => ({ row, diff: compareById.get(String(row.id ?? row.question_id)) }))
    .filter(({ row, diff }) => filter.value === 'all' || (filter.value === 'positive' && row.expects_evidence) || (filter.value === 'negative' && !row.expects_evidence) || (filter.value === 'changed' && diff?.changed))
})
function questionId(row: EvaluationQuestion) { return String(row.id ?? row.question_id ?? '') }
function toggle(id: string) { expanded.value = expanded.value === id ? null : id }
</script>

<template>
  <section class="questions" aria-labelledby="questions-title">
    <div class="section-head"><div><span class="eyebrow">CANONICAL / {{ result?.metrics.question_count ?? 0 }} QUESTIONS</span><h2 id="questions-title">题目明细</h2></div><div class="filters" role="group" aria-label="题目筛选"><button v-for="item in [{key:'all',label:'全部'},{key:'positive',label:'Positive'},{key:'negative',label:'Negative'},{key:'changed',label:'有变化'}]" :key="item.key" :class="{active: filter === item.key}" @click="filter = item.key as typeof filter">{{ item.label }}</button></div></div>
    <div v-if="!rows.length" class="empty">暂无匹配题目。</div>
    <div v-else class="table-wrap"><table><thead><tr><th scope="col">ID</th><th scope="col">问题</th><th scope="col">类型</th><th scope="col">结果</th><th scope="col">Citation</th><th scope="col">Trace</th><th scope="col"><span class="sr-only">详情</span></th></tr></thead><tbody><template v-for="item in rows" :key="questionId(item.row)"><tr :class="{changed:item.diff?.changed}"><td class="mono">{{ questionId(item.row) }}</td><td>{{ item.row.query ?? item.row.question }}</td><td>{{ item.row.expects_evidence ? 'positive' : 'negative' }}</td><td><span class="pill">{{ item.row.answer_status ?? (item.row.negative_case_passed ? 'refused' : 'unknown') }}</span></td><td>{{ item.row.citation_trace?.length ?? 0 }}</td><td>{{ item.row.trace_complete ? '完整' : '缺失' }}</td><td><button class="detail" :aria-expanded="expanded === questionId(item.row)" @click="toggle(questionId(item.row))">{{ expanded === questionId(item.row) ? '收起' : '展开' }}</button></td></tr><tr v-if="expanded === questionId(item.row)" class="detail-row"><td colspan="7"><div class="detail-content"><h3>{{ item.row.query ?? item.row.question }}</h3><p><strong>Answer status：</strong>{{ item.row.answer_status ?? '未提供' }}</p><p><strong>答案：</strong>{{ item.row.answer || (item.row.negative_case_passed ? '正确拒答' : '未返回答案') }}</p><p><strong>Citation：</strong>{{ item.row.citation_trace?.map(c => `${c.document_name ?? '未知文档'} · p.${c.page ?? '?'}`).join('；') || '无 citation' }}</p><p><strong>Trace：</strong>{{ item.row.trace_complete ? '可读取且完整' : '不完整或未记录' }} · {{ item.row.latency_ms ?? 0 }} ms</p><details><summary>查看原始 trace</summary><pre>{{ JSON.stringify(item.row, null, 2) }}</pre></details></div></td></tr></template></tbody></table></div>
  </section>
</template>

<style scoped>
.questions{padding:22px;border:1px solid var(--color-line);border-radius:var(--radius-panel);background:white}.section-head{display:flex;justify-content:space-between;gap:16px;align-items:end}.eyebrow{color:var(--color-amber);font:11px Bahnschrift,monospace;letter-spacing:.12em}h2{margin:8px 0 0;font-size:22px}.filters{display:flex;gap:5px;flex-wrap:wrap}.filters button,.detail{min-height:32px;padding:0 9px;border:1px solid var(--color-line);border-radius:6px;color:var(--color-muted);background:white}.filters button.active,.filters button:hover,.detail:hover{color:var(--color-cobalt);border-color:#9ab8ee;background:var(--color-cobalt-soft)}.table-wrap{overflow:auto;margin-top:20px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{padding:12px 10px;border-bottom:1px solid var(--color-line);text-align:left;vertical-align:top;font-size:12px}th{color:var(--color-muted);font-size:11px;font-weight:700}.mono{font-family:Bahnschrift,monospace}.pill{padding:4px 6px;border-radius:5px;background:#f0f3f6}.changed{background:#fffaf0}.detail-row td{padding:0}.detail-content{padding:18px;background:#f7f9fb}.detail-content h3{margin:0 0 12px;font-size:15px}.detail-content p{margin:7px 0;line-height:1.6}.detail-content pre{overflow:auto;max-height:300px;padding:12px;background:#17212b;color:#d8e6ff;font-size:11px}.empty{margin-top:18px;padding:18px;color:var(--color-muted);background:#f5f8fb}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:800px){.section-head{align-items:start;flex-direction:column}}
</style>
