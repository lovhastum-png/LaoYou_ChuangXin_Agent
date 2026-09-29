<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowRight, HeartHandshake, LockKeyhole, UserRound } from 'lucide-vue-next'
import { ApiError, api, saveSession } from '../lib/api'
import type { Role, Session } from '../types'

const emit = defineEmits<{
  success: [session: Session]
}>()

const presets: Array<{ role: Role; label: string; username: string; description: string }> = [
  { role: 'elder', label: '老人屏', username: 'elder', description: '大字提醒、语音和通话' },
  { role: 'child', label: '家属端', username: 'child', description: '查看家人状态和事件' },
  { role: 'community', label: '社区端', username: 'community', description: '协助处理社区事件' },
  { role: 'admin', label: '演示管理', username: 'admin', description: '注入观测和检查通知' },
]

const selectedRole = ref<Role>('elder')
const username = ref('elder')
// 不再预填演示口令：构建产物里的任何口令都等同于公开凭据。
const password = ref('')
const busy = ref(false)
const error = ref('')

const selectedPreset = computed(() => presets.find((preset) => preset.role === selectedRole.value))

function choose(role: Role) {
  selectedRole.value = role
  const preset = presets.find((item) => item.role === role)
  if (preset) username.value = preset.username
  error.value = ''
}

async function submit() {
  // 表单的 @submit 会被回车触发，按钮 disabled 拦不住；这里短路避免重复登录
  // （每次登录都会在服务端新建一个会话）。
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    const session = await api.login(username.value.trim(), password.value)
    saveSession(session)
    emit('success', session)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.detail : '登录失败，请检查网络后重试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="login-intro">
      <div class="login-brand">老友</div>
      <p>通通陪着您</p>
      <div class="intro-mark" aria-hidden="true"><HeartHandshake :size="122" :stroke-width="1.1" /></div>
      <div class="intro-copy">
        <strong>让每一天，都有人惦记。</strong>
        <span>用简单的方式，连接家人、社区和照护。</span>
      </div>
    </section>

    <section class="login-panel" aria-labelledby="login-heading">
      <div class="login-panel-inner">
        <div class="login-heading-row">
          <div>
            <p class="eyebrow">老友服务</p>
            <h1 id="login-heading">欢迎回来</h1>
            <p class="login-subtitle">请选择要进入的工作界面</p>
          </div>
          <div class="login-lock"><LockKeyhole :size="22" :stroke-width="1.8" /></div>
        </div>

        <div class="role-grid" aria-label="选择工作界面">
          <button
            v-for="preset in presets"
            :key="preset.role"
            type="button"
            class="role-option"
            :class="{ selected: selectedRole === preset.role }"
            @click="choose(preset.role)"
          >
            <UserRound :size="22" :stroke-width="1.8" />
            <span>{{ preset.label }}</span>
          </button>
        </div>
        <p class="role-description">{{ selectedPreset?.description }}</p>

        <form class="login-form" @submit.prevent="submit">
          <label class="field-label" for="username">账号</label>
          <div class="input-with-icon">
            <UserRound :size="21" :stroke-width="1.8" aria-hidden="true" />
            <input id="username" v-model="username" autocomplete="username" required />
          </div>
          <label class="field-label" for="password">密码</label>
          <div class="input-with-icon">
            <LockKeyhole :size="21" :stroke-width="1.8" aria-hidden="true" />
            <input id="password" v-model="password" type="password" autocomplete="current-password" required />
          </div>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <button class="primary-button login-submit" type="submit" :disabled="busy">
            {{ busy ? '正在登录…' : '进入老友' }}
            <ArrowRight :size="22" :stroke-width="2" aria-hidden="true" />
          </button>
        </form>

        <p class="login-note">本地演示账号可在上方选择。演示口令见服务端启动提示或《安装使用与维护》，对外部署请先设置 LAOYOU_DEMO_PASSWORD 或关闭演示账号。</p>
      </div>
    </section>
  </div>
</template>
