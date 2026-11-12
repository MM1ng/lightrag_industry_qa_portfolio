/**
 * Extract a readable answer preview from a partially streamed model response.
 *
 * In structured-citation mode the model streams a JSON envelope, so raw
 * fragments look like `{"status":"success","answer_points":[{"text":"维护前…"`.
 * Showing that to a reader is worse than showing nothing, so the preview keeps
 * only the `text` field values decoded and joined.
 *
 * Plain-text answers pass through unchanged: the extractor only switches on when
 * the buffer looks like the structured envelope.
 */
const TEXT_FIELD = /"text"\s*:\s*"((?:[^"\\]|\\.)*)(?:"|$)/g

export function extractAnswerPreview(raw: string): string {
  const trimmed = raw.trimStart()
  const looksStructured = trimmed.startsWith('{') || trimmed.includes('"answer_points"')
  if (!looksStructured) return raw

  const parts: string[] = []
  TEXT_FIELD.lastIndex = 0
  for (;;) {
    const match = TEXT_FIELD.exec(raw)
    if (!match) break
    parts.push(decodeJsonString(match[1]))
  }
  return parts.join('\n')
}

function decodeJsonString(value: string): string {
  // The newest field may be cut mid-escape; dropping the dangling backslash
  // beats showing "\u00a" to a reader for one frame.
  const safe = value.replace(/\\u?[0-9a-fA-F]{0,3}$/, '')
  try {
    return JSON.parse(`"${safe}"`) as string
  } catch {
    return safe
  }
}
