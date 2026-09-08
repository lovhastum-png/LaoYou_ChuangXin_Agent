<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowRight, Camera, CameraOff, Check, CheckCircle2, CircleAlert, Cloud, CloudSun, Clock3, LoaderCircle, MapPin, Mic, RefreshCw, Sun, Video, X } from 'lucide-vue-next'
import AssistantPanel from '../components/AssistantPanel.vue'
import WakeWordControl from '../components/WakeWordControl.vue'
import { ApiError, api } from '../lib/api'
import { formatClock, formatDate, greetingFor } from '../lib/format'
import { useCameraSession } from '../lib/useCameraSession'
import type { Capabilities, Dashboard, Elder, NavRoute, User } from '../types'

const props = defineProps<{ user: User }>()
const emit = defineEmits<{ navigate: [route: NavRoute]; openCall: [callId: string] }>()

const elder = ref<Elder | null>(null)
const dashboard = ref<Dashboard | null>(null)
const capabilities = ref<Capabilities | null>(null)
const loading = ref(true)
const error = ref('')
const assistantOpen = ref(false)
const assistantPanel = ref<{ sendMessage: (value?: string) => Promise<void> } | null>(null)
const camera = useCameraSession()
const cameraConfirmOpen = ref(false)
const cameraCloseBusy = ref(false)
const cameraCloseError = ref('')
const cameraCloseNotice = ref('')
const now = ref(new Date())
let clockTimer: number | undefined
let broadcastTimer: number | undefined

const dateLabel = computed(() => formatDate(now.value))
const greeting = computed(() => `${elder.value?.name || props.user.display_name}，${greetingFor(now.value)}`)
const clock = computed(() => formatClock(now.value))
const hasTemperature = computed(() => dashboard.value?.weather?.temperature !== null && dashboard.value?.weather?.temperature !== undefined)
const weatherSourceLabel = computed(() => {
  const source = dashboard.value?.weather?.source
  if (source === 'live') return '实时天气'
  if (source === 'simulated') return '模拟天气'
  return source ? `天气来源：${source}` : '天气暂不可用'
})
const weatherIcon = computed(() => {
  const description = dashboard.value?.weather?.description || ''
  if (/雨|雪/.test(description)) return Cloud
  if (/云|阴/.test(description)) return CloudSun
  return Sun
})
const reminders = computed(() => (dashboard.value?.reminders || []).filter(item => item.enabled).sort((a, b) => a.time.localeCompare(b.time)))
const todayBroadcasts = computed(() => dashboard.value?.broadcasts || [])
const dialectAvailable = computed(() => {
  const dialect = elder.value?.dialect
  if (!dialect || !capabilities.value) return false
  return capabilities.value.speech.dialects.some((item) => item.id === dialect && item.available)
})

function reminderPlayed(reminderId: string): boolean {
  return todayBroadcasts.value.some((broadcast) => {
    if (broadcast.kind !== 'medication' || !broadcast.played_at) return false
    return broadcast.reminder_id === reminderId
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const elders = await api.elders()
    elder.value = elders[0] || null
    if (!elder.value) return
    const [dashboardResult, capabilityResult] = await Promise.allSettled([
      api.dashboard(elder.value.id),
      api.capabilities(),
    ])
    if (dashboardResult.status === 'fulfilled') {
      dashboard.value = dashboardResult.value
      elder.value = dashboardResult.value.elder
    } else {
      throw dashboardResult.reason
    }
    if (capabilityResult.status === 'fulfilled') capabilities.value = capabilityResult.value
    await pollBroadcasts()
    if (broadcastTimer) window.clearInterval(broadcastTimer)
    broadcastTimer = window.setInterval(() => { void pollBroadcasts() }, 15_000)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取老友服务，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function pollBroadcasts() {
  if (!elder.value || !dashboard.value) return
  try {
    const [broadcasts, elders] = await Promise.all([api.broadcasts(elder.value.id), api.elders()])
    dashboard.value.broadcasts = broadcasts
    const current = elders.find(item => item.id === elder.value?.id)
    if (current) { elder.value = current; dashboard.value.elder = current }
  } catch {
    // The dashboard remains usable when the optional broadcast poll fails.
  }
}

function openAssistant() {
  assistantOpen.value = true
}

function handleCameraClosed() {
  camera.stop()
  if (elder.value) elder.value.camera_enabled = false
}

function handleWake(payload: { text: string }) {
  assistantOpen.value = true
  if (payload.text.trim()) void nextTick(() => assistantPanel.value?.sendMessage(payload.text))
}

function openCameraAction() {
  if (!elder.value) return
  cameraCloseError.value = ''
  cameraCloseNotice.value = ''
  if (elder.value.camera_enabled) cameraConfirmOpen.value = true
  else emit('navigate', 'safety')
}

async function confirmCameraClose() {
  if (!elder.value || cameraCloseBusy.value) return
  cameraCloseBusy.value = true
  cameraCloseError.value = ''
  camera.stop()
  try {
    elder.value = await api.updateElderSettings(elder.value.id, { camera_enabled: false, confirm_camera_off: true })
    if (dashboard.value) dashboard.value.elder = elder.value
    cameraConfirmOpen.value = false
    cameraCloseNotice.value = '摄像头已关闭，已停止采集并清除保存的快照。'
  } catch (cause) {
    cameraCloseError.value = `本机采集已停止，远端状态尚未同步。${cause instanceof ApiError ? cause.detail : '请检查网络后重试。'}`
  } finally {
    cameraCloseBusy.value = false
  }
}

onMounted(() => {
  void load()
  clockTimer = window.setInterval(() => { now.value = new Date() }, 30_000)
})

onBeforeUnmount(() => {
  if (clockTimer) window.clearInterval(clockTimer)
  if (broadcastTimer) window.clearInterval(broadcastTimer)
})
</script>

<template>
  <div class="page-content elder-home-page">
    <header class="page-header home-header">
      <div>
        <h1>{{ greeting }}</h1>
        <p>{{ dateLabel }}</p>
      </div>
      <div class="home-context"><MapPin :size="20" :stroke-width="1.8" />{{ elder?.city || '—' }}</div>
    </header>

    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在加载您的今日信息…</div>
    <div v-else-if="error" class="page-error-state">
      <p>{{ error }}</p>
      <button class="secondary-button" type="button" @click="load"><RefreshCw :size="19" />重新加载</button>
    </div>
    <div v-else-if="!elder || !dashboard" class="page-empty-state">
      <p>当前账号还没有可查看的老人信息。</p>
    </div>
    <template v-else>
      <section class="home-grid-top">
        <article class="weather-card surface-card">
          <div class="weather-main">
            <div>
              <div class="time-display">{{ clock }}</div>
              <h2>今日天气</h2>
            </div>
            <component :is="weatherIcon" class="weather-icon" :size="84" :stroke-width="1.35" aria-hidden="true" />
            <div class="weather-value" v-if="dashboard.weather && hasTemperature">
              <strong>{{ dashboard.weather.temperature }}°</strong>
              <span>{{ dashboard.weather.description }}</span>
              <span class="weather-city">{{ dashboard.weather.city }}</span>
            </div>
            <div v-else class="weather-unavailable"><strong>天气暂不可用</strong><span v-if="dashboard.weather?.description">{{ dashboard.weather.description }}</span></div>
          </div>
          <p class="weather-advice">{{ dashboard.weather?.advice || '稍后再来看看今天的天气建议。' }}</p>
          <span class="data-source">{{ weatherSourceLabel }}</span>
        </article>

        <article class="reminder-summary surface-card">
          <div class="card-heading-row">
            <h2>今天的提醒</h2>
            <span class="count-label">{{ reminders.length }} 项</span>
          </div>
          <div v-if="reminders.length" class="reminder-list compact-list">
            <div v-for="reminder in reminders.slice(0, 3)" :key="reminder.id" class="reminder-row">
              <strong class="reminder-time">{{ formatClock(reminder.time) }}</strong>
              <div class="reminder-copy"><strong>{{ reminder.title }}</strong><span>{{ reminder.medicine }} {{ reminder.dose }}</span></div>
              <span v-if="reminderPlayed(reminder.id)" class="reminder-state played"><CheckCircle2 :size="22" />已播报</span>
              <span v-else class="reminder-state pending"><Clock3 :size="22" />待提醒</span>
            </div>
          </div>
          <div v-else class="inline-empty">今天还没有用药提醒。</div>
          <button class="card-link-button" type="button" @click="emit('navigate', 'reminders')">添加提醒 <ArrowRight :size="21" /></button>
        </article>
      </section>

      <div v-if="assistantOpen" class="assistant-wrap">
        <AssistantPanel
          ref="assistantPanel"
          :elder-id="elder.id"
          :voice-enabled="elder.voice_enabled"
          :dialect="elder.dialect"
          :dialect-available="dialectAvailable"
          @close="assistantOpen = false"
          @call-requested="callId => emit('openCall', callId)"
          @camera-closed="handleCameraClosed"
        />
      </div>
      <div v-else class="assistant-entry">
        <button class="assistant-banner" type="button" @click="openAssistant">
          <span class="assistant-banner-icon"><Mic :size="34" :stroke-width="1.8" /></span>
          <span><strong>你好通通</strong><small>有什么需要，直接告诉我</small></span>
          <span class="assistant-banner-cta">点击说话</span>
        </button>
        <WakeWordControl :voice-enabled="elder.voice_enabled" :dialect="elder.dialect" @wake="handleWake" />
      </div>

      <section class="home-grid-bottom">
        <button class="quick-card surface-card" type="button" @click="emit('navigate', 'calls')">
          <span class="quick-icon"><Video :size="43" :stroke-width="1.5" /></span>
          <span class="quick-copy"><strong>视频通话</strong><small>联系家人</small></span>
          <ArrowRight class="quick-arrow" :size="34" :stroke-width="1.7" />
        </button>
        <button class="quick-card surface-card camera-card" type="button" @click="openCameraAction">
          <span class="quick-icon"><Camera :size="43" :stroke-width="1.5" /></span>
          <span class="quick-copy"><strong>摄像头</strong><small>{{ !elder.camera_enabled ? '已关闭' : camera.previewActive.value ? '正在采集' : '已允许，尚未采集' }}</small></span>
          <span class="outline-action">{{ elder.camera_enabled ? '关闭摄像头' : '开启摄像头' }}</span>
        </button>
      </section>
      <p v-if="cameraCloseNotice" class="notice-bar success home-notice"><Check :size="18" />{{ cameraCloseNotice }}</p>
    </template>

    <div v-if="cameraConfirmOpen" class="modal-backdrop" role="presentation" @click.self="cameraConfirmOpen = false"><section class="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="home-camera-dialog-title"><header class="modal-header"><h2 id="home-camera-dialog-title">关闭摄像头？</h2><button class="icon-button" type="button" aria-label="取消" @click="cameraConfirmOpen = false"><X :size="22" /></button></header><p class="modal-copy">关闭后会停止摄像头采集，并清除已保存的快照。之后需要重新开启才能查看画面。</p><p v-if="cameraCloseError" class="form-error"><CircleAlert :size="17" />{{ cameraCloseError }}</p><div class="modal-actions"><button class="secondary-button" type="button" @click="cameraConfirmOpen = false">暂不关闭</button><button class="danger-button" type="button" :disabled="cameraCloseBusy" @click="confirmCameraClose"><CameraOff :size="19" />{{ cameraCloseBusy ? '处理中…' : '确认关闭' }}</button></div></section></div>
  </div>
</template>
