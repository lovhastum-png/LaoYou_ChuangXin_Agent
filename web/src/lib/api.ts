import type {
  AssistantResponse,
  Broadcast,
  Call,
  Capabilities,
  Dashboard,
  Elder,
  Escort,
  EventDetail,
  EventItem,
  Notification,
  Observation,
  Reminder,
  Session,
  User,
} from '../types'

const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
const TOKEN_KEY = 'laoyou.token'
const USER_KEY = 'laoyou.user'
/** 单次请求上限：超过即中断，避免弱网下界面永久停留在加载态。 */
const REQUEST_TIMEOUT_MS = 15_000
/** 会话失效时广播，由 App.vue 监听后回登录页。 */
export const UNAUTHORIZED_EVENT = 'laoyou:unauthorized'

let unauthorizedNotified = false

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUser(): User | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as User
  } catch {
    return null
  }
}

export function saveSession(session: Session): void {
  unauthorizedNotified = false
  localStorage.setItem(TOKEN_KEY, session.token)
  localStorage.setItem(USER_KEY, JSON.stringify(session.user))
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/** 会话失效：清掉本地凭证并通知界面回登录页，只广播一次。 */
function handleUnauthorized(): void {
  clearSession()
  if (unauthorizedNotified) return
  unauthorizedNotified = true
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
}

function hasChinese(text: string): boolean {
  return /[\u4e00-\u9fa5]/.test(text)
}

function readableErrorDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (!item || typeof item !== 'object') return ''
      const record = item as { loc?: unknown; msg?: unknown }
      const message = typeof record.msg === 'string' ? record.msg : '字段格式不正确'
      const locationParts = Array.isArray(record.loc)
        ? record.loc.filter((part): part is string | number => typeof part === 'string' || typeof part === 'number').filter((part) => part !== 'body' && part !== 'query' && part !== 'path')
        : []
      const location = locationParts.join('.')
      if (location === 'password' || location.endsWith('.password')) return '密码字段格式不正确'
      if (/input should be .*dictionary/i.test(message)) return '请求体格式不正确'
      if (/field required/i.test(message)) return location ? `${location}为必填项` : '缺少必填项'
      if (/valid string/i.test(message)) return location ? `${location}格式不正确` : '文本格式不正确'
      // 老人端会直接看到这句话，英文原文（含字段名）对老人没有意义。
      if (!hasChinese(message)) return location ? `${location}填写不正确` : '填写内容有问题，请检查后重试'
      return location ? `${location}：${message}` : message
    }).filter(Boolean)
    if (messages.length) return status === 422 ? `请求参数有误：${messages.join('；')}` : messages.join('；')
  }
  if (status >= 500) return '服务暂时不可用，请稍后重试。'
  return '操作没有成功，请重试或找家人帮忙。'
}

async function request<T>(path: string, init: RequestInit = {}, authToken?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')
  const token = authToken || getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const controller = new AbortController()
  const callerSignal = init.signal ?? null
  const abortFromCaller = () => controller.abort()
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort()
    else callerSignal.addEventListener('abort', abortFromCaller, { once: true })
  }
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: controller.signal })
  } catch (cause) {
    // 区分"超时/主动中断"与"连不上服务"，前者可重试，后者要查网络。
    if (controller.signal.aborted) throw new ApiError(0, '网络超时，请稍后重试。')
    throw new ApiError(0, '连接不上老友服务，请检查网络后重试。')
  } finally {
    window.clearTimeout(timer)
    callerSignal?.removeEventListener('abort', abortFromCaller)
  }

  // 登录接口本身的 401 是"密码错误"，不应触发全局登出。
  if (response.status === 401 && !path.startsWith('/auth/login')) handleUnauthorized()

  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body.detail !== undefined) detail = readableErrorDetail(body.detail, response.status)
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return (await response.json()) as T
  if (contentType.startsWith('image/')) return (await response.blob()) as T
  return (await response.text()) as T
}

function json<T>(path: string, method: string, body?: unknown, authToken?: string, keepalive = false): Promise<T> {
  return request<T>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
    keepalive,
  }, authToken)
}

export const api = {
  login(username: string, password: string): Promise<Session> {
    return json('/auth/login', 'POST', { username, password })
  },
  logout(): Promise<{ ok: true }> {
    return json('/auth/logout', 'POST')
  },
  me(authToken?: string): Promise<User> {
    return request('/auth/me', {}, authToken)
  },
  capabilities(): Promise<Capabilities> {
    return request('/capabilities')
  },
  elders(): Promise<Elder[]> {
    return request('/elders')
  },
  dashboard(elderId: string): Promise<Dashboard> {
    return request(`/elders/${encodeURIComponent(elderId)}/dashboard`)
  },
  updateElderSettings(elderId: string, settings: Record<string, unknown>): Promise<Elder> {
    return json(`/elders/${encodeURIComponent(elderId)}/settings`, 'PATCH', settings)
  },
  reminders(elderId: string): Promise<Reminder[]> {
    return request(`/elders/${encodeURIComponent(elderId)}/reminders`)
  },
  createReminder(elderId: string, body: Pick<Reminder, 'title' | 'medicine' | 'dose' | 'time'> & { enabled?: boolean }): Promise<Reminder> {
    return json(`/elders/${encodeURIComponent(elderId)}/reminders`, 'POST', body)
  },
  updateReminder(reminderId: string, body: Partial<Pick<Reminder, 'title' | 'medicine' | 'dose' | 'time' | 'enabled'>>): Promise<Reminder> {
    return json(`/reminders/${encodeURIComponent(reminderId)}`, 'PATCH', body)
  },
  deleteReminder(reminderId: string): Promise<{ ok: true }> {
    return json(`/reminders/${encodeURIComponent(reminderId)}`, 'DELETE')
  },
  broadcasts(elderId: string): Promise<Broadcast[]> {
    return request(`/elders/${encodeURIComponent(elderId)}/broadcasts`)
  },
  playedBroadcast(broadcastId: string): Promise<Broadcast> {
    return json(`/broadcasts/${encodeURIComponent(broadcastId)}/played`, 'POST')
  },
  observations(elderId: string): Promise<Observation[]> {
    return request(`/elders/${encodeURIComponent(elderId)}/observations`)
  },
  createObservation(elderId: string, body: Record<string, unknown>): Promise<{ observation: Observation; events: EventItem[] }> {
    return json(`/elders/${encodeURIComponent(elderId)}/observations`, 'POST', body)
  },
  events(params: { elder_id?: string; status?: string } = {}): Promise<EventItem[]> {
    const search = new URLSearchParams()
    if (params.elder_id) search.set('elder_id', params.elder_id)
    if (params.status) search.set('status', params.status)
    const suffix = search.toString() ? `?${search.toString()}` : ''
    return request(`/events${suffix}`)
  },
  event(eventId: string): Promise<EventDetail> {
    return request(`/events/${encodeURIComponent(eventId)}`)
  },
  eventAction(eventId: string, action: string, note?: string): Promise<EventDetail> {
    return json(`/events/${encodeURIComponent(eventId)}/actions`, 'POST', note ? { action, note } : { action })
  },
  notifications(): Promise<Notification[]> {
    return request('/notifications')
  },
  ackNotification(notificationId: string): Promise<Notification> {
    return json(`/notifications/${encodeURIComponent(notificationId)}/ack`, 'POST')
  },
  retryNotification(notificationId: string): Promise<Notification> {
    return json(`/notifications/${encodeURIComponent(notificationId)}/retry`, 'POST')
  },
  configureNotificationFailures(failTargets: string[]): Promise<{ fail_targets: string[] }> {
    return json('/demo/notifications', 'PATCH', { fail_targets: failTargets })
  },
  escorts(): Promise<Escort[]> {
    return request('/escorts')
  },
  escortAction(escortId: string, action: 'accept' | 'complete', note?: string): Promise<Escort> {
    return json(`/escorts/${encodeURIComponent(escortId)}/actions`, 'POST', note ? { action, note } : { action })
  },
  assistant(elderId: string, text: string, dialect?: string, confirmToken?: string): Promise<AssistantResponse> {
    return json(`/elders/${encodeURIComponent(elderId)}/assistant`, 'POST', {
      text,
      ...(dialect ? { dialect } : {}),
      ...(confirmToken ? { confirm_token: confirmToken } : {}),
    })
  },
  speech(elderId: string, dialect: string, pcm: Blob): Promise<{ text: string; dialect: string; provider: string }> {
    return request(`/elders/${encodeURIComponent(elderId)}/speech?dialect=${encodeURIComponent(dialect)}`, {
      method: 'POST',
      body: pcm,
      headers: { 'Content-Type': 'audio/L16' },
    })
  },
  calls(elderId: string): Promise<Call[]> {
    return request(`/elders/${encodeURIComponent(elderId)}/calls`)
  },
  call(callId: string, authToken?: string): Promise<Call> {
    return request(`/calls/${encodeURIComponent(callId)}`, {}, authToken)
  },
  createCall(elderId: string): Promise<Call> {
    return json(`/elders/${encodeURIComponent(elderId)}/calls`, 'POST')
  },
  callAction(callId: string, action: 'answer' | 'end' | 'decline', authToken?: string): Promise<Call> {
    return json(`/calls/${encodeURIComponent(callId)}/actions`, 'POST', { action }, authToken, action === 'end')
  },
  async uploadSnapshot(elderId: string, blob: Blob): Promise<void> {
    await request(`/elders/${encodeURIComponent(elderId)}/snapshot`, {
      method: 'POST',
      body: blob,
      headers: { 'Content-Type': 'image/jpeg' },
    })
  },
  snapshot(elderId: string): Promise<Blob> {
    return request(`/elders/${encodeURIComponent(elderId)}/snapshot`, { headers: { Accept: 'image/jpeg' } })
  },
}

function websocketOrigin(): string {
  // API_BASE 可能是相对路径（同源部署）或绝对地址（独立 API 域名）。
  // 后者必须按 API 地址推导，否则信令会连到前端页面所在的域名。
  if (/^https?:\/\//i.test(API_BASE)) {
    return API_BASE.replace(/^http/i, 'ws').replace(/\/api$/, '')
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}`
}

export function websocketUrl(callId: string, token: string): string {
  return `${websocketOrigin()}/ws/calls/${encodeURIComponent(callId)}?token=${encodeURIComponent(token)}`
}
