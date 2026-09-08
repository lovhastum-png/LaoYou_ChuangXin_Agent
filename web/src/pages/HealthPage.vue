<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Activity, HeartPulse, LoaderCircle, MapPin, RefreshCw, Thermometer, Watch } from 'lucide-vue-next'
import { ApiError, api } from '../lib/api'
import { formatClock, formatDateTime, observationLabel, observationValue } from '../lib/format'
import type { Dashboard, Elder, Observation, User } from '../types'

const props = defineProps<{ user: User }>()
const elder = ref<Elder | null>(null)
const dashboard = ref<Dashboard | null>(null)
const observations = ref<Observation[]>([])
const loading = ref(true)
const error = ref('')

const latest = computed(() => dashboard.value?.latest_observation || observations.value[0] || null)
const healthBroadcasts = computed(() => (dashboard.value?.broadcasts || []).filter((item) => item.kind === 'health'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const elders = await api.elders()
    elder.value = elders[0] || null
    if (!elder.value) return
    const [dashboardResult, observationResult] = await Promise.all([
      api.dashboard(elder.value.id),
      api.observations(elder.value.id),
    ])
    dashboard.value = dashboardResult
    elder.value = dashboardResult.elder
    observations.value = observationResult
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '暂时无法读取健康记录，请稍后重试。'
  } finally {
    loading.value = false
  }
}

function sourceLabel(source: string): string {
  if (source === 'simulated' || source === 'simulated_wearable') return '模拟观测'
  return source
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-content">
    <header class="page-header"><div><h1>健康记录</h1><p>{{ elder ? `${elder.name}的近期观测与健康播报` : '近期观测与健康播报' }}</p></div><button class="secondary-button" type="button" :disabled="loading" @click="load"><RefreshCw :size="19" :class="{ spin: loading }" />刷新</button></header>

    <div v-if="error" class="notice-bar error" role="alert">{{ error }}</div>
    <div v-if="loading" class="page-loading"><LoaderCircle class="spin" :size="32" />正在加载健康记录…</div>
    <div v-else-if="!elder" class="page-empty-state"><p>当前账号还没有可查看的老人信息。</p></div>
    <template v-else>
      <section class="health-overview-grid">
        <article class="health-overview-card surface-card">
          <span class="health-card-icon green"><Activity :size="28" /></span>
          <div><span>最近观测</span><strong>{{ latest ? observationLabel(latest) : '暂无记录' }}</strong><small>{{ latest ? formatDateTime(latest.occurred_at) : '等待设备或手动记录' }}</small></div>
        </article>
        <article class="health-overview-card surface-card">
          <span class="health-card-icon orange"><HeartPulse :size="28" /></span>
          <div><span>观测值</span><strong>{{ latest ? observationValue(latest) : '—' }}</strong><small>{{ latest ? sourceLabel(latest.source) : '没有可显示的数值' }}</small></div>
        </article>
        <article class="health-overview-card surface-card">
          <span class="health-card-icon blue"><Watch :size="28" /></span>
          <div><span>记录数量</span><strong>{{ observations.length }}</strong><small>最近 100 条</small></div>
        </article>
      </section>

      <div class="health-columns">
        <section class="surface-card observation-card">
          <div class="table-heading"><h2>观测记录</h2><span>按时间倒序</span></div>
          <div v-if="observations.length" class="observation-list">
            <div v-for="observation in observations" :key="observation.id" class="observation-row">
              <span class="observation-dot" :class="{ alert: ['fall', 'wandering', 'immobility', 'away'].includes(observation.kind) }" />
              <div class="observation-main"><strong>{{ observationLabel(observation) }}</strong><span>{{ observationValue(observation) }}<template v-if="observation.location"><MapPin :size="14" />{{ observation.location.label || `${observation.location.latitude}, ${observation.location.longitude}` }}</template></span></div>
              <div class="observation-meta"><span>{{ formatClock(observation.occurred_at) }}</span><small>{{ sourceLabel(observation.source) }}</small></div>
            </div>
          </div>
          <div v-else class="inline-empty"><p>还没有健康观测记录。</p><span>设备接入或管理端注入观测后会显示在这里。</span></div>
        </section>

        <aside class="health-side-column">
          <section class="surface-card health-advice-card">
            <h2>健康建议</h2>
            <div v-if="healthBroadcasts.length" class="advice-list"><div v-for="broadcast in healthBroadcasts.slice(0, 4)" :key="broadcast.id" class="advice-item"><Thermometer :size="22" /><div><strong>{{ formatClock(broadcast.scheduled_at) }}</strong><p>{{ broadcast.text }}</p></div></div></div>
            <div v-else class="inline-empty">今天还没有健康播报。</div>
          </section>
          <p class="health-disclaimer">异常阈值属于演示可配置规则，健康记录不构成医疗诊断。</p>
        </aside>
      </div>
    </template>
  </div>
</template>
