import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import GraphView from '../../views/GraphView.vue'
import { useSessionStore } from '../../app/stores/session'

const kb = { id: 'kb-1', name: 'KB', status: 'ready', document_count: 1, active_document_count: 1 }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})

function readyStore() {
  const store = useSessionStore()
  store.setReadiness({ apiReachability: 'available', knowledgeBaseListState: 'success', knowledgeBases: [kb] })
  store.selectKnowledgeBase('kb-1')
  return store
}

describe('GraphView', () => {
  it('loads the read-only overview and exposes entity search controls', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ nodes: [{ id: 'n1', label: '离心泵', type: '设备', x: 0, y: 0, degree: 1 }], edges: [], stats: { node_count: 1, edge_count: 0, mode: 'overview', query: null } }), { status: 200 }))
    readyStore()
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('离心泵'))
    expect(wrapper.text()).toContain('常用实体')
    expect(wrapper.text()).toContain('只读')
  })

  it('embeds the native graph document scoped to the active knowledge base', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ nodes: [], edges: [], stats: { node_count: 0, edge_count: 0, mode: 'overview', query: null } }), { status: 200 }))
    readyStore()
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    await vi.waitFor(() => expect(wrapper.find('iframe').exists()).toBe(true))
    expect(wrapper.find('iframe').attributes('src')).toContain('/v1/knowledge-bases/kb-1/graph/native')
  })

  it('asks for a knowledge base instead of loading an unscoped graph', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.text()).toContain('请先选择一个可用知识库')
  })

  it('shows a retryable graph failure instead of a blank iframe', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('service down', { status: 503 }))
    readyStore()
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('图谱暂不可用'))
    expect(wrapper.text()).toContain('图谱服务暂不可用')
    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.text()).toContain('重试')
  })

  it('shows an empty graph state with retry', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 200 }))
    readyStore()
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('当前没有可展示的图谱数据'))
    expect(wrapper.find('iframe').exists()).toBe(false)
  })
})
