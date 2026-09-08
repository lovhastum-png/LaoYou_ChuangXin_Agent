<script setup lang="ts">
import { computed } from 'vue'
import {
  BellRing,
  Camera,
  CircleUserRound,
  HeartPulse,
  Home,
  LogOut,
  MessageCircleHeart,
  Pill,
  ShieldCheck,
  UsersRound,
  Video,
  Volume2,
  Wifi,
} from 'lucide-vue-next'
import type { NavRoute, Role, User } from '../types'

const props = defineProps<{
  user: User
  active: NavRoute
  audioEnabled?: boolean
}>()

const emit = defineEmits<{
  navigate: [route: NavRoute]
  logout: []
}>()

const navItems = computed(() => {
  const elderItems = [
    { route: 'home' as NavRoute, label: '首页', icon: Home },
    { route: 'reminders' as NavRoute, label: '用药提醒', icon: Pill },
    { route: 'calls' as NavRoute, label: '视频通话', icon: Video },
    { route: 'health' as NavRoute, label: '健康记录', icon: HeartPulse },
    { route: 'safety' as NavRoute, label: '安全设置', icon: ShieldCheck },
  ]
  if (props.user.role === 'elder') return elderItems
  return [
    { route: 'family' as NavRoute, label: '家属与社区', icon: UsersRound },
    { route: 'calls' as NavRoute, label: '视频通话', icon: Video },
    { route: 'health' as NavRoute, label: '健康记录', icon: HeartPulse },
    { route: 'safety' as NavRoute, label: '作息与安全', icon: ShieldCheck },
  ]
})

const roleLabel: Record<Role, string> = {
  elder: '老人屏',
  child: '家属端',
  community: '社区端',
  admin: '演示管理',
}

function select(route: NavRoute) {
  emit('navigate', route)
}
</script>

<template>
  <div class="app-shell">
    <aside class="side-nav" aria-label="主导航">
      <div class="brand-block">
        <div class="brand-name">老友</div>
        <div class="brand-tagline">通通陪着您</div>
      </div>

      <nav class="main-nav">
        <button
          v-for="item in navItems"
          :key="item.route"
          class="nav-item"
          :class="{ active: props.active === item.route }"
          type="button"
          @click="select(item.route)"
        >
          <component :is="item.icon" :size="28" :stroke-width="1.8" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="side-nav-bottom">
        <button class="nav-item family-link" :class="{ active: props.active === 'family' }" type="button" @click="select('family')">
          <UsersRound :size="28" :stroke-width="1.8" aria-hidden="true" />
          <span>{{ props.user.role === 'elder' ? '家属与社区' : roleLabel[props.user.role] }}</span>
        </button>
        <div class="account-row">
          <CircleUserRound :size="23" :stroke-width="1.8" aria-hidden="true" />
          <span class="account-name">{{ props.user.display_name }}</span>
          <button class="icon-button quiet" type="button" title="退出登录" aria-label="退出登录" @click="emit('logout')">
            <LogOut :size="19" :stroke-width="1.8" />
          </button>
        </div>
      </div>
    </aside>

    <main class="main-area">
      <slot />
      <footer class="status-bar">
        <span class="status-item"><Wifi :size="24" :stroke-width="2" />老友服务</span>
        <span class="status-divider" aria-hidden="true" />
        <span class="status-item"><Volume2 :size="24" :stroke-width="2" />语音设置</span>
        <span v-if="props.user.role !== 'elder'" class="status-item status-role"><BellRing :size="21" :stroke-width="1.8" />{{ roleLabel[props.user.role] }}</span>
      </footer>
    </main>
  </div>
</template>
