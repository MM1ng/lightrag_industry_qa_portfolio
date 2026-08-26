import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EvidenceDrawer from './EvidenceDrawer.vue'
import { useSessionStore } from '../../app/stores/session'

const evidence = [
  { evidence_id: 'E1', citation_id: 'c-1', document_name: '离心泵运行手册.pdf', document_id: 'doc-1', generation_id: 'gen-1', page: 12, chunk_id: 'hidden', section_path: ['启动'], excerpt: '确认入口阀门已打开。', relevance_label: '核心依据' },
  { evidence_id: 'E2', citation_id: 'c-2', document_name: '离心泵运行手册.pdf', document_id: 'doc-1', generation_id: 'gen-1', page: 13, chunk_id: 'hidden-2', excerpt: '观察压力表。', relevance_label: '补充依据' },
]

beforeEach(() => {
  setActivePinia(createPinia())
  useSessionStore().selectKnowledgeBase('kb-1')
  vi.restoreAllMocks()
})

afterEach(() => { document.body.innerHTML = '' })

describe('EvidenceDrawer', () => {
  it('opens with the selected evidence and can switch citations', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ document_id: 'doc-1', document_name: '离心泵运行手册.pdf', knowledge_base_id: 'kb-1', generation_id: 'gen-1', document_version: 2, page: 12, page_context: '入口阀门应保持开启。', excerpt: '确认入口阀门已打开。', source_available: true, source_url: '/source-file' }), { status: 200 }))
    const wrapper = mount(EvidenceDrawer, { props: { visible: true, evidence, selectedCitationId: 'c-1' }, attachTo: document.body })
    expect(document.body.textContent).toContain('确认入口阀门已打开。')
    await vi.waitFor(() => expect(document.body.textContent).toContain('入口阀门应保持开启。'))
    expect(document.body.textContent).not.toContain('hidden')
    await document.querySelectorAll('.evidence-switch button')[1].dispatchEvent(new MouseEvent('click'))
    expect(wrapper.emitted('select')?.[0]).toEqual(['c-2'])
  })

  it('highlights the selected excerpt inside the page context', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      document_id: 'doc-1',
      document_name: '离心泵运行手册.pdf',
      knowledge_base_id: 'kb-1',
      generation_id: 'gen-1',
      document_version: 2,
      page: 12,
      page_context: '启动前确认入口阀门已打开。随后观察压力表。',
      excerpt: '确认入口阀门已打开。',
      source_available: true,
      source_url: '/source-file',
    }), { status: 200 }))
    mount(EvidenceDrawer, { props: { visible: true, evidence, selectedCitationId: 'c-1' }, attachTo: document.body })

    await vi.waitFor(() => {
      expect(document.querySelector('.source-highlight')?.textContent).toBe('确认入口阀门已打开。')
    })
  })

  it('closes on Escape and explains empty evidence', async () => {
    const wrapper = mount(EvidenceDrawer, { props: { visible: true, evidence: [], selectedCitationId: null }, attachTo: document.body })
    expect(document.body.textContent).toContain('没有可展示的证据')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('preserves the excerpt and offers retry when the source cannot be opened', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ code: 'SOURCE_DOCUMENT_NOT_FOUND', message: '原始文档不存在或已不可用。', retryable: false }), { status: 404 }))
    mount(EvidenceDrawer, { props: { visible: true, evidence, selectedCitationId: 'c-1' }, attachTo: document.body })
    await vi.waitFor(() => expect(document.body.textContent).toContain('当前无法打开原文'))
    expect(document.body.textContent).toContain('确认入口阀门已打开。')
    expect(document.body.textContent).toContain('重试')
  })

  it('shows a clear fallback when citation identity is missing', async () => {
    mount(EvidenceDrawer, { props: { visible: true, evidence: [{ ...evidence[0], document_id: null }], selectedCitationId: 'c-1' }, attachTo: document.body })
    await vi.waitFor(() => expect(document.body.textContent).toContain('当前无法打开原文，已保留引用摘录供核验。'))
  })
})
