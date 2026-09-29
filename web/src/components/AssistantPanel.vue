<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Check, LoaderCircle, MessageCircleHeart, Mic, MicOff, Send, Volume2, X } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { assistantActionLabel } from '../lib/format'
import type { AssistantResponse } from '../types'

interface RecognitionResultEvent {
  results: ArrayLike<{ isFinal: boolean; 0?: { transcript: string } }>
}

interface RecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: RecognitionResultEvent) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}

type RecognitionConstructor = new () => RecognitionLike

const props = defineProps<{
  elderId: string
  voiceEnabled: boolean
  dialect?: string
  dialectAvailable?: boolean
}>()

const emit = defineEmits<{
  close: []
  callRequested: [callId: string]
  cameraClosed: []
}>()

const text = ref('')
const interim = ref('')
const listening = ref(false)
const busy = ref(false)
const speaking = ref(false)
const recording = ref(false)
const recordingSeconds = ref(0)
const error = ref('')
const confirmToken = ref<string | null>(null)
const lastResponse = ref<AssistantResponse | null>(null)
const messages = ref<Array<{ role: 'user' | 'assistant'; text: string }>>([])
let recognition: RecognitionLike | null = null
let audioContext: AudioContext | null = null
let audioSource: MediaStreamAudioSourceNode | null = null
let audioProcessor: ScriptProcessorNode | null = null
let audioStream: MediaStream | null = null
let disposed = false
let audioChunks: Float32Array[] = []
let recordingTimer: number | undefined

const canUseSpeech = computed(() => {
  const speechWindow = window as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor }
  return Boolean(speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition)
})

const needsServerDialect = computed(() => ['yue-HK', 'sichuan', 'northeast'].includes(props.dialect || ''))
const proposalSummary = computed(() => {
  const response = lastResponse.value
  if (!response?.proposal) return ''
  if (response.action === 'camera_confirm') return '关闭后停止监控采集，并清除最近的画面。'
  if (response.action === 'reminder_proposal') {
    const proposal = response.proposal
    return `每天 ${proposal.time}，提醒服用 ${proposal.medicine}。剂量：${proposal.dose || '尚未填写，可在提醒设置中补充'}。`
  }
  return response.reply
})

function say(reply: string) {
  if (!props.voiceEnabled || !('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(reply)
  utterance.lang = props.dialect === 'yue-HK' ? 'zh-HK' : 'zh-CN'
  utterance.rate = 0.92
  utterance.onstart = () => { speaking.value = true }
  utterance.onend = () => { speaking.value = false }
  utterance.onerror = () => { speaking.value = false }
  window.speechSynthesis.speak(utterance)
}

async function sendMessage(value = text.value) {
  const content = value.trim()
  if (!content || busy.value) return
  text.value = ''
  interim.value = ''
  error.value = ''
  messages.value.push({ role: 'user', text: content })
  busy.value = true
  try {
    const confirming = content.replace(/[\s，。！？,.!?]/g, '') === '确认' && Boolean(confirmToken.value)
    const token = confirming ? confirmToken.value || undefined : undefined
    if (!confirming) confirmToken.value = null
    const response = await api.assistant(props.elderId, confirming ? '确认' : content, props.dialect, token)
    lastResponse.value = response
    confirmToken.value = response.confirm_token
    messages.value.push({ role: 'assistant', text: response.reply })
    say(response.reply)
    if (response.action === 'call' && typeof response.proposal?.call_id === 'string') emit('callRequested', response.proposal.call_id)
    if (response.action === 'camera_updated' && response.proposal?.camera_enabled === false) emit('cameraClosed')
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法联系老友服务，请稍后再试。'
  } finally {
    busy.value = false
  }
}

function confirmProposal() {
  if (confirmToken.value) void sendMessage('确认')
}

/**
 * 中文输入法用回车确认候选词时也会触发 keydown.enter。
 * 不判断组合态就会把半成品文本直接发出去。
 */
function onEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void sendMessage()
}

function downsample(samples: Float32Array[], inputRate: number, outputRate: number): Float32Array {
  const totalLength = samples.reduce((total, sample) => total + sample.length, 0)
  const joined = new Float32Array(totalLength)
  let offset = 0
  for (const sample of samples) {
    joined.set(sample, offset)
    offset += sample.length
  }
  if (inputRate === outputRate) return joined
  const ratio = inputRate / outputRate
  const output = new Float32Array(Math.max(1, Math.round(joined.length / ratio)))
  for (let index = 0; index < output.length; index += 1) {
    const start = Math.floor(index * ratio)
    const end = Math.min(joined.length, Math.floor((index + 1) * ratio))
    let total = 0
    let count = 0
    for (let cursor = start; cursor < end; cursor += 1) {
      total += joined[cursor]
      count += 1
    }
    output[index] = count ? total / count : 0
  }
  return output
}

function encodePcm16(samples: Float32Array): Blob {
  const buffer = new ArrayBuffer(samples.length * 2)
  const view = new DataView(buffer)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
  }
  return new Blob([buffer], { type: 'audio/L16' })
}

function cleanupRecording() {
  if (recordingTimer) window.clearInterval(recordingTimer)
  recordingTimer = undefined
  audioProcessor?.disconnect()
  audioSource?.disconnect()
  audioStream?.getTracks().forEach((track) => track.stop())
  void audioContext?.close()
  audioProcessor = null
  audioSource = null
  audioStream = null
  audioContext = null
}

async function stopDialectRecording() {
  if (!recording.value) return
  recording.value = false
  const contextRate = audioContext?.sampleRate || 48_000
  cleanupRecording()
  const chunks = audioChunks
  audioChunks = []
  if (!chunks.length) {
    error.value = '没有录到声音，请再试一次。'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const pcm = encodePcm16(downsample(chunks, contextRate, 16_000))
    const response = await api.speech(props.elderId, props.dialect || '', pcm)
    if (!response.text.trim()) {
      error.value = '方言服务没有识别到文字，请再说一次或改用文字入口。'
      return
    }
    busy.value = false
    await sendMessage(response.text)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '方言识别暂时不可用，请改用普通话或文字入口。'
  } finally {
    busy.value = false
  }
}

async function startDialectRecording() {
  if (!props.dialectAvailable) {
    error.value = '当前服务尚未配置该方言识别，请使用文字入口。'
    return
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    error.value = '当前浏览器不支持麦克风录音，请使用文字入口。'
    return
  }
  if (recording.value) {
    await stopDialectRecording()
    return
  }
  try {
    const AudioContextCtor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioContextCtor) throw new Error('unsupported')
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
    // 授权弹窗未决期间用户可能已经关闭面板：卸载钩子跑在前面，这里必须补收流，
    // 否则麦克风会一直开着。
    if (disposed) {
      audioStream.getTracks().forEach((track) => track.stop())
      audioStream = null
      return
    }
    audioContext = new AudioContextCtor()
    audioSource = audioContext.createMediaStreamSource(audioStream)
    audioProcessor = audioContext.createScriptProcessor(4096, 1, 1)
    audioChunks = []
    audioProcessor.onaudioprocess = (event) => {
      audioChunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
    }
    audioSource.connect(audioProcessor)
    const silentGain = audioContext.createGain()
    silentGain.gain.value = 0
    audioProcessor.connect(silentGain)
    silentGain.connect(audioContext.destination)
    recording.value = true
    recordingSeconds.value = 0
    error.value = ''
    recordingTimer = window.setInterval(() => {
      recordingSeconds.value += 1
      if (recordingSeconds.value >= 30) void stopDialectRecording()
    }, 1000)
  } catch (cause) {
    cleanupRecording()
    error.value = cause instanceof Error && cause.message === 'unsupported' ? '当前浏览器不支持音频采集，请使用文字入口。' : (cause instanceof DOMException && cause.name === 'NotAllowedError' ? '浏览器没有授予麦克风权限，请改用文字入口。' : '方言录音启动失败，请检查麦克风权限。')
  }
}

function startListening() {
  if (!props.voiceEnabled) {
    error.value = '语音入口已关闭，请在安全设置中开启，或直接输入文字。'
    return
  }
  if (needsServerDialect.value) {
    void startDialectRecording()
    return
  }
  if (!canUseSpeech.value) {
    error.value = '当前浏览器不支持语音识别，请直接输入文字。'
    return
  }
  if (listening.value) {
    recognition?.stop()
    return
  }
  const speechWindow = window as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor }
  const Recognition = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
  if (!Recognition) return
  recognition = new Recognition()
  recognition.lang = props.dialect === 'yue-HK' ? 'zh-HK' : 'zh-CN'
  recognition.continuous = false
  recognition.interimResults = true
  let recognizedText = ''
  recognition.onresult = (event) => {
    let finalText = ''
    let interimText = ''
    for (let index = 0; index < event.results.length; index += 1) {
      const result = event.results[index]
      if (!result?.[0]) continue
      if (result.isFinal) finalText += result[0].transcript
      else interimText += result[0].transcript
    }
    interim.value = interimText
    if (finalText) { recognizedText = finalText; text.value = finalText }
  }
  recognition.onerror = (event) => {
    listening.value = false
    error.value = event.error === 'not-allowed' ? '浏览器没有授予麦克风权限，请改用文字入口。' : '没有听清，请再说一次或直接输入文字。'
  }
  recognition.onend = () => {
    listening.value = false
    if (recognizedText.trim()) void sendMessage(recognizedText)
  }
  try {
    listening.value = true
    error.value = ''
    recognition.start()
  } catch {
    listening.value = false
    error.value = '语音识别暂时无法启动，请直接输入文字。'
  }
}

function stopSpeaking() {
  window.speechSynthesis?.cancel()
  speaking.value = false
}

defineExpose({ sendMessage })

onBeforeUnmount(() => {
  disposed = true
  if (recognition) {
    recognition.onend = null
    recognition.onresult = null
    recognition.onerror = null
    recognition.stop()
  }
  recording.value = false
  cleanupRecording()
  audioChunks = []
  if (speaking.value) window.speechSynthesis?.cancel()
})
</script>

<template>
  <section class="assistant-panel" aria-labelledby="assistant-title">
    <header class="assistant-header">
      <div class="assistant-title-wrap">
        <span class="assistant-icon"><MessageCircleHeart :size="28" :stroke-width="1.8" /></span>
        <div>
          <h2 id="assistant-title">你好通通</h2>
          <p>有什么需要，直接告诉我</p>
        </div>
      </div>
      <button class="icon-button on-dark" type="button" aria-label="关闭语音面板" title="关闭" @click="emit('close')"><X :size="23" /></button>
    </header>

    <div class="assistant-messages" aria-live="polite">
      <div v-if="messages.length === 0" class="assistant-empty">可以试试：问天气、查健康，或说“每天晚上八点提醒我吃药”。</div>
      <div v-for="(message, index) in messages" :key="`${message.role}-${index}`" class="assistant-message" :class="message.role">
        <span>{{ message.text }}</span>
      </div>
      <div v-if="busy" class="assistant-message assistant pending"><LoaderCircle class="spin" :size="18" />正在听通通回复…</div>
    </div>

    <div v-if="lastResponse && confirmToken" class="assistant-proposal">
      <div>
        <strong>{{ assistantActionLabel(lastResponse.action) }}</strong>
        <span v-if="lastResponse.proposal">{{ proposalSummary }}</span>
      </div>
      <button class="small-primary" type="button" :disabled="busy" @click="confirmProposal"><Check :size="17" />确认执行</button>
    </div>

    <p v-if="error" class="assistant-error" role="alert">{{ error }}</p>
    <div class="assistant-input-row">
      <button class="mic-button" :class="{ listening: listening || recording }" type="button" :aria-label="listening || recording ? '停止听取' : '开始说话'" :title="listening || recording ? '停止听取' : '开始说话'" @click="startListening">
        <MicOff v-if="listening || recording" :size="23" />
        <Mic v-else :size="23" />
      </button>
      <input v-model="text" type="text" placeholder="输入文字告诉通通" @keydown.enter="onEnter" />
      <button class="send-button" type="button" aria-label="发送" title="发送" :disabled="busy || !text.trim()" @click="sendMessage()"><Send :size="21" /></button>
    </div>
    <div class="assistant-tools">
      <span v-if="recording">方言录音 {{ recordingSeconds }}/30 秒，停止后发送识别</span>
      <span v-else>{{ props.voiceEnabled ? (needsServerDialect && props.dialectAvailable ? '已配置方言识别' : '浏览器系统语音识别') : '语音已关闭，文字入口可用' }}</span>
      <button v-if="speaking" class="text-button on-dark-text" type="button" @click="stopSpeaking"><Volume2 :size="16" />停止播报</button>
    </div>
  </section>
</template>
