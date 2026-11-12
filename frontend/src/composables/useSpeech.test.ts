import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSpeech } from './useSpeech'

class FakeAudio {
  static instances: FakeAudio[] = []
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  paused = false
  constructor(public src: string) { FakeAudio.instances.push(this) }
  async play() { this.paused = false }
  pause() { this.paused = true }
}

beforeEach(() => {
  FakeAudio.instances = []
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('URL', { createObjectURL: () => 'blob:fake', revokeObjectURL: vi.fn() })
})

describe('useSpeech', () => {
  it('starts idle', () => {
    const speech = useSpeech(async () => new Blob([new Uint8Array([1])]))
    expect(speech.state.value).toBe('idle')
  })

  it('synthesizes then plays', async () => {
    const synthesize = vi.fn().mockResolvedValue(new Blob([new Uint8Array([1])]))
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
    const speech = useSpeech(async () => new Blob([new Uint8Array([1])]))
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
})
