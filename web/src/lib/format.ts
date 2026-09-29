import type { EventSeverity, EventStatus, NotificationStatus, Observation } from '../types'

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
  weekday: 'short',
  timeZone: 'Asia/Shanghai',
})

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'Asia/Shanghai',
})

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return dateFormatter.format(date)
}

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return dateTimeFormatter.format(date)
}

export function formatClock(value: string | Date | null | undefined): string {
  if (!value) return '—'
  if (typeof value === 'string' && /^\d{1,2}:\d{2}$/.test(value)) return value.padStart(5, '0')
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(date)
}

export function greetingFor(date = new Date()): string {
  const hourPart = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).formatToParts(date).find((part) => part.type === 'hour')?.value
  const hour = Number(hourPart)
  if (hour < 5) return '晚上好'
  if (hour < 12) return '早上好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

export function observationLabel(observation: Observation): string {
  const map: Record<string, string> = {
    heart_rate: '心率',
    blood_pressure: '血压',
    activity: '活动',
    wake: '起床',
    lunch: '午餐',
    dinner: '晚餐',
    sleep: '入睡',
    return_home: '回家',
    fall: '跌倒',
    wandering: '徘徊',
    immobility: '静止',
    away: '未归',
  }
  return map[observation.kind] || observation.kind
}

export function observationValue(observation: Observation): string {
  if (observation.kind === 'blood_pressure' && observation.value && typeof observation.value === 'object') {
    const value = observation.value as { systolic?: number; diastolic?: number }
    if (value.systolic !== undefined && value.diastolic !== undefined) return `${value.systolic}/${value.diastolic} mmHg`
  }
  if (observation.value === null || observation.value === undefined || observation.value === '') {
    if (observation.duration_minutes !== null && observation.duration_minutes !== undefined) return `${observation.duration_minutes} 分钟`
    return '已记录'
  }
  // 未知结构不把原始 JSON 显示给老人看（例如 {"foo":1}），改为业务化文案。
  if (typeof observation.value === 'object') return '已记录'
  return String(observation.value)
}

export function eventStatusLabel(status: EventStatus): string {
  return {
    alerted: '待确认',
    acknowledged: '已确认',
    handling: '处理中',
    resolved: '已处理',
    false_positive: '误报已标记',
  }[status]
}

export function eventSeverityLabel(severity: EventSeverity): string {
  return severity === 'critical' ? '紧急' : '提醒'
}

export function notificationStatusLabel(status: NotificationStatus): string {
  return {
    pending: '待发送',
    sent: '已发送',
    failed: '发送失败',
    acknowledged: '已确认',
  }[status]
}

export function notificationTargetLabel(target: string): string {
  return { child: '子女', community: '社区', emergency: '模拟120' }[target] || target
}

export function timelineNodeLabel(node: string): string {
  return {
    alerted: '事件已触发',
    acknowledge: '确认收到事件',
    start: '开始处理',
    resolve: '完成处理',
    correct: '标记误报',
    community_unavailable: '社区无法协助',
    escort_requested: '创建陪诊工单',
    escort_accepted: '陪诊工单已接单',
    escort_completed: '陪诊工单已完成',
    notification_sent: '通知已发送',
    notification_failed: '通知发送失败',
    notification_ack: '通知已确认',
  }[node] || node
}

export function timelineActorLabel(actor: string): string {
  return {
    system: '系统',
    elder: '老人端',
    child: '家属端',
    community: '社区端',
    admin: '演示管理',
  }[actor] || actor
}

export function assistantActionLabel(action: string): string {
  return {
    none: '信息回复',
    reminder_proposal: '待确认的提醒',
    reminder_created: '提醒已创建',
    call: '视频通话',
    camera_confirm: '待确认的摄像头设置',
    camera_updated: '摄像头设置已更新',
    weather: '天气查询',
    health: '健康查询',
  }[action] || action
}
