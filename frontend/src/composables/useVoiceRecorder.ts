import { ref } from 'vue'

export type RecorderState =
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'denied'
  | 'too-short'

export interface RecordingResult {
  blob: Blob
  durationMs: number
}

export interface UseVoiceRecorderOptions {
  minDurationMs?: number
  maxDurationMs?: number
}

/**
 * Microphone capture state machine.
 *
 * The recording is never uploaded from here: the caller decides what to do with
 * the blob. Recordings shorter than `minDurationMs` are rejected locally because
 * the ASR service hallucinates text for near-silent input instead of failing.
 */
export function useVoiceRecorder(options: UseVoiceRecorderOptions = {}) {
  const minDurationMs = options.minDurationMs ?? 500
  const maxDurationMs = options.maxDurationMs ?? 60_000

  const state = ref<RecorderState>('idle')
  const errorMessage = ref('')

  let recorder: MediaRecorder | null = null
  let stream: MediaStream | null = null
  let startedAt = 0
  let chunks: Blob[] = []
  let autoStopTimer: ReturnType<typeof setTimeout> | null = null

  function clearTimer(): void {
    if (autoStopTimer !== null) {
      clearTimeout(autoStopTimer)
      autoStopTimer = null
    }
  }

  async function start(): Promise<void> {
    state.value = 'requesting'
    errorMessage.value = ''
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (error) {
      const name = (error as DOMException)?.name ?? ''
      state.value = 'denied'
      errorMessage.value =
        name === 'NotAllowedError'
          ? '请允许麦克风权限后再试。'
          : '无法访问麦克风，请检查设备。'
      return
    }

    chunks = []
    recorder = new MediaRecorder(stream)
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    recorder.start()
    startedAt = Date.now()
    state.value = 'recording'

    if (maxDurationMs > 0) {
      autoStopTimer = setTimeout(() => {
        if (state.value === 'recording') void stop()
      }, maxDurationMs)
    }
  }

  async function stop(): Promise<RecordingResult | null> {
    if (!recorder || state.value !== 'recording') return null
    clearTimer()
    const durationMs = Date.now() - startedAt

    const active = recorder
    const finished = new Promise<Blob>((resolve) => {
      active.onstop = () => resolve(new Blob(chunks, { type: 'audio/webm' }))
    })
    active.stop()
    const blob = await finished

    stream?.getTracks().forEach((track) => track.stop())
    stream = null
    recorder = null

    if (durationMs < minDurationMs || blob.size === 0) {
      state.value = 'too-short'
      errorMessage.value = '说话时间太短，请重试。'
      return null
    }

    state.value = 'idle'
    return { blob, durationMs }
  }

  function cancel(): void {
    clearTimer()
    if (recorder && state.value === 'recording') {
      recorder.onstop = null
      recorder.stop()
    }
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
    recorder = null
    chunks = []
    state.value = 'idle'
  }

  return { state, errorMessage, start, stop, cancel }
}
