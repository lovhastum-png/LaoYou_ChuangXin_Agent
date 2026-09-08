import { ref } from 'vue'
import { ApiError, api } from './api'

const MAX_SNAPSHOT_BYTES = 512 * 1024

const elderId = ref<string | null>(null)
const stream = ref<MediaStream | null>(null)
const previewActive = ref(false)
const busy = ref(false)
const error = ref('')
const lastSnapshotAt = ref<string | null>(null)

let previewElement: HTMLVideoElement | null = null
let captureElement: HTMLVideoElement | null = null
let snapshotTimer: number | undefined
let settingsPollTimer: number | undefined
let generation = 0

function clearTimer() {
  if (snapshotTimer) window.clearInterval(snapshotTimer)
  snapshotTimer = undefined
}

function clearSettingsPoll() {
  if (settingsPollTimer) window.clearInterval(settingsPollTimer)
  settingsPollTimer = undefined
}

function detachCaptureElement() {
  if (captureElement) {
    captureElement.pause()
    captureElement.srcObject = null
  }
  captureElement = null
}

function stopTracks() {
  generation += 1
  stream.value?.getTracks().forEach((track) => track.stop())
  stream.value = null
  previewActive.value = false
  if (previewElement) previewElement.srcObject = null
  detachCaptureElement()
  clearTimer()
  clearSettingsPoll()
}

async function checkRemoteState() {
  if (!elderId.value || !previewActive.value) return
  try {
    const elders = await api.elders()
    const current = elders.find((elder) => elder.id === elderId.value)
    if (current && !current.camera_enabled) {
      stopTracks()
      error.value = '摄像头已从远端关闭，已停止本机采集。'
    }
  } catch {
    // A temporary settings poll failure does not stop an active local preview.
  }
}

function mediaError(cause: unknown): string {
  if (cause instanceof DOMException) {
    if (cause.name === 'NotAllowedError' || cause.name === 'PermissionDeniedError') return '浏览器没有授予摄像头权限，请在地址栏允许后再试。'
    if (cause.name === 'NotFoundError') return '没有找到摄像头设备。'
    if (cause.name === 'NotReadableError') return '摄像头正在被其他应用使用。'
  }
  return '摄像头预览启动失败，请检查设备和浏览器权限。'
}

async function compressFrame(): Promise<Blob | null> {
  if (!captureElement || captureElement.videoWidth === 0 || captureElement.videoHeight === 0) return null
  const maxWidth = 960
  const scale = Math.min(1, maxWidth / captureElement.videoWidth)
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(captureElement.videoWidth * scale))
  canvas.height = Math.max(1, Math.round(captureElement.videoHeight * scale))
  const context = canvas.getContext('2d')
  if (!context) return null
  context.drawImage(captureElement, 0, 0, canvas.width, canvas.height)
  for (const quality of [0.72, 0.58, 0.44, 0.32]) {
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality))
    if (blob && blob.size <= MAX_SNAPSHOT_BYTES) return blob
  }
  return null
}

async function uploadSnapshot() {
  if (!elderId.value || !previewActive.value || !captureElement) return
  const blob = await compressFrame()
  if (!blob) {
    error.value = '快照压缩后仍超过 512KB，已跳过本次上传。'
    return
  }
  try {
    await api.uploadSnapshot(elderId.value, blob)
    lastSnapshotAt.value = new Date().toISOString()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '快照上传失败，仍会继续本地预览。'
  }
}

async function attachPreview(element: HTMLVideoElement | null) {
  previewElement = element
  if (!element || !stream.value) return
  element.srcObject = stream.value
  await element.play().catch(() => undefined)
}

function detachPreview(element?: HTMLVideoElement | null) {
  if (!element || previewElement === element) {
    if (previewElement) previewElement.srcObject = null
    previewElement = null
  }
}

async function start(targetElderId: string, options: { initialSnapshot?: boolean } = {}): Promise<void> {
  if (busy.value) return
  if (previewActive.value && elderId.value === targetElderId && stream.value) return
  busy.value = true
  error.value = ''
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('unsupported')
    stopTracks()
    const requestGeneration = generation
    const nextStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
    if (requestGeneration !== generation) {
      nextStream.getTracks().forEach(track => track.stop())
      throw new DOMException('摄像头请求已取消', 'AbortError')
    }
    elderId.value = targetElderId
    stream.value = nextStream
    captureElement = document.createElement('video')
    captureElement.muted = true
    captureElement.playsInline = true
    captureElement.srcObject = nextStream
    await captureElement.play()
    previewActive.value = true
    await attachPreview(previewElement)
    if (options.initialSnapshot !== false) await uploadSnapshot()
    clearTimer()
    snapshotTimer = window.setInterval(() => { void uploadSnapshot() }, 30_000)
    clearSettingsPoll()
    settingsPollTimer = window.setInterval(() => { void checkRemoteState() }, 10_000)
  } catch (cause) {
    stopTracks()
    error.value = cause instanceof Error && cause.message === 'unsupported' ? '当前浏览器不支持摄像头访问。' : mediaError(cause)
    throw cause
  } finally {
    busy.value = false
  }
}

async function uploadNow(): Promise<void> {
  await uploadSnapshot()
}

function stop() {
  stopTracks()
  elderId.value = null
  lastSnapshotAt.value = null
}

function clearError() {
  error.value = ''
}

export function useCameraSession() {
  return {
    elderId,
    stream,
    previewActive,
    busy,
    error,
    lastSnapshotAt,
    start,
    uploadNow,
    stop,
    attachPreview,
    detachPreview,
    clearError,
  }
}
