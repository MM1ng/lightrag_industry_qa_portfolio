import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ApiClient, ApiClientError } from './client'

const queryResult = {
  request_id: 'req-1', status: 'success', answer: '结论\n\n请停机。', citations: [], claims: [], evidence: [], latency_ms: 42,
}

beforeEach(() => vi.restoreAllMocks())

describe('ApiClient', () => {
  it('sends query and history and returns the public result', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(queryResult), { status: 200 }))
    const client = new ApiClient()
    const result = await client.queryKnowledgeBase('kb/1', '如何停机？', [{ role: 'user', content: '之前的问题' }])
    expect(result).toEqual(queryResult)
    expect(fetchMock).toHaveBeenCalledWith('/v1/knowledge-bases/kb%2F1/query', expect.objectContaining({ method: 'POST', body: JSON.stringify({ query: '如何停机？', history: [{ role: 'user', content: '之前的问题' }] }) }))
  })

  it('preserves insufficient evidence status', async () => {
    const result = { ...queryResult, status: 'insufficient_evidence', answer: '无法可靠回答。' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(result), { status: 200 }))
    await expect(new ApiClient().queryKnowledgeBase('kb-1', 'x', [])).resolves.toMatchObject({ status: 'insufficient_evidence' })
  })

  it('keeps source-opening ids but projects chunk ids out of the ordinary-user result', async () => {
    const result = { ...queryResult, citations: [{ citation_id: 'c-1', document_name: 'manual.pdf', page: 4, chunk_id: 'secret-chunk', document_id: 'doc-1', generation_id: 'gen-1', evidence_id: 'E1' }], evidence: [{ evidence_id: 'E1', citation_id: 'c-1', document_name: 'manual.pdf', document_id: 'doc-1', page: 4, chunk_id: 'secret-chunk', generation_id: 'gen-1', excerpt: '片段' }] }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(result), { status: 200 }))
    const projected = await new ApiClient().queryKnowledgeBase('kb-1', 'x', [])
    expect(projected.citations[0]).not.toHaveProperty('chunk_id')
    expect(projected.citations[0]).toMatchObject({ document_id: 'doc-1', generation_id: 'gen-1', evidence_id: 'E1' })
    expect(projected.evidence[0]).not.toHaveProperty('chunk_id')
    expect(projected.evidence[0]).toMatchObject({ document_id: 'doc-1', generation_id: 'gen-1' })
    expect(projected.evidence[0].excerpt).toBe('片段')
  })

  it('loads document source metadata for citation verification', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ document_id: 'doc-1', document_name: 'manual.pdf', knowledge_base_id: 'kb-1', generation_id: 'gen-1', document_version: 2, page: 4, page_context: '上下文', excerpt: '片段', source_available: true, source_url: '/source-file' }), { status: 200 }))
    const source = await new ApiClient().getDocumentSource({ kbId: 'kb-1', documentId: 'doc/1', page: 4, generationId: 'gen-1', evidenceId: 'E1', excerpt: '片段' })
    expect(source.page_context).toBe('上下文')
    expect(fetchMock).toHaveBeenCalledWith('/v1/knowledge-bases/kb-1/documents/doc%2F1/source?page=4&generation_id=gen-1&evidence_id=E1&excerpt=%E7%89%87%E6%AE%B5', expect.any(Object))
  })

  it('normalizes public 503 errors without exposing the raw body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('secret traceback', { status: 503 }))
    await expect(new ApiClient().getGraphOverview()).rejects.toMatchObject({ code: 'http_503', retryable: true, message: '服务暂时不可用，请稍后重试。' } satisfies Partial<ApiClientError>)
  })

  it('submits feedback with the request id', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    await new ApiClient().submitFeedback({ request_id: 'req-1', feedback_type: 'helpful' })
    expect(fetchMock).toHaveBeenCalledWith('/v1/feedback', expect.objectContaining({ method: 'POST', body: JSON.stringify({ request_id: 'req-1', feedback_type: 'helpful' }) }))
  })
})
