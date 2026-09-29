/**
 * 语音播报：服务端优先，浏览器兜底。
 *
 * 为什么不能只靠浏览器：`speechSynthesis` 按 BCP-47 选系统音色，四川话和
 * 东北话在绝大多数设备上没有对应音色，只能拿普通话念方言文本。服务端合成
 * 才有真方言音色（东北话/粤语）。
 *
 * 降级是正常路径而不是异常：离线、服务未启用、上游抖动都会走到浏览器。
 */
import { api } from './api'

export interface SpeakHandle {
  /** 主动停止；幂等。 */
  stop: () => void
}

export interface SpeakOptions {
  elderId: string
  text: string
  dialect?: string
  /** 语速，默认 0.92（适老化偏慢）。 */
  rate?: number
  onStart?: () => void
  /** 播放结束或失败后调用，用于清理 UI 状态。 */
  onEnd?: () => void
  onError?: (message: string) => void
}

/** 浏览器音色的语言标识。服务端不可用时才用得到。 */
function fallbackLang(dialect?: string): string {
  if (dialect === 'yue-HK') return 'zh-HK'
  return 'zh-CN'
}

/**
 * 播报一句话。返回句柄以便在用户离开页面或点"停止"时中断。
 *
 * 注意：服务端合成有网络往返，期间如果用户已经关闭面板，必须丢弃结果，
 * 否则声音会在面板关闭后突然响起。所以这里带 cancelled 检查。
 */
export function speakReply(options: SpeakOptions): SpeakHandle {
  const { elderId, text, dialect, rate = 0.92, onStart, onEnd, onError } = options
  let cancelled = false
  let audio: HTMLAudioElement | null = null
  let objectUrl: string | null = null

  const finish = () => {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl)
      objectUrl = null
    }
    audio = null
    onEnd?.()
  }

  const stop = () => {
    cancelled = true
    if (audio) {
      audio.pause()
      audio.src = ''
    }
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    finish()
  }

  const browserFallback = (reason?: string) => {
    if (cancelled) return
    if (!('speechSynthesis' in window)) {
      onError?.('当前设备没有可用的语音播报能力。')
      finish()
      return
    }
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = fallbackLang(dialect)
    utterance.rate = rate
    utterance.onstart = () => { if (!cancelled) onStart?.() }
    const settle = () => { if (!cancelled) finish() }
    utterance.onend = settle
    utterance.onerror = () => {
      if (!cancelled) {
        onError?.(reason || '语音播报失败，请查看屏幕文字。')
        finish()
      }
    }
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  }

  const trimmed = text.trim()
  if (!trimmed) {
    onEnd?.()
    return { stop }
  }

  void (async () => {
    try {
      const blob = await api.synthesizeSpeech(elderId, trimmed, dialect || 'zh-CN')
      if (cancelled) return
      if (!blob.size) throw new Error('empty audio')
      objectUrl = URL.createObjectURL(blob)
      audio = new Audio(objectUrl)
      const element = audio
      element.onplay = () => { if (!cancelled) onStart?.() }
      element.onended = () => { if (!cancelled) finish() }
      element.onerror = () => {
        if (!cancelled) browserFallback('音频播放失败，已改用系统语音。')
      }
      await element.play()
    } catch {
      // 服务端不可用是常态（离线、未部署、上游抖动），静默降级即可。
      browserFallback()
    }
  })()

  return { stop }
}

/** 停止所有正在进行的播报（浏览器侧）。 */
export function stopAllSpeech(): void {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}
