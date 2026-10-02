package cn.laoyou.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.ViewGroup
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.Locale

private enum class AppTab { HOME, EVENTS, REMINDERS, CALLS, ESCORTS, SETTINGS }

/**
 * 把异常翻译成老人看得懂的中文。
 *
 * 直接用 e.message 会把 "Failed to connect to /192.168.1.100:18080" 这类英文原文
 * 显示在界面上；后端返回的 ApiException.detail 本来就是中文，原样透传。
 */
private fun friendlyMessage(e: Exception): String {
    return when (e) {
        is ApiException -> e.message ?: "操作没有成功，请重试。"
        is UnknownHostException -> "找不到这台电脑，请检查服务地址是否填写正确。"
        is ConnectException -> "连接不上电脑，请确认电脑已启动老友。同一个网络填电脑的局域网地址；不同网络请填 Cloudflare 公网地址。"
        is SocketTimeoutException -> "网络有点慢，请稍后重试。"
        else -> "操作没有成功，请检查网络后重试。"
    }
}

private val LaoyouColors = lightColorScheme(
    primary = Color(0xFF165198),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE8F1FB),
    onPrimaryContainer = Color(0xFF0F3D73),
    secondary = Color(0xFF37485C),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE3EDFA),
    onSecondaryContainer = Color(0xFF37485C),
    tertiary = Color(0xFF37485C),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFE3EDFA),
    onTertiaryContainer = Color(0xFF0F3D73),
    background = Color(0xFFF7F9FC),
    surface = Color.White,
    surfaceVariant = Color(0xFFDEE4EC),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFF3F6FA),
    surfaceContainer = Color(0xFFEDF1F7),
    surfaceContainerHigh = Color(0xFFE7ECF3),
    surfaceContainerHighest = Color(0xFFDFE5EC),
    onSurfaceVariant = Color(0xFF37485C),
    outline = Color(0xFF6980A2),
    outlineVariant = Color(0xFF7D95B8),
    error = Color(0xFF922E1E)
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createNotificationChannel(this)
        setContent { LaoyouApp() }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LaoyouApp() {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("laoyou", Context.MODE_PRIVATE) }
    val scope = rememberCoroutineScope()
    // Do not prefill the emulator-only address on a real phone.  The packaged
    // desktop service is reached through the computer's LAN address (usually
    // port 18080), while instrumentation still supplies its own test URL.
    // 打包时若在 android/local.properties 配了 laoyou.baseUrl，就用它预填，
    // 装好打开即可直接登录；用户改过之后以本地保存的地址为准（本地优先）。
    var baseUrl by rememberSaveable {
        mutableStateOf(
            prefs.getString("base_url", "").orEmpty().ifBlank { BuildConfig.DEFAULT_BASE_URL }
        )
    }
    var username by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var api by remember { mutableStateOf<ApiClient?>(null) }
    var user by remember { mutableStateOf<User?>(null) }
    var elders by remember { mutableStateOf<List<Elder>>(emptyList()) }
    var selectedElder by remember { mutableStateOf<Elder?>(null) }
    var dashboard by remember { mutableStateOf<Dashboard?>(null) }
    var observations by remember { mutableStateOf<List<Observation>>(emptyList()) }
    var events by remember { mutableStateOf<List<Event>>(emptyList()) }
    var selectedEvent by remember { mutableStateOf<EventDetail?>(null) }
    var reminders by remember { mutableStateOf<List<Reminder>>(emptyList()) }
    var escorts by remember { mutableStateOf<List<Escort>>(emptyList()) }
    var calls by remember { mutableStateOf<List<CallModel>>(emptyList()) }
    var capabilities by remember { mutableStateOf<Capabilities?>(null) }
    var currentTab by rememberSaveable { mutableStateOf(AppTab.HOME.name) }
    var activeCall by remember { mutableStateOf<CallModel?>(null) }
    var snapshot by remember { mutableStateOf<Bitmap?>(null) }
    var snapshotAt by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var snapshotLoading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var loginMessage by remember { mutableStateOf<String?>(null) }
    var assistantResult by remember { mutableStateOf<AssistantResult?>(null) }
    var incomingCall by remember { mutableStateOf<CallModel?>(null) }
    var notifiedIncomingCallIds by remember { mutableStateOf<Set<String>>(emptySet()) }
    var seenNotificationIds by remember { mutableStateOf<Set<String>>(emptySet()) }
    var notificationPollingStarted by remember { mutableStateOf(false) }
    val notificationPermissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }

    val tts = remember {
        TextToSpeech(context) {}.also { it.language = Locale.CHINA }
    }
    DisposableEffect(Unit) { onDispose { tts.shutdown() } }

    LaunchedEffect(user?.id) {
        if (user != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    fun <T> perform(action: () -> T, onFailure: (Exception) -> Unit = {}, onSuccess: (T) -> Unit = {}) {
        scope.launch {
            loading = true
            error = null
            try {
                val result = withContext(Dispatchers.IO) { action() }
                onSuccess(result)
            } catch (e: Exception) {
                error = friendlyMessage(e)
                onFailure(e)
            } finally {
                loading = false
            }
        }
    }

    fun refreshDashboard() {
        val client = api ?: return
        val elder = selectedElder ?: return
        perform({
            val result = client.getDashboard(elder.id)
            val obs = client.getObservations(elder.id)
            result to obs
        }) { (result, obs) ->
            dashboard = result
            observations = obs
            selectedElder = result.elder
            reminders = result.reminders
            snapshot = null
            snapshotAt = null
        }
    }

    fun refreshEvents() {
        val client = api ?: return
        perform({ client.getEvents(selectedElder?.id) }) { events = it }
    }

    fun refreshReminders() {
        val client = api ?: return
        selectedElder?.let { elder -> perform({ client.getReminders(elder.id) }) { reminders = it } }
    }

    fun refreshCalls() {
        val client = api ?: return
        selectedElder?.let { elder -> perform({ client.getCalls(elder.id) }) { calls = it } }
    }

    fun endActiveCallFromApp() {
        val client = api
        val callToEnd = activeCall
        val elderToRefresh = selectedElder
        // Drop the WebView first so local media tracks are released immediately.
        activeCall = null
        if (client == null || callToEnd == null || elderToRefresh == null) return
        scope.launch {
            loading = true
            error = null
            var syncError: String? = null
            if (callToEnd.status in setOf("ringing", "active")) {
                try {
                    withContext(Dispatchers.IO) { client.callAction(callToEnd.id, "end") }
                } catch (e: Exception) {
                    syncError = e.message ?: "无法同步挂断状态"
                }
            }
            try {
                calls = withContext(Dispatchers.IO) { client.getCalls(elderToRefresh.id) }
            } catch (e: Exception) {
                syncError = syncError ?: (e.message ?: "无法刷新通话记录")
            }
            error = syncError?.let { "通话状态同步失败：$it" }
            loading = false
        }
    }

    fun refreshEscorts() {
        val client = api ?: return
        perform({ client.getEscorts() }) { escorts = it }
    }

    LaunchedEffect(api?.token) {
        val client = api ?: return@LaunchedEffect
        while (true) {
            val result = runCatching { withContext(Dispatchers.IO) { client.getNotifications() } }.getOrNull()
            if (result != null) {
                if (!notificationPollingStarted) {
                    seenNotificationIds = result.map { it.id }.toSet()
                    notificationPollingStarted = true
                } else {
                    val newItems = result.filter { it.id !in seenNotificationIds && it.status in setOf("pending", "sent") }
                    newItems.forEach { postLocalNotification(context, it) }
                    seenNotificationIds = seenNotificationIds + result.map { it.id }
                }
            }
            delay(30_000)
        }
    }

    LaunchedEffect(api?.token, selectedElder?.id, user?.id, activeCall?.id, incomingCall?.id) {
        val client = api ?: return@LaunchedEffect
        val currentUser = user ?: return@LaunchedEffect
        if (currentUser.role !in setOf("child", "admin", "elder")) return@LaunchedEffect
        while (true) {
            val elderForPolling = selectedElder
            if (elderForPolling != null) {
                val latestCalls = runCatching { withContext(Dispatchers.IO) { client.getCalls(elderForPolling.id) } }.getOrNull()
                if (latestCalls != null) {
                    calls = latestCalls
                    val ringingFromFamily = latestCalls.firstOrNull {
                        it.status == "ringing" && it.createdBy != currentUser.id
                    }
                    if (ringingFromFamily != null && ringingFromFamily.id !in notifiedIncomingCallIds) {
                        notifiedIncomingCallIds = notifiedIncomingCallIds + ringingFromFamily.id
                        if (activeCall == null && incomingCall == null) incomingCall = ringingFromFamily
                    }
                }
            }
            delay(5_000)
        }
    }

    LaunchedEffect(selectedElder?.id, api?.token) {
        if (api != null && selectedElder != null) refreshDashboard()
    }

    MaterialTheme(colorScheme = LaoyouColors, typography = MaterialTheme.typography.copy(bodyLarge = MaterialTheme.typography.bodyLarge.copy(fontSize = 18.sp))) {
        when {
            user == null -> LoginScreen(
                baseUrl = baseUrl,
                onBaseUrlChange = { baseUrl = it },
                username = username,
                onUsernameChange = { username = it },
                password = password,
                onPasswordChange = { password = it },
                loading = loading,
                error = error ?: loginMessage,
                onLogin = {
                    val normalized = normalizeBaseUrl(baseUrl)
                    if (baseUrl.isBlank()) {
                        loginMessage = "请先填写服务地址，例如 http://192.168.1.100:18080"
                    } else if (username.isBlank() || password.isBlank()) {
                        loginMessage = "请输入账号和密码"
                    } else {
                        loginMessage = null
                        perform({
                            val client = ApiClient(normalized)
                            val login = client.login(username.trim(), password)
                            val accessibleElders = client.getElders()
                            Triple(client, login, accessibleElders)
                        }) { (client, login, accessibleElders) ->
                            api = client
                            user = login.user
                            elders = accessibleElders
                            selectedElder = accessibleElders.singleOrNull()
                            currentTab = AppTab.HOME.name
                            prefs.edit().putString("base_url", normalized).apply()
                        }
                    }
                }
            )
            selectedElder == null -> ElderPickerScreen(
                user = user!!,
                elders = elders,
                loading = loading,
                error = error,
                onSelect = { selectedElder = it; incomingCall = null },
                onLogout = {
                    api?.let { client -> perform({ client.logout() }) }
                    api = null
                    user = null
                    selectedElder = null
                    elders = emptyList()
                    notificationPollingStarted = false
                    seenNotificationIds = emptySet()
                    incomingCall = null
                    notifiedIncomingCallIds = emptySet()
                }
            )
            activeCall != null -> CallWebViewScreen(
                call = activeCall!!,
                callUrl = api!!.callWebUrl(activeCall!!.id),
                backendUrl = api!!.callBackendUrl(),
                onBack = ::endActiveCallFromApp,
                onAction = { action ->
                    val client = api
                    val callToAction = activeCall
                    if (client != null && callToAction != null) {
                        perform({ client.callAction(callToAction.id, action) }) {
                            if (it.status == "ended" || it.status == "declined") {
                                activeCall = null
                                refreshCalls()
                            } else {
                                activeCall = it
                            }
                        }
                    }
                }
            )
            else -> AppShell(
                user = user!!,
                elder = selectedElder!!,
                dashboard = dashboard,
                observations = observations,
                events = events,
                selectedEvent = selectedEvent,
                reminders = reminders,
                escorts = escorts,
                calls = calls,
                incomingCall = incomingCall,
                capabilities = capabilities,
                currentTab = AppTab.valueOf(currentTab),
                loading = loading,
                snapshot = snapshot,
                snapshotAt = snapshotAt,
                snapshotLoading = snapshotLoading,
                assistantResult = assistantResult,
                error = error,
                onTab = { currentTab = it.name; selectedEvent = null },
                onSwitchElder = {
                    selectedElder = null
                    incomingCall = null
                    currentTab = AppTab.HOME.name
                },
                onRefreshDashboard = ::refreshDashboard,
                onLoadEvents = { refreshEvents() },
                onLoadEvent = { id ->
                    api?.let { client -> perform({ client.getEvent(id) }) { selectedEvent = it } }
                },
                onEventAction = { id, action, note ->
                    api?.let { client -> perform({ client.eventAction(id, action, note) }) {
                        selectedEvent = it
                        refreshEvents()
                    } }
                },
                onAckNotification = { id ->
                    api?.let { client -> perform({ client.notificationAck(id) }) { acked -> selectedEvent = selectedEvent?.let { current -> current.copy(notifications = current.notifications.map { n -> if (n.id == id) acked else n }) } } }
                },
                onRetryNotification = { id ->
                    api?.let { client -> perform({ client.notificationRetry(id) }) { retried -> selectedEvent = selectedEvent?.let { current -> current.copy(notifications = current.notifications.map { n -> if (n.id == id) retried else n }) } } }
                },
                onLoadReminders = ::refreshReminders,
                onCreateReminder = { title, medicine, dose, time, enabled ->
                    val elder = selectedElder!!
                    api?.let { client -> perform({ client.createReminder(elder.id, title, medicine, dose, time, enabled) }) { refreshReminders() } }
                },
                onUpdateReminder = { reminder, title, medicine, dose, time, enabled ->
                    api?.let { client -> perform({ client.updateReminder(reminder.id, title, medicine, dose, time, enabled) }) { refreshReminders() } }
                },
                onDeleteReminder = { reminder -> api?.let { client -> perform({ client.deleteReminder(reminder.id) }) { refreshReminders() } } },
                onLoadEscorts = ::refreshEscorts,
                onEscortAction = { escort, action, note -> api?.let { client -> perform({ client.escortAction(escort.id, action, note) }) { refreshEscorts() } } },
                onLoadCalls = ::refreshCalls,
                onStartCall = {
                    val elder = selectedElder!!
                    api?.let { client -> perform({ client.createCall(elder.id) }) { activeCall = it; calls = calls + it } }
                },
                onOpenCall = { activeCall = it },
                onAcceptIncomingCall = {
                    val client = api
                    val callToAnswer = incomingCall
                    incomingCall = null
                    if (client != null && callToAnswer != null) {
                        perform(
                            action = { client.callAction(callToAnswer.id, "answer") },
                            onSuccess = { updated ->
                                activeCall = updated
                                calls = calls.map { call -> if (call.id == updated.id) updated else call }
                            },
                            onFailure = { incomingCall = callToAnswer }
                        )
                    }
                },
                onDeclineIncomingCall = {
                    val client = api
                    val callToDecline = incomingCall
                    incomingCall = null
                    if (client != null && callToDecline != null) {
                        perform(
                            action = { client.callAction(callToDecline.id, "decline") },
                            onSuccess = { updated ->
                                calls = calls.map { call -> if (call.id == updated.id) updated else call }
                            },
                            onFailure = { incomingCall = callToDecline }
                        )
                    }
                },
                onLoadCapabilities = { api?.let { client -> perform({ client.getCapabilities() }) { capabilities = it } } },
                onUpdateElderSettings = { cameraEnabled, voiceEnabled, dialect ->
                    val elder = selectedElder!!
                    api?.let { client -> perform({ client.updateElderSettings(elder.id, cameraEnabled, voiceEnabled, dialect) }) { updated -> selectedElder = updated; elders = elders.map { if (it.id == updated.id) updated else it }; dashboard = dashboard?.copy(elder = updated) } }
                },
                onConfirmCameraOff = {
                    val elder = selectedElder!!
                    api?.let { client -> perform({ client.updateElderSettings(elder.id, cameraEnabled = false, confirmCameraOff = true) }) { updated -> selectedElder = updated; elders = elders.map { if (it.id == updated.id) updated else it }; dashboard = dashboard?.copy(elder = updated); snapshot = null } }
                },
                onLoadSnapshot = {
                    val client = api
                    val elderForSnapshot = selectedElder
                    if (client != null && elderForSnapshot != null) {
                        scope.launch {
                            snapshotLoading = true
                            error = null
                            try {
                                snapshot = withContext(Dispatchers.IO) { client.loadSnapshot(elderForSnapshot.id) }
                                snapshotAt = java.time.OffsetDateTime.now().toString()
                            } catch (e: Exception) {
                                error = e.message ?: "无法读取快照"
                            } finally {
                                snapshotLoading = false
                            }
                        }
                    }
                },
                onAssistant = { text, confirmToken ->
                    val client = api
                    val elderForAssistant = selectedElder
                    if (client != null && elderForAssistant != null) {
                        perform({ client.assistant(elderForAssistant.id, text, elderForAssistant.dialect, confirmToken) }) {
                            assistantResult = it
                            if (elderForAssistant.voiceEnabled && it.reply.isNotBlank()) tts.speak(it.reply, TextToSpeech.QUEUE_FLUSH, null, "laoyou-assistant")
                        }
                    }
                },
                onLogout = {
                    api?.let { client -> perform({ client.logout() }) }
                    api = null
                    user = null
                    selectedElder = null
                    elders = emptyList()
                    dashboard = null
                    selectedEvent = null
                    notificationPollingStarted = false
                    seenNotificationIds = emptySet()
                    incomingCall = null
                    notifiedIncomingCallIds = emptySet()
                },
                onErrorDismiss = { error = null }
            )
        }
    }
}

private fun normalizeBaseUrl(value: String): String {
    val clean = value.trim().trimEnd('/')
    return if (clean.endsWith("/api")) clean else "$clean/api"
}

@Composable
private fun LoginScreen(
    baseUrl: String,
    onBaseUrlChange: (String) -> Unit,
    username: String,
    onUsernameChange: (String) -> Unit,
    password: String,
    onPasswordChange: (String) -> Unit,
    loading: Boolean,
    error: String?,
    onLogin: () -> Unit
) {
    val context = LocalContext.current
    val isDebuggable = remember(context) {
        (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
    }
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 28.dp, vertical = 48.dp),
            verticalArrangement = Arrangement.Center
        ) {
            Text("老友", fontSize = 38.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
            Text("子女与社区照护", fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(10.dp))
            Text("登录后可查看已关联家人的老人、事件和通知。首次使用请填写电脑总控的地址。", color = Color(0xFF37485C))
            Spacer(Modifier.height(24.dp))
            OutlinedTextField(
                value = baseUrl,
                onValueChange = onBaseUrlChange,
                label = { Text("服务地址") },
                placeholder = { Text("例如：http://192.168.1.100:18080") },
                // 预置了地址就直说，避免用户以为还要自己去找电脑IP。
                supportingText = {
                    Text(
                        if (BuildConfig.DEFAULT_BASE_URL.isBlank()) {
                            "APP 会自动补上 /api。同一网络填电脑局域网地址；不同网络填 Cloudflare 公网 https 地址（如 https://xxx.trycloudflare.com）。模拟器可填 http://10.0.2.2:8000"
                        } else {
                            "已预置服务地址，一般不用改；换到别的网络或电脑时可手动修改。APP 会自动补上 /api。"
                        }
                    )
                },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(username, onUsernameChange, label = { Text("账号") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(password, onPasswordChange, label = { Text("密码") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(18.dp))
            if (error != null) Text(error, color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.height(8.dp))
            Button(onClick = onLogin, enabled = !loading, modifier = Modifier.fillMaxWidth().height(54.dp)) {
                if (loading) CircularProgressIndicator(modifier = Modifier.size(22.dp), color = MaterialTheme.colorScheme.onPrimary)
                else Text("登录", fontSize = 18.sp)
            }
            Spacer(Modifier.height(18.dp))
            // 局域网地址只能是明文 HTTP，跨网络的 Cloudflare 公网地址是 HTTPS。
            // 这里把风险讲清楚，避免用户在不可信网络里输入口令。
            Text(
                "提示：填局域网地址时是明文连接，仅请在可信的家庭或办公网络中使用；不同网络请用 Cloudflare 公网 https 地址，链路已加密。",
                fontSize = 16.sp,
                color = Color(0xFF884005)
            )
            // 演示口令只在 debug 构建里显示；打包出去的 release 不该印出任何口令。
            if (isDebuggable) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "本地演示账号：elder / child / community / admin；密码见服务端启动提示。",
                    fontSize = 16.sp,
                    color = Color(0xFF37485C)
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ElderPickerScreen(
    user: User,
    elders: List<Elder>,
    loading: Boolean,
    error: String?,
    onSelect: (Elder) -> Unit,
    onLogout: () -> Unit
) {
    Scaffold(topBar = { TopAppBar(title = { Text("选择老人") }, actions = { TextButton(onClick = onLogout) { Text("退出") } }) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(20.dp)) {
            Text("你好，${user.displayName}（${roleLabel(user.role)}）", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text("当前账号可访问以下家庭成员", color = Color(0xFF37485C), modifier = Modifier.padding(top = 6.dp, bottom = 16.dp))
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (error != null) Text(error, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(vertical = 8.dp))
            if (elders.isEmpty() && !loading) Text("当前账号还没有可查看的家人，请检查家庭或社区关联。")
            LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(elders, key = { it.id }) { elder ->
                    Card(onClick = { onSelect(elder) }, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(20.dp)) {
                            Text(elder.name, fontSize = 24.sp, fontWeight = FontWeight.Bold)
                            Text("城市：${elder.city} · 摄像头：${if (elder.cameraEnabled) "已开启" else "已关闭"}")
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AppShell(
    user: User,
    elder: Elder,
    dashboard: Dashboard?,
    observations: List<Observation>,
    events: List<Event>,
    selectedEvent: EventDetail?,
    reminders: List<Reminder>,
    escorts: List<Escort>,
    calls: List<CallModel>,
    incomingCall: CallModel?,
    capabilities: Capabilities?,
    currentTab: AppTab,
    loading: Boolean,
    snapshot: Bitmap?,
    snapshotAt: String?,
    snapshotLoading: Boolean,
    assistantResult: AssistantResult?,
    error: String?,
    onTab: (AppTab) -> Unit,
    onSwitchElder: () -> Unit,
    onRefreshDashboard: () -> Unit,
    onLoadEvents: () -> Unit,
    onLoadEvent: (String) -> Unit,
    onEventAction: (String, String, String?) -> Unit,
    onAckNotification: (String) -> Unit,
    onRetryNotification: (String) -> Unit,
    onLoadReminders: () -> Unit,
    onCreateReminder: (String, String, String, String, Boolean) -> Unit,
    onUpdateReminder: (Reminder, String, String, String, String, Boolean) -> Unit,
    onDeleteReminder: (Reminder) -> Unit,
    onLoadEscorts: () -> Unit,
    onEscortAction: (Escort, String, String?) -> Unit,
    onLoadCalls: () -> Unit,
    onStartCall: () -> Unit,
    onOpenCall: (CallModel) -> Unit,
    onAcceptIncomingCall: () -> Unit,
    onDeclineIncomingCall: () -> Unit,
    onLoadCapabilities: () -> Unit,
    onUpdateElderSettings: (Boolean?, Boolean?, String?) -> Unit,
    onConfirmCameraOff: () -> Unit,
    onLoadSnapshot: () -> Unit,
    onAssistant: (String, String?) -> Unit,
    onLogout: () -> Unit,
    onErrorDismiss: () -> Unit
) {
    LaunchedEffect(currentTab) {
        when (currentTab) {
            AppTab.EVENTS -> onLoadEvents()
            AppTab.REMINDERS -> onLoadReminders()
            AppTab.ESCORTS -> onLoadEscorts()
            AppTab.CALLS -> onLoadCalls()
            AppTab.SETTINGS -> onLoadCapabilities()
            AppTab.HOME -> Unit
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Column { Text(elder.name); Text(roleLabel(user.role), fontSize = 16.sp, color = Color(0xFF37485C)) } },
                actions = {
                    TextButton(onClick = onSwitchElder) { Text("换老人") }
                    TextButton(onClick = onLogout) { Text("退出") }
                }
            )
        },
        bottomBar = {
            val tabs = buildList {
                add(AppTab.HOME to "首页")
                add(AppTab.EVENTS to "异常")
                add(AppTab.REMINDERS to "提醒")
                add(AppTab.CALLS to "通话")
                if (user.role == "community" || user.role == "admin") add(AppTab.ESCORTS to "陪诊")
                add(AppTab.SETTINGS to "设置")
            }
            NavigationBar(containerColor = Color(0xFFF0F4FA)) {
                tabs.forEach { (tab, label) ->
                    NavigationBarItem(selected = currentTab == tab, onClick = { onTab(tab) }, icon = { Text(tabIcon(tab), fontSize = 20.sp) }, label = { Text(label) })
                }
            }
        }
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (error != null) {
                Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFEDEA)), modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(error, color = MaterialTheme.colorScheme.error, modifier = Modifier.weight(1f))
                        TextButton(onClick = onErrorDismiss) { Text("关闭") }
                    }
                }
            }
            when (currentTab) {
                AppTab.HOME -> HomeScreen(elder, dashboard, observations, snapshot, snapshotAt, snapshotLoading, assistantResult, loading, onRefreshDashboard, onLoadSnapshot, onAssistant)
                AppTab.EVENTS -> if (selectedEvent == null) EventsScreen(events, onLoadEvents, onLoadEvent) else EventDetailScreen(user.role, selectedEvent, onBack = { onTab(AppTab.EVENTS) }, onAction = onEventAction, onAckNotification = onAckNotification, onRetryNotification = onRetryNotification)
                AppTab.REMINDERS -> RemindersScreen(user.role, reminders, onLoadReminders, onCreateReminder, onUpdateReminder, onDeleteReminder)
                AppTab.CALLS -> CallsScreen(user.role, calls, onLoadCalls, onStartCall, onOpenCall)
                AppTab.ESCORTS -> EscortsScreen(escorts, onLoadEscorts, onEscortAction)
                AppTab.SETTINGS -> SettingsScreen(user.role, elder, capabilities, onLoadCapabilities, onUpdateElderSettings, onConfirmCameraOff)
            }
        }
    }
    if (incomingCall != null) {
        IncomingCallDialog(elderName = elder.name, onAccept = onAcceptIncomingCall, onDecline = onDeclineIncomingCall)
    }
}

@Composable
private fun IncomingCallDialog(elderName: String, onAccept: () -> Unit, onDecline: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDecline,
        title = { Text("家人视频来电") },
        text = { Text("$elderName 有新的家人视频来电。接听后将打开通话页面。") },
        confirmButton = { TextButton(onClick = onAccept) { Text("接听") } },
        dismissButton = { TextButton(onClick = onDecline) { Text("拒绝") } }
    )
}

private fun tabIcon(tab: AppTab): String = when (tab) {
    AppTab.HOME -> "⌂"
    AppTab.EVENTS -> "!"
    AppTab.REMINDERS -> "⏰"
    AppTab.CALLS -> "☎"
    AppTab.ESCORTS -> "＋"
    AppTab.SETTINGS -> "⚙"
}

@Composable
private fun HomeScreen(
    elder: Elder,
    dashboard: Dashboard?,
    observations: List<Observation>,
    snapshot: Bitmap?,
    snapshotAt: String?,
    snapshotLoading: Boolean,
    assistantResult: AssistantResult?,
    loading: Boolean,
    onRefresh: () -> Unit,
    onLoadSnapshot: () -> Unit,
    onAssistant: (String, String?) -> Unit
) {
    var assistantText by remember { mutableStateOf("") }
    val scroll = rememberScrollState()
    Column(Modifier.fillMaxSize().verticalScroll(scroll).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("健康与照护概览", fontSize = 25.sp, fontWeight = FontWeight.Bold)
                Text("查看家人的健康记录；每条记录都会标明来源。", color = Color(0xFF37485C))
            }
            OutlinedButton(onClick = onRefresh, enabled = !loading) { Text("刷新") }
        }
        if (dashboard == null) {
            Card(Modifier.fillMaxWidth()) { Text("正在读取老人数据…", Modifier.padding(18.dp)) }
        } else {
            dashboard.weather?.let { weather ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(18.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("今日天气 · ${weather.city}", fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                            AssistChip(onClick = {}, label = { Text(if (weather.source == "live") "实时" else "模拟/降级") })
                        }
                        Text("${weather.temperature}  ${weather.description}", fontSize = 23.sp, modifier = Modifier.padding(top = 8.dp))
                        Text(weather.advice, modifier = Modifier.padding(top = 6.dp))
                        Text("观测：${formatTime(weather.observedAt)}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 8.dp))
                    }
                }
            }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("健康观测", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    val latest = dashboard.latestObservation
                    if (latest == null) Text("暂无最新观测。尚未接入设备时没有实时数据；演示记录会标为模拟来源。", modifier = Modifier.padding(top = 8.dp))
                    else {
                        Text(observationLabel(latest), fontSize = 19.sp, modifier = Modifier.padding(top = 8.dp))
                        Text("时间 ${formatTime(latest.occurredAt)} · 来源 ${sourceLabel(latest.source)}", color = Color(0xFF37485C), fontSize = 16.sp)
                        latest.location?.let { Text("位置：$it", fontSize = 16.sp) }
                    }
                    Text("当前活跃异常：${dashboard.activeEventCount} 条", modifier = Modifier.padding(top = 10.dp), fontWeight = FontWeight.SemiBold)
                    if (observations.isNotEmpty()) Text("最近观测 ${observations.size} 条已同步", color = Color(0xFF37485C), fontSize = 16.sp)
                }
            }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("摄像头快照", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    if (!elder.cameraEnabled) {
                        Text("摄像头已关闭，系统不会接收摄像头监控推断。", modifier = Modifier.padding(top = 8.dp))
                    } else {
                        Text("每次手动读取一帧；非实时视频。", color = Color(0xFF37485C), modifier = Modifier.padding(top = 6.dp))
                        snapshot?.let { bitmap ->
                            Image(bitmap.asImageBitmap(), contentDescription = "老人摄像头快照", modifier = Modifier.fillMaxWidth().height(220.dp).padding(top = 10.dp), contentScale = ContentScale.Crop)
                            Text("本次获取：${snapshotAt?.let(::formatTime) ?: "未知"} · 非实时视频快照", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 5.dp))
                        }
                        OutlinedButton(onClick = onLoadSnapshot, enabled = !snapshotLoading, modifier = Modifier.padding(top = 10.dp)) {
                            if (snapshotLoading) CircularProgressIndicator(Modifier.size(18.dp)) else Text("读取最新快照")
                        }
                    }
                }
            }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("你好通通 · 文字等价入口", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("可以查询天气、健康和提醒；未配置的能力会明确提示。", color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 5.dp))
                    OutlinedTextField(assistantText, { assistantText = it }, label = { Text("例如：查天气、查询提醒、每天晚上八点提醒我吃药") }, modifier = Modifier.fillMaxWidth().padding(top = 10.dp))
                    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.End) {
                        Button(onClick = { if (assistantText.isNotBlank()) { onAssistant(assistantText.trim(), null); assistantText = "" } }, enabled = assistantText.isNotBlank()) { Text("发送") }
                    }
                    assistantResult?.let { result ->
                        HorizontalDivider(Modifier.padding(vertical = 10.dp))
                        Text(result.reply, fontSize = 17.sp)
                        Text("服务：${assistantModeLabel(result.mode)} · ${assistantActionLabel(result.action)}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 5.dp))
                        result.confirmToken?.let { token ->
                            Text("为避免误操作，请确认后再执行。", color = Color(0xFF884005), modifier = Modifier.padding(top = 6.dp))
                            Button(onClick = { onAssistant("确认", token) }, modifier = Modifier.padding(top = 6.dp)) { Text("确认执行") }
                        }
                    }
                }
            }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("提醒与播报", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("启用提醒：${dashboard.reminders.count { it.enabled }} 条；今日播报：${dashboard.broadcasts.size} 条", modifier = Modifier.padding(top = 8.dp))
                    Text("APP运行期间每30秒轮询通知并尝试发本地通知；APP被系统终止后不保证后台提醒。", color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 8.dp))
                }
            }
        }
    }
}

@Composable
private fun EventsScreen(events: List<Event>, onRefresh: () -> Unit, onOpen: (String) -> Unit) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("异常事件", fontSize = 25.sp, fontWeight = FontWeight.Bold)
                Text("查看处理进展和每次通知结果。", color = Color(0xFF37485C))
            }
            OutlinedButton(onClick = onRefresh) { Text("刷新") }
        }
        Spacer(Modifier.height(10.dp))
        if (events.isEmpty()) Text("暂无可见异常。列表由当前账号授权范围决定。", modifier = Modifier.padding(top = 12.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(events, key = { it.id }) { event -> EventCard(event, onOpen) }
        }
    }
}

@Composable
private fun EventCard(event: Event, onOpen: (String) -> Unit) {
    val color = if (event.severity == "critical") Color(0xFFFFE4E1) else Color(0xFFFFF5D6)
    Card(onClick = { onOpen(event.id) }, colors = CardDefaults.cardColors(containerColor = color), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(event.title, fontSize = 19.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                AssistChip(onClick = {}, label = { Text(eventSeverityLabel(event.severity)) })
            }
            Text(event.description, modifier = Modifier.padding(top = 7.dp))
            Text("${eventStatusLabel(event.status)} · ${formatTime(event.createdAt)} · ${sourceLabel(event.source)}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 8.dp))
        }
    }
}

@Composable
private fun EventDetailScreen(
    role: String,
    detail: EventDetail,
    onBack: () -> Unit,
    onAction: (String, String, String?) -> Unit,
    onAckNotification: (String) -> Unit,
    onRetryNotification: (String) -> Unit
) {
    var note by remember(detail.event.id, detail.event.updatedAt) { mutableStateOf("") }
    var showCorrectDialog by remember { mutableStateOf(false) }
    var showUnavailableDialog by remember { mutableStateOf(false) }
    val canHandle = role == "child" || role == "community" || role == "admin"
    val canCorrect = role == "child" || role == "admin"
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        TextButton(onClick = onBack) { Text("‹ 返回异常列表") }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(detail.event.title, fontSize = 25.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            AssistChip(onClick = {}, label = { Text(eventStatusLabel(detail.event.status)) })
        }
        Text(detail.event.description)
        Text("来源：${sourceLabel(detail.event.source)} · 创建：${formatTime(detail.event.createdAt)}", fontSize = 16.sp, color = Color(0xFF37485C))
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("处置", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                if (canHandle && detail.event.status == "alerted") Button(onClick = { onAction(detail.event.id, "acknowledge", null) }, modifier = Modifier.padding(top = 10.dp)) { Text("确认事件") }
                if (canHandle && detail.event.status == "acknowledged") Button(onClick = { onAction(detail.event.id, "start", null) }, modifier = Modifier.padding(top = 10.dp)) { Text("开始处理") }
                if (canHandle && detail.event.status == "handling") {
                    OutlinedTextField(note, { note = it }, label = { Text("完成说明（必填）") }, modifier = Modifier.fillMaxWidth().padding(top = 10.dp))
                    Button(onClick = { onAction(detail.event.id, "resolve", note.trim()) }, enabled = note.isNotBlank(), modifier = Modifier.padding(top = 8.dp)) { Text("完成处理") }
                }
                if (canCorrect && detail.event.status != "false_positive") {
                    OutlinedButton(onClick = { showCorrectDialog = true }, modifier = Modifier.padding(top = 10.dp)) { Text("修正为误报") }
                }
                if ((role == "community" || role == "admin") && detail.event.kind in setOf("heart_rate", "blood_pressure") && detail.event.status != "resolved" && detail.event.status != "false_positive") {
                    OutlinedButton(onClick = { showUnavailableDialog = true }, modifier = Modifier.padding(top = 10.dp)) { Text("社区无法协助，转放心医") }
                }
                if (!canHandle && !canCorrect) Text("当前账号只能查看，不能处置或修正事件。", color = Color(0xFF37485C), modifier = Modifier.padding(top = 10.dp))
            }
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("时间线", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                if (detail.timeline.isEmpty()) Text("暂无时间线")
                detail.timeline.forEach { item ->
                    Column(Modifier.padding(top = 12.dp)) {
                        Text("${timelineNodeLabel(item.node)} · ${formatTime(item.at)}", fontWeight = FontWeight.SemiBold)
                        Text("${if (item.actor == "system") "系统" else roleLabel(item.actor)}：${item.detail}", fontSize = 16.sp)
                    }
                }
            }
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("通知结果", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                if (detail.notifications.isEmpty()) Text("暂无通知记录")
                detail.notifications.forEach { notification ->
                    Column(Modifier.padding(top = 12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(notificationTargetLabel(notification.target), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                            Text(notificationStatusLabel(notification.status))
                        }
                            Text("${notificationStatusLabel(notification.status)} · ${if (notification.simulated) "模拟接入" else "真实通道"} · 尝试 ${notification.attempts} 次 · 创建 ${formatTime(notification.createdAt)}", fontSize = 16.sp, color = Color(0xFF37485C))
                        notification.lastError?.let { Text("失败原因：$it", color = MaterialTheme.colorScheme.error, fontSize = 16.sp) }
                        Row {
                            if ((role == "admin" || (role == "child" && notification.target == "child") || (role == "community" && notification.target == "community")) && notification.status == "sent") {
                                TextButton(onClick = { onAckNotification(notification.id) }) { Text("确认收到") }
                            }
                            if (role == "admin" && notification.status == "failed") TextButton(onClick = { onRetryNotification(notification.id) }) { Text("重试") }
                        }
                    }
                }
            }
        }
        detail.escort?.let { escort ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("陪诊工单", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("${escort.platform} · ${escortStatusLabel(escort.status)} · ${if (escort.simulated) "模拟接入" else "真实平台"}", modifier = Modifier.padding(top = 8.dp))
                }
            }
        }
    }
    if (showCorrectDialog) {
        NoteDialog(title = "修正误报", label = "必须填写修正原因", onDismiss = { showCorrectDialog = false }) { reason ->
            onAction(detail.event.id, "correct", reason)
            showCorrectDialog = false
        }
    }
    if (showUnavailableDialog) {
        NoteDialog(title = "转放心医陪诊", label = "请说明社区无法协助的原因", onDismiss = { showUnavailableDialog = false }) { reason ->
            onAction(detail.event.id, "community_unavailable", reason)
            showUnavailableDialog = false
        }
    }
}

@Composable
private fun NoteDialog(title: String, label: String, onDismiss: () -> Unit, onConfirm: (String) -> Unit) {
    var note by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) }, text = { OutlinedTextField(note, { note = it }, label = { Text(label) }, minLines = 2) }, confirmButton = { TextButton(onClick = { onConfirm(note.trim()) }, enabled = note.isNotBlank()) { Text("提交") } }, dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } })
}

@Composable
private fun RemindersScreen(
    role: String,
    reminders: List<Reminder>,
    onRefresh: () -> Unit,
    onCreate: (String, String, String, String, Boolean) -> Unit,
    onUpdate: (Reminder, String, String, String, String, Boolean) -> Unit,
    onDelete: (Reminder) -> Unit
) {
    var editing by remember { mutableStateOf<Reminder?>(null) }
    var showForm by remember { mutableStateOf(false) }
    val canEdit = role == "child" || role == "admin" || role == "elder"
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("用药提醒", fontSize = 25.sp, fontWeight = FontWeight.Bold)
                Text("时间严格按每天 HH:mm 保存，名称和剂量由家属录入。", color = Color(0xFF37485C))
            }
            OutlinedButton(onClick = onRefresh) { Text("刷新") }
        }
        if (canEdit) Button(onClick = { editing = null; showForm = true }, modifier = Modifier.padding(top = 10.dp)) { Text("新增提醒") }
        else Text("社区账号只读。", color = Color(0xFF37485C), modifier = Modifier.padding(top = 12.dp))
        if (reminders.isEmpty()) Text("暂无提醒记录。", modifier = Modifier.padding(top = 12.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(top = 10.dp)) {
            items(reminders, key = { it.id }) { reminder ->
                Card(onClick = { if (canEdit) { editing = reminder; showForm = true } }, modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(reminder.title, fontSize = 19.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                            Text(if (reminder.enabled) "启用" else "停用", color = if (reminder.enabled) Color(0xFF165198) else Color(0xFF37485C))
                        }
                        Text("${reminder.time} · ${reminder.medicine} · ${reminder.dose}", modifier = Modifier.padding(top = 6.dp))
                    }
                }
            }
        }
    }
    if (showForm) ReminderFormDialog(editing, onDismiss = { showForm = false }) { reminder, title, medicine, dose, time, enabled ->
        if (reminder == null) onCreate(title, medicine, dose, time, enabled) else onUpdate(reminder, title, medicine, dose, time, enabled)
        showForm = false
    }
    if (showForm && editing != null) {
        // The delete action stays inside the edit dialog through the dedicated callback below.
    }
}

@Composable
private fun ReminderFormDialog(initial: Reminder?, onDismiss: () -> Unit, onSave: (Reminder?, String, String, String, String, Boolean) -> Unit) {
    var title by remember(initial?.id) { mutableStateOf(initial?.title.orEmpty()) }
    var medicine by remember(initial?.id) { mutableStateOf(initial?.medicine.orEmpty()) }
    var dose by remember(initial?.id) { mutableStateOf(initial?.dose.orEmpty()) }
    var time by remember(initial?.id) { mutableStateOf(initial?.time.orEmpty()) }
    var enabled by remember(initial?.id) { mutableStateOf(initial?.enabled ?: true) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (initial == null) "新增提醒" else "编辑提醒") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(title, { title = it }, label = { Text("提醒标题") }, singleLine = true)
                OutlinedTextField(medicine, { medicine = it }, label = { Text("药物名称") }, singleLine = true)
                OutlinedTextField(dose, { dose = it }, label = { Text("剂量") }, singleLine = true)
                OutlinedTextField(time, { time = it }, label = { Text("时间 HH:mm") }, singleLine = true)
                Row(verticalAlignment = Alignment.CenterVertically) { Text("启用", modifier = Modifier.weight(1f)); Switch(enabled, { enabled = it }) }
            }
        },
        confirmButton = { TextButton(onClick = { onSave(initial, title.trim(), medicine.trim(), dose.trim(), time.trim(), enabled) }, enabled = title.isNotBlank() && medicine.isNotBlank() && dose.isNotBlank() && Regex("^\\d{2}:\\d{2}$").matches(time.trim())) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } }
    )
}

@Composable
private fun CallsScreen(role: String, calls: List<CallModel>, onRefresh: () -> Unit, onStart: () -> Unit, onOpen: (CallModel) -> Unit) {
    val canCall = role == "child" || role == "elder" || role == "admin"
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("视频通话", fontSize = 25.sp, fontWeight = FontWeight.Bold)
                Text("与家人视频通话，接通后可使用摄像头和麦克风。", color = Color(0xFF37485C))
            }
            OutlinedButton(onClick = onRefresh) { Text("刷新") }
        }
        if (canCall) Button(onClick = onStart, modifier = Modifier.padding(top = 10.dp)) { Text("发起视频通话") }
        else Text("社区账号可查看授权信息，但没有通话参与权限。", color = Color(0xFF37485C), modifier = Modifier.padding(top = 12.dp))
        if (calls.isEmpty()) Text("暂无通话记录。", modifier = Modifier.padding(top = 12.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(top = 10.dp)) {
            items(calls, key = { it.id }) { call ->
                Card(onClick = { if (canCall && (call.status == "ringing" || call.status == "active")) onOpen(call) }, modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(callStatusLabel(call.status), fontSize = 19.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                            if (call.status == "ringing" || call.status == "active") Text("打开")
                        }
                        Text("创建：${formatTime(call.createdAt)}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 6.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun CallWebViewScreen(
    call: CallModel,
    callUrl: String,
    backendUrl: String,
    onBack: () -> Unit,
    onAction: (String) -> Unit
) {
    val context = LocalContext.current
    var permissionRefresh by remember { mutableStateOf(0) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permissionRefresh++ }
    val hasCamera = remember(permissionRefresh) { ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED }
    val hasMic = remember(permissionRefresh) { ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED }
    var callProxy by remember(call.id, backendUrl) { mutableStateOf<LocalCallProxy?>(null) }
    var proxyError by remember(call.id, backendUrl) { mutableStateOf<String?>(null) }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    DisposableEffect(call.id, backendUrl) {
        val proxy = runCatching { LocalCallProxy.fromBackendUrl(backendUrl).start() }
            .onFailure { proxyError = it.message ?: "无法启动通话连接" }
            .getOrNull()
        callProxy = proxy
        onDispose {
            webViewRef?.apply { stopLoading(); destroy() }
            webViewRef = null
            callProxy = null
            proxy?.close()
        }
    }
    val localCallUrl = callProxy?.localUrlFor(callUrl)
    LaunchedEffect(call.id) {
        if (!hasCamera || !hasMic) permissionLauncher.launch(arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO))
    }
    LaunchedEffect(permissionRefresh, localCallUrl, webViewRef) {
        if (permissionRefresh > 0 && hasCamera && hasMic) webViewRef?.loadUrl(localCallUrl ?: return@LaunchedEffect)
    }
    BackHandler(enabled = true, onBack = onBack)
    Column(Modifier.fillMaxSize().background(Color.Black)) {
        Row(Modifier.fillMaxWidth().background(Color(0xFF101820)).padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("‹ 返回", color = Color.White) }
            Text("家人视频通话", color = Color.White, modifier = Modifier.weight(1f))
            if (call.status == "active" || call.status == "ringing") TextButton(onClick = { onAction("end") }) { Text("挂断", color = Color(0xFFFFB4AB)) }
        }
        if (proxyError != null) {
            Column(Modifier.fillMaxWidth().padding(18.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("无法建立安全通话连接：${proxyError}", color = Color.White)
                Text("请检查服务地址和电脑是否已启动。", color = Color(0xFFB0BEC5), modifier = Modifier.padding(top = 8.dp))
            }
        }
        if (!hasCamera || !hasMic) {
            Column(Modifier.fillMaxWidth().padding(18.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("通话需要摄像头和麦克风权限。只有本次家人通话会使用这些权限，离开通话后会释放。", color = Color.White)
                OutlinedButton(onClick = { permissionLauncher.launch(arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)) }, modifier = Modifier.padding(top = 12.dp)) { Text("授予权限") }
            }
        }
        localCallUrl?.let { pageUrl ->
            AndroidView(
                modifier = Modifier.fillMaxWidth().weight(1f),
                factory = { ctx ->
                    WebView(ctx).apply {
                        webViewRef = this
                        layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        settings.mediaPlaybackRequiresUserGesture = false
                        // The call page has a device-width viewport. Respect it at the final MATCH_PARENT size
                        // instead of loading an overview-scaled page during the initial zero-size measure.
                        settings.useWideViewPort = true
                        settings.loadWithOverviewMode = false
                        webViewClient = object : WebViewClient() {
                            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                                return !sameOrigin(request.url, Uri.parse(pageUrl))
                            }
                        }
                        webChromeClient = object : WebChromeClient() {
                            override fun onPermissionRequest(request: PermissionRequest) {
                                val origin = runCatching { Uri.parse(request.origin.toString()) }.getOrNull()
                                val expected = Uri.parse(pageUrl)
                                val allowedOrigin = origin != null && sameOrigin(origin, expected)
                                val cameraGranted = ContextCompat.checkSelfPermission(ctx, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                                val micGranted = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                                val requestedResources = request.resources.toSet()
                                val supportedResources = requestedResources.filter {
                                    it == PermissionRequest.RESOURCE_VIDEO_CAPTURE || it == PermissionRequest.RESOURCE_AUDIO_CAPTURE
                                }
                                val hasUnsupportedResource = requestedResources.any { it !in supportedResources }
                                val allowed = allowedOrigin && !hasUnsupportedResource && supportedResources.isNotEmpty() &&
                                    (!requestedResources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE) || cameraGranted) &&
                                    (!requestedResources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE) || micGranted)
                                if (allowed) request.grant(supportedResources.toTypedArray()) else request.deny()
                            }
                        }
                        // AndroidView creates the WebView before its weighted height is measured. Start the
                        // first navigation on the next UI turn so CSS 100vh sees the actual call-stage size.
                        post { loadUrl(pageUrl) }
                    }
                },
                update = { }
            )
        }
    }
}

@Composable
private fun EscortsScreen(escorts: List<Escort>, onRefresh: () -> Unit, onAction: (Escort, String, String?) -> Unit) {
    var completing by remember { mutableStateOf<Escort?>(null) }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("陪诊工单", fontSize = 25.sp, fontWeight = FontWeight.Bold)
                Text("放心医陪诊服务为模拟接入，申请、接单和完成进展会显示在这里。", color = Color(0xFF37485C))
            }
            OutlinedButton(onClick = onRefresh) { Text("刷新") }
        }
        if (escorts.isEmpty()) Text("暂无陪诊工单。", modifier = Modifier.padding(top = 12.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(top = 10.dp)) {
            items(escorts, key = { it.id }) { escort ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("${escort.platform} · ${escortStatusLabel(escort.status)}", fontSize = 19.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                            if (escort.status == "requested") Button(onClick = { onAction(escort, "accept", null) }) { Text("接单") }
                            if (escort.status == "accepted") Button(onClick = { completing = escort }) { Text("完成") }
                        }
                        Text("申请：${formatTime(escort.requestedAt)} · ${if (escort.simulated) "模拟接入" else "真实平台"}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 6.dp))
                        escort.note?.let { Text("备注：$it", modifier = Modifier.padding(top = 5.dp)) }
                    }
                }
            }
        }
    }
    completing?.let { escort ->
        NoteDialog(title = "完成陪诊", label = "完成说明（必填）", onDismiss = { completing = null }) { note ->
            onAction(escort, "complete", note)
            completing = null
        }
    }
}

@Composable
private fun SettingsScreen(
    role: String,
    elder: Elder,
    capabilities: Capabilities?,
    onLoadCapabilities: () -> Unit,
    onUpdateSettings: (Boolean?, Boolean?, String?) -> Unit,
    onConfirmCameraOff: () -> Unit
) {
    var confirmCameraOff by remember { mutableStateOf(false) }
    val canEdit = role == "child" || role == "admin" || role == "elder"
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("家庭与服务设置", fontSize = 25.sp, fontWeight = FontWeight.Bold)
        Text("社区账号只读；可用服务会显示当前状态。", color = Color(0xFF37485C))
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(18.dp)) {
                Text("摄像头", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Text(if (elder.cameraEnabled) "已开启" else "已关闭", modifier = Modifier.weight(1f))
                    Switch(checked = elder.cameraEnabled, onCheckedChange = { checked -> if (!canEdit) Unit else if (!checked) confirmCameraOff = true else onUpdateSettings(true, null, null) }, enabled = canEdit)
                }
                Text("关闭需要二次确认；已保存快照会被清除，摄像头监控也会停止。", fontSize = 16.sp, color = Color(0xFF37485C))
            }
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(18.dp)) {
                Text("语音与方言", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Text("播报 ${if (elder.voiceEnabled) "已开启" else "已关闭"}", modifier = Modifier.weight(1f))
                    Switch(checked = elder.voiceEnabled, onCheckedChange = { if (canEdit) onUpdateSettings(null, it, null) }, enabled = canEdit)
                }
                Text("当前方言：${elder.dialect}", modifier = Modifier.padding(top = 8.dp))
                Text("方言能力会标明可用状态；系统普通话识别不会冒充方言。", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 4.dp))
            }
        }
        capabilities?.let { cap ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("服务能力", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("助手：${assistantModeLabel(cap.assistantMode)}")
                    Text("语音：${speechProviderLabel(cap.speechProvider)} · ${if (cap.speechConfigured) "可用" else "未配置"}")
                    Text("视频：${videoModeLabel(cap.videoMode)}")
                    cap.dialects.forEach { dialect -> Text("${dialect.label}：${if (dialect.available) "可用" else "不可用"} · ${dialect.note}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 4.dp)) }
                }
            }
        }
    }
    if (confirmCameraOff) {
        AlertDialog(onDismissRequest = { confirmCameraOff = false }, title = { Text("关闭摄像头？") }, text = { Text("关闭后会清除已保存快照，并停止摄像头监控。穿戴设备的独立观测仍按规则工作。") }, confirmButton = { TextButton(onClick = { confirmCameraOff = false; onConfirmCameraOff() }) { Text("确认关闭") } }, dismissButton = { TextButton(onClick = { confirmCameraOff = false }) { Text("取消") } })
    }
}

private fun observationLabel(observation: Observation): String {
    val value = observation.value?.let { "：$it" }.orEmpty()
    val duration = observation.durationMinutes?.let { "，持续 $it 分钟" }.orEmpty()
    return "${observationKindLabel(observation.kind)}$value$duration"
}

private fun sourceLabel(source: String): String = when (source) {
    "simulated", "simulated_wearable" -> "模拟${if (source == "simulated_wearable") "穿戴设备" else "接入"}"
    "camera" -> "摄像头"
    "live" -> "实时接入"
    else -> source
}

private fun timelineNodeLabel(node: String): String = when (node) {
    "alerted" -> "异常预警"
    "acknowledge" -> "确认预警"
    "start" -> "开始处理"
    "resolve" -> "处理完成"
    "correct" -> "误报修正"
    "community_unavailable" -> "社区无法协助"
    "escort_requested" -> "申请陪诊"
    "escort_accepted" -> "陪诊已接单"
    "escort_completed" -> "陪诊已完成"
    "notification_sent" -> "通知已发送"
    "notification_failed" -> "通知发送失败"
    "notification_acknowledged", "notification_ack" -> "通知已确认"
    else -> "流程记录"
}

private fun callStatusLabel(status: String): String = when (status) {
    "ringing" -> "响铃中"
    "active" -> "通话中"
    "ended" -> "已结束"
    "declined" -> "已拒绝"
    else -> status
}

private fun observationKindLabel(kind: String): String = when (kind) {
    "fall" -> "跌倒"
    "wandering" -> "徘徊"
    "immobility" -> "长时间静止"
    "away" -> "未归"
    "heart_rate" -> "心率"
    "blood_pressure" -> "血压"
    "activity" -> "活动"
    "wake" -> "起床"
    "lunch" -> "午餐"
    "dinner" -> "晚餐"
    "sleep" -> "入睡"
    "return_home" -> "回家"
    else -> kind
}

private fun assistantModeLabel(mode: String): String = when (mode) {
    "local_rules" -> "指令助手"
    "" -> "未配置"
    else -> "智能助手"
}

private fun assistantActionLabel(action: String): String = when (action) {
    "reminder_proposal" -> "待确认提醒"
    "reminder_created" -> "提醒已创建"
    "camera_confirm" -> "待确认设置"
    "camera_updated" -> "设置已更新"
    "weather" -> "天气查询"
    "health" -> "健康查询"
    "call" -> "视频通话"
    "none", "" -> "查询"
    else -> "已处理"
}

private fun speechProviderLabel(provider: String): String = when (provider) {
    "browser" -> "系统语音"
    "xfyun" -> "讯飞语音"
    "" -> "未配置"
    else -> provider
}

private fun videoModeLabel(mode: String): String = when (mode) {
    "webrtc_signaling" -> "视频通话"
    "" -> "未配置"
    else -> "视频通话"
}

private fun notificationStatusLabel(status: String): String = when (status) {
    "pending" -> "待发送"
    "sent" -> "已送达"
    "failed" -> "发送失败"
    "acknowledged" -> "已确认"
    else -> status
}

private fun escortStatusLabel(status: String): String = when (status) {
    "requested" -> "待接单"
    "accepted" -> "已接单"
    "completed" -> "已完成"
    else -> status
}

private fun sameOrigin(left: Uri, right: Uri): Boolean {
    val leftPort = left.port.takeIf { it != -1 } ?: if (left.scheme == "https") 443 else 80
    val rightPort = right.port.takeIf { it != -1 } ?: if (right.scheme == "https") 443 else 80
    return left.scheme == right.scheme && left.host == right.host && leftPort == rightPort
}

private fun createNotificationChannel(context: Context) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val channel = NotificationChannel("laoyou-alerts", "老友异常通知", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "APP运行期间轮询到的异常通知"
        }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}

private fun postLocalNotification(context: Context, notification: NotificationItem) {
    val builder = NotificationCompat.Builder(context, "laoyou-alerts")
        .setSmallIcon(android.R.drawable.ic_dialog_alert)
        .setContentTitle("老友异常通知 · ${notificationTargetLabel(notification.target)}")
        .setContentText("有新的${notificationStatusLabel(notification.status)}通知，请打开老友查看事件时间线。")
        .setPriority(NotificationCompat.PRIORITY_HIGH)
        .setAutoCancel(true)
    runCatching { NotificationManagerCompat.from(context).notify(notification.id.hashCode(), builder.build()) }
}
