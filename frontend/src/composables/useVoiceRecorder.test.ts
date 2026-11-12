import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useVoiceRecorder } from './useVoiceRecorder'

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  state: 'inactive' | 'recording' = 'inactive'
  constructor(public stream: MediaStream) {
    FakeMediaRecorder.instances.push(this)
  }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob([new Uint8Array([1])], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

function fakeStream(): MediaStream {
  return { getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream
}

beforeEach(() => {
  FakeMediaRecorder.instances = []
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
})

describe('useVoiceRecorder', () => {
  it('starts idle', () => {
    const recorder = useVoiceRecorder()
    expect(recorder.state.value).toBe('idle')
  })

  it('reports permission denial without throwing', async () => {
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new DOMException('no', 'NotAllowedError')) },
    })
    const recorder = useVoiceRecorder()
    await recorder.start()
    expect(recorder.state.value).toBe('denied')
    expect(recorder.errorMessage.value).toContain('麦克风')
  })

  it('records and produces a blob on stop', async () => {
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream()) },
    })
    // Pin the clock so the synthetic recording is longer than the minimum
    // duration; real elapsed time in a unit test is only a few milliseconds.
    let now = 1_000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    const recorder = useVoiceRecorder()
    await recorder.start()
    expect(recorder.state.value).toBe('recording')

    now += 900
    const result = await recorder.stop()
    expect(result).not.toBeNull()
    expect(result!.blob).toBeInstanceOf(Blob)
    expect(result!.durationMs).toBe(900)
    expect(recorder.state.value).toBe('idle')
  })

  it('rejects recordings shorter than the minimum', async () => {
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream()) },
    })
    let now = 1_000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    const recorder = useVoiceRecorder({ minDurationMs: 500 })
    await recorder.start()
    now += 200
    const result = await recorder.stop()
    expect(result).toBeNull()
    expect(recorder.state.value).toBe('too-short')
  })

  it('stops all tracks after stopping', async () => {
    const stopTrack = vi.fn()
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }) },
    })
    const recorder = useVoiceRecorder()
    await recorder.start()
    await recorder.stop()
    expect(stopTrack).toHaveBeenCalled()
  })
})
