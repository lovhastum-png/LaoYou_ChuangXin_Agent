<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Check, ChevronRight, CircleAlert, LoaderCircle, MapPin, RefreshCw, Save, Send, ShieldAlert, UsersRound } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { eventStatusLabel, formatDateTime } from '../lib/format'
import type { Elder, EventItem, User } from '../types'

const props = defineProps<{ user: User }>()

const emit = defineEmits<{ openEvent: [id: string] }>()

const elders = ref<Elder[]>([])
const selectedElderId = ref('')
const events = ref<EventItem[]>([])
const loading = ref(true)
const eventsLoading = ref(false)
const error = ref('')
const notice = ref('')
const injectBusy = ref(false)
const injectError = ref('')
const injectNotice = ref('')
const failTargets = ref<string[]>([])
const failConfigBusy = ref(false)
const failConfigNotice = ref('')
const injection = ref({ kind: 'heart_rate', value: '145', duration_minutes: '', sleeping: false, occurred_at: '', label: '', latitude: '', longitude: '', systolic: '170', diastolic: '105' })

const roleLabel: Record<User['role'], string> = { elder: '老人端', child: '家属端', community: '社区端', admin: '演示管理' }
const canInject = computed(() => props.user.role === 'child' || props.user.role === 'admin')
const injectionFields = computed(() => injection.value.kind === 'blood_pressure' ? 'blood-pressure' : 'simple')

function eventClass(event: EventItem) {
  return [`severity-${event.severity}`, `status-${event.status}`]
}

async function loadEvents() {
  if (!selectedElderId.value) {
    events.value = []
    return
  }
  eventsLoading.value = true
  error.value = ''
  try {
    events.value = await api.events({ elder_id: selectedElderId.value })
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
    await loadEvents()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取家庭与社区数据。'
  } finally {
    loading.value = false
  }
}

async function chooseElder() {
  await loadEvents()
}

function openEvent(event: EventItem) {
  // 事件详情是独立整页（/event/:id），列表这里只负责跳转。
  emit('openEvent', event.id)
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
    if (result.events[0]) emit('openEvent', result.events[0].id)
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
      <section class="event-list-panel surface-card">
        <div class="table-heading"><h2>事件列表</h2><span>{{ events.length }} 条</span></div>
        <div v-if="events.length" class="event-list">
          <button v-for="event in events" :key="event.id" class="event-row" :class="eventClass(event)" type="button" @click="openEvent(event)">
            <span class="event-severity-icon"><ShieldAlert v-if="event.severity === 'critical'" :size="22" /><AlertTriangle v-else :size="22" /></span>
            <span class="event-main">
              <strong>{{ event.title }}</strong>
              <small>{{ event.elder_name }} · {{ formatDateTime(event.created_at) }}</small>
              <span class="event-description">{{ event.description }}</span>
            </span>
            <span class="event-status">{{ eventStatusLabel(event.status) }}</span>
            <ChevronRight :size="22" />
          </button>
        </div>
        <div v-else class="empty-table"><p>当前家庭没有可见事件。</p><span>符合规则的观测事件会出现在这里。</span></div>
        <p class="event-list-hint">点开任意一条事件，可以查看触发依据、现场录屏与完整处置记录。</p>
      </section>

      <section v-if="canInject" class="admin-tools-grid">
        <article class="surface-card admin-tool-card"><div class="section-heading"><div><h2>注入观测</h2><p>仅用于复验固定规则，来源会明确记录为模拟观测。</p></div><MapPin :size="25" /></div><div class="injection-form"><label class="field-label" for="observation-kind">观测类型</label><select id="observation-kind" v-model="injection.kind"><option value="heart_rate">心率</option><option value="blood_pressure">血压</option><option value="fall">跌倒</option><option value="wandering">徘徊</option><option value="immobility">静止</option><option value="away">未归</option><option value="return_home">回家</option><option value="activity">活动</option><option value="wake">起床</option><option value="lunch">午餐</option><option value="dinner">晚餐</option><option value="sleep">入睡</option></select><div v-if="injectionFields === 'blood-pressure'" class="form-two-columns"><label><span>高压</span><input v-model="injection.systolic" type="number" /></label><label><span>低压</span><input v-model="injection.diastolic" type="number" /></label></div><label v-else class="field-label" for="observation-value">数值（可选）</label><input v-if="injectionFields !== 'blood-pressure'" id="observation-value" v-model="injection.value" type="number" placeholder="例如：145" /><label v-if="['immobility', 'away'].includes(injection.kind)" class="check-field"><input v-model="injection.sleeping" type="checkbox" /> <span>记录为睡眠状态</span></label><label class="field-label" for="observation-duration">持续分钟（可选）</label><input id="observation-duration" v-model="injection.duration_minutes" type="number" min="0" placeholder="例如：45" /><label class="field-label" for="observation-occurred-at">观测时间（可选）</label><input id="observation-occurred-at" v-model="injection.occurred_at" type="datetime-local" /><label class="field-label" for="observation-label">位置标签（可选）</label><input id="observation-label" v-model="injection.label" placeholder="例如：小区门口" /><div class="form-two-columns"><label><span>纬度（可选）</span><input v-model="injection.latitude" type="number" step="any" placeholder="例如：30.67" /></label><label><span>经度（可选）</span><input v-model="injection.longitude" type="number" step="any" placeholder="例如：104.06" /></label></div><p class="field-help">时间按本机时区转换为 ISO8601；只有位置标签、纬度、经度都填写时才会附加位置。</p><p v-if="injectError" class="form-error">{{ injectError }}</p><p v-if="injectNotice" class="form-success">{{ injectNotice }}</p><button class="primary-button" type="button" :disabled="injectBusy || !selectedElderId" @click="injectObservation"><Send :size="18" />{{ injectBusy ? '写入中…' : '写入模拟观测' }}</button></div></article>
        <article v-if="props.user.role === 'admin'" class="surface-card admin-tool-card"><div class="section-heading"><div><h2>通知故障模拟</h2><p>只影响后续通知发送或重试，响应会返回实际配置。</p></div><CircleAlert :size="25" /></div><div class="failure-options"><label class="check-field"><input v-model="failTargets" value="community" type="checkbox" /> <span>社区通知失败</span></label><label class="check-field"><input v-model="failTargets" value="emergency" type="checkbox" /> <span>模拟120通知失败</span></label><label class="check-field"><input v-model="failTargets" value="child" type="checkbox" /> <span>子女通知失败</span></label></div><p v-if="failConfigNotice" class="form-success">{{ failConfigNotice }}</p><button class="secondary-button" type="button" :disabled="failConfigBusy" @click="saveFailureConfig"><Save :size="18" />{{ failConfigBusy ? '保存中…' : '保存故障配置' }}</button></article>
      </section>
    </template>
  </div>
</template>
