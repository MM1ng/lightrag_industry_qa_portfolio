<script setup lang="ts">
import { computed } from 'vue'
import type { GenerationSummary } from '../../types/api'

const props = defineProps<{ generation: GenerationSummary }>()
const stages = ['Candidate', 'Validation', 'Promote', 'Active'] as const
const stageState = computed(() => {
  const status = props.generation.status.toLowerCase()
  if (status === 'active') return { Candidate: 'done', Validation: 'done', Promote: 'done', Active: 'current' }
  if (status === 'failed') return { Candidate: 'done', Validation: 'failed', Promote: 'pending', Active: 'pending' }
  if (status === 'ready') return { Candidate: 'done', Validation: 'current', Promote: 'pending', Active: 'pending' }
  if (status === 'archived') return { Candidate: 'done', Validation: 'done', Promote: 'done', Active: 'done' }
  return { Candidate: 'current', Validation: 'pending', Promote: 'pending', Active: 'pending' }
})
</script>

<template>
  <ol class="timeline" aria-label="Generation 生命周期阶段">
    <li v-for="stage in stages" :key="stage" :class="`timeline-step timeline-step--${stageState[stage]}`">
      <span class="marker" aria-hidden="true">{{ stageState[stage] === 'done' ? '✓' : stageState[stage] === 'failed' ? '!' : '·' }}</span>
      <span><strong>{{ stage }}</strong><small v-if="stage === 'Validation' && stageState[stage] === 'current'">需要验证</small><small v-else-if="stage === 'Active' && stageState[stage] === 'current'">线上版本</small></span>
    </li>
  </ol>
</template>

<style scoped>
.timeline { display: flex; gap: 0; margin: 15px 0 0; padding: 0; list-style: none; }
.timeline-step { position: relative; display: flex; align-items: center; gap: 7px; flex: 1; color: var(--color-muted); font-size: 11px; }
.timeline-step:not(:last-child)::after { content: ''; height: 1px; flex: 1; margin: 0 8px; background: var(--color-line); }
.marker { display: grid; place-items: center; width: 22px; height: 22px; border: 1px solid var(--color-line); border-radius: 50%; background: white; font-weight: 800; }
.timeline-step--done { color: var(--color-success); }.timeline-step--done .marker { border-color: #9ed6c4; background: #e8f7f1; }
.timeline-step--current { color: var(--color-cobalt); font-weight: 700; }.timeline-step--current .marker { border-color: #8fb1ef; background: var(--color-cobalt-soft); }
.timeline-step--failed { color: var(--color-danger); }.timeline-step--failed .marker { border-color: #f4c5bf; background: #fff0ee; }
.timeline-step span:last-child { display: grid; gap: 2px; }.timeline-step small { color: var(--color-muted); font-size: 10px; font-weight: 400; }
@media (max-width: 650px) { .timeline { align-items: start; flex-direction: column; gap: 7px; }.timeline-step { flex: none; width: 100%; }.timeline-step:not(:last-child)::after { width: 1px; height: 9px; flex: none; margin: 0 0 0 10px; } }
</style>
