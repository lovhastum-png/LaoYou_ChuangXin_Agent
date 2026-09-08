<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, Clock3, LoaderCircle, Phone, PhoneCall, RefreshCw, Video, XCircle } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { formatDateTime } from '../lib/format'
import type { Call, Elder, User } from '../types'

const props = defineProps<{ user: User }>()
const emit = defineEmits<{ openCall: [call: Call] }>()

interface CallRow extends Call { elderName: string }

const elders = ref<Elder[]>([])
const calls = ref<CallRow[]>([])
const loading = ref(true)
const busyElderId = ref<string | null>(null)
const error = ref('')
const notice = ref('')

const canStart = computed(() => props.user.role === 'elder' || props.user.role === 'child')
const activeCalls = computed(() => calls.value.filter((call) => call.status === 'ringing' || call.status === 'active'))
const historyCalls = computed(() => calls.value.filter((call) => call.status === 'ended' || call.status === 'declined'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    elders.value = await api.elders()
    const results = await Promise.all(elders.value.map(async (elder) => ({ elder, calls: await api.calls(elder.id) })))
    calls.value = results.flatMap(({ elder, calls: elderCalls }) => elderCalls.map((call) => ({ ...call, elderName: elder.name }))).sort((a, b) => b.created_at.localeCompare(a.created_at))
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取通话记录，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function startCall(elder: Elder) {
  if (!canStart.value || busyElderId.value) return
  busyElderId.value = elder.id
  error.value = ''
  try {
    const call = await api.createCall(elder.id)
    emit('openCall', call)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '无法发起通话，请稍后重试。'
  } finally {
    busyElderId.value = null
  }
}

async function endCall(call: CallRow) {
  try {
    const updated = await api.callAction(call.id, 'end')
    const index = calls.value.findIndex((item) => item.id === call.id)
    if (index >= 0) calls.value[index] = { ...updated, elderName: call.elderName }
    notice.value = '通话已挂断。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '挂断失败，请稍后重试。'
  }
}

function statusLabel(status: Call['status']): string {
  return { ringing: '等待接听', active: '通话中', ended: '已结束', declined: '已拒接' }[status]
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-content calls-page">
    <header class="page-header"><div><h1>视频通话</h1><p>和家人进行真实的浏览器端到端通话。</p></div><button class="secondary-button" type="button" :disabled="loading" @click="load"><RefreshCw :size="19" :class="{ spin: loading }" />刷新</button></header>
    <div class="call-note"><Video :size="20" /><span>通话会请求摄像头和麦克风权限。首版支持同机或局域网验证，公网通话需要 HTTPS 与 TURN 服务。</span></div>
    <div v-if="notice" class="notice-bar success"><CheckCircle2 :size="18" />{{ notice }}</div>
    <div v-if="error" class="notice-bar error" role="alert">{{ error }}</div>
    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在读取通话记录…</div>
    <template v-else>
      <section class="call-start-section surface-card"><div><h2>联系家人</h2><p>选择一位老人后开始通话，等待对方接听。</p></div><div class="call-elder-actions"><div v-for="elder in elders" :key="elder.id" class="call-elder-row"><span class="elder-avatar">{{ elder.name.slice(0, 1) }}</span><strong>{{ elder.name }}</strong><small>{{ elder.city }}</small><button class="primary-button" type="button" :disabled="!canStart || busyElderId === elder.id" @click="startCall(elder)"><PhoneCall :size="19" />{{ busyElderId === elder.id ? '发起中…' : '发起通话' }}</button></div><div v-if="!elders.length" class="inline-empty">当前账号没有可联系的家庭成员。</div></div></section>

      <section v-if="activeCalls.length" class="call-list-section"><div class="section-title-row"><h2>进行中的通话</h2><span>{{ activeCalls.length }} 个</span></div><div class="call-list"> <div v-for="call in activeCalls" :key="call.id" class="call-list-row active"><span class="call-status-icon"><Phone :size="22" /></span><div><strong>{{ call.elderName }}</strong><span>{{ statusLabel(call.status) }} · {{ formatDateTime(call.created_at) }}</span></div><button class="secondary-button" type="button" @click="emit('openCall', call)"><Video :size="18" />进入通话</button><button class="icon-button danger" type="button" title="挂断" aria-label="挂断" @click="endCall(call)"><XCircle :size="21" /></button></div></div></section>
      <section class="call-list-section"><div class="section-title-row"><h2>通话记录</h2><span>{{ historyCalls.length }} 条</span></div><div v-if="historyCalls.length" class="call-list"><div v-for="call in historyCalls" :key="call.id" class="call-list-row"><span class="call-status-icon muted"><Clock3 :size="22" /></span><div><strong>{{ call.elderName }}</strong><span>{{ statusLabel(call.status) }} · {{ formatDateTime(call.created_at) }}</span></div><CheckCircle2 v-if="call.status === 'ended'" class="call-ended" :size="21" /><XCircle v-else class="call-declined" :size="21" /></div></div><div v-else class="surface-card empty-table"><p>还没有通话记录。</p></div></section>
    </template>
  </div>
</template>
