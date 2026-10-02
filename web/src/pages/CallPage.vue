<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Camera, CameraOff, CircleAlert, LoaderCircle, Mic, MicOff, PhoneOff, RefreshCw, Video } from 'lucide-vue-next'
import { ApiError, api, getToken, websocketUrl } from '../lib/api'
import type { CallSignalMessage, User } from '../types'

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
const remoteMicMuted = ref(false)
const remoteCameraOff = ref(false)
const initiator = ref(Boolean(props.initiator))
const signalingQueue: string[] = []
let callStateTimer: number | undefined
let reconnectTimer: number | undefined
let reconnectAttempts = 0
let socketWasOpen = false
const MAX_RECONNECT_ATTEMPTS = 8
let disposed = false
const pendingCandidates: RTCIceCandidateInit[] = []
const signalingToken = computed(() => props.routeToken || getToken())

// 通话页原先写死公共 STUN：局域网能直接打洞，跨网络（家庭宽带 + 4G、两端都在
// NAT 之后）经常不通，需要 TURN 中继。ICE 配置改由后端下发，TURN key 只留在
// 服务端。拉取失败保留公共 STUN，局域网行为与改造前一致。
const FALLBACK_ICE_SERVERS: RTCIceServer[] = [{ urls: 'stun:stun.l.google.com:19302' }]
let iceServers: RTCIceServer[] = FALLBACK_ICE_SERVERS
let iceServersRequest: Promise<void> | null = null

function loadIceServers(): Promise<void> {
  if (iceServersRequest) return iceServersRequest
  const token = signalingToken.value
  // 没有凭证时不发请求：401 会触发全局登出提示，而这种情况本来就该用
  // 公共 STUN 兜底。
  iceServersRequest = token
    ? (async () => {
        try {
          const config = await api.iceServers(token)
          if (Array.isArray(config.iceServers) && config.iceServers.length) iceServers = config.iceServers
        } catch {
          // 保留公共 STUN，不因为这一个请求失败让通话整体不可用。
        }
      })()
    : Promise.resolve()
  return iceServersRequest
}

// Cloudflare 对空闲 WebSocket 约 100 秒就切断（免费与 Pro 套餐一致）。通话
// 中段本来没有信令，靠应用层 ping 保活，否则远端会被误判为掉线重连。
const HEARTBEAT_INTERVAL_MS = 25_000
let heartbeatTimer: number | undefined

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = window.setInterval(() => {
    if (socket.value?.readyState === WebSocket.OPEN) {
      socket.value.send(JSON.stringify({ type: 'ping', payload: {} }))
    }
  }, HEARTBEAT_INTERVAL_MS)
}

function stopHeartbeat() {
  if (heartbeatTimer !== undefined) {
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = undefined
  }
}

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

function sendSignal(message: Extract<CallSignalMessage, { type: 'offer' | 'answer' | 'candidate' | 'media_state' }>) {
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
  sendSignal({ type: 'offer', payload: { sdp: offer.sdp, type: offer.type } })
}

function setupPeer() {
  const connection = new RTCPeerConnection({
    iceServers,
  })
  peer.value = connection
  localStream.value?.getTracks().forEach((track) => connection.addTrack(track, localStream.value as MediaStream))
  connection.onicecandidate = (event) => {
    if (event.candidate) sendSignal({ type: 'candidate', payload: event.candidate.toJSON() })
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
      const resumed = socketWasOpen
      socketWasOpen = true
      reconnectAttempts = 0
      startHeartbeat()
      // 重连后本端 RTCPeerConnection 未处于 connected 就地重建（disconnected
      // 也可能一去不回）；对端保持原连接，房间重新满员时服务端重发
      // peer_ready，由重发的 offer 完成再协商。
      if (resumed && peer.value && peer.value.connectionState !== 'connected') {
        peer.value.close()
        setupPeer()
      }
      flushSignals()
      if (state.value === 'waiting' || state.value === 'connecting') {
        state.value = 'connecting'
        statusText.value = initiator.value ? '已连接，等待家人接听…' : '已连接信令，等待对方画面…'
      } else if (state.value === 'active') {
        statusText.value = resumed ? '信令已恢复，通话继续' : '通话已接通'
      }
    }
    connection.onmessage = (message) => {
      try {
        const data = JSON.parse(String(message.data)) as CallSignalMessage
        void handleSignal(data)
      } catch {
        // Ignore malformed signaling packets; the call status remains visible.
      }
    }
    connection.onerror = () => {
      // 浏览器在 error 之后必然触发 close，重连/终态分类统一在 onclose 处理。
    }
    connection.onclose = (event) => {
      socket.value = null
      stopHeartbeat()
      if (disposed || ['ended', 'error'].includes(state.value)) return
      if (event.code === 4401) {
        setError('通话凭证已失效，请返回应用重新进入。')
        return
      }
      // 4409：通话终结或房间已满，属终态；3 秒轮询会进一步同步最终状态。
      if (event.code === 4409) {
        statusText.value = '通话已结束'
        return
      }
      scheduleReconnect()
    }
  } catch {
    // 构造 WebSocket 同步失败：已连过说明是网络抖动，交给退避重连；
    // 从未连过则按配置/地址错误直接报错，不做无意义重试。
    if (socketWasOpen) scheduleReconnect()
    else setError('无法打开通话信令连接。')
  }
}

function scheduleReconnect() {
  if (reconnectTimer !== undefined) return
  reconnectAttempts += 1
  if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
    setError('信令连接多次重连失败，请检查网络后挂断并重新进入通话。')
    return
  }
  const delay = Math.min(1000 * 2 ** (reconnectAttempts - 1), 10000)
  statusText.value = `信令中断，约 ${Math.max(1, Math.round(delay / 1000))} 秒后自动重连…`
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined
    if (disposed || ['ended', 'error'].includes(state.value)) return
    setupSocket()
  }, delay)
}

async function handleSignal(data: CallSignalMessage) {
  // peer_left / media_state 只影响提示层，不依赖 RTCPeerConnection 存在。
  if (data.type === 'peer_left') {
    if (!['ended', 'error'].includes(state.value)) statusText.value = '对方连接中断，等待对方恢复…'
    return
  }
  if (data.type === 'media_state') {
    if (typeof data.payload.audio === 'boolean') remoteMicMuted.value = !data.payload.audio
    if (typeof data.payload.video === 'boolean') remoteCameraOff.value = !data.payload.video
    return
  }
  if (data.type === 'error' || !peer.value) return
  try {
    if (data.type === 'peer_ready') {
      // 对端（重新）入房后清掉掉线提示，避免文案停在"对方连接中断"。
      if (state.value === 'active') statusText.value = '通话已接通'
      if (initiator.value) await makeOffer()
    } else if (data.type === 'offer' && data.payload) {
      await peer.value.setRemoteDescription(data.payload)
      while (pendingCandidates.length) await peer.value.addIceCandidate(pendingCandidates.shift() as RTCIceCandidateInit)
      const answer = await peer.value.createAnswer()
      await peer.value.setLocalDescription(answer)
      sendSignal({ type: 'answer', payload: { sdp: answer.sdp, type: answer.type } })
      try { await api.callAction(props.callId, 'answer', signalingToken.value || undefined) } catch (cause) { if (cause instanceof ApiError && cause.status !== 409) throw cause }
    } else if (data.type === 'answer' && data.payload) {
      await peer.value.setRemoteDescription(data.payload)
      while (pendingCandidates.length) await peer.value.addIceCandidate(pendingCandidates.shift() as RTCIceCandidateInit)
    } else if (data.type === 'candidate' && data.payload) {
      if (peer.value.remoteDescription) await peer.value.addIceCandidate(data.payload)
      else pendingCandidates.push(data.payload)
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
    // 与系统授权弹窗并行拉取 ICE 配置，避免串行等待拖慢接通。
    const iceReady = loadIceServers()
    const media = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
    if (disposed) { media.getTracks().forEach(track => track.stop()); return }
    localStream.value = media
    if (localVideo.value) {
      localVideo.value.srcObject = media
      await localVideo.value.play().catch(() => undefined)
    }
    await iceReady
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
  if (reconnectTimer !== undefined) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = undefined
  }
  socketWasOpen = false
  stopHeartbeat()
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

function pushMediaState() {
  const audio = localStream.value?.getAudioTracks()[0]?.enabled ?? false
  const video = localStream.value?.getVideoTracks()[0]?.enabled ?? false
  sendSignal({ type: 'media_state', payload: { audio, video } })
}

function toggleMic() {
  const track = localStream.value?.getAudioTracks()[0]
  if (!track) return
  track.enabled = !track.enabled
  micMuted.value = !track.enabled
  pushMediaState()
}

function toggleCamera() {
  const track = localStream.value?.getVideoTracks()[0]
  if (!track) return
  track.enabled = !track.enabled
  cameraOff.value = !track.enabled
  pushMediaState()
}

function retry() {
  stopMedia()
  // 上一次可能正是在拉不到 ICE 配置时失败的，重试时重新取一次。
  iceServersRequest = null
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
      <div class="remote-stage"><video v-show="remoteReady && !remoteCameraOff" ref="remoteVideo" class="remote-video" autoplay playsinline aria-label="家人画面" /><div v-if="remoteReady && remoteCameraOff" class="remote-placeholder"><CameraOff :size="56" /><span>对方已关闭摄像头</span></div><div v-else-if="!remoteReady" class="remote-placeholder"><Video :size="56" /><span>{{ state === 'error' ? '请先解决上方提示的问题' : '等待对方接入画面…' }}</span></div><span class="remote-label">对方画面<template v-if="remoteMicMuted">（已静音）</template></span></div>
      <div class="local-stage"><video v-show="localStream && !cameraOff" ref="localVideo" class="local-video" muted autoplay playsinline aria-label="我的画面" /><div v-if="!localStream || cameraOff" class="local-placeholder"><CameraOff :size="28" /></div><span class="local-label">我的画面<template v-if="cameraOff">（已关闭）</template></span></div>
      <div v-if="error" class="call-error" role="alert"><CircleAlert :size="22" /><span>{{ error }}</span></div>
    </main>
    <footer class="call-controls"><button class="call-control" :class="{ active: micMuted }" type="button" :disabled="!localStream" :title="micMuted ? '打开麦克风' : '关闭麦克风'" :aria-label="micMuted ? '打开麦克风' : '关闭麦克风'" @click="toggleMic"><MicOff v-if="micMuted" :size="23" /><Mic v-else :size="23" /></button><button class="call-control" :class="{ active: cameraOff }" type="button" :disabled="!localStream" :title="cameraOff ? '打开摄像头' : '关闭摄像头'" :aria-label="cameraOff ? '打开摄像头' : '关闭摄像头'" @click="toggleCamera"><CameraOff v-if="cameraOff" :size="23" /><Camera v-else :size="23" /></button><button class="hangup-button" type="button" @click="endCall"><PhoneOff :size="22" />挂断</button><button v-if="state === 'error'" class="call-control" type="button" title="重试" aria-label="重试" @click="retry"><RefreshCw :size="22" /></button></footer>
    <p class="call-privacy">挂断后会停止本机摄像头和麦克风。</p>
  </div>
</template>
