<script setup lang="ts">
import type { EvaluationComparison, EvaluationGeneration, EvaluationResult } from '../../types/api'

defineProps<{ current: EvaluationResult | null; currentGeneration: EvaluationGeneration | null; comparisonGeneration: EvaluationGeneration | null; comparison: EvaluationComparison }>()
</script>

<template>
  <section class="comparison" aria-labelledby="comparison-title">
    <div class="section-head"><div><span class="eyebrow">GENERATION / DIFF</span><h2 id="comparison-title">Generation 对比</h2></div><span class="compare-status" :class="comparison.status">{{ comparison.status === 'comparable' ? '可比较' : '不可比较' }}</span></div>
    <p v-if="comparison.status === 'not_comparable'" class="notice">{{ comparison.reason }}</p>
    <div v-else class="compare-grid">
      <div class="generation-box"><span>当前 Active</span><strong>{{ currentGeneration?.generation }}</strong><small>{{ current?.passed ? 'PASS' : current?.status }}</small></div>
      <div class="delta-box"><span>指标变化</span><strong>{{ Object.keys(comparison.metrics).length }} 项</strong><small>正值代表当前结果增加</small></div>
      <div class="generation-box"><span>对比 Generation</span><strong>{{ comparisonGeneration?.generation }}</strong><small>{{ comparisonGeneration?.status }}</small></div>
    </div>
  </section>
</template>

<style scoped>
.comparison { padding: 22px; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: white; } .section-head { display:flex; justify-content:space-between; gap:16px; align-items:start; } .eyebrow { color: var(--color-amber); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } h2 { margin:8px 0 0; font-size:22px; } .compare-status { padding:6px 9px; border-radius:6px; font-size:11px; font-weight:800; } .comparable { color:var(--color-success); background:#e8f7f1; } .not_comparable { color:#956819; background:#fff5d8; } .compare-grid { display:grid; grid-template-columns:1fr .7fr 1fr; gap:10px; margin-top:20px; } .generation-box,.delta-box { padding:16px; background:#f5f8fb; } .generation-box span,.delta-box span { display:block; color:var(--color-muted); font-size:11px; } strong { display:block; margin-top:10px; font:16px Bahnschrift,monospace; overflow-wrap:anywhere; } small { display:block; margin-top:8px; color:var(--color-muted); } .notice { margin:18px 0 0; padding:15px; color:#765319; background:#fff8e4; } @media (max-width:700px){.compare-grid{grid-template-columns:1fr;}}
</style>
