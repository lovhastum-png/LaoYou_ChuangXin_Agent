<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, Edit3, LoaderCircle, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { formatClock, formatDateTime } from '../lib/format'
import type { Elder, Reminder, User } from '../types'

const props = defineProps<{ user: User }>()

const elder = ref<Elder | null>(null)
const reminders = ref<Reminder[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const formOpen = ref(false)
const editingId = ref<string | null>(null)
const draft = ref({ title: '', medicine: '', dose: '', time: '08:00', enabled: true })

const canEdit = computed(() => props.user.role === 'elder' || props.user.role === 'child' || props.user.role === 'admin')
const pageTitle = computed(() => elder.value ? `${elder.value.name}的用药提醒` : '用药提醒')

function resetDraft() {
  draft.value = { title: '', medicine: '', dose: '', time: '08:00', enabled: true }
  editingId.value = null
}

function beginCreate() {
  resetDraft()
  formOpen.value = true
  notice.value = ''
}

function beginEdit(reminder: Reminder) {
  draft.value = { title: reminder.title, medicine: reminder.medicine, dose: reminder.dose, time: reminder.time, enabled: reminder.enabled }
  editingId.value = reminder.id
  formOpen.value = true
  notice.value = ''
}

function cancelForm() {
  formOpen.value = false
  resetDraft()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const elders = await api.elders()
    elder.value = elders[0] || null
    reminders.value = elder.value ? await api.reminders(elder.value.id) : []
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取提醒，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!elder.value || busy.value) return
  notice.value = ''
  error.value = ''
  busy.value = true
  try {
    if (editingId.value) {
      await api.updateReminder(editingId.value, draft.value)
      notice.value = '提醒已更新。'
    } else {
      await api.createReminder(elder.value.id, draft.value)
      notice.value = '提醒已添加。'
    }
    formOpen.value = false
    resetDraft()
    reminders.value = await api.reminders(elder.value.id)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '保存失败，请稍后重试。'
  } finally {
    busy.value = false
  }
}

async function remove(reminder: Reminder) {
  if (!window.confirm(`确定删除“${reminder.title}”吗？`)) return
  error.value = ''
  try {
    await api.deleteReminder(reminder.id)
    notice.value = '提醒已删除。'
    if (elder.value) reminders.value = await api.reminders(elder.value.id)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '删除失败，请稍后重试。'
  }
}

async function toggle(reminder: Reminder) {
  try {
    const updated = await api.updateReminder(reminder.id, { enabled: !reminder.enabled })
    const index = reminders.value.findIndex((item) => item.id === reminder.id)
    if (index >= 0) reminders.value[index] = updated
    notice.value = updated.enabled ? '提醒已开启。' : '提醒已暂停。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '更新失败，请稍后重试。'
  }
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-content">
    <header class="page-header">
      <div><h1>{{ pageTitle }}</h1><p>每天的时间由您自己决定，通通会按时提醒。</p></div>
      <button v-if="canEdit && elder" class="primary-button" type="button" @click="beginCreate"><Plus :size="22" />添加提醒</button>
    </header>

    <div v-if="notice" class="notice-bar success" role="status"><Check :size="19" />{{ notice }}</div>
    <div v-if="error" class="notice-bar error" role="alert">{{ error }} <button class="text-button" type="button" @click="load"><RefreshCw :size="16" />重试</button></div>
    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在加载提醒…</div>
    <div v-else-if="!elder" class="page-empty-state"><p>当前账号还没有可查看的老人信息。</p></div>
    <div v-else class="reminder-page-layout">
      <section class="surface-card reminder-table-card">
        <div class="table-heading"><h2>每日提醒</h2><span>{{ reminders.length }} 项</span></div>
        <div v-if="reminders.length" class="reminder-table">
          <div v-for="reminder in reminders" :key="reminder.id" class="reminder-table-row" :class="{ disabled: !reminder.enabled }">
            <div class="table-time">{{ formatClock(reminder.time) }}</div>
            <div class="table-main"><strong>{{ reminder.title }}</strong><span>{{ reminder.medicine }} · {{ reminder.dose }}</span></div>
            <span class="enabled-state" :class="{ off: !reminder.enabled }">{{ reminder.enabled ? '每天提醒' : '已暂停' }}</span>
            <span class="table-created">添加于 {{ formatDateTime(reminder.created_at) }}</span>
            <div v-if="canEdit" class="row-actions">
              <button class="icon-button" type="button" :title="reminder.enabled ? '暂停提醒' : '开启提醒'" :aria-label="reminder.enabled ? '暂停提醒' : '开启提醒'" @click="toggle(reminder)"><Check v-if="reminder.enabled" :size="19" /><RefreshCw v-else :size="19" /></button>
              <button class="icon-button" type="button" title="编辑提醒" aria-label="编辑提醒" @click="beginEdit(reminder)"><Edit3 :size="19" /></button>
              <button class="icon-button danger" type="button" title="删除提醒" aria-label="删除提醒" @click="remove(reminder)"><Trash2 :size="19" /></button>
            </div>
          </div>
        </div>
        <div v-else class="empty-table"><p>还没有设置用药提醒。</p><button v-if="canEdit" class="secondary-button" type="button" @click="beginCreate"><Plus :size="19" />添加第一条提醒</button></div>
      </section>

      <aside class="side-info-panel">
        <h2>提醒设置</h2>
        <p>名称、剂量和时间由您录入。老友不会提供药物推荐，也不会擅自调整用药时间。</p>
        <div class="info-rule" />
        <p>语音播报需要在安全设置中开启，并且会在播报成功后记录为已播报。</p>
      </aside>
    </div>

    <div v-if="formOpen" class="modal-backdrop" role="presentation" @click.self="cancelForm">
      <section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="reminder-dialog-title">
        <header class="modal-header"><h2 id="reminder-dialog-title">{{ editingId ? '编辑提醒' : '添加提醒' }}</h2><button class="icon-button" type="button" aria-label="关闭" @click="cancelForm"><X :size="22" /></button></header>
        <form class="settings-form" @submit.prevent="submit">
          <label class="field-label" for="reminder-title">提醒名称</label><input id="reminder-title" v-model="draft.title" required placeholder="例如：早餐后用药" />
          <label class="field-label" for="reminder-medicine">药品名称</label><input id="reminder-medicine" v-model="draft.medicine" required placeholder="请输入药品名称" />
          <div class="form-two-columns"><div><label class="field-label" for="reminder-dose">剂量</label><input id="reminder-dose" v-model="draft.dose" required placeholder="例如：1片" /></div><div><label class="field-label" for="reminder-time">每天时间</label><input id="reminder-time" v-model="draft.time" type="time" required /></div></div>
          <label class="check-field"><input v-model="draft.enabled" type="checkbox" /> <span>保存后启用提醒</span></label>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="cancelForm">取消</button><button class="primary-button" type="submit" :disabled="busy">{{ busy ? '保存中…' : '保存提醒' }}</button></div>
        </form>
      </section>
    </div>
  </div>
</template>
