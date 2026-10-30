import type {
  ApiErrorShape,
  ChatHistoryItem,
  FeedbackInput,
  GraphPayload,
  DocumentSource,
  DocumentSummary,
  GenerationSummary,
  KnowledgeBase,
  KnowledgeBaseDetail,
  QueryResult,
  UpdateJob,
  EvaluationDashboard,
} from '../types/api'

export class ApiClientError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number

  constructor(error: ApiErrorShape, status: number) {
    super(error.message)
    this.name = 'ApiClientError'
    this.code = error.code
    this.retryable = error.retryable
    this.status = status
  }
}

type TokenProvider = () => string | null

export type QueryStreamHandlers = {
  onStage?: (stage: string) => void
  onDelta?: (text: string) => void
}

type QueryStreamEvent =
  | { type: 'stage'; stage: string }
  | { type: 'delta'; text: string }
  | { type: 'final'; payload: unknown }
  | { type: 'error'; code: string; message: string }

export class ApiClient {
  constructor(
    private readonly baseUrl = '',
    private readonly tokenProvider: TokenProvider = () => null,
  ) {}

  async listKnowledgeBases(): Promise<KnowledgeBase[]> {
    const payload = await this.request<{ items?: KnowledgeBase[] } | KnowledgeBase[]>('/v1/knowledge-bases')
    return Array.isArray(payload) ? payload : payload.items ?? []
  }

  async health(): Promise<{ status: string; service?: string }> {
    return this.request<{ status: string; service?: string }>('/health')
  }

  async queryKnowledgeBase(kbId: string, question: string, history: ChatHistoryItem[]): Promise<QueryResult> {
    const result = await this.request<QueryResult>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/query`, {
      method: 'POST',
      body: JSON.stringify({ query: question, history }),
    })
    return projectPublicQueryResult(result)
  }

  /**
   * Same query as `queryKnowledgeBase`, delivered as server-sent events.
   *
   * `onDelta` receives an unvalidated preview of the answer. The returned
   * result is the validated payload from the terminal `final` event and must
   * replace whatever the preview showed.
   */
  async queryKnowledgeBaseStream(
    kbId: string,
    question: string,
    history: ChatHistoryItem[],
    handlers: QueryStreamHandlers = {},
  ): Promise<QueryResult> {
    const headers = new Headers({ Accept: 'text/event-stream', 'Content-Type': 'application/json' })
    const token = this.tokenProvider()
    if (token) headers.set('Authorization', `Bearer ${token}`)

    let response: Response
    try {
      response = await fetch(`${this.baseUrl}/v1/knowledge-bases/${encodeURIComponent(kbId)}/query/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ query: question, history }),
      })
    } catch {
      throw new ApiClientError({ code: 'network_error', message: '服务暂时不可达，请检查服务状态后重试。', retryable: true }, 0)
    }

    if (!response.ok || !response.body) {
      throw await this.streamError(response)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let finalPayload: unknown

    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let boundary = buffer.indexOf('\n\n')
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const dataLine = frame.split('\n').find((line) => line.startsWith('data: '))
          if (dataLine) {
            const event = JSON.parse(dataLine.slice(6)) as QueryStreamEvent
            if (event.type === 'delta') handlers.onDelta?.(event.text)
            else if (event.type === 'stage') handlers.onStage?.(event.stage)
            else if (event.type === 'final') finalPayload = event.payload
            else if (event.type === 'error') {
              throw new ApiClientError({ code: event.code, message: event.message, retryable: true }, response.status)
            }
          }
          boundary = buffer.indexOf('\n\n')
        }
      }
    } finally {
      reader.cancel().catch(() => undefined)
    }

    if (finalPayload === undefined) {
      throw new ApiClientError({ code: 'stream_incomplete', message: '回答没有完成，请重试。', retryable: true }, 0)
    }
    const failure = (finalPayload as { error?: ApiErrorShape }).error
    if (failure) throw new ApiClientError(failure, 200)
    return projectPublicQueryResult(finalPayload as QueryResult)
  }

  private async streamError(response: Response): Promise<ApiClientError> {
    let error: Partial<ApiErrorShape> = {}
    try {
      const body = await response.json() as { code?: string; message?: string; detail?: { code?: string; message?: string } }
      error = body.detail ?? body
    } catch {
      // Never surface raw backend text in the UI.
    }
    return new ApiClientError({
      code: error.code || `http_${response.status}`,
      message: error.message || (response.status >= 500 ? '服务暂时不可用，请稍后重试。' : '请求未获授权或无法完成。'),
      retryable: response.status === 408 || response.status === 429 || response.status >= 500,
    }, response.status)
  }

  async submitFeedback(input: FeedbackInput): Promise<void> {
    await this.request('/v1/feedback', { method: 'POST', body: JSON.stringify(input) })
  }

  async verifyAdminAccess(): Promise<void> {
    await this.request('/v1/admin/feedback/metrics')
  }

  async getKnowledgeBase(kbId: string): Promise<KnowledgeBaseDetail> {
    return this.request<KnowledgeBaseDetail>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}`)
  }

  async listDocuments(kbId: string): Promise<DocumentSummary[]> {
    const payload = await this.request<{ items?: DocumentSummary[] }>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents`)
    return payload.items ?? []
  }

  async uploadDocument(kbId: string, file: File): Promise<unknown> {
    const body = new FormData(); body.append('file', file)
    return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents`, { method: 'POST', body })
  }

  async getDocument(kbId: string, documentId: string): Promise<DocumentSummary> {
    return this.request<DocumentSummary>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(documentId)}`)
  }

  async getDocumentSource(input: {
    kbId: string
    documentId: string
    page: number
    generationId?: string | null
    evidenceId?: string | null
    excerpt?: string
  }): Promise<DocumentSource> {
    const params = new URLSearchParams({ page: String(input.page) })
    if (input.generationId) params.set('generation_id', input.generationId)
    if (input.evidenceId) params.set('evidence_id', input.evidenceId)
    if (input.excerpt) params.set('excerpt', input.excerpt)
    return this.request<DocumentSource>(`/v1/knowledge-bases/${encodeURIComponent(input.kbId)}/documents/${encodeURIComponent(input.documentId)}/source?${params.toString()}`)
  }

  async replaceDocument(kbId: string, documentId: string, file: File): Promise<unknown> {
    const body = new FormData(); body.append('file', file)
    return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(documentId)}`, { method: 'PUT', body })
  }

  async deleteDocument(kbId: string, documentId: string): Promise<unknown> {
    return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' })
  }

  async listUpdateJobs(kbId: string): Promise<UpdateJob[]> {
    const payload = await this.request<{ items?: UpdateJob[] }>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/update-jobs`)
    return payload.items ?? []
  }

  async listGenerations(kbId: string): Promise<GenerationSummary[]> {
    return this.request<GenerationSummary[]>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations`)
  }

  async validateGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/validate`, { method: 'POST' }) }
  async promoteGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/promote`, { method: 'POST' }) }
  async rollbackGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/rollback`, { method: 'POST' }) }
  async diffGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/diff`) }

  async getEvaluationDashboard(kbId: string, generationId?: string, compareGenerationId?: string): Promise<EvaluationDashboard> {
    const params = new URLSearchParams()
    if (generationId) params.set('generation_id', generationId)
    if (compareGenerationId) params.set('compare_generation_id', compareGenerationId)
    const suffix = params.toString() ? `?${params.toString()}` : ''
    return this.request<EvaluationDashboard>(`/v1/admin/evaluation/knowledge-bases/${encodeURIComponent(kbId)}${suffix}`)
  }

  async getGraphOverview(kbId: string, limit = 50): Promise<GraphPayload> {
    return this.request<GraphPayload>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/graph/overview?limit=${limit}`)
  }

  async getGraphNeighborhood(kbId: string, query: string, hops: 1 | 2): Promise<GraphPayload> {
    return this.request<GraphPayload>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/graph/neighborhood?query=${encodeURIComponent(query)}&hops=${hops}`)
  }

  async request<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    const token = this.tokenProvider()
    if (token) headers.set('Authorization', `Bearer ${token}`)

    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, { ...init, headers })
    } catch {
      throw new ApiClientError({ code: 'network_error', message: '服务暂时不可达，请检查服务状态后重试。', retryable: true }, 0)
    }

    if (!response.ok) {
      let error: Partial<ApiErrorShape> = {}
      try {
        const body = await response.json() as { code?: string; message?: string; detail?: { code?: string; message?: string } }
        error = body.detail ?? body
      } catch {
        // Deliberately ignore non-JSON bodies so raw backend text cannot reach the UI.
      }
      throw new ApiClientError({
        code: error.code || `http_${response.status}`,
        message: error.message || (response.status >= 500 ? '服务暂时不可用，请稍后重试。' : '请求未获授权或无法完成。'),
        retryable: response.status === 408 || response.status === 429 || response.status >= 500,
      }, response.status)
    }

    if (response.status === 204) return undefined as T
    return await response.json() as T
  }
}

export const apiClient = new ApiClient(import.meta.env.VITE_API_BASE_URL ?? '')

function projectPublicQueryResult(result: QueryResult): QueryResult {
  return {
    request_id: result.request_id,
    status: result.status,
    answer: result.answer,
    citations: result.citations.map(({ citation_id, document_name, page, document_id, generation_id, evidence_id }) => ({ citation_id, document_name, page, document_id, generation_id, evidence_id })),
    claims: result.claims.map(({ claim_id, text, citation_ids, evidence_ids }) => ({ claim_id, text, citation_ids, evidence_ids })),
    evidence: result.evidence.map(({ evidence_id, citation_id, document_name, document_id, page, generation_id, section_path, excerpt, relevance_label }) => ({ evidence_id, citation_id, document_name, document_id, page, generation_id, section_path, excerpt, relevance_label })),
    partial_reason: result.partial_reason,
    latency_ms: result.latency_ms,
  }
}
