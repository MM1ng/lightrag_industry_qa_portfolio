import { ref } from 'vue'
import type { SpeechClip } from '../types/api'

export type SpeechState = 'idle' | 'synthesizing' | 'playing' | 'error'

export type Synthesize = (text: string) => Promise<SpeechClip>

/**
 * Playback state machine for spoken answers.
 *
 * Synthesis is injected rather than imported so the composable can be tested
 * without network access. Only one clip plays at a time: calling `speak` again
 * stops the previous one.
 *
 * `notice` is for answers that played successfully but were not spoken whole —
 * a truncated clip must not be reported as an error, and it must not play into
 * a silent stop either, because the user would read the cut as the real answer.
 */
export function useSpeech(synthesize: Synthesize) {
  const state = ref<SpeechState>('idle')
  const errorMessage = ref('')
  const notice = ref('')

  let audio: HTMLAudioElement | null = null
  let objectUrl: string | null = null

  function cleanup(): void {
    if (audio) {
      audio.onended = null
      audio.onerror = null
      audio.pause()
      audio = null
    }
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl)
      objectUrl = null
    }
  }

  async function speak(text: string): Promise<void> {
    const trimmed = text.trim()
    if (!trimmed) return

    stop()
    state.value = 'synthesizing'
    errorMessage.value = ''

    let clip: SpeechClip
    try {
      clip = await synthesize(trimmed)
    } catch {
      state.value = 'error'
      errorMessage.value = '语音合成失败，请稍后重试。'
      return
    }
    notice.value = clip.truncated
      ? `回答较长，本次朗读分 ${clip.segments} 段合成，未朗读完整内容。`
      : ''

    objectUrl = URL.createObjectURL(clip.blob)
    audio = new Audio(objectUrl)
    audio.onended = () => {
      cleanup()
      state.value = 'idle'
    }
    audio.onerror = () => {
      cleanup()
      state.value = 'error'
      errorMessage.value = '音频播放失败。'
    }

    try {
      await audio.play()
      state.value = 'playing'
    } catch {
      cleanup()
      state.value = 'error'
      errorMessage.value = '浏览器阻止了音频播放。'
    }
  }

  function stop(): void {
    cleanup()
    notice.value = ''
    state.value = 'idle'
  }

  return { state, errorMessage, notice, speak, stop }
}
