<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Camera, CameraOff, Check, CircleAlert, LoaderCircle, RefreshCw, Save, ShieldCheck, Volume2, X } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { formatDateTime } from '../lib/format'
import { useCameraSession } from '../lib/useCameraSession'
import type { Capabilities, Elder, Role, User } from '../types'

const props = defineProps<{ user: User }>()

const elder = ref<Elder | null>(null)
const capabilities = ref<Capabilities | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const showCameraConfirm = ref(false)
const previewVideo = ref<HTMLVideoElement | null>(null)
const camera = useCameraSession()
const cameraBusy = camera.busy
const previewActive = camera.previewActive
const lastSnapshotAt = camera.lastSnapshotAt
const localCameraError = ref('')
const cameraError = computed(() => localCameraError.value || camera.error.value)
const remoteSnapshotUrl = ref<string | null>(null)

const canEdit = computed(() => props.user.role === 'elder' || props.user.role === 'child' || props.user.role === 'admin')
const canCaptureLocally = computed(() => props.user.role === 'elder')
const canInject = computed(() => props.user.role === 'child' || props.user.role === 'admin')
const roleLabel: Record<Role, string> = { elder: '老人端', child: '家属端', community: '社区端', admin: '演示管理' }

const draft = ref({
  city: '', voice_enabled: true, dialect: 'zh-CN',
  routine: { wake_time: '07:00', lunch_time: '12:00', dinner_time: '18:00', sleep_time: '22:00' },
  rules: { night_start: '22:00', night_end: '06:00', immobility_minutes: 30, sleep_immobility_minutes: 120, away_minutes: 60, heart_rate_low: 50, heart_rate_high: 120, systolic_high: 160, diastolic_high: 100 },
})

function copyElderToDraft(value: Elder) {
  draft.value = {
    city: value.city,
    voice_enabled: value.voice_enabled,
    dialect: value.dialect,
    routine: { ...value.routine },
    rules: { ...value.rules },
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [elders, capabilityResult] = await Promise.all([api.elders(), api.capabilities()])
    elder.value = elders[0] || null
    capabilities.value = capabilityResult
    if (elder.value) copyElderToDraft(elder.value)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取安全设置，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function saveSettings() {
  if (!elder.value || !canEdit.value || saving.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    elder.value = await api.updateElderSettings(elder.value.id, {
      city: draft.value.city,
      voice_enabled: draft.value.voice_enabled,
      dialect: draft.value.dialect,
      routine: draft.value.routine,
      rules: draft.value.rules,
    })
    copyElderToDraft(elder.value)
    notice.value = '设置已保存。'
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '保存失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

async function startCamera() {
  if (!elder.value || !elder.value.camera_enabled || !canCaptureLocally.value || cameraBusy.value) return
  localCameraError.value = ''
  camera.clearError()
  try {
    await camera.start(elder.value.id)
  } catch {
    // The shared session stores a precise browser permission/device error.
  }
}

function stopStream() {
  camera.stop()
}

function askCameraToggle() {
  if (!elder.value || !canEdit.value) return
  if (elder.value.camera_enabled) showCameraConfirm.value = true
  else void enableCamera()
}

async function enableCamera() {
  if (!elder.value) return
  localCameraError.value = ''
  camera.clearError()
  if (!canCaptureLocally.value) {
    try {
      elder.value = await api.updateElderSettings(elder.value.id, { camera_enabled: true })
      notice.value = '老人摄像头已开启；请在老人端启动本机预览。'
    } catch (cause) {
      localCameraError.value = cause instanceof ApiError ? cause.detail : '开启摄像头失败，请稍后重试。'
    }
    return
  }
  try {
    await camera.start(elder.value.id, { initialSnapshot: false })
    elder.value = await api.updateElderSettings(elder.value.id, { camera_enabled: true })
    await camera.uploadNow()
    notice.value = '摄像头已开启，正在使用真实预览。'
  } catch (cause) {
    if (cause instanceof ApiError) localCameraError.value = cause.detail
    camera.stop()
  }
}

async function confirmCameraOff() {
  if (!elder.value) return
  localCameraError.value = ''
  camera.clearError()
  camera.stop()
  try {
    elder.value = await api.updateElderSettings(elder.value.id, { camera_enabled: false, confirm_camera_off: true })
    remoteSnapshotUrl.value = null
    showCameraConfirm.value = false
    notice.value = '摄像头已关闭，已停止采集并清除保存的快照。'
  } catch (cause) {
    localCameraError.value = `${canCaptureLocally.value ? '本机采集已停止。' : ''}远端状态尚未同步，${cause instanceof ApiError ? cause.detail : '请检查网络后重试。'}`
  }
}

async function loadRemoteSnapshot() {
  if (!elder.value || !elder.value.camera_enabled) return
  try {
    const blob = await api.snapshot(elder.value.id)
    if (remoteSnapshotUrl.value) URL.revokeObjectURL(remoteSnapshotUrl.value)
    remoteSnapshotUrl.value = URL.createObjectURL(blob)
  } catch (cause) {
    localCameraError.value = cause instanceof ApiError ? cause.detail : '暂时无法读取快照。'
  }
}

watch([previewActive, previewVideo], () => {
  if (previewActive.value && previewVideo.value) void camera.attachPreview(previewVideo.value)
}, { flush: 'post' })

onMounted(() => { void load() })
onBeforeUnmount(() => {
  camera.detachPreview(previewVideo.value)
  if (remoteSnapshotUrl.value) URL.revokeObjectURL(remoteSnapshotUrl.value)
})
</script>

<template>
  <div class="page-content">
    <header class="page-header"><div><h1>安全设置</h1><p>{{ elder ? `${elder.name}的设备、作息和异常规则` : '设备、作息和异常规则' }}</p></div><span class="role-readonly"><ShieldCheck :size="18" />{{ roleLabel[props.user.role] }}{{ canEdit ? '' : '（只读）' }}</span></header>

    <div v-if="notice" class="notice-bar success" role="status"><Check :size="18" />{{ notice }}</div>
    <div v-if="error" class="notice-bar error" role="alert">{{ error }} <button class="text-button" type="button" @click="load"><RefreshCw :size="16" />重试</button></div>
    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在加载安全设置…</div>
    <div v-else-if="!elder" class="page-empty-state"><p>当前账号还没有可查看的老人信息。</p></div>
    <template v-else>
      <section class="camera-section surface-card">
        <div class="section-heading"><div><h2>摄像头</h2><p>关闭前需要二次确认。开启后仅上传周期性 JPEG 快照，不代表实时视频或视觉识别。</p></div><span class="camera-state" :class="{ enabled: elder.camera_enabled }"><span class="state-dot" />{{ elder.camera_enabled ? '已开启' : '已关闭' }}</span></div>
        <div class="camera-layout">
          <div class="camera-preview-wrap" :class="{ inactive: !previewActive }">
            <video v-if="previewActive" ref="previewVideo" class="camera-preview" muted playsinline aria-label="摄像头真实预览" />
            <img v-else-if="remoteSnapshotUrl" :src="remoteSnapshotUrl" class="camera-preview" alt="最近一次摄像头快照" />
            <div v-else class="camera-placeholder"><CameraOff :size="46" /><span>{{ elder.camera_enabled ? (canCaptureLocally ? '点击“启动本机预览”查看真实画面' : '本机不采集画面，请读取最近快照') : '摄像头已关闭' }}</span></div>
            <span v-if="previewActive" class="preview-live-label"><span class="state-dot" />本机预览</span>
          </div>
          <div class="camera-controls">
            <div class="camera-control-row"><strong>采集状态</strong><span>{{ previewActive ? '正在预览' : '未开始预览' }}</span></div>
            <div class="camera-control-row"><strong>最近快照</strong><span>{{ lastSnapshotAt ? formatDateTime(lastSnapshotAt) : '暂无本次会话快照' }}</span></div>
            <div v-if="cameraError" class="camera-error"><CircleAlert :size="19" />{{ cameraError }}</div>
            <div class="camera-buttons">
              <button v-if="canCaptureLocally && elder.camera_enabled && !previewActive" class="primary-button" type="button" :disabled="cameraBusy" @click="startCamera"><Camera :size="20" />{{ cameraBusy ? '启动中…' : '启动本机预览' }}</button>
              <button v-if="previewActive" class="secondary-button" type="button" @click="stopStream"><CameraOff :size="20" />停止预览</button>
              <button v-if="elder.camera_enabled" class="outline-danger-button" type="button" :disabled="cameraBusy || !canEdit" @click="askCameraToggle"><CameraOff :size="20" />关闭摄像头</button>
              <button v-else class="primary-button" type="button" :disabled="cameraBusy || !canEdit" @click="askCameraToggle"><Camera :size="20" />开启摄像头</button>
              <button v-if="!previewActive && elder.camera_enabled && !canCaptureLocally" class="text-button" type="button" @click="loadRemoteSnapshot"><RefreshCw :size="17" />读取最近快照</button>
            </div>
            <p v-if="!canCaptureLocally && canEdit" class="permission-note">当前为远端老人摄像头设置，本机不会采集画面；请在老人端启动真实预览。</p>
            <p v-if="!canEdit" class="permission-note">社区账号可以查看状态和快照，不能修改摄像头设置。</p>
          </div>
        </div>
      </section>

      <form class="settings-columns" @submit.prevent="saveSettings">
        <section class="surface-card settings-card"><div class="section-heading"><div><h2>服务偏好</h2><p>浏览器语音由当前设备提供，方言能力以服务端配置为准。</p></div><Volume2 :size="27" /></div>
          <label class="field-label" for="city">所在城市</label><input id="city" v-model="draft.city" :disabled="!canEdit" />
          <label class="switch-field"><input v-model="draft.voice_enabled" type="checkbox" :disabled="!canEdit" /><span class="switch-track" /><span>开启语音播报与语音入口</span></label>
          <label class="field-label" for="dialect">语音配置</label><select id="dialect" v-model="draft.dialect" :disabled="!canEdit"><option v-for="dialect in capabilities?.speech.dialects || []" :key="dialect.id" :value="dialect.id">{{ dialect.label }}{{ dialect.available ? '' : '（未配置）' }}</option></select>
          <p class="field-help">{{ capabilities?.speech.provider || '服务端语音配置加载中' }}。普通话识别由浏览器决定，不会伪装成未配置的方言。</p>
        </section>

        <section class="surface-card settings-card"><div class="section-heading"><div><h2>作息时间</h2><p>作息学习只影响天气和健康提示，不会改动用药提醒。</p></div></div>
          <div class="settings-time-grid"><label><span>起床</span><input v-model="draft.routine.wake_time" type="time" :disabled="!canEdit" /></label><label><span>午餐</span><input v-model="draft.routine.lunch_time" type="time" :disabled="!canEdit" /></label><label><span>晚餐</span><input v-model="draft.routine.dinner_time" type="time" :disabled="!canEdit" /></label><label><span>入睡</span><input v-model="draft.routine.sleep_time" type="time" :disabled="!canEdit" /></label></div>
        </section>

        <section class="surface-card settings-card rules-card"><div class="section-heading"><div><h2>异常规则</h2><p>演示可配置阈值，触发后按固定规则记录事件和通知。</p></div></div>
          <div class="settings-rule-grid"><label><span>夜间开始</span><input v-model="draft.rules.night_start" type="time" :disabled="!canEdit" /></label><label><span>夜间结束</span><input v-model="draft.rules.night_end" type="time" :disabled="!canEdit" /></label><label><span>静止（分钟）</span><input v-model.number="draft.rules.immobility_minutes" type="number" min="1" :disabled="!canEdit" /></label><label><span>睡眠静止（分钟）</span><input v-model.number="draft.rules.sleep_immobility_minutes" type="number" min="1" :disabled="!canEdit" /></label><label><span>未归（分钟）</span><input v-model.number="draft.rules.away_minutes" type="number" min="1" :disabled="!canEdit" /></label><label><span>心率下限</span><input v-model.number="draft.rules.heart_rate_low" type="number" min="1" :disabled="!canEdit" /></label><label><span>心率上限</span><input v-model.number="draft.rules.heart_rate_high" type="number" min="1" :disabled="!canEdit" /></label><label><span>高压上限</span><input v-model.number="draft.rules.systolic_high" type="number" min="1" :disabled="!canEdit" /></label><label><span>低压上限</span><input v-model.number="draft.rules.diastolic_high" type="number" min="1" :disabled="!canEdit" /></label></div>
        </section>
        <div v-if="canEdit" class="settings-submit-row"><button class="primary-button" type="submit" :disabled="saving"><Save :size="20" />{{ saving ? '保存中…' : '保存全部设置' }}</button></div>
      </form>
    </template>

    <div v-if="showCameraConfirm" class="modal-backdrop" role="presentation" @click.self="showCameraConfirm = false"><section class="modal-card small-modal" role="dialog" aria-modal="true" aria-labelledby="camera-dialog-title"><header class="modal-header"><h2 id="camera-dialog-title">关闭摄像头？</h2><button class="icon-button" type="button" aria-label="取消" @click="showCameraConfirm = false"><X :size="22" /></button></header><p class="modal-copy">关闭后会停止本机采集，并清除已保存的快照。之后需要重新开启才能查看画面。</p><div class="modal-actions"><button class="secondary-button" type="button" @click="showCameraConfirm = false">暂不关闭</button><button class="danger-button" type="button" :disabled="cameraBusy" @click="confirmCameraOff">{{ cameraBusy ? '处理中…' : '确认关闭' }}</button></div></section></div>
  </div>
</template>
