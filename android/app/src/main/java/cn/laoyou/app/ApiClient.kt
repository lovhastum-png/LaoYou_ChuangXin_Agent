package cn.laoyou.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

data class User(
    val id: String,
    val username: String,
    val displayName: String,
    val role: String
)

data class LoginResult(val token: String, val user: User)

data class Routine(
    val wakeTime: String = "07:00",
    val lunchTime: String = "12:00",
    val dinnerTime: String = "18:00",
    val sleepTime: String = "22:00"
)

data class ElderRules(
    val nightStart: String = "22:00",
    val nightEnd: String = "06:00",
    val immobilityMinutes: Int = 60,
    val sleepImmobilityMinutes: Int = 180,
    val awayMinutes: Int = 120,
    val heartRateLow: Int = 50,
    val heartRateHigh: Int = 120,
    val systolicHigh: Int = 160,
    val diastolicHigh: Int = 100
)

data class Elder(
    val id: String,
    val name: String,
    val city: String,
    val cameraEnabled: Boolean,
    val voiceEnabled: Boolean,
    val dialect: String,
    val routine: Routine = Routine(),
    val rules: ElderRules = ElderRules()
)

data class Weather(
    val city: String,
    val temperature: String,
    val description: String,
    val advice: String,
    val observedAt: String,
    val source: String
)

data class Reminder(
    val id: String,
    val elderId: String,
    val title: String,
    val medicine: String,
    val dose: String,
    val time: String,
    val enabled: Boolean,
    val createdAt: String
)

data class Broadcast(
    val id: String,
    val elderId: String,
    val kind: String,
    val text: String,
    val scheduledAt: String,
    val playedAt: String?,
    val source: String
)

data class Observation(
    val id: String,
    val elderId: String,
    val kind: String,
    val value: String?,
    val durationMinutes: Int?,
    val sleeping: Boolean?,
    val occurredAt: String,
    val location: String?,
    val source: String
)

data class Dashboard(
    val elder: Elder,
    val weather: Weather?,
    val reminders: List<Reminder>,
    val broadcasts: List<Broadcast>,
    val latestObservation: Observation?,
    val activeEventCount: Int
)

data class Event(
    val id: String,
    val elderId: String,
    val elderName: String,
    val kind: String,
    val title: String,
    val severity: String,
    val status: String,
    val source: String,
    val description: String,
    val createdAt: String,
    val updatedAt: String
)

data class TimelineItem(
    val id: String,
    val node: String,
    val at: String,
    val actor: String,
    val detail: String
)

data class NotificationItem(
    val id: String,
    val eventId: String,
    val target: String,
    val status: String,
    val createdAt: String,
    val sentAt: String?,
    val acknowledgedAt: String?,
    val attempts: Int,
    val lastError: String?,
    val simulated: Boolean
)

data class Escort(
    val id: String,
    val eventId: String,
    val elderId: String,
    val platform: String,
    val status: String,
    val requestedAt: String,
    val acceptedAt: String?,
    val completedAt: String?,
    val note: String?,
    val simulated: Boolean
)

data class EventDetail(
    val event: Event,
    val timeline: List<TimelineItem>,
    val notifications: List<NotificationItem>,
    val escort: Escort?
)

data class CallModel(
    val id: String,
    val elderId: String,
    val createdBy: String,
    val status: String,
    val createdAt: String,
    val answeredAt: String?,
    val endedAt: String?
)

data class CapabilityDialect(val id: String, val label: String, val available: Boolean, val note: String)
data class Capabilities(
    val assistantMode: String,
    val speechProvider: String,
    val speechConfigured: Boolean,
    val dialects: List<CapabilityDialect>,
    val videoMode: String,
    val integrations: Map<String, String>
)

data class AssistantResult(
    val reply: String,
    val mode: String,
    val action: String,
    val proposal: String?,
    val confirmToken: String?
)

class ApiException(val status: Int, message: String) : Exception(message)

private fun JSONObject.stringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) optString(key).takeUnless { it == "null" } else null

private fun JSONObject.objOrNull(key: String): JSONObject? =
    if (has(key) && !isNull(key)) optJSONObject(key) else null

private fun jsonArray(value: String): JSONArray = JSONArray(value)

private fun parseUser(json: JSONObject): User = User(
    id = json.optString("id"),
    username = json.optString("username"),
    displayName = json.optString("display_name", json.optString("username")),
    role = json.optString("role")
)

private fun parseRoutine(json: JSONObject?): Routine = Routine(
    wakeTime = json?.optString("wake_time", "07:00") ?: "07:00",
    lunchTime = json?.optString("lunch_time", "12:00") ?: "12:00",
    dinnerTime = json?.optString("dinner_time", "18:00") ?: "18:00",
    sleepTime = json?.optString("sleep_time", "22:00") ?: "22:00"
)

private fun parseRules(json: JSONObject?): ElderRules = ElderRules(
    nightStart = json?.optString("night_start", "22:00") ?: "22:00",
    nightEnd = json?.optString("night_end", "06:00") ?: "06:00",
    immobilityMinutes = json?.optInt("immobility_minutes", 60) ?: 60,
    sleepImmobilityMinutes = json?.optInt("sleep_immobility_minutes", 180) ?: 180,
    awayMinutes = json?.optInt("away_minutes", 120) ?: 120,
    heartRateLow = json?.optInt("heart_rate_low", 50) ?: 50,
    heartRateHigh = json?.optInt("heart_rate_high", 120) ?: 120,
    systolicHigh = json?.optInt("systolic_high", 160) ?: 160,
    diastolicHigh = json?.optInt("diastolic_high", 100) ?: 100
)

private fun parseElder(json: JSONObject): Elder = Elder(
    id = json.optString("id"),
    name = json.optString("name"),
    city = json.optString("city"),
    cameraEnabled = json.optBoolean("camera_enabled", false),
    voiceEnabled = json.optBoolean("voice_enabled", true),
    dialect = json.optString("dialect", "zh-CN"),
    routine = parseRoutine(json.objOrNull("routine")),
    rules = parseRules(json.objOrNull("rules"))
)

private fun parseWeather(json: JSONObject?): Weather? = json?.let {
    Weather(
        city = it.optString("city"),
        temperature = if (it.isNull("temperature")) "—" else it.optString("temperature"),
        description = it.optString("description"),
        advice = it.optString("advice"),
        observedAt = it.optString("observed_at"),
        source = it.optString("source", "simulated")
    )
}

private fun parseReminder(json: JSONObject): Reminder = Reminder(
    id = json.optString("id"),
    elderId = json.optString("elder_id"),
    title = json.optString("title"),
    medicine = json.optString("medicine"),
    dose = json.optString("dose"),
    time = json.optString("time"),
    enabled = json.optBoolean("enabled", true),
    createdAt = json.optString("created_at")
)

private fun parseBroadcast(json: JSONObject): Broadcast = Broadcast(
    id = json.optString("id"),
    elderId = json.optString("elder_id"),
    kind = json.optString("kind"),
    text = json.optString("text"),
    scheduledAt = json.optString("scheduled_at"),
    playedAt = json.stringOrNull("played_at"),
    source = json.optString("source", "backend")
)

private fun parseObservation(json: JSONObject): Observation {
    val value = when (val raw = json.opt("value")) {
        null, JSONObject.NULL -> null
        is JSONObject -> raw.toString()
        else -> raw.toString()
    }
    val location = json.objOrNull("location")?.let {
        it.stringOrNull("label") ?: "${it.optDouble("latitude")}, ${it.optDouble("longitude")}"
    }
    return Observation(
        id = json.optString("id"),
        elderId = json.optString("elder_id"),
        kind = json.optString("kind"),
        value = value,
        durationMinutes = if (json.has("duration_minutes") && !json.isNull("duration_minutes")) json.optInt("duration_minutes") else null,
        sleeping = if (json.has("sleeping") && !json.isNull("sleeping")) json.optBoolean("sleeping") else null,
        occurredAt = json.optString("occurred_at"),
        location = location,
        source = json.optString("source", "simulated")
    )
}

private fun parseEvent(json: JSONObject): Event = Event(
    id = json.optString("id"),
    elderId = json.optString("elder_id"),
    elderName = json.optString("elder_name"),
    kind = json.optString("kind"),
    title = json.optString("title"),
    severity = json.optString("severity"),
    status = json.optString("status"),
    source = json.optString("source", "simulated"),
    description = json.optString("description"),
    createdAt = json.optString("created_at"),
    updatedAt = json.optString("updated_at")
)

private fun parseTimeline(json: JSONObject): TimelineItem = TimelineItem(
    id = json.optString("id"),
    node = json.optString("node"),
    at = json.optString("at"),
    actor = json.optString("actor"),
    detail = json.optString("detail")
)

private fun parseNotification(json: JSONObject): NotificationItem = NotificationItem(
    id = json.optString("id"),
    eventId = json.optString("event_id"),
    target = json.optString("target"),
    status = json.optString("status"),
    createdAt = json.optString("created_at"),
    sentAt = json.stringOrNull("sent_at"),
    acknowledgedAt = json.stringOrNull("acknowledged_at"),
    attempts = json.optInt("attempts", 0),
    lastError = json.stringOrNull("last_error"),
    simulated = json.optBoolean("simulated", false)
)

private fun parseEscort(json: JSONObject?): Escort? = json?.let {
    Escort(
        id = it.optString("id"),
        eventId = it.optString("event_id"),
        elderId = it.optString("elder_id"),
        platform = it.optString("platform", "放心医"),
        status = it.optString("status"),
        requestedAt = it.optString("requested_at"),
        acceptedAt = it.stringOrNull("accepted_at"),
        completedAt = it.stringOrNull("completed_at"),
        note = it.stringOrNull("note"),
        simulated = it.optBoolean("simulated", true)
    )
}

private fun parseCall(json: JSONObject): CallModel = CallModel(
    id = json.optString("id"),
    elderId = json.optString("elder_id"),
    createdBy = json.optString("created_by"),
    status = json.optString("status"),
    createdAt = json.optString("created_at"),
    answeredAt = json.stringOrNull("answered_at"),
    endedAt = json.stringOrNull("ended_at")
)

class ApiClient(baseUrl: String, var token: String? = null) {
    private val base: String = baseUrl.trim().removeSuffix("/")

    private fun url(path: String): URL = URL(base + if (path.startsWith("/")) path else "/$path")

    private fun read(connection: HttpURLConnection): ByteArray {
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return (stream ?: ByteArrayInputStream(byteArrayOf())).use { input ->
            val bytes = ByteArrayOutputStream()
            BufferedInputStream(input).use { it.copyTo(bytes) }
            bytes.toByteArray()
        }
    }

    private fun requestRaw(method: String, path: String, body: ByteArray? = null, contentType: String = "application/json"): Pair<Int, ByteArray> {
        val connection = (url(path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 10_000
            readTimeout = 15_000
            useCaches = false
            setRequestProperty("Accept", "application/json")
            token?.takeIf { it.isNotBlank() }?.let { setRequestProperty("Authorization", "Bearer $it") }
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", contentType)
                setFixedLengthStreamingMode(body.size)
            }
        }
        try {
            if (body != null) connection.outputStream.use { it.write(body) }
            val status = connection.responseCode
            val bytes = read(connection)
            if (status !in 200..299) {
                val detail = runCatching { JSONObject(String(bytes, Charsets.UTF_8)).optString("detail") }.getOrNull()
                    ?.takeIf { it.isNotBlank() } ?: "请求失败（HTTP $status）"
                throw ApiException(status, detail)
            }
            return status to bytes
        } finally {
            connection.disconnect()
        }
    }

    private fun requestJson(method: String, path: String, body: JSONObject? = null): JSONObject {
        val (_, bytes) = requestRaw(method, path, body?.toString()?.toByteArray(Charsets.UTF_8))
        return if (bytes.isEmpty()) JSONObject() else JSONObject(String(bytes, Charsets.UTF_8))
    }

    private fun requestJsonArray(method: String, path: String, body: JSONObject? = null): JSONArray {
        val (_, bytes) = requestRaw(method, path, body?.toString()?.toByteArray(Charsets.UTF_8))
        return JSONArray(String(bytes, Charsets.UTF_8))
    }

    fun login(username: String, password: String): LoginResult {
        val response = requestJson("POST", "/auth/login", JSONObject().put("username", username).put("password", password))
        token = response.optString("token")
        return LoginResult(token.orEmpty(), parseUser(response.getJSONObject("user")))
    }

    fun logout() { requestJson("POST", "/auth/logout") }

    fun me(): User = parseUser(requestJson("GET", "/auth/me"))

    fun getElders(): List<Elder> = buildList {
        val array = requestJsonArray("GET", "/elders")
        for (i in 0 until array.length()) add(parseElder(array.getJSONObject(i)))
    }

    fun getDashboard(elderId: String): Dashboard {
        val json = requestJson("GET", "/elders/$elderId/dashboard")
        val reminders = buildList {
            val array = json.optJSONArray("reminders") ?: JSONArray()
            for (i in 0 until array.length()) add(parseReminder(array.getJSONObject(i)))
        }
        val broadcasts = buildList {
            val array = json.optJSONArray("broadcasts") ?: JSONArray()
            for (i in 0 until array.length()) add(parseBroadcast(array.getJSONObject(i)))
        }
        return Dashboard(
            elder = parseElder(json.getJSONObject("elder")),
            weather = parseWeather(json.objOrNull("weather")),
            reminders = reminders,
            broadcasts = broadcasts,
            latestObservation = json.objOrNull("latest_observation")?.let(::parseObservation),
            activeEventCount = json.optInt("active_event_count", 0)
        )
    }

    fun getObservations(elderId: String): List<Observation> = buildList {
        val array = requestJsonArray("GET", "/elders/$elderId/observations")
        for (i in 0 until array.length()) add(parseObservation(array.getJSONObject(i)))
    }

    fun getEvents(elderId: String? = null, status: String? = null): List<Event> = buildList {
        val query = mutableListOf<String>()
        elderId?.let { query += "elder_id=${enc(it)}" }
        status?.let { query += "status=${enc(it)}" }
        val path = "/events" + if (query.isEmpty()) "" else "?${query.joinToString("&")}"
        val array = requestJsonArray("GET", path)
        for (i in 0 until array.length()) add(parseEvent(array.getJSONObject(i)))
    }

    fun getEvent(eventId: String): EventDetail {
        val json = requestJson("GET", "/events/$eventId")
        val timeline = buildList {
            val array = json.optJSONArray("timeline") ?: JSONArray()
            for (i in 0 until array.length()) add(parseTimeline(array.getJSONObject(i)))
        }
        val notifications = buildList {
            val array = json.optJSONArray("notifications") ?: JSONArray()
            for (i in 0 until array.length()) add(parseNotification(array.getJSONObject(i)))
        }
        return EventDetail(parseEvent(json), timeline, notifications, parseEscort(json.objOrNull("escort")))
    }

    fun eventAction(eventId: String, action: String, note: String? = null): EventDetail {
        val body = JSONObject().put("action", action)
        note?.takeIf { it.isNotBlank() }?.let { body.put("note", it) }
        val json = requestJson("POST", "/events/$eventId/actions", body)
        val timeline = buildList {
            val array = json.optJSONArray("timeline") ?: JSONArray()
            for (i in 0 until array.length()) add(parseTimeline(array.getJSONObject(i)))
        }
        val notifications = buildList {
            val array = json.optJSONArray("notifications") ?: JSONArray()
            for (i in 0 until array.length()) add(parseNotification(array.getJSONObject(i)))
        }
        return EventDetail(parseEvent(json), timeline, notifications, parseEscort(json.objOrNull("escort")))
    }

    fun getNotifications(): List<NotificationItem> = buildList {
        val array = requestJsonArray("GET", "/notifications")
        for (i in 0 until array.length()) add(parseNotification(array.getJSONObject(i)))
    }

    fun notificationAck(id: String): NotificationItem = parseNotification(requestJson("POST", "/notifications/$id/ack"))

    fun notificationRetry(id: String): NotificationItem = parseNotification(requestJson("POST", "/notifications/$id/retry"))

    fun getEscorts(): List<Escort> = buildList {
        val array = requestJsonArray("GET", "/escorts")
        // 服务端返回异常条目时跳过，不要用 !! 直接崩掉整个列表。
        for (i in 0 until array.length()) parseEscort(array.getJSONObject(i))?.let { add(it) }
    }

    fun escortAction(id: String, action: String, note: String? = null): Escort {
        val body = JSONObject().put("action", action)
        note?.takeIf { it.isNotBlank() }?.let { body.put("note", it) }
        return parseEscort(requestJson("POST", "/escorts/$id/actions", body))
            ?: throw ApiException(0, "服务返回的陪诊数据不完整，请重试。")
    }

    fun getReminders(elderId: String): List<Reminder> = buildList {
        val array = requestJsonArray("GET", "/elders/$elderId/reminders")
        for (i in 0 until array.length()) add(parseReminder(array.getJSONObject(i)))
    }

    fun createReminder(elderId: String, title: String, medicine: String, dose: String, time: String, enabled: Boolean): Reminder {
        val body = JSONObject()
            .put("title", title)
            .put("medicine", medicine)
            .put("dose", dose)
            .put("time", time)
            .put("enabled", enabled)
        return parseReminder(requestJson("POST", "/elders/$elderId/reminders", body))
    }

    fun updateReminder(id: String, title: String, medicine: String, dose: String, time: String, enabled: Boolean): Reminder {
        val body = JSONObject()
            .put("title", title)
            .put("medicine", medicine)
            .put("dose", dose)
            .put("time", time)
            .put("enabled", enabled)
        return parseReminder(requestJson("PATCH", "/reminders/$id", body))
    }

    fun deleteReminder(id: String) { requestJson("DELETE", "/reminders/$id") }

    fun updateElderSettings(elderId: String, cameraEnabled: Boolean? = null, voiceEnabled: Boolean? = null, dialect: String? = null, city: String? = null, confirmCameraOff: Boolean? = null): Elder {
        val body = JSONObject()
        cameraEnabled?.let { body.put("camera_enabled", it) }
        voiceEnabled?.let { body.put("voice_enabled", it) }
        dialect?.let { body.put("dialect", it) }
        city?.let { body.put("city", it) }
        confirmCameraOff?.let { body.put("confirm_camera_off", it) }
        return parseElder(requestJson("PATCH", "/elders/$elderId/settings", body))
    }

    fun getCapabilities(): Capabilities {
        val json = requestJson("GET", "/capabilities")
        val speech = json.objOrNull("speech")
        val dialects = buildList {
            val array = speech?.optJSONArray("dialects") ?: JSONArray()
            for (i in 0 until array.length()) {
                val d = array.getJSONObject(i)
                add(CapabilityDialect(d.optString("id"), d.optString("label"), d.optBoolean("available"), d.optString("note")))
            }
        }
        val integrations = buildMap {
            val obj = json.objOrNull("integrations")
            obj?.keys()?.forEach { key -> put(key, obj.opt(key)?.toString().orEmpty()) }
        }
        return Capabilities(
            assistantMode = json.optString("assistant_mode"),
            speechProvider = speech?.optString("provider").orEmpty(),
            speechConfigured = speech?.optBoolean("configured", false) ?: false,
            dialects = dialects,
            videoMode = json.objOrNull("video")?.optString("mode").orEmpty(),
            integrations = integrations
        )
    }

    fun assistant(elderId: String, text: String, dialect: String? = null, confirmToken: String? = null): AssistantResult {
        val body = JSONObject().put("text", text)
        dialect?.let { body.put("dialect", it) }
        confirmToken?.let { body.put("confirm_token", it) }
        val json = requestJson("POST", "/elders/$elderId/assistant", body)
        return AssistantResult(
            reply = json.optString("reply"),
            mode = json.optString("mode"),
            action = json.optString("action"),
            proposal = json.opt("proposal")?.takeUnless { it == JSONObject.NULL }?.toString(),
            confirmToken = json.stringOrNull("confirm_token")
        )
    }

    fun getCalls(elderId: String): List<CallModel> = buildList {
        val array = requestJsonArray("GET", "/elders/$elderId/calls")
        for (i in 0 until array.length()) add(parseCall(array.getJSONObject(i)))
    }

    fun createCall(elderId: String): CallModel = parseCall(requestJson("POST", "/elders/$elderId/calls"))

    fun callAction(id: String, action: String): CallModel = parseCall(requestJson("POST", "/calls/$id/actions", JSONObject().put("action", action)))

    fun loadSnapshot(elderId: String): Bitmap {
        val (_, bytes) = requestRaw("GET", "/elders/$elderId/snapshot")
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: error("无法读取摄像头快照")
    }

    fun callWebUrl(callId: String): String {
        val root = base.replace(Regex("/api/?$"), "")
        return "$root/call/$callId?token=${enc(token.orEmpty())}&embedded=1"
    }

    /** Backend origin used by the loopback bridge for embedded WebView calls. */
    fun callBackendUrl(): String = base.replace(Regex("/api/?$"), "")

    private fun enc(value: String): String = URLEncoder.encode(value, Charsets.UTF_8.name())
}

fun formatTime(value: String): String {
    if (value.isBlank()) return value
    return runCatching {
        OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.of("Asia/Shanghai"))
            .format(DateTimeFormatter.ofPattern("MM-dd HH:mm"))
    }.getOrElse { value.replace('T', ' ').replace(Regex("[+]\\d{2}:\\d{2}$"), "") }
}

fun roleLabel(role: String): String = when (role) {
    "child" -> "子女"
    "community" -> "社区"
    "admin" -> "演示管理"
    "elder" -> "老人"
    else -> role
}

fun eventStatusLabel(status: String): String = when (status) {
    "alerted" -> "待确认"
    "acknowledged" -> "已确认"
    "handling" -> "处理中"
    "resolved" -> "已完成"
    "false_positive" -> "已修正误报"
    else -> status
}

fun eventSeverityLabel(severity: String): String = if (severity == "critical") "紧急" else "提醒"

fun notificationTargetLabel(target: String): String = when (target) {
    "child" -> "子女"
    "community" -> "社区"
    "emergency" -> "模拟120"
    else -> target
}
