<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Volume2 } from 'lucide-vue-next'
import { api, ApiError } from '../lib/api'
import type { Broadcast } from '../types'

const props = defineProps<{ suspended?: boolean }>()
const ready = ref(false)
const voiceEnabled = ref(true)
const error = ref('')
const playingText = ref('')
let timer: number | undefined
let current: SpeechSynthesisUtterance | null = null
let ticking = false
let disposed = false
// A completed utterance awaiting an HTTP acknowledgement must not be spoken again.
const pendingAcknowledgements = new Set<string>()

function cancelOwnedSpeech() {
  if (!current) return
  current.onend = null
  current.onerror = null
  window.speechSynthesis.cancel()
  current = null
  playingText.value = ''
}

function speak(item: Broadcast, dialect: string) {
  const utterance = new SpeechSynthesisUtterance(item.text)
  utterance.lang = dialect === 'yue-HK' ? 'zh-HK' : 'zh-CN'
  utterance.rate = 0.92
  current = utterance
  playingText.value = item.text
  utterance.onend = () => {
    current = null
    playingText.value = ''
    pendingAcknowledgements.add(item.id)
    void tick()
  }
  utterance.onerror = () => {
    current = null
    playingText.value = ''
    ready.value = false
    error.value = '提醒声音未能播放，请点击开启播报后重试。'
  }
  window.speechSynthesis.speak(utterance)
}

async function tick() {
  if (disposed || ticking) return
  ticking = true
  try {
    for (const id of pendingAcknowledgements) {
      await api.playedBroadcast(id)
      pendingAcknowledgements.delete(id)
    }
    const [elder] = await api.elders()
    if (!elder || disposed) return
    voiceEnabled.value = elder.voice_enabled
    if (!elder.voice_enabled || props.suspended) {
      cancelOwnedSpeech()
      return
    }
    if (!ready.value || !('speechSynthesis' in window) || window.speechSynthesis.speaking || current) return
    const broadcasts = await api.broadcasts(elder.id)
    if (disposed || props.suspended) return
    const next = broadcasts.find(item => !item.played_at && !pendingAcknowledgements.has(item.id)
      && new Date(item.scheduled_at).getTime() <= Date.now())
    error.value = ''
    if (next) speak(next, elder.dialect)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '提醒记录同步暂不可用，正在等待恢复。'
  } finally {
    ticking = false
  }
}

function enable() {
  if (!('speechSynthesis' in window)) {
    error.value = '此浏览器没有声音播报能力，请查看屏幕提醒。'
    return
  }
  ready.value = true
  error.value = ''
  void tick()
}

watch(() => props.suspended, value => { if (value) cancelOwnedSpeech(); else void tick() })
onMounted(() => {
  ready.value = Boolean(navigator.userActivation?.hasBeenActive && 'speechSynthesis' in window)
  void tick()
  timer = window.setInterval(() => { void tick() }, 5000)
})
onBeforeUnmount(() => {
  disposed = true
  if (timer) window.clearInterval(timer)
  cancelOwnedSpeech()
})
</script>

<template>
  <div v-if="!suspended && voiceEnabled && (!ready || error || playingText)" class="broadcast-player" role="status">
    <Volume2 :size="22" aria-hidden="true" />
    <span v-if="playingText">正在播报：{{ playingText }}</span>
    <span v-else-if="error">{{ error }}</span>
    <span v-else>点击一次开启声音，之后会按时播报提醒。</span>
    <button v-if="!ready" type="button" class="secondary-button" @click="enable">开启提醒播报</button>
  </div>
</template>

<style scoped>
/* 令牌化：原来的 background 是写死的 #EDF4FC、字号写死 20px，深色模式下这条
   播报栏会一直保持浅色，字号也不会跟随「字号档位」。现改回令牌与 rem。 */
.broadcast-player { display:flex; flex-wrap:wrap; gap:var(--sp-12); align-items:center; padding:var(--sp-12) var(--sp-20); background:var(--brand-soft); border-bottom:1px solid var(--line); color:var(--brand-deep); font-size:1.25rem; line-height:1.5; }
/* 用 rem 而非 px：特大字号档位下换行位置才不会塌在一起 */
.broadcast-player > span { flex:1; min-width:11.25rem; }
.broadcast-player button { min-height:48px; }
</style>
