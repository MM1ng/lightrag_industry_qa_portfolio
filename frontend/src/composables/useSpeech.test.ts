import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSpeech } from './useSpeech'
import type { SpeechClip } from '../types/api'

class FakeAudio {
  static instances: FakeAudio[] = []
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  paused = false
  constructor(public src: string) { FakeAudio.instances.push(this) }
  async play() { this.paused = false }
  pause() { this.paused = true }
}

const clip = (bytes: number[] = [1]): SpeechClip => ({
  blob: new Blob([new Uint8Array(bytes)]),
  truncated: false,
  segments: 1,
})

beforeEach(() => {
  FakeAudio.instances = []
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('URL', { createObjectURL: () => 'blob:fake', revokeObjectURL: vi.fn() })
})

describe('useSpeech', () => {
  it('starts idle', () => {
    const speech = useSpeech(async () => clip())
    expect(speech.state.value).toBe('idle')
  })

  it('synthesizes then plays', async () => {
    const synthesize = vi.fn().mockResolvedValue(clip())
    const speech = useSpeech(synthesize)
    await speech.speak('温度 85 摄氏度')
    expect(synthesize).toHaveBeenCalledWith('温度 85 摄氏度')
    expect(speech.state.value).toBe('playing')
    expect(FakeAudio.instances).toHaveLength(1)
  })

  it('reports synthesis failure', async () => {
    const speech = useSpeech(async () => { throw new Error('boom') })
    await speech.speak('x')
    expect(speech.state.value).toBe('error')
    expect(speech.errorMessage.value).toContain('语音')
  })

  it('stop pauses playback and resets state', async () => {
    const speech = useSpeech(async () => clip())
    await speech.speak('x')
    speech.stop()
    expect(FakeAudio.instances[0].paused).toBe(true)
    expect(speech.state.value).toBe('idle')
  })

  it('does nothing for empty text', async () => {
    const synthesize = vi.fn()
    const speech = useSpeech(synthesize)
    await speech.speak('   ')
    expect(synthesize).not.toHaveBeenCalled()
    expect(speech.state.value).toBe('idle')
  })

  it('notices when part of the answer was not spoken', async () => {
    // Playing to a silent stop is exactly what truncation must not look like.
    const speech = useSpeech(async () => ({ ...clip(), truncated: true, segments: 3 }))
    await speech.speak('很长的回答')
    expect(speech.state.value).toBe('playing')
    expect(speech.notice.value).toContain('未朗读完整')
    expect(speech.notice.value).toContain('3')
  })

  it('clears the notice between answers', async () => {
    let truncated = true
    const speech = useSpeech(async () => {
      const result = { ...clip(), truncated, segments: 3 }
      truncated = false
      return result
    })
    await speech.speak('a')
    expect(speech.notice.value).not.toBe('')
    await speech.speak('b')
    expect(speech.notice.value).toBe('')
  })
})
