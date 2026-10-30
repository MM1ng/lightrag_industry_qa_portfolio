import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AnswerMessage from './AnswerMessage.vue'
import type { AssistantMessage } from '../../app/stores/session'
import type { QueryResult } from '../../types/api'

const citations = [
  { citation_id: 'cite_1', evidence_id: 'E1', document_name: 't1739cn.pdf', document_id: 'doc-1', page: 49, chunk_id: 'c1' },
  { citation_id: 'cite_2', evidence_id: 'E2', document_name: 't1739cn.pdf', document_id: 'doc-1', page: 48, chunk_id: 'c2' },
]

function message(result: Partial<QueryResult>): AssistantMessage {
  return {
    id: 'm1',
    role: 'assistant',
    question: '拆卸泵体前有哪些安全注意事项？',
    createdAt: '2026-10-30T00:00:00Z',
    result: {
      request_id: 'r1',
      status: 'success',
      answer: '',
      citations: [],
      claims: [],
      evidence: [],
      latency_ms: 1000,
      ...result,
    } as QueryResult,
  }
}

describe('AnswerMessage', () => {
  it('does not repeat the answer when claims are the same sentences', () => {
    const answer = '拆卸前应确保水泵已停机。[1]\n拆卸泵体前应切断泵组电源。[2]'
    const wrapper = mount(AnswerMessage, {
      props: {
        message: message({
          answer,
          citations,
          claims: [
            { claim_id: 'claim_1', text: '拆卸前应确保水泵已停机。[1]', citation_ids: ['cite_1'], evidence_ids: [] },
            { claim_id: 'claim_2', text: '拆卸泵体前应切断泵组电源。[2]', citation_ids: ['cite_2'], evidence_ids: [] },
          ],
        }),
      },
    })

    expect(wrapper.findAll('.answer-body p')).toHaveLength(2)
    // The verification block would be a verbatim second copy, so it is dropped.
    expect(wrapper.text()).not.toContain('核验要点')
    expect(wrapper.findAll('.claims p')).toHaveLength(0)
  })

  it('keeps claims that say something the answer body does not', () => {
    const wrapper = mount(AnswerMessage, {
      props: {
        message: message({
          answer: '拆卸前应确保水泵已停机。[1]',
          citations,
          claims: [
            { claim_id: 'claim_1', text: '拆卸前应确保水泵已停机。[1]', citation_ids: ['cite_1'], evidence_ids: [] },
            { claim_id: 'claim_2', text: '冷却至环境温度后方可接触泵体。[2]', citation_ids: ['cite_2'], evidence_ids: [] },
          ],
        }),
      },
    })

    const claims = wrapper.findAll('.claims p')
    expect(claims).toHaveLength(1)
    expect(claims[0].text()).toContain('冷却至环境温度后方可接触泵体')
  })

  it('treats a legacy whole-answer claim as a duplicate', () => {
    const answer = '第一句。[1]\n第二句。[2]'
    const wrapper = mount(AnswerMessage, {
      props: {
        message: message({
          answer,
          citations,
          claims: [{ claim_id: 'claim_1', text: answer, citation_ids: ['cite_1'], evidence_ids: [] }],
        }),
      },
    })

    expect(wrapper.text()).not.toContain('核验要点')
  })

  it('emits selectCitation when an inline marker in the body is clicked', async () => {
    const wrapper = mount(AnswerMessage, {
      props: {
        message: message({ answer: '拆卸前应确保水泵已停机。[2]', citations }),
      },
    })

    await wrapper.get('.answer-body button[data-cite-index="2"]').trigger('click')

    expect(wrapper.emitted('selectCitation')).toEqual([['cite_2']])
  })
})
