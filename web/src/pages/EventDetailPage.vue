<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  CircleAlert,
  Film,
  LoaderCircle,
  MapPin,
  Play,
  RotateCcw,
  Send,
  ShieldAlert,
  Stethoscope,
  UserCheck,
  XCircle,
} from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import {
  eventSeverityLabel,
  eventStatusLabel,
  formatClock,
  formatDateTime,
  notificationStatusLabel,
  notificationTargetLabel,
  observationLabel,
  observationValue,
  timelineActorLabel,
  timelineNodeLabel,
} from '../lib/format'
import type { EventDetail, EventRecording, Notification, User } from '../types'

const props = defineProps<{ user: User; eventId: string }>()

const emit = defineEmits<{ back: []; openEvent: [id: string]; navigate: [route: 'family'] }>()

const event = ref<EventDetail | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const actionNote = ref('')
const correctionReason = ref('')
const showCorrection = ref(false)
const activeRecording = ref(0)
const videoEl = ref<HTMLVideoElement | null>(null)

const canAct = computed(() => ['child', 'community', 'admin'].includes(props.user.role))
const canCorrect = computed(() => ['child', 'admin'].includes(props.user.role))
const recordings = computed<EventRecording[]>(() => event.value?.recordings || [])
const currentRecording = computed<EventRecording | null>(() => recordings.value[activeRecording.value] || null)

/** 触发依据的明细行：把原始观测摊开给人看，而不是只留一句结论。 */
const triggerRows = computed(() => {
  const item = event.value
  if (!item) return [] as Array<{ label: string; value: string }>
  const rows: Array<{ label: string; value: string }> = []
  const observation = item.observation
  if (observation) {
    rows.push({ label: '观测类型', value: observationLabel(observation) })
    // 跌倒观测的 value 是姿态状态对象，具体指标已经在上面单独摊开，
    // 这里再塞一个"已记录"只会让人以为漏了数据，跳过。
    const hasPoseDetail = observation.kind === 'fall'
    if (!hasPoseDetail) rows.push({ label: '观测数值', value: observationValue(observation) })
    if (observation.duration_minutes !== null && observation.duration_minutes !== undefined) {
      rows.push({ label: '持续时长', value: `${observation.duration_minutes} 分钟` })
    }
    if (observation.sleeping !== null && observation.sleeping !== undefined) {
      rows.push({ label: '睡眠状态', value: observation.sleeping ? '睡眠时段' : '清醒时段' })
    }
    rows.push({ label: '发生时间', value: formatClock(observation.occurred_at) })
    rows.push({ label: '观测来源', value: sourceLabel(observation.source) })
    if (observation.location?.label) {
      rows.push({ label: '位置', value: observation.location.label })
    }
  }
  rows.push({ label: '触发时刻', value: formatDateTime(item.created_at) })
  rows.push({ label: '事件编号', value: item.id.slice(0, 8) })
  return rows
})

function sourceLabel(source: string): string {
  return {
    simulated: '模拟观测',
    simulated_wearable: '模拟手环',
    camera: '摄像头看护',
    camera_pose_v1: '摄像头看护（姿态识别）',
    wearable: '手环',
  }[source] || source
}

/**
 * 跌倒观测里带了一整套姿态特征（躯干角、下降速度、角速度…），
 * 这是"到底触发了什么"最硬的证据，单独摊开成一行行的指标。
 */
const poseMetrics = computed(() => {
  const value = event.value?.observation?.value
  if (!value || typeof value !== 'object') return [] as Array<{ label: string; value: string }>
  const detail = (value as { detail?: Record<string, unknown> }).detail
  if (!detail || typeof detail !== 'object') return [] as Array<{ label: string; value: string }>
  const metrics: Array<{ label: string; value: string }> = []
  const numeric: Array<[string, string, string]> = [
    ['torso_angle', '躯干倾角', '°'],
    ['descent_speed', '躯干下降速度', ' 画面高/秒'],
    ['angular_velocity', '角速度', '°/秒'],
    ['vertical_ratio', '离地比例', ''],
    ['motion', '画面运动量', ''],
  ]
  for (const [key, label, unit] of numeric) {
    const raw = detail[key]
    if (typeof raw === 'number') metrics.push({ label, value: `${Math.round(raw * 100) / 100}${unit}` })
  }
  const detectedAt = detail['fall_detected_at']
  if (typeof detectedAt === 'number') metrics.push({ label: '判定用时', value: `${detectedAt} 秒` })
  return metrics
})

function notificationClass(status: Notification['status']) {
  return `notification-${status}`
}

function canAckNotification(notification: Notification): boolean {
  return props.user.role === 'admin' || props.user.role === notification.target
}

function actionAllowed(action: string): boolean {
  const item = event.value
  if (!item) return false
  if (action === 'acknowledge') return item.status === 'alerted' && canAct.value
  if (action === 'start') return item.status === 'acknowledged' && canAct.value
  if (action === 'resolve') return item.status === 'handling' && canAct.value
  if (action === 'correct') return item.status !== 'false_positive' && canCorrect.value
  if (action === 'community_unavailable') {
    return ['heart_rate', 'blood_pressure'].includes(item.kind) && !['resolved', 'false_positive'].includes(item.status) && ['community', 'admin'].includes(props.user.role)
  }
  return false
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    event.value = await api.event(props.eventId)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取事件详情。'
  } finally {
    loading.value = false
  }
}

function selectRecording(index: number) {
  if (index === activeRecording.value) return
  activeRecording.value = index
  const el = videoEl.value
  if (el) {
    el.load()
    void el.play().catch(() => {})
  }
}

function replay() {
  const el = videoEl.value
  if (!el) return
  el.currentTime = 0
  void el.play().catch(() => {})
}

async function performAction(action: string) {
  if (!event.value || !actionAllowed(action) || busy.value) return
  if (action === 'resolve' && !actionNote.value.trim()) {
    error.value = '完成处理需要填写处理说明。'
    return
  }
  if (action === 'community_unavailable' && !actionNote.value.trim()) {
    error.value = '无法协助时需要填写原因。'
    return
  }
  if (action === 'correct' && !correctionReason.value.trim()) {
    error.value = '标记误报需要填写原因。'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const note = action === 'correct' ? correctionReason.value.trim() : actionNote.value.trim()
    event.value = await api.eventAction(event.value.id, action, note || undefined)
    actionNote.value = ''
    correctionReason.value = ''
    showCorrection.value = false
    notice.value = action === 'correct' ? '已保留原始事件并标记为误报。' : '事件状态已更新，时间线已记录。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '事件操作失败，请刷新后重试。'
  } finally {
    busy.value = false
  }
}

async function ackNotification(notification: Notification) {
  if (!event.value) return
  try {
    const updated = await api.ackNotification(notification.id)
    event.value = { ...event.value, notifications: event.value.notifications.map((item) => item.id === updated.id ? updated : item) }
    notice.value = `${notificationTargetLabel(updated.target)}通知已确认。`
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '通知确认失败，请稍后重试。'
  }
}

async function retryNotification(notification: Notification) {
  if (!event.value) return
  try {
    const updated = await api.retryNotification(notification.id)
    event.value = { ...event.value, notifications: event.value.notifications.map((item) => item.id === updated.id ? updated : item) }
    notice.value = '已提交通知重试。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '通知重试失败，请稍后重试。'
  }
}

async function escortAction(action: 'accept' | 'complete') {
  if (!event.value?.escort) return
  if (action === 'complete' && !actionNote.value.trim()) {
    error.value = '完成陪诊需要填写处理说明。'
    return
  }
  try {
    const updated = await api.escortAction(event.value.escort.id, action, action === 'complete' ? actionNote.value.trim() : undefined)
    if (event.value) event.value = { ...event.value, escort: updated }
    actionNote.value = ''
    notice.value = action === 'accept' ? '陪诊工单已接单。' : '陪诊工单已完成。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '陪诊操作失败。'
  }
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-content event-page">
    <header class="event-page-bar">
      <button class="back-button" type="button" @click="emit('back')">
        <ArrowLeft :size="22" />返回事件列表
      </button>
      <span v-if="event" class="event-bar-meta">{{ event.elder_name }} · {{ event.title }}</span>
    </header>

    <div v-if="error" class="notice-bar error" role="alert"><CircleAlert :size="18" />{{ error }}<button class="text-button" type="button" @click="load"><RotateCcw :size="16" />重试</button></div>
    <div v-if="notice" class="notice-bar success" role="status"><Check :size="18" />{{ notice }}</div>

    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在读取事件详情…</div>

    <template v-else-if="event">
      <section class="event-hero surface-card" :class="`hero-${event.severity}`">
        <div class="hero-icon">
          <ShieldAlert v-if="event.severity === 'critical'" :size="34" />
          <AlertTriangle v-else :size="34" />
        </div>
        <div class="hero-body">
          <div class="hero-tags">
            <span class="severity-tag" :class="`tag-${event.severity}`">{{ eventSeverityLabel(event.severity) }}</span>
            <span class="hero-source">{{ sourceLabel(event.source) }}</span>
          </div>
          <h1>{{ event.title }}</h1>
          <p class="hero-desc">{{ event.description }}</p>
        </div>
        <div class="hero-status">
          <span class="detail-status">{{ eventStatusLabel(event.status) }}</span>
          <small>{{ formatDateTime(event.created_at) }}</small>
        </div>
      </section>

      <section class="surface-card trigger-card">
        <div class="table-heading"><h2>触发了什么</h2><span>规则判定依据</span></div>
        <p class="trigger-clause">{{ event.trigger_clause }}</p>
        <div v-if="poseMetrics.length" class="pose-metrics">
          <div class="pose-metrics-title">姿态识别指标（判定时的实测值）</div>
          <dl class="trigger-grid">
            <div v-for="metric in poseMetrics" :key="metric.label" class="trigger-cell">
              <dt>{{ metric.label }}</dt>
              <dd>{{ metric.value }}</dd>
            </div>
          </dl>
        </div>
        <dl class="trigger-grid">
          <div v-for="row in triggerRows" :key="row.label" class="trigger-cell">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </div>
        </dl>
      </section>

      <section v-if="recordings.length" class="surface-card recording-card">
        <div class="table-heading"><h2><Film :size="22" />现场录屏</h2><span>{{ recordings.length }} 段可用</span></div>
        <div class="recording-player">
          <video
            v-if="currentRecording"
            ref="videoEl"
            class="recording-video"
            :src="currentRecording.src"
            :key="currentRecording.src"
            controls
            playsinline
            preload="metadata"
          />
          <div class="recording-caption">
            <div>
              <strong>{{ currentRecording?.label }}</strong>
              <p>{{ currentRecording?.detail }}</p>
            </div>
            <button class="secondary-button" type="button" @click="replay"><Play :size="18" />重新播放</button>
          </div>
        </div>
        <div v-if="recordings.length > 1" class="recording-tabs">
          <button
            v-for="(recording, index) in recordings"
            :key="recording.src"
            class="recording-tab"
            :class="{ active: index === activeRecording }"
            type="button"
            @click="selectRecording(index)"
          >
            {{ recording.label }}
          </button>
        </div>
      </section>

      <div class="event-detail-columns">
        <div class="event-detail-main">
          <article class="surface-card action-card">
            <div class="table-heading"><h2>处置操作</h2><span>每一步都会写入时间线</span></div>
            <div class="action-buttons">
              <button v-if="actionAllowed('acknowledge')" class="primary-button" type="button" :disabled="busy" @click="performAction('acknowledge')"><UserCheck :size="18" />确认收到</button>
              <button v-if="actionAllowed('start')" class="primary-button" type="button" :disabled="busy" @click="performAction('start')"><Play :size="18" />开始处理</button>
              <button v-if="actionAllowed('resolve')" class="primary-button" type="button" :disabled="busy" @click="performAction('resolve')"><CheckCircle2 :size="18" />完成处理</button>
              <button v-if="actionAllowed('community_unavailable')" class="secondary-button" type="button" :disabled="busy" @click="performAction('community_unavailable')"><Stethoscope :size="18" />社区无法协助</button>
              <button v-if="actionAllowed('correct') && !showCorrection" class="outline-danger-button" type="button" :disabled="busy" @click="showCorrection = true"><XCircle :size="18" />标记误报</button>
            </div>
            <textarea v-if="event.status === 'handling' || actionAllowed('community_unavailable')" v-model="actionNote" class="action-note" rows="2" placeholder="处理说明（完成处理或社区无法协助时必填）" />
            <div v-if="showCorrection" class="correction-box">
              <label for="correction-reason">误报原因</label>
              <textarea id="correction-reason" v-model="correctionReason" rows="2" placeholder="请记录为什么判定为误报" />
              <div>
                <button class="secondary-button" type="button" @click="showCorrection = false">取消</button>
                <button class="danger-button" type="button" :disabled="busy" @click="performAction('correct')">保存误报原因</button>
              </div>
            </div>
            <p v-if="!canAct" class="inline-empty">当前角色的账号只能查看，不能处置事件。</p>
          </article>

          <article class="surface-card timeline-card">
            <div class="table-heading"><h2>处理时间线</h2><span>{{ event.timeline.length }} 个节点</span></div>
            <div v-if="event.timeline.length" class="timeline-list">
              <div v-for="node in event.timeline" :key="node.id" class="timeline-item">
                <span class="timeline-dot" />
                <div>
                  <strong>{{ timelineNodeLabel(node.node) }}</strong>
                  <p>{{ node.detail }}</p>
                  <small>{{ timelineActorLabel(node.actor) }} · {{ formatDateTime(node.at) }}</small>
                </div>
              </div>
            </div>
            <div v-else class="inline-empty">暂无处理节点。</div>
          </article>
        </div>

        <div class="event-detail-side">
          <article class="surface-card notification-card">
            <div class="table-heading"><h2>通知结果</h2><span>{{ event.notifications.length }} 个目标</span></div>
            <div v-if="event.notifications.length" class="notification-list">
              <div v-for="notification in event.notifications" :key="notification.id" class="notification-row" :class="notificationClass(notification.status)">
                <span class="notification-icon"><Send :size="18" /></span>
                <div>
                  <strong>{{ notificationTargetLabel(notification.target) }}</strong>
                  <span>{{ notificationStatusLabel(notification.status) }} · 尝试 {{ notification.attempts }} 次</span>
                  <small v-if="notification.last_error">{{ notification.last_error }}</small>
                </div>
                <div class="notification-actions">
                  <button v-if="notification.status === 'sent' && canAckNotification(notification)" class="text-button" type="button" @click="ackNotification(notification)"><Check :size="16" />确认</button>
                  <button v-if="notification.status === 'failed' && props.user.role === 'admin'" class="text-button" type="button" @click="retryNotification(notification)"><RotateCcw :size="16" />重试</button>
                  <span v-if="notification.simulated" class="simulated-label">模拟通知</span>
                </div>
              </div>
            </div>
            <div v-else class="inline-empty">暂无通知记录。</div>
          </article>

          <article v-if="event.escort" class="surface-card escort-card">
            <div class="table-heading"><h2><Stethoscope :size="22" />陪诊工单</h2><span>放心医 · {{ event.escort.simulated ? '模拟接入' : '已接入' }}</span></div>
            <div class="escort-row">
              <div>
                <strong>{{ event.escort.status === 'requested' ? '等待接单' : event.escort.status === 'accepted' ? '陪诊进行中' : '已完成' }}</strong>
                <span>申请于 {{ formatDateTime(event.escort.requested_at) }}</span>
              </div>
              <button v-if="event.escort.status === 'requested' && ['community', 'admin'].includes(props.user.role)" class="primary-button" type="button" @click="escortAction('accept')"><Check :size="18" />接单</button>
              <button v-if="event.escort.status === 'accepted' && ['community', 'admin'].includes(props.user.role)" class="secondary-button" type="button" @click="escortAction('complete')"><CheckCircle2 :size="18" />完成陪诊</button>
            </div>
          </article>

          <article v-if="event.observation?.location" class="surface-card location-card">
            <div class="table-heading"><h2><MapPin :size="22" />位置</h2></div>
            <p>{{ event.observation.location.label || '已记录坐标' }}</p>
            <small>{{ event.observation.location.latitude }}, {{ event.observation.location.longitude }}</small>
          </article>
        </div>
      </div>
    </template>

    <div v-else class="surface-card detail-empty">
      <ShieldAlert :size="42" />
      <h2>事件不存在或不可见</h2>
      <p>它可能已经被清理，或者不属于当前账号可见的范围。</p>
      <button class="secondary-button" type="button" @click="emit('back')">返回事件列表</button>
    </div>
  </div>
</template>
