/** Extract only commands preceded by the explicitly requested wake phrase. */
export function parseWakePhrase(transcript: string): { text: string } | null {
  const normalized = transcript.replace(/[\s，。！？、,.!?：:]/g, '')
  const phrase = '你好通通'
  const start = normalized.indexOf(phrase)
  if (start < 0) return null
  return { text: normalized.slice(start + phrase.length) }
}
