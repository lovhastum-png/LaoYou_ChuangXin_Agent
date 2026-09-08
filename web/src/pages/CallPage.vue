<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Camera, CameraOff, CircleAlert, LoaderCircle, Mic, MicOff, PhoneOff, RefreshCw, Video } from 'lucide-vue-next'
import { ApiError, api, getToken, websocketUrl } from '../lib/api'
import type { User } from '../types'

const props = defineProps<{
  callId: string
  routeToken?: string
  initiator?: boolean
  embedded?: boolean
  user?: User | null
}>()

const emit = defineEmits<{ exit: [] }>()

const localVideo = ref<HTMLVideoElement | null>(null)
const remoteVideo = ref<HTMLVideoElement | null>(null)
const localStream = ref<MediaStream | null>(null)
const peer = ref<RTCPeerConnection | null>(null)
const socket = ref<WebSocket | null>(null)
const state = ref<'starting' | 'waiting' | 'connecting' | 'active' | 'ended' | 'error'>('starting')
const statusText = ref('正在准备通话…')
const error = ref('')
const micMuted = ref(false)
const cameraOff = ref(false)
const remoteReady = ref(false)
const initiator = ref(Boolean(props.initiator))
const signalingQueue: string[] = []
let callStateTimer: number | undefined
let disposed = false
const pendingCandidates: RTCIceCandidateInit[] = []
const signalingToken = computed(() => props.routeToken || getToken())

function permissionMessage(cause: unknown): string {
  if (cause instanceof DOMException) {
    if (cause.name === 'NotAllowedError' || cause.name === 'PermissionDeniedError') return '浏览器没有授予摄像头或麦克风权限，请在地址栏允许后重试。'
    if (cause.name === 'NotFoundError') return '没有找到摄像头或麦克风设备。'
    if (cause.name === 'NotReadableError') return '无法读取摄像头或麦克风，请检查设备是否可用或被其他应用占用。'
  }
  return '无法打开摄像头和麦克风，请检查设备后重试。'
}

function setError(message: string) {
  error.value = message
  state.value = 'error'
  statusText.value = '通话无法建立'
}

function sendSignal(message: { type: 'offer' | 'answer' | 'candidate'; payload: object }) {
  const encoded = JSON.stringify(message)
  if (socket.value?.readyState === WebSocket.OPEN) socket.value.send(encoded)
  else signalingQueue.push(encoded)
}

function flushSignals() {
  if (!socket.value || socket.value.readyState !== WebSocket.OPEN) return
  while (signalingQueue.length) socket.value.send(signalingQueue.shift() || '')
}

async function makeOffer() {
  if (!peer.value) return
  const offer = await peer.value.createOffer()
  await peer.value.setLocalDescription(offer)
  sendSignal({ type: 'offer', payload: offer as unknown as object })
}

function setupPeer() {
  const connection = new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  })
  peer.value = connection
  localStream.value?.getTracks().forEach((track) => connection.addTrack(track, localStream.value as MediaStream))
  connection.onicecandidate = (event) => {
    if (event.candidate) sendSignal({ type: 'candidate', payload: event.candidate.toJSON() as object })
  }
  connection.ontrack = (event) => {
    const [remoteStream] = event.streams
    if (remoteVideo.value && remoteStream) {
      remoteVideo.value.srcObject = remoteStream
      void remoteVideo.value.play().catch(() => undefined)
      remoteReady.value = true
    }
  }
  connection.onconnectionstatechange = () => {
    if (connection.connectionState === 'connected') {
      state.value = 'active'
      statusText.value = '通话已接通'
    } else if (['failed', 'disconnected'].includes(connection.connectionState)) {
      statusText.value = '通话连接不稳定'
    }
  }
}

function setupSocket() {
  if (!signalingToken.value) {
    setError('通话链接缺少临时凭证，请从老友应用重新进入。')
    return
  }
  try {
    const connection = new WebSocket(websocketUrl(props.callId, signalingToken.value))
    socket.value = connection
    connection.onopen = () => {
      flushSignals()
      state.value = 'connecting'
      statusText.value = initiator.value ? '已连接，等待家人接听…' : '已连接信令，等待对方画面…'
    }
    connection.onmessage = (message) => {
      try {
        const data = JSON.parse(String(message.data)) as { type: 'peer_ready' | 'offer' | 'answer' | 'candidate'; payload?: RTCSessionDescriptionInit | RTCIceCandidateInit }
        void handleSignal(data)
      } catch {
        // Ignore malformed signaling packets; the call status remains visible.
      }
    }
    connection.onerror = () => setError('通话信令连接失败，请检查网络或服务端状态。')
    connection.onclose = () => {
      if (!['ended', 'error'].includes(state.value)) statusText.value = '信令连接已断开'
    }
  } catch {
    setError('无法打开通话信令连接。')
  }
}

async function handleSignal(data: { type: 'peer_ready' | 'offer' | 'answer' | 'candidate'; payload?: RTCSessionDescriptionInit | RTCIceCandidateInit }) {
  if (!peer.value) return
  try {
    if (data.type === 'peer_ready') {
      if (initiator.value) await makeOffer()
    } else if (data.type === 'offer' && data.payload) {
      await peer.value.setRemoteDescription(data.payload as RTCSessionDescriptionInit)
      while (pendingCandidates.length) await peer.value.addIceCandidate(pendingCandidates.shift() as RTCIceCandidateInit)
      const answer = await peer.value.createAnswer()
      await peer.value.setLocalDescription(answer)
      sendSignal({ type: 'answer', payload: answer as unknown as object })
      try { await api.callAction(props.callId, 'answer', signalingToken.value || undefined) } catch (cause) { if (cause instanceof ApiError && cause.status !== 409) throw cause }
    } else if (data.type === 'answer' && data.payload) {
      await peer.value.setRemoteDescription(data.payload as RTCSessionDescriptionInit)
      while (pendingCandidates.length) await peer.value.addIceCandidate(pendingCandidates.shift() as RTCIceCandidateInit)
    } else if (data.type === 'candidate' && data.payload) {
      if (peer.value.remoteDescription) await peer.value.addIceCandidate(data.payload as RTCIceCandidateInit)
      else pendingCandidates.push(data.payload as RTCIceCandidateInit)
    }
  } catch (cause) {
    setError(cause instanceof ApiError ? cause.detail : '通话协商失败，请重新发起。')
  }
}

async function start() {
  error.value = ''
  state.value = 'starting'
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('unsupported')
    const media = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
    if (disposed) { media.getTracks().forEach(track => track.stop()); return }
    localStream.value = media
    if (localVideo.value) {
      localVideo.value.srcObject = media
      await localVideo.value.play().catch(() => undefined)
    }
    setupPeer()
    setupSocket()
    state.value = 'waiting'
    statusText.value = initiator.value ? '已连接，等待家人接听…' : '等待对方发起画面…'
  } catch (cause) {
    setError(cause instanceof Error && cause.message === 'unsupported' ? '当前浏览器不支持 WebRTC 媒体访问。' : permissionMessage(cause))
    stopMedia()
  }
}

function stopMedia() {
  localStream.value?.getTracks().forEach((track) => track.stop())
  localStream.value = null
  if (localVideo.value) localVideo.value.srcObject = null
  if (remoteVideo.value) remoteVideo.value.srcObject = null
  peer.value?.close()
  peer.value = null
  socket.value?.close()
  socket.value = null
}

async function endCall() {
  try { await api.callAction(props.callId, 'end', signalingToken.value || undefined) } catch {
    error.value = '本机音视频已停止，挂断记录尚未同步，请检查网络后再次点击挂断。'
  }
  state.value = 'ended'
  statusText.value = '通话已结束'
  stopMedia()
}

function toggleMic() {
  const track = localStream.value?.getAudioTracks()[0]
  if (!track) return
  track.enabled = !track.enabled
  micMuted.value = !track.enabled
}

function toggleCamera() {
  const track = localStream.value?.getVideoTracks()[0]
  if (!track) return
  track.enabled = !track.enabled
  cameraOff.value = !track.enabled
}

function retry() {
  stopMedia()
  void start()
}

onMounted(() => {
  if (props.routeToken) window.history.replaceState({}, document.title, `/call/${encodeURIComponent(props.callId)}`)
  void (async () => {
    if (!initiator.value && signalingToken.value) {
      try {
        const call = await api.call(props.callId, signalingToken.value)
        if (props.user) initiator.value = call.created_by === props.user.id
        else {
          const current = await api.me(signalingToken.value)
          initiator.value = call.created_by === current.id
        }
      } catch (cause) {
        if (cause instanceof ApiError && cause.status !== 404 && cause.status !== 401 && cause.status !== 403) {
          setError(cause.detail)
          return
        }
      }
    }
    if (disposed) return
    await start()
    if (disposed) return
    callStateTimer = window.setInterval(async () => {
      if (state.value === 'ended') return
      try {
        const call = await api.call(props.callId, signalingToken.value || undefined)
        if (call.status === 'ended' || call.status === 'declined') {
          state.value = 'ended'
          statusText.value = call.status === 'declined' ? '对方已拒绝通话' : '通话已结束'
          stopMedia()
        }
      } catch (cause) {
        if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
          setError('此通话已不可访问，请返回应用重新进入。')
          stopMedia()
        }
      }
    }, 3000)
  })()
})

onBeforeUnmount(() => {
  disposed = true
  if (callStateTimer) window.clearInterval(callStateTimer)
  stopMedia()
  if (state.value !== 'ended') void api.callAction(props.callId, 'end', signalingToken.value || undefined).catch(() => undefined)
})
</script>

<template>
  <div class="call-page">
    <header class="call-header"><div class="call-brand">老友 <span>视频通话</span></div><div class="call-header-actions"><span class="call-status" :class="`call-${state}`"><span class="state-dot" />{{ statusText }}</span><button v-if="state === 'ended' && !embedded" class="call-exit" type="button" @click="emit('exit')">返回应用</button></div></header>
    <main class="call-stage">
      <div class="remote-stage"><video v-show="remoteReady" ref="remoteVideo" class="remote-video" autoplay playsinline aria-label="家人画面" /><div v-if="!remoteReady" class="remote-placeholder"><Video :size="56" /><span>{{ state === 'error' ? '请先解决上方提示的问题' : '等待对方接入画面…' }}</span></div><span class="remote-label">对方画面</span></div>
      <div class="local-stage"><video v-show="localStream" ref="localVideo" class="local-video" muted autoplay playsinline aria-label="我的画面" /><div v-if="!localStream" class="local-placeholder"><CameraOff :size="28" /></div><span class="local-label">我的画面</span></div>
      <div v-if="error" class="call-error" role="alert"><CircleAlert :size="22" /><span>{{ error }}</span></div>
    </main>
    <footer class="call-controls"><button class="call-control" :class="{ active: micMuted }" type="button" :disabled="!localStream" :title="micMuted ? '打开麦克风' : '关闭麦克风'" :aria-label="micMuted ? '打开麦克风' : '关闭麦克风'" @click="toggleMic"><MicOff v-if="micMuted" :size="23" /><Mic v-else :size="23" /></button><button class="call-control" :class="{ active: cameraOff }" type="button" :disabled="!localStream" :title="cameraOff ? '打开摄像头' : '关闭摄像头'" :aria-label="cameraOff ? '打开摄像头' : '关闭摄像头'" @click="toggleCamera"><CameraOff v-if="cameraOff" :size="23" /><Camera v-else :size="23" /></button><button class="hangup-button" type="button" @click="endCall"><PhoneOff :size="22" />挂断</button><button v-if="state === 'error'" class="call-control" type="button" title="重试" aria-label="重试" @click="retry"><RefreshCw :size="22" /></button></footer>
    <p class="call-privacy">挂断后会停止本机摄像头和麦克风。</p>
  </div>
</template>
