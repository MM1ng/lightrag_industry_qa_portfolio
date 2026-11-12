import { describe, expect, it } from 'vitest'
import { extractAnswerPreview } from './streamPreview'

describe('extractAnswerPreview', () => {
  it('passes plain text through unchanged', () => {
    expect(extractAnswerPreview('维护前必须停机。')).toBe('维护前必须停机。')
  })

  it('pulls the answer text out of a partially streamed JSON envelope', () => {
    const partial = '{"status":"success","answer_points":[{"text":"维护/拆卸泵组前，应确保泵组已停机'
    expect(extractAnswerPreview(partial)).toBe('维护/拆卸泵组前，应确保泵组已停机')
  })

  it('joins multiple answer points with a newline', () => {
    const partial = '{"answer_points":[{"text":"必须先停机","source_ids":["S2"]},{"text":"并切断电源","source_ids":["S2"]}]}'
    expect(extractAnswerPreview(partial)).toBe('必须先停机\n并切断电源')
  })

  it('decodes escaped quotes and unicode inside the text field', () => {
    const partial = '{"answer_points":[{"text":"间隙\\u22640.1 mm，标注为\\"上限\\""}]}'
    expect(extractAnswerPreview(partial)).toBe('间隙≤0.1 mm，标注为"上限"')
  })

  it('returns an empty preview while only the envelope has arrived', () => {
    expect(extractAnswerPreview('{"status":"success","answer_points":[')).toBe('')
  })
})
