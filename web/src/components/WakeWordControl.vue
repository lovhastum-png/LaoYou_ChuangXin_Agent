<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Mic, MicOff } from 'lucide-vue-next'
import { parseWakePhrase } from '../lib/wake'

type RecognitionEvent = { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }
type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean
  onresult: ((event: RecognitionEvent) => void) | null
  onstart: (() => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void; abort(): void
}
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition
  webkitSpeechRecognition?: new () => Recognition
}

const props = defineProps<{ voiceEnabled: boolean; dialect?: string }>()
const emit = defineEmits<{ wake: [payload: { text: string }] }>()
const enabled = ref(false)
const listening = ref(false)
const error = ref('')
let recognizer: Recognition | null = null
let restartTimer: number | undefined
let disposed = false
const preference = 'laoyou.wake.enabled'

function release() {
  if (restartTimer) window.clearTimeout(restartTimer)
  restartTimer = undefined
  if (recognizer) {
    recognizer.onend = null
    recognizer.onstart = null
    recognizer.onresult = null
    recognizer.onerror = null
    recognizer.abort()
  }
  recognizer = null
  listening.value = false
}

function stop() {
  enabled.value = false
  sessionStorage.removeItem(preference)
  release()
}

function start() {
  if (disposed || !props.voiceEnabled) return
  const Speech = (window as SpeechWindow).SpeechRecognition || (window as SpeechWindow).webkitSpeechRecognition
  if (!Speech) {
    error.value = '此浏览器没有语音识别能力，请点击“你好通通”使用文字入口。'
    stop()
    return
  }
  // The optional dialect service records commands on demand; it is not a local
  // always-on wake engine. Never mislabel Mandarin recognition as dialect support.
  if (['sichuan', 'northeast'].includes(props.dialect || '')) {
    error.value = '当前方言请点击“你好通通”录音；持续唤醒使用设备普通话或粤语识别。'
    stop()
    return
  }
  release()
  error.value = ''
  enabled.value = true
  sessionStorage.setItem(preference, '1')
  const current = new Speech()
  recognizer = current
  current.lang = props.dialect === 'yue-HK' ? 'zh-HK' : 'zh-CN'
  current.continuous = true
  current.interimResults = false
  current.onstart = () => { listening.value = true }
  current.onresult = (event) => {
    for (let index = event.resultIndex; index < event.results.length; index++) {
      const result = event.results[index]
      if (!result.isFinal) continue
      const match = parseWakePhrase(result[0].transcript)
      if (!match) continue
      // Keep the user's opt-in for a later return to the home screen, but release
      // this recognizer before the assistant begins its own command interaction.
      release()
      enabled.value = false
      emit('wake', match)
      return
    }
  }
  current.onerror = ({ error: reason }) => {
    if (reason === 'no-speech' || reason === 'aborted') return
    error.value = reason === 'not-allowed' || reason === 'service-not-allowed'
      ? '未获得麦克风或语音服务权限，请点击文字入口，或允许权限后重试。'
      : '设备语音识别暂不可用，请点击“你好通通”使用文字入口。'
    stop()
  }
  current.onend = () => {
    listening.value = false
    if (!disposed && enabled.value) restartTimer = window.setTimeout(start, 600)
  }
  try {
    current.start()
  } catch {
    error.value = '语音识别未能启动，请稍后重试或使用文字入口。'
    stop()
  }
}

watch(() => props.voiceEnabled, (value) => { if (!value) stop() })
watch(() => props.dialect, () => { if (enabled.value) start() })
onMounted(() => { if (sessionStorage.getItem(preference) === '1') start() })
onBeforeUnmount(() => { disposed = true; release() })
</script>

<template>
  <div class="wake-control">
    <button class="text-button" type="button" :disabled="!voiceEnabled" @click="enabled ? stop() : start()">
      <Mic v-if="listening" :size="21" aria-hidden="true" /><MicOff v-else :size="21" aria-hidden="true" />
      {{ enabled ? '关闭语音唤醒' : '开启语音唤醒' }}
    </button>
    <span v-if="listening" role="status">正在聆听，说“你好通通”即可唤醒</span>
    <span v-else-if="!error">首次开启需要允许麦克风</span>
    <span v-if="error" class="wake-error" role="alert">{{ error }}</span>
  </div>
</template>

<style scoped>
/* 字号与间距改为 rem / --sp-*，跟随字号档位与密度档位 */
.wake-control { display:flex; flex-wrap:wrap; align-items:center; gap:var(--sp-10) var(--sp-18); margin:var(--sp-12) 2px 0; font-size:1.125rem; line-height:1.5; color:var(--muted-strong); }
.wake-control button { min-height:48px; font-size:1.25rem; }
.wake-error { color:var(--red); }
</style>
