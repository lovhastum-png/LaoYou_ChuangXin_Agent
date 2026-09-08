<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import AppShell from './components/AppShell.vue'
import BroadcastPlayer from './components/BroadcastPlayer.vue'
import IncomingCall from './components/IncomingCall.vue'
import { api, clearSession, getStoredUser, getToken, saveSession } from './lib/api'
import { useCameraSession } from './lib/useCameraSession'
import CallPage from './pages/CallPage.vue'
import CallsPage from './pages/CallsPage.vue'
import ElderHome from './pages/ElderHome.vue'
import FamilyWorkspace from './pages/FamilyWorkspace.vue'
import HealthPage from './pages/HealthPage.vue'
import LoginPage from './pages/LoginPage.vue'
import ReminderPage from './pages/ReminderPage.vue'
import SafetyPage from './pages/SafetyPage.vue'
import type { Call, NavRoute, Session, User } from './types'

const user = ref<User | null>(getStoredUser())
const loading = ref(Boolean(getToken()))
const routePath = ref(window.location.pathname)
const routeQuery = ref(new URLSearchParams(window.location.search))
const camera = useCameraSession()
let resumeCameraId: string | null = null

const route = computed(() => {
  const match = routePath.value.match(/^\/call\/([^/]+)/)
  if (match) return { kind: 'call' as const, callId: decodeURIComponent(match[1]), token: routeQuery.value.get('token') || undefined, initiator: routeQuery.value.get('offer') === '1', embedded: routeQuery.value.get('embedded') === '1' }
  const path = routePath.value.replace(/^\/+|\/+$/g, '')
  return { kind: (path || 'home') as NavRoute }
})

const activeRoute = computed<NavRoute>(() => route.value.kind === 'call' ? 'calls' : route.value.kind)
const needsCallOnlyAuth = computed(() => route.value.kind === 'call' && Boolean(route.value.token))

function updateRoute() {
  const leavingCall = routePath.value.startsWith('/call/') && !window.location.pathname.startsWith('/call/')
  routePath.value = window.location.pathname
  routeQuery.value = new URLSearchParams(window.location.search)
  if (leavingCall && resumeCameraId) void resumeMonitoring()
}

async function resumeMonitoring() {
  const id = resumeCameraId
  resumeCameraId = null
  if (!id) return
  try {
    const elders = await api.elders()
    if (user.value && elders.some(elder => elder.id === id && elder.camera_enabled)) await camera.start(id)
  } catch { /* The shared camera session exposes device errors in safety settings. */ }
}

function navigate(target: NavRoute) {
  const path = target === 'home' ? '/' : `/${target}`
  window.history.pushState({}, '', path)
  updateRoute()
}

function openCall(call: Pick<Call, 'id'>) {
  const token = getToken()
  if (!token) return
  resumeCameraId = camera.previewActive.value ? camera.elderId.value : null
  camera.stop()
  window.history.pushState({}, '', `/call/${encodeURIComponent(call.id)}?token=${encodeURIComponent(token)}`)
  updateRoute()
}

async function handleLogin(session: Session) {
  saveSession(session)
  user.value = session.user
  navigate(session.user.role === 'elder' ? 'home' : 'family')
}

async function logout() {
  resumeCameraId = null
  camera.stop()
  try { await api.logout() } catch {
    // A local logout remains useful if the server is unavailable.
  }
  clearSession()
  user.value = null
  navigate('home')
}

onMounted(async () => {
  window.addEventListener('popstate', updateRoute)
  const token = getToken()
  if (!token) {
    loading.value = false
    return
  }
  try {
    user.value = await api.me()
  } catch {
    clearSession()
    user.value = null
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => window.removeEventListener('popstate', updateRoute))
</script>

<template>
  <IncomingCall v-if="user && ['elder', 'child'].includes(user.role) && !loading" :key="user.id" :user="user" :suspended="route.kind === 'call'" @answer="openCall" />
  <BroadcastPlayer v-if="user?.role === 'elder' && !loading" :suspended="route.kind === 'call'" />
  <CallPage v-if="route.kind === 'call' && (user || needsCallOnlyAuth)" :key="route.callId" :call-id="route.callId" :route-token="route.token" :initiator="route.initiator" :embedded="route.embedded" :user="user" @exit="navigate('calls')" />
  <div v-else-if="loading" class="app-loading"><span class="loading-mark">老友</span><span>正在连接老友服务…</span></div>
  <LoginPage v-else-if="!user" @success="handleLogin" />
  <AppShell v-else :user="user" :active="activeRoute" :audio-enabled="user.role === 'elder' ? undefined : true" @navigate="navigate" @logout="logout">
    <ElderHome v-if="activeRoute === 'home'" :user="user" @navigate="navigate" @open-call="callId => openCall({ id: callId })" />
    <ReminderPage v-else-if="activeRoute === 'reminders'" :user="user" />
    <CallsPage v-else-if="activeRoute === 'calls'" :user="user" @open-call="openCall" />
    <HealthPage v-else-if="activeRoute === 'health'" :user="user" />
    <SafetyPage v-else-if="activeRoute === 'safety'" :user="user" />
    <FamilyWorkspace v-else :user="user" />
  </AppShell>
</template>
