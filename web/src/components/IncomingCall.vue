<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { Video, PhoneOff } from 'lucide-vue-next'
import { api, ApiError } from '../lib/api'
import type { Call, User } from '../types'

const props = defineProps<{ user: User; suspended: boolean }>()
const emit = defineEmits<{ answer: [call: Call] }>()
const incoming = ref<Call | null>(null)
const error = ref('')
const busy = ref(false)
let timer: number | undefined
let disposed = false
let polling = false
let version = 0

async function poll() {
  if (polling || disposed || props.suspended) { if (props.suspended) incoming.value = null; return }
  polling = true
  const currentVersion = version
  try {
    const elders = await api.elders()
    const lists = await Promise.all(elders.map(elder => api.calls(elder.id)))
    if (!disposed && !props.suspended && version === currentVersion) {
      incoming.value = lists.flat().find(call => call.status === 'ringing' && call.created_by !== props.user.id) || null
    }
  } catch {
    // The main screen handles connection errors. A failed poll must not invent a call.
  } finally { polling = false }
}

function answer() {
  const call = incoming.value
  if (!call) return
  version++
  incoming.value = null
  emit('answer', call)
}

async function decline() {
  if (!incoming.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api.callAction(incoming.value.id, 'decline')
    version++
    incoming.value = null
  } catch (cause) { error.value = cause instanceof ApiError ? cause.detail : '暂时无法拒绝来电，请重试。' }
  finally { busy.value = false }
}

const INCOMING_POLL_MS = 5000

function startTimer() {
  if (timer === undefined) timer = window.setInterval(() => { void poll() }, INCOMING_POLL_MS)
}

function stopTimer() {
  if (timer !== undefined) { window.clearInterval(timer); timer = undefined }
}

/** 页面在后台时不轮询；回到前台立刻补一次，避免漏掉来电。 */
function handleVisibility() {
  if (document.hidden) {
    stopTimer()
    return
  }
  void poll()
  startTimer()
}

onMounted(() => {
  void poll()
  startTimer()
  document.addEventListener('visibilitychange', handleVisibility)
})

onBeforeUnmount(() => {
  disposed = true
  stopTimer()
  document.removeEventListener('visibilitychange', handleVisibility)
})
</script>

<template>
  <div v-if="incoming && !suspended" class="modal-backdrop">
    <section class="modal-card small-modal incoming-call" role="dialog" aria-modal="true" aria-labelledby="incoming-title">
      <Video :size="42" aria-hidden="true" />
      <h2 id="incoming-title">家人视频来电</h2>
      <p>接听后将使用摄像头和麦克风。</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="modal-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="decline"><PhoneOff :size="22" />拒绝</button>
        <button type="button" class="primary-button" :disabled="busy" @click="answer"><Video :size="22" />接听</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 字号与间距改为 rem / --sp-*，跟随字号档位与密度档位 */
.incoming-call { color:var(--brand-deep); }
.incoming-call h2 { font-size:1.875rem; margin:var(--sp-16) 0; }
.incoming-call p { font-size:1.375rem; line-height:1.5; }
</style>
