import { computed, shallowRef, watch } from 'vue'
import { ApiClient, ApiClientError } from '../api/client'
import { useSessionStore } from '../app/stores/session'
import type { EvaluationDashboard } from '../types/api'

export function useEvaluation() {
  const session = useSessionStore()
  const client = new ApiClient('', () => session.adminToken)
  const dashboard = shallowRef<EvaluationDashboard | null>(null)
  const loading = shallowRef(false)
  const error = shallowRef('')
  const kbId = computed(() => session.activeKnowledgeBaseId)

  async function load(generationId?: string, compareGenerationId?: string) {
    if (!kbId.value) return
    loading.value = true
    error.value = ''
    try {
      dashboard.value = await client.getEvaluationDashboard(kbId.value, generationId, compareGenerationId)
    } catch (cause) {
      error.value = cause instanceof ApiClientError ? cause.message : '评估结果暂时无法读取。'
    } finally {
      loading.value = false
    }
  }

  watch(kbId, () => { void load() }, { immediate: true })
  return { dashboard, loading, error, kbId, load }
}
