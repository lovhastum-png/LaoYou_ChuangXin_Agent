<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Check, CheckCircle2, ChevronRight, CircleAlert, Clock3, LoaderCircle, MapPin, Play, RefreshCw, RotateCcw, Save, Send, ShieldAlert, Stethoscope, UserCheck, UsersRound, XCircle } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { eventSeverityLabel, eventStatusLabel, formatDateTime, notificationStatusLabel, notificationTargetLabel, timelineActorLabel, timelineNodeLabel } from '../lib/format'
import type { Elder, EventDetail, EventItem, Escort, Notification, User } from '../types'

const props = defineProps<{ user: User }>()

const elders = ref<Elder[]>([])
const selectedElderId = ref('')
const events = ref<EventItem[]>([])
const selectedEvent = ref<EventDetail | null>(null)
const notifications = ref<Notification[]>([])
const escorts = ref<Escort[]>([])
const loading = ref(true)
const eventsLoading = ref(false)
const detailLoading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const actionNote = ref('')
const correctionReason = ref('')
const showCorrection = ref(false)
const injectBusy = ref(false)
const injectError = ref('')
const injectNotice = ref('')
const failTargets = ref<string[]>([])
const failConfigBusy = ref(false)
const failConfigNotice = ref('')
const injection = ref({ kind: 'heart_rate', value: '145', duration_minutes: '', sleeping: false, occurred_at: '', label: '', latitude: '', longitude: '', systolic: '170', diastolic: '105' })

const roleLabel: Record<User['role'], string> = { elder: '老人端', child: '家属端', community: '社区端', admin: '演示管理' }
const canAct = computed(() => props.user.role === 'child' || props.user.role === 'community' || props.user.role === 'admin')
const canCorrect = computed(() => props.user.role === 'child' || props.user.role === 'admin')
const canInject = computed(() => props.user.role === 'child' || props.user.role === 'admin')
const selectedNotifications = computed(() => selectedEvent.value?.notifications || notifications.value.filter((notification) => notification.event_id === selectedEvent.value?.id))
const selectedEscort = computed(() => selectedEvent.value?.escort || escorts.value.find((escort) => escort.event_id === selectedEvent.value?.id) || null)
const injectionFields = computed(() => injection.value.kind === 'blood_pressure' ? 'blood-pressure' : 'simple')

function eventClass(event: EventItem) {
  return [`severity-${event.severity}`, `status-${event.status}`]
}

async function loadEvents() {
  if (!selectedElderId.value) {
    events.value = []
    selectedEvent.value = null
    return
  }
  eventsLoading.value = true
  error.value = ''
  try {
    events.value = await api.events({ elder_id: selectedElderId.value })
    if (selectedEvent.value && !events.value.some((event) => event.id === selectedEvent.value?.id)) selectedEvent.value = null
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取事件列表。'
  } finally {
    eventsLoading.value = false
  }
}

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    elders.value = await api.elders()
    selectedElderId.value = selectedElderId.value && elders.value.some((elder) => elder.id === selectedElderId.value) ? selectedElderId.value : elders.value[0]?.id || ''
    const [notificationResult, escortResult] = await Promise.allSettled([api.notifications(), api.escorts()])
    if (notificationResult.status === 'fulfilled') notifications.value = notificationResult.value
    if (escortResult.status === 'fulfilled') escorts.value = escortResult.value
    await loadEvents()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取家庭与社区数据。'
  } finally {
    loading.value = false
  }
}

async function chooseElder() {
  selectedEvent.value = null
  await loadEvents()
}

async function openEvent(event: EventItem) {
  detailLoading.value = true
  error.value = ''
  try {
    selectedEvent.value = await api.event(event.id)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取事件详情。'
  } finally {
    detailLoading.value = false
  }
}

function actionAllowed(action: string): boolean {
  const event = selectedEvent.value
  if (!event) return false
  if (action === 'acknowledge') return event.status === 'alerted' && canAct.value
  if (action === 'start') return event.status === 'acknowledged' && canAct.value
  if (action === 'resolve') return event.status === 'handling' && canAct.value
  if (action === 'correct') return event.status !== 'false_positive' && canCorrect.value
  if (action === 'community_unavailable') return ['heart_rate', 'blood_pressure'].includes(event.kind) && !['resolved', 'false_positive'].includes(event.status) && (props.user.role === 'community' || props.user.role === 'admin')
  return false
}

async function performAction(action: string) {
  if (!selectedEvent.value || !actionAllowed(action) || busy.value) return
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
    selectedEvent.value = await api.eventAction(selectedEvent.value.id, action, note || undefined)
    const index = events.value.findIndex((event) => event.id === selectedEvent.value?.id)
    if (index >= 0 && selectedEvent.value) events.value[index] = selectedEvent.value
    notifications.value = selectedEvent.value.notifications
    if (selectedEvent.value.escort && !escorts.value.some((escort) => escort.id === selectedEvent.value?.escort?.id)) escorts.value.unshift(selectedEvent.value.escort)
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
  try {
    const updated = await api.ackNotification(notification.id)
    notifications.value = notifications.value.map((item) => item.id === updated.id ? updated : item)
    if (selectedEvent.value) selectedEvent.value = { ...selectedEvent.value, notifications: selectedEvent.value.notifications.map((item) => item.id === updated.id ? updated : item) }
    notice.value = `${notificationTargetLabel(updated.target)}通知已确认。`
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '通知确认失败，请稍后重试。'
  }
}

async function retryNotification(notification: Notification) {
  try {
    const updated = await api.retryNotification(notification.id)
    notifications.value = notifications.value.map((item) => item.id === updated.id ? updated : item)
    if (selectedEvent.value) selectedEvent.value = { ...selectedEvent.value, notifications: selectedEvent.value.notifications.map((item) => item.id === updated.id ? updated : item) }
    notice.value = '已提交通知重试。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '通知重试失败，请稍后重试。'
  }
}

async function injectObservation() {
  if (!selectedElderId.value || injectBusy.value) return
  injectBusy.value = true
  injectError.value = ''
  injectNotice.value = ''
  try {
    // Vue 对 <input type="number"> 的 v-model 会直接给出 number（runtime-dom 里
    // type 为 number 时走 looseToNumber）。这些字段必须先转成字符串再 trim，
    // 否则用户一在"数值/持续分钟/经纬度"里打字，这里就抛 TypeError，
    // 请求根本发不出去，页面只会显示"观测注入失败"。
    const text = (value: unknown) => (value === null || value === undefined ? '' : String(value))
    const input = injection.value
    const body: Record<string, unknown> = { kind: input.kind, source: 'simulated', idempotency_key: crypto.randomUUID() }
    if (injectionFields.value === 'blood-pressure') body.value = { systolic: Number(input.systolic), diastolic: Number(input.diastolic) }
    else if (text(input.value).trim()) body.value = Number(input.value)
    if (text(input.duration_minutes).trim()) body.duration_minutes = Number(input.duration_minutes)
    if (input.kind === 'immobility') body.sleeping = input.sleeping
    if (text(input.occurred_at).trim()) {
      const occurredAt = new Date(input.occurred_at)
      if (!Number.isNaN(occurredAt.getTime())) body.occurred_at = occurredAt.toISOString()
    }
    if (text(input.label).trim() && text(input.latitude).trim() && text(input.longitude).trim()) body.location = { latitude: Number(input.latitude), longitude: Number(input.longitude), label: text(input.label).trim() }
    const result = await api.createObservation(selectedElderId.value, body)
    injectNotice.value = `已写入 ${result.observation.kind} 观测，产生 ${result.events.length} 个事件。`
    await loadEvents()
    if (result.events[0]) await openEvent(result.events[0])
  } catch (cause) {
    injectError.value = cause instanceof ApiError ? cause.detail : '观测注入失败，请稍后重试。'
  } finally {
    injectBusy.value = false
  }
}

async function saveFailureConfig() {
  failConfigBusy.value = true
  failConfigNotice.value = ''
  try {
    const result = await api.configureNotificationFailures(failTargets.value)
    failTargets.value = result.fail_targets
    failConfigNotice.value = '模拟通知故障配置已更新。'
  } catch (cause) {
    failConfigNotice.value = cause instanceof ApiError ? cause.detail : '配置保存失败。'
  } finally {
    failConfigBusy.value = false
  }
}

async function escortAction(action: 'accept' | 'complete') {
  if (!selectedEscort.value) return
  if (action === 'complete' && !actionNote.value.trim()) {
    error.value = '完成陪诊需要填写处理说明。'
    return
  }
  try {
    const updated = await api.escortAction(selectedEscort.value.id, action, action === 'complete' ? actionNote.value.trim() : undefined)
    escorts.value = escorts.value.map((escort) => escort.id === updated.id ? updated : escort)
    if (selectedEvent.value?.escort?.id === updated.id) selectedEvent.value = { ...selectedEvent.value, escort: updated }
    actionNote.value = ''
    notice.value = action === 'accept' ? '陪诊工单已接单。' : '陪诊工单已完成。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '陪诊操作失败。'
  }
}

function notificationClass(status: Notification['status']) {
  return `notification-${status}`
}

function canAckNotification(notification: Notification): boolean {
  return props.user.role === 'admin' || props.user.role === notification.target
}

onMounted(() => { void loadAll() })
</script>

<template>
  <div class="page-content family-page">
    <header class="page-header"><div><h1>家属与社区</h1><p>查看授权家庭的事件、通知和处理时间线。</p></div><div class="family-role"><UsersRound :size="20" />{{ roleLabel[props.user.role] }}</div></header>
    <div v-if="error" class="notice-bar error" role="alert"><CircleAlert :size="18" />{{ error }}<button class="text-button" type="button" @click="loadAll"><RefreshCw :size="16" />重试</button></div>
    <div v-if="notice" class="notice-bar success" role="status"><Check :size="18" />{{ notice }}</div>
    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在加载家庭数据…</div>
    <template v-else>
      <section class="family-toolbar surface-card"><label for="family-elder">当前家庭成员</label><select id="family-elder" v-model="selectedElderId" @change="chooseElder"><option v-for="elder in elders" :key="elder.id" :value="elder.id">{{ elder.name }} · {{ elder.city }}</option></select><button class="secondary-button" type="button" :disabled="eventsLoading" @click="loadAll"><RefreshCw :size="18" :class="{ spin: eventsLoading }" />刷新事件</button></section>
      <div class="family-layout">
        <section class="event-list-panel surface-card">
          <div class="table-heading"><h2>事件列表</h2><span>{{ events.length }} 条</span></div>
          <div v-if="events.length" class="event-list"><button v-for="event in events" :key="event.id" class="event-row" :class="eventClass(event)" type="button" @click="openEvent(event)"><span class="event-severity-icon"><ShieldAlert v-if="event.severity === 'critical'" :size="22" /><AlertTriangle v-else :size="22" /></span><span class="event-main"><strong>{{ event.title }}</strong><small>{{ event.elder_name }} · {{ formatDateTime(event.created_at) }}</small><span class="event-description">{{ event.description }}</span></span><span class="event-status">{{ eventStatusLabel(event.status) }}</span><ChevronRight :size="22" /></button></div>
          <div v-else class="empty-table"><p>当前家庭没有可见事件。</p><span>符合规则的观测事件会出现在这里。</span></div>
        </section>

        <section class="event-detail-panel">
          <div v-if="detailLoading" class="surface-card page-loading"><LoaderCircle class="spin" :size="28" />正在读取事件详情…</div>
          <div v-else-if="selectedEvent" class="detail-stack">
            <article class="surface-card event-detail-card"><header class="detail-header"><div><div class="detail-kicker"><span class="severity-tag" :class="`tag-${selectedEvent.severity}`">{{ eventSeverityLabel(selectedEvent.severity) }}</span><span>{{ selectedEvent.source === 'simulated' || selectedEvent.source === 'simulated_wearable' ? '模拟观测' : selectedEvent.source }}</span></div><h2>{{ selectedEvent.title }}</h2><p>{{ selectedEvent.elder_name }} · {{ formatDateTime(selectedEvent.created_at) }}</p></div><span class="detail-status">{{ eventStatusLabel(selectedEvent.status) }}</span></header><p class="detail-description">{{ selectedEvent.description }}</p>
              <div class="action-panel"><div class="action-buttons"><button v-if="actionAllowed('acknowledge')" class="primary-button" type="button" :disabled="busy" @click="performAction('acknowledge')"><UserCheck :size="18" />确认收到</button><button v-if="actionAllowed('start')" class="primary-button" type="button" :disabled="busy" @click="performAction('start')"><Play :size="18" />开始处理</button><button v-if="actionAllowed('resolve')" class="primary-button" type="button" :disabled="busy" @click="performAction('resolve')"><CheckCircle2 :size="18" />完成处理</button><button v-if="actionAllowed('community_unavailable')" class="secondary-button" type="button" :disabled="busy" @click="performAction('community_unavailable')"><Stethoscope :size="18" />社区无法协助</button><button v-if="actionAllowed('correct') && !showCorrection" class="outline-danger-button" type="button" :disabled="busy" @click="showCorrection = true"><XCircle :size="18" />标记误报</button></div><textarea v-if="['handling'].includes(selectedEvent.status) || actionAllowed('community_unavailable')" v-model="actionNote" class="action-note" rows="2" placeholder="处理说明（完成处理或社区无法协助时必填）" /><div v-if="showCorrection" class="correction-box"><label for="correction-reason">误报原因</label><textarea id="correction-reason" v-model="correctionReason" rows="2" placeholder="请记录为什么判定为误报" /><div><button class="secondary-button" type="button" @click="showCorrection = false">取消</button><button class="danger-button" type="button" :disabled="busy" @click="performAction('correct')">保存误报原因</button></div></div></div>
            </article>

            <article class="surface-card timeline-card"><div class="table-heading"><h2>处理时间线</h2><span>{{ selectedEvent.timeline.length }} 个节点</span></div><div v-if="selectedEvent.timeline.length" class="timeline-list"><div v-for="node in selectedEvent.timeline" :key="node.id" class="timeline-item"><span class="timeline-dot" /><div><strong>{{ timelineNodeLabel(node.node) }}</strong><p>{{ node.detail }}</p><small>{{ timelineActorLabel(node.actor) }} · {{ formatDateTime(node.at) }}</small></div></div></div><div v-else class="inline-empty">暂无处理节点。</div></article>

            <article class="surface-card notification-card"><div class="table-heading"><h2>通知结果</h2><span>{{ selectedNotifications.length }} 个目标</span></div><div v-if="selectedNotifications.length" class="notification-list"><div v-for="notification in selectedNotifications" :key="notification.id" class="notification-row" :class="notificationClass(notification.status)"><span class="notification-icon"><Send :size="18" /></span><div><strong>{{ notificationTargetLabel(notification.target) }}</strong><span>{{ notificationStatusLabel(notification.status) }} · 尝试 {{ notification.attempts }} 次</span><small v-if="notification.last_error">{{ notification.last_error }}</small></div><div class="notification-actions"><button v-if="notification.status === 'sent' && canAckNotification(notification)" class="text-button" type="button" @click="ackNotification(notification)"><Check :size="16" />确认</button><button v-if="notification.status === 'failed' && props.user.role === 'admin'" class="text-button" type="button" @click="retryNotification(notification)"><RotateCcw :size="16" />重试</button><span v-if="notification.simulated" class="simulated-label">模拟通知</span></div></div></div><div v-else class="inline-empty">暂无通知记录。</div></article>

            <article v-if="selectedEscort" class="surface-card escort-card"><div class="table-heading"><h2><Stethoscope :size="22" />陪诊工单</h2><span>放心医 · {{ selectedEscort.simulated ? '模拟接入' : '已接入' }}</span></div><div class="escort-row"><div><strong>{{ selectedEscort.status === 'requested' ? '等待接单' : selectedEscort.status === 'accepted' ? '陪诊进行中' : '已完成' }}</strong><span>申请于 {{ formatDateTime(selectedEscort.requested_at) }}</span></div><button v-if="selectedEscort.status === 'requested' && (props.user.role === 'community' || props.user.role === 'admin')" class="primary-button" type="button" @click="escortAction('accept')"><Check :size="18" />接单</button><button v-if="selectedEscort.status === 'accepted' && (props.user.role === 'community' || props.user.role === 'admin')" class="secondary-button" type="button" @click="escortAction('complete')"><CheckCircle2 :size="18" />完成陪诊</button></div></article>
          </div>
          <div v-else class="surface-card detail-empty"><ShieldAlert :size="42" /><h2>选择一条事件</h2><p>事件详情、通知结果和处理时间线会显示在这里。</p></div>
        </section>
      </div>

      <section v-if="canInject" class="admin-tools-grid">
        <article class="surface-card admin-tool-card"><div class="section-heading"><div><h2>注入观测</h2><p>仅用于复验固定规则，来源会明确记录为模拟观测。</p></div><MapPin :size="25" /></div><div class="injection-form"><label class="field-label" for="observation-kind">观测类型</label><select id="observation-kind" v-model="injection.kind"><option value="heart_rate">心率</option><option value="blood_pressure">血压</option><option value="fall">跌倒</option><option value="wandering">徘徊</option><option value="immobility">静止</option><option value="away">未归</option><option value="return_home">回家</option><option value="activity">活动</option><option value="wake">起床</option><option value="lunch">午餐</option><option value="dinner">晚餐</option><option value="sleep">入睡</option></select><div v-if="injectionFields === 'blood-pressure'" class="form-two-columns"><label><span>高压</span><input v-model="injection.systolic" type="number" /></label><label><span>低压</span><input v-model="injection.diastolic" type="number" /></label></div><label v-else class="field-label" for="observation-value">数值（可选）</label><input v-if="injectionFields !== 'blood-pressure'" id="observation-value" v-model="injection.value" type="number" placeholder="例如：145" /><label v-if="['immobility', 'away'].includes(injection.kind)" class="check-field"><input v-model="injection.sleeping" type="checkbox" /> <span>记录为睡眠状态</span></label><label class="field-label" for="observation-duration">持续分钟（可选）</label><input id="observation-duration" v-model="injection.duration_minutes" type="number" min="0" placeholder="例如：45" /><label class="field-label" for="observation-occurred-at">观测时间（可选）</label><input id="observation-occurred-at" v-model="injection.occurred_at" type="datetime-local" /><label class="field-label" for="observation-label">位置标签（可选）</label><input id="observation-label" v-model="injection.label" placeholder="例如：小区门口" /><div class="form-two-columns"><label><span>纬度（可选）</span><input v-model="injection.latitude" type="number" step="any" placeholder="例如：30.67" /></label><label><span>经度（可选）</span><input v-model="injection.longitude" type="number" step="any" placeholder="例如：104.06" /></label></div><p class="field-help">时间按本机时区转换为 ISO8601；只有位置标签、纬度、经度都填写时才会附加位置。</p><p v-if="injectError" class="form-error">{{ injectError }}</p><p v-if="injectNotice" class="form-success">{{ injectNotice }}</p><button class="primary-button" type="button" :disabled="injectBusy || !selectedElderId" @click="injectObservation"><Send :size="18" />{{ injectBusy ? '写入中…' : '写入模拟观测' }}</button></div></article>
        <article v-if="props.user.role === 'admin'" class="surface-card admin-tool-card"><div class="section-heading"><div><h2>通知故障模拟</h2><p>只影响后续通知发送或重试，响应会返回实际配置。</p></div><CircleAlert :size="25" /></div><div class="failure-options"><label class="check-field"><input v-model="failTargets" value="community" type="checkbox" /> <span>社区通知失败</span></label><label class="check-field"><input v-model="failTargets" value="emergency" type="checkbox" /> <span>模拟120通知失败</span></label><label class="check-field"><input v-model="failTargets" value="child" type="checkbox" /> <span>子女通知失败</span></label></div><p v-if="failConfigNotice" class="form-success">{{ failConfigNotice }}</p><button class="secondary-button" type="button" :disabled="failConfigBusy" @click="saveFailureConfig"><Save :size="18" />{{ failConfigBusy ? '保存中…' : '保存故障配置' }}</button></article>
      </section>
    </template>
  </div>
</template>
