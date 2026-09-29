export type Role = 'elder' | 'child' | 'community' | 'admin'

export interface User {
  id: string
  username: string
  display_name: string
  role: Role
}

export interface Session {
  token: string
  user: User
}

export interface Routine {
  wake_time: string
  lunch_time: string
  dinner_time: string
  sleep_time: string
}

export interface Rules {
  night_start: string
  night_end: string
  immobility_minutes: number
  sleep_immobility_minutes: number
  away_minutes: number
  heart_rate_low: number
  heart_rate_high: number
  systolic_high: number
  diastolic_high: number
}

export interface Elder {
  id: string
  name: string
  city: string
  camera_enabled: boolean
  voice_enabled: boolean
  dialect: string
  routine: Routine
  rules: Rules
}

export interface Weather {
  city: string
  temperature: number
  description: string
  advice: string
  observed_at: string
  source: 'live' | 'simulated' | string
}

export interface Reminder {
  id: string
  elder_id: string
  title: string
  medicine: string
  dose: string
  time: string
  enabled: boolean
  created_at: string
}

export interface Broadcast {
  id: string
  elder_id: string
  reminder_id: string | null
  kind: 'medication' | 'weather' | 'health' | string
  text: string
  scheduled_at: string
  played_at: string | null
  source: string
}

export interface Observation {
  id: string
  elder_id: string
  kind: string
  value: unknown
  duration_minutes: number | null
  sleeping: boolean | null
  occurred_at: string
  location: { latitude: number; longitude: number; label?: string } | null
  source: string
}

export type EventSeverity = 'warning' | 'critical'
export type EventStatus = 'alerted' | 'acknowledged' | 'handling' | 'resolved' | 'false_positive'

export interface EventItem {
  id: string
  elder_id: string
  elder_name: string
  kind: string
  title: string
  severity: EventSeverity
  status: EventStatus
  source: string
  description: string
  created_at: string
  updated_at: string
}

export interface Timeline {
  id: string
  node: string
  at: string
  actor: string
  detail: string
}

export type NotificationTarget = 'child' | 'community' | 'emergency'
export type NotificationStatus = 'pending' | 'sent' | 'failed' | 'acknowledged'

export interface Notification {
  id: string
  event_id: string
  target: NotificationTarget
  status: NotificationStatus
  created_at: string
  sent_at: string | null
  acknowledged_at: string | null
  attempts: number
  last_error: string | null
  simulated: boolean
}

export interface Escort {
  id: string
  event_id: string
  elder_id: string
  platform: string
  status: 'requested' | 'accepted' | 'completed'
  requested_at: string
  accepted_at: string | null
  completed_at: string | null
  note: string | null
  simulated: boolean
}

export interface EventDetail extends EventItem {
  timeline: Timeline[]
  notifications: Notification[]
  escort: Escort | null
}

export interface Dashboard {
  elder: Elder
  weather: Weather | null
  reminders: Reminder[]
  broadcasts: Broadcast[]
  latest_observation: Observation | null
  active_event_count: number
}

export interface Capabilities {
  assistant_mode: string
  speech: {
    provider: string
    configured: boolean
    /** 为 true 时方言会先归一化成普通话，再交给规则助手。 */
    normalizes_dialect?: boolean
    dialects: Array<{ id: string; label: string; available: boolean; note: string }>
  }
  tts?: {
    provider: string
    server_side: boolean
    note: string
  }
  video: { mode: string }
  integrations: {
    camera: string
    wearable: string
    emergency: string
    escort: string
  }
}

export interface AssistantResponse {
  reply: string
  mode: 'local_rules' | string
  action: 'none' | 'reminder_proposal' | 'reminder_created' | 'call' | 'camera_confirm' | 'camera_updated' | 'weather' | 'health' | string
  proposal: Record<string, unknown> | null
  confirm_token: string | null
}

export interface Call {
  id: string
  elder_id: string
  created_by: string
  status: 'ringing' | 'active' | 'ended' | 'declined'
  created_at: string
  answered_at: string | null
  ended_at: string | null
}

export type NavRoute = 'home' | 'reminders' | 'calls' | 'health' | 'safety' | 'family'
