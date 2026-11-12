import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ChatView from '../../views/ChatView.vue'
import HighFrequencyPrompts from './HighFrequencyPrompts.vue'
import { useSessionStore } from '../../app/stores/session'

const result = { request_id: 'request-1', status: 'success', answer: '结论\n\n请先停机。', citations: [], claims: [], evidence: [], latency_ms: 19 }
const kb = { id: 'kb-1', name: 'KB', status: 'ready', document_count: 1, active_document_count: 1 }


function sseResponse(frames: unknown[]): Response {
  const body = frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join('')
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function sseFinal(payload: unknown): Response {
  return sseResponse([
    { type: 'stage', stage: 'retrieving' },
    { type: 'stage', stage: 'generating' },
    { type: 'delta', text: '结论' },
    { type: 'final', payload },
  ])
}

beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })

function readyStore() {
  const store = useSessionStore()
  store.setReadiness({ apiReachability: 'available', knowledgeBaseListState: 'success', knowledgeBases: [kb] })
  store.selectKnowledgeBase('kb-1')
  return store
}

describe('chat workbench', () => {
  it('emits one question from the high-frequency prompt groups', async () => {
    const wrapper = mount(HighFrequencyPrompts)
    expect(wrapper.text()).toContain('启动与停机')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('submit')).toHaveLength(1)
    expect(wrapper.emitted('submit')?.[0]).toEqual(['离心泵启动前需要检查哪些项目？'])
  })

  it('locks the composer while loading and renders the returned answer', async () => {
    let push!: (chunk: string) => void
    let close!: () => void
    const body = new ReadableStream({
      start(controller) {
        push = (chunk) => controller.enqueue(new TextEncoder().encode(chunk))
        close = () => controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
    readyStore()
    const wrapper = mount(ChatView)
    await wrapper.get('textarea').setValue('泵为什么振动？')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()

    push(`data: ${JSON.stringify({ type: 'delta', text: '结论：' })}\n\n`)
    await vi.waitFor(() => expect(wrapper.text()).toContain('生成中 · 未校验'))
    push(`data: ${JSON.stringify({ type: 'final', payload: result })}\n\n`)
    close()

    await vi.waitFor(() => expect(wrapper.text()).toContain('请先停机。'))
    expect(wrapper.text()).toContain('可执行回答')
    expect(wrapper.text()).not.toContain('生成中 · 未校验')
  })

  it('clears the composer after submitting a high-frequency prompt', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(sseFinal(result))
    readyStore()
    const wrapper = mount(ChatView)
    await wrapper.findComponent(HighFrequencyPrompts).get('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('请先停机。'))
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('clears the typed question after submitting', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(sseFinal(result))
    readyStore()
    const wrapper = mount(ChatView)
    await wrapper.get('textarea').setValue('泵为什么振动？')
    await wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('请先停机。'))
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('renders insufficient evidence and preserves the retry question', async () => {
    const insufficient = { ...result, status: 'insufficient_evidence', answer: '当前手册没有足够证据。' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(sseFinal(insufficient))
    const store = readyStore()
    const wrapper = mount(ChatView)
    await wrapper.get('textarea').setValue('特殊问题')
    await wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('证据不足'))
    expect(wrapper.text()).toContain('重新查询')
    expect(store.messages.find((message) => message.role === 'assistant')?.question).toBe('特殊问题')
  })

  it('disables questions when the service is unavailable', () => {
    const store = useSessionStore()
    store.setReadiness({ apiReachability: 'unavailable', knowledgeBaseListState: 'error', knowledgeBaseError: '服务暂不可用，请确认 FastAPI 已启动后重试。' })
    const wrapper = mount(ChatView)
    expect(wrapper.text()).toContain('FastAPI 服务暂不可用')
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('API 就绪')
  })

  it('shows an empty manual state for ordinary users', () => {
    const store = useSessionStore()
    store.setReadiness({ apiReachability: 'available', knowledgeBaseListState: 'empty', knowledgeBases: [] })
    const wrapper = mount(ChatView)
    expect(wrapper.text()).toContain('当前没有可用手册，请联系管理员上传手册。')
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
  })
})
