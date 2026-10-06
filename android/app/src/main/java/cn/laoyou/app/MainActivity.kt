package cn.laoyou.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
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
import androidx.compose.runtime.mutableIntStateOf
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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

private enum class AppTab { HOME, EVENTS, REMINDERS, CALLS, ESCORTS, SETTINGS }

// 录音参数：必须与后端 /speech 的约定一致（16kHz / 单声道 / 16bit 裸 PCM）。
// 长度上下限来自后端校验：3200 字节（0.1 秒）≤ 长度 ≤ 960000 字节（30 秒）。
// 这里上限取 20 秒：够说完一句话，上传也还快。
private const val MIC_SAMPLE_RATE = 16_000
private const val MIC_MIN_BYTES = 3_200
private const val MIC_MAX_BYTES = MIC_SAMPLE_RATE * 2 * 20

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

/** 录音/识别/播报的界面状态。集中成对象是为了让 HomeScreen 的参数不至于爆炸。 */
private data class MicState(
    val recording: Boolean = false,
    val busy: Boolean = false,
    val seconds: Int = 0,
    val message: String? = null
)

private data class SpeechState(
    val busy: Boolean = false,
    val message: String? = null,
    val voice: String? = null,
    /** 是否已经回落到手机自带 TTS。回落只有普通话，必须让用户知道。 */
    val degraded: Boolean = false
)

private data class AssistantVoiceUi(
    val text: String = "",
    val mic: MicState = MicState(),
    val speech: SpeechState = SpeechState(),
    /** 老人档案里是否开着语音播报；关了就不该出声。 */
    val voiceEnabled: Boolean = true,
    /** 方言标识 -> 说明当前识别/播报走的是哪条路。 */
    val speechNote: String = "",
    val broadcasts: List<Broadcast> = emptyList(),
    val broadcastNote: String = "",
    /** 只有 elder/admin 能回传"已播报"（后端契约第 3 节）。 */
    val canReportPlayed: Boolean = false
)

/**
 * 方言标识 -> 设备语音识别的 BCP-47 语言标签。
 *
 * 设备上的 SpeechRecognizer 只可能认识普通话与粤语；四川话/东北话没有对应的
 * 系统识别语言，一律按普通话送进去 —— 反正普通话说出来的四川话它也听得懂，
 * 真正需要方言的场景走服务端识别（那样还能顺带归一化）。
 */
private fun deviceRecognitionLanguage(dialect: String): String = when (dialect) {
    "yue-HK" -> "zh-HK"
    else -> "zh-CN"
}

private fun dialectLabel(dialect: String): String = when (dialect) {
    "yue-HK" -> "粤语"
    "sichuan" -> "四川话"
    "northeast" -> "东北话"
    else -> "普通话"
}

/**
 * 把长文本切成不超过 [limit] 字的片段。
 *
 * 后端 /speech/synthesize 的 text 上限是 300 字（schemas.SpeechSynthesisRequest
 * 的 max_length 与 edge_tts.MAX_TEXT_LENGTH 对齐），超了直接 422。
 * 这里留出余量，优先在句末切，其次逗号，实在没有标点才硬切 —— 硬切会把
 * 一个词拆成两半，只在超长无标点文本上才会发生。
 */
private fun chunkForSpeech(text: String, limit: Int = 280): List<String> {
    val trimmed = text.trim()
    if (trimmed.length <= limit) return listOf(trimmed)
    val chunks = mutableListOf<String>()
    var rest = trimmed
    while (rest.length > limit) {
        val window = rest.substring(0, limit)
        val cut = listOf('。', '！', '？', '；', '\n', '，', '、').maxOf { window.lastIndexOf(it) }
        val at = if (cut > 0) cut + 1 else limit
        chunks += rest.substring(0, at)
        rest = rest.substring(at).trimStart()
    }
    if (rest.isNotBlank()) chunks += rest
    return chunks
}

private fun broadcastKindLabel(kind: String): String = when (kind) {
    "weather" -> "天气"
    "health" -> "健康"
    "medication" -> "用药"
    else -> kind
}

/**
 * 一句话说清"按下麦克风会发生什么"。
 *
 * 两条路的差别是真实存在的，界面不能含糊：配了后端方言识别就是录音上传
 * （能认方言、顺带归一化），没配就是设备自带识别（不联网，但只认普通话，
 * 而且很多国产机没有引擎）。
 */
private fun speechRouteNote(capabilities: Capabilities?, dialect: String): String {
    return if (capabilities?.speechConfigured == true) {
        "按麦克风录音后发给电脑端识别，${dialectLabel(dialect)}会归一化成普通话"
    } else {
        "按麦克风用手机自带的语音识别，只认普通话；" +
            "方言需在电脑端 runtime/local.env 填 QWEN_API_KEY 后重启服务"
    }
}

/** 一句话说清"播报用哪个声音"。 */
private fun broadcastRouteNote(capabilities: Capabilities?, dialect: String): String {
    if (capabilities?.ttsServerSide != true) {
        return "电脑端的语音合成不可用，播报会用手机自带语音，只有普通话"
    }
    val hasVoice = capabilities.ttsVoices.any { it.id == dialect && it.available }
    return when {
        dialect == "zh-CN" -> "播报用电脑端合成，普通话女声"
        hasVoice -> "播报用电脑端合成，${dialectLabel(dialect)}音色"
        // 四川话目前没有免费音色，服务端会回落普通话音色但文本已归一化，
        // 这一点必须说出来，否则用户会以为方言合成坏了。
        else -> "电脑端没有${dialectLabel(dialect)}音色，会用普通话读出（文本已按方言归一化）"
    }
}

/** 把系统语音识别的错误码翻译成一句人话。 */
private fun deviceRecognitionErrorText(error: Int): String = when (error) {
    SpeechRecognizer.ERROR_AUDIO -> "录音出错，请看看麦克风是不是被别的应用占用了。"
    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "没有麦克风权限，无法语音输入。"
    SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "设备上的语音识别需要联网，请检查网络。"
    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没有听清，请再说一次。"
    SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "语音识别正忙，请稍后再试。"
    SpeechRecognizer.ERROR_SERVER, SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> "设备上的语音识别服务出错，请改用文字输入。"
    SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED, SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE ->
        "这台手机不认识这个语言。方言请在电脑端配好识别后用文字输入，或直接用普通话说。"
    // 用户主动取消时系统会回调 ERROR_CLIENT，不必当成故障提示。
    SpeechRecognizer.ERROR_CLIENT -> "已取消语音输入。"
    else -> "语音识别没有成功，请改用文字输入。"
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

    // ---- 语音播报 / 语音输入 ----
    var voiceUi by remember { mutableStateOf(AssistantVoiceUi()) }
    var assistantText by rememberSaveable { mutableStateOf("") }
    // 播报用"代次"做取消：每次重新播报/停止都 +1，被取消的协程发现代次变了就退出，
    // 避免两段语音叠在一起说话。
    var speechGeneration by remember { mutableIntStateOf(0) }
    var activePlayer by remember { mutableStateOf<MediaPlayer?>(null) }
    var recognizer by remember { mutableStateOf<SpeechRecognizer?>(null) }
    // 录音时用原子布尔量做停止信号：AudioRecord 的读取在 IO 线程里，
    // 读 Compose 状态跨线程不可靠，而且这个标志是要被另一个线程轮询的。
    val recordingStop = remember { AtomicBoolean(true) }
    var micJob by remember { mutableStateOf<Job?>(null) }

    fun stopSpeech() {
        speechGeneration += 1
        activePlayer?.let { player ->
            runCatching { if (player.isPlaying) player.stop() }
            runCatching { player.release() }
        }
        activePlayer = null
        voiceUi = voiceUi.copy(speech = SpeechState())
    }

    DisposableEffect(Unit) {
        onDispose {
            speechGeneration += 1
            recordingStop.set(true)
            activePlayer?.let { player -> runCatching { player.release() } }
            activePlayer = null
            recognizer?.let { engine -> runCatching { engine.destroy() } }
            recognizer = null
            tts.shutdown()
        }
    }

    /** 服务端返回的是 mp3 字节，而 MediaPlayer 只能从文件/URI 播，所以先落盘。 */
    fun writeSpeechFile(bytes: ByteArray): String {
        val file = File(context.cacheDir, "laoyou-speech.mp3")
        file.writeBytes(bytes)
        return file.absolutePath
    }

    /**
     * 播放一个本地音频文件，挂起直到播完（或协程被取消）。
     *
     * 用挂起而不是回调：一段长文本会被拆成多段分别合成，必须等前一段播完
     * 再播下一段，否则几段声音会同时响。
     */
    suspend fun playAudioFile(path: String) {
        suspendCancellableCoroutine<Unit> { continuation ->
            val player = MediaPlayer()
            try {
                player.setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                player.setDataSource(path)
                player.setOnCompletionListener { if (continuation.isActive) continuation.resume(Unit) }
                player.setOnErrorListener { _, _, _ ->
                    if (continuation.isActive) continuation.resume(Unit)
                    true
                }
                player.prepare()
                activePlayer = player
                player.start()
            } catch (e: Exception) {
                runCatching { player.release() }
                if (continuation.isActive) continuation.resume(Unit)
                return@suspendCancellableCoroutine
            }
            continuation.invokeOnCancellation {
                runCatching { player.stop() }
                runCatching { player.release() }
            }
        }
    }

    /**
     * 播报一段文字。
     *
     * 优先走服务端 edge-tts —— 这是手机上唯一能说粤语/东北话的路子：
     * 系统 TextToSpeech 固定 Locale.CHINA，只有普通话，老人设了粤语也读不出来。
     * 服务端不可用时才降级到系统 TTS，并且界面必须说明"现在只有普通话"，
     * 不能假装方言播报成功了。
     */
    fun speakReply(text: String) {
        val client = api
        val elder = selectedElder
        val trimmed = text.trim()
        if (client == null || elder == null || trimmed.isEmpty()) return
        stopSpeech()
        val generation = speechGeneration
        val dialect = elder.dialect
        scope.launch {
            voiceUi = voiceUi.copy(speech = SpeechState(busy = true, message = "正在合成语音…"))
            for (chunk in chunkForSpeech(trimmed)) {
                if (generation != speechGeneration) return@launch
                val audio = try {
                    withContext(Dispatchers.IO) { client.synthesizeSpeech(elder.id, chunk, dialect) }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    if (generation != speechGeneration) return@launch
                    voiceUi = voiceUi.copy(
                        speech = SpeechState(
                            busy = true,
                            degraded = true,
                            message = "电脑端语音不可用（${friendlyMessage(e)}），已改用手机自带语音，只有普通话。"
                        )
                    )
                    tts.speak(trimmed, TextToSpeech.QUEUE_FLUSH, null, "laoyou-assistant")
                    return@launch
                }
                if (generation != speechGeneration) return@launch
                voiceUi = voiceUi.copy(
                    speech = SpeechState(busy = true, message = "正在播放…", voice = audio.voice)
                )
                val path = withContext(Dispatchers.IO) { writeSpeechFile(audio.bytes) }
                if (generation != speechGeneration) return@launch
                playAudioFile(path)
                withContext(Dispatchers.IO) { runCatching { File(path).delete() } }
            }
            // 播完只清"正在播放"的状态，保留音色信息：
            // 用户往往正是想知道刚才念的是不是一个方言音色。
            if (generation == speechGeneration) {
                voiceUi = voiceUi.copy(speech = voiceUi.speech.copy(busy = false, message = null))
            }
        }
    }

    fun releaseRecognizer(engine: SpeechRecognizer) {
        runCatching { engine.destroy() }
        if (recognizer === engine) recognizer = null
    }

    /**
     * 用设备自带的语音识别（SpeechRecognizer）。
     *
     * 这是没有配置后端方言识别时的退化路径：不联网、不用 Key，
     * 但**只认普通话**，而且未装 Google 语音服务的国产机上通常压根没有引擎。
     * 这两件事都要如实告诉用户，不要让人以为按了没反应是自己的问题。
     */
    fun startDeviceRecognition(dialect: String) {
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            voiceUi = voiceUi.copy(
                mic = MicState(
                    message = "这台手机没有可用的语音识别服务（未装 Google 语音服务的手机会这样）。" +
                        "请改用文字输入；在电脑端配好方言识别后，这里的麦克风会自动换成录音上传。"
                )
            )
            return
        }
        recognizer?.let { engine -> runCatching { engine.destroy() } }
        val engine = SpeechRecognizer.createSpeechRecognizer(context)
        recognizer = engine
        voiceUi = voiceUi.copy(mic = MicState(busy = true, message = "正在启动语音识别…"))
        engine.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                voiceUi = voiceUi.copy(mic = MicState(recording = true, message = "正在听，请说话；说完再点一下麦克风。"))
            }

            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}

            override fun onEndOfSpeech() {
                voiceUi = voiceUi.copy(mic = MicState(busy = true, message = "正在识别…"))
            }

            override fun onError(error: Int) {
                releaseRecognizer(engine)
                // 用户主动取消时系统也会回调 ERROR_CLIENT，此时清掉提示更合适。
                voiceUi = voiceUi.copy(
                    mic = if (error == SpeechRecognizer.ERROR_CLIENT) MicState()
                    else MicState(message = deviceRecognitionErrorText(error))
                )
            }

            override fun onResults(results: Bundle?) {
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
                releaseRecognizer(engine)
                if (text.isBlank()) {
                    voiceUi = voiceUi.copy(mic = MicState(message = "没有听清，请再说一次。"))
                } else {
                    voiceUi = voiceUi.copy(mic = MicState())
                    // 只填进输入框、不自动发送：助手能创建提醒，
                    // 误听一句就自动提交可能生成一条错的安排。
                    assistantText = text
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, deviceRecognitionLanguage(dialect))
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        engine.startListening(intent)
    }

    /**
     * 录音：16kHz / 单声道 / 16bit 裸 PCM —— 正好是后端 /speech 要求的格式，
     * 不需要在手机上做任何转码。
     *
     * 走这条路（而不是设备识别）的原因：**只有后端能把方言归一化成普通话**。
     * 阻塞式读取，每 0.2 秒检查一次停止标志，所以必须在 IO 线程调用。
     */
    fun recordPcm(maxBytes: Int): ByteArray {
        val minBuffer = AudioRecord.getMinBufferSize(
            MIC_SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuffer <= 0) throw IllegalStateException("这台设备不支持 16kHz 录音")
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            MIC_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuffer, 12_800) * 2
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            throw IllegalStateException("麦克风不可用或被其它应用占用")
        }
        val out = ByteArrayOutputStream(maxBytes)
        val buffer = ByteArray(6_400) // 0.2 秒，决定"点结束"后多久真的停
        try {
            recorder.startRecording()
            while (out.size() < maxBytes && !recordingStop.get()) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read <= 0) break
                out.write(buffer, 0, read)
            }
        } finally {
            runCatching { recorder.stop() }
            recorder.release()
        }
        return out.toByteArray()
    }

    /** 录一段上传给后端识别（方言会被归一化成普通话书面语），结果填进输入框。 */
    fun startServerRecognition() {
        val client = api
        val elder = selectedElder
        if (client == null || elder == null) return
        val dialect = elder.dialect
        recordingStop.set(false)
        voiceUi = voiceUi.copy(mic = MicState(recording = true, seconds = 0, message = "正在录音，说完再点一下麦克风结束。"))
        micJob = scope.launch {
            // 秒数交给主线程上的计时器，不要在 IO 线程里写 Compose 状态。
            val ticker = launch {
                var elapsed = 0
                while (isActive && !recordingStop.get()) {
                    delay(1_000)
                    elapsed += 1
                    voiceUi = voiceUi.copy(mic = voiceUi.mic.copy(seconds = elapsed))
                }
            }
            val pcm = try {
                withContext(Dispatchers.IO) { recordPcm(MIC_MAX_BYTES) }
            } catch (e: CancellationException) {
                ticker.cancel()
                throw e
            } catch (e: Exception) {
                ticker.cancel()
                recordingStop.set(true)
                voiceUi = voiceUi.copy(mic = MicState(message = "录音没有成功：${friendlyMessage(e)}"))
                return@launch
            }
            ticker.cancel()
            recordingStop.set(true)
            if (pcm.size < MIC_MIN_BYTES) {
                voiceUi = voiceUi.copy(mic = MicState(message = "录得太短了，请把一句话说完再结束。"))
                return@launch
            }
            voiceUi = voiceUi.copy(mic = MicState(busy = true, message = "正在识别…"))
            val text = try {
                withContext(Dispatchers.IO) { client.transcribeSpeech(elder.id, dialect, pcm) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                voiceUi = voiceUi.copy(mic = MicState(message = "没有识别成功：${friendlyMessage(e)}"))
                return@launch
            }
            voiceUi = voiceUi.copy(
                mic = if (text.isBlank()) MicState(message = "没有听清，请再说一次。") else MicState()
            )
            if (text.isNotBlank()) assistantText = text
        }
    }

    // 声明顺序说明：launcher 的回调要用到上面两个 start*，所以只能放在它们之后；
    // toggleMic 又要用 launcher，放最后。
    val micPermissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val elder = selectedElder
        when {
            !granted -> voiceUi = voiceUi.copy(
                mic = MicState(message = "没有麦克风权限，无法语音输入。可以在系统设置里允许「老友」使用麦克风。")
            )
            elder != null && capabilities?.speechConfigured == true -> startServerRecognition()
            elder != null -> startDeviceRecognition(elder.dialect)
        }
    }

    /**
     * 麦克风按钮。同一个按钮承担"开始/结束"，并且按后端能力自动选择识别方式：
     * 配了方言识别就录音上传（能认方言），否则用设备自带识别（只有普通话）。
     */
    fun toggleMic() {
        val elder = selectedElder ?: return
        when {
            voiceUi.mic.busy -> voiceUi = voiceUi.copy(mic = MicState())
            voiceUi.mic.recording -> {
                // 两条路径的"停止"不同：录音要置停止标志，设备识别要 stopListening。
                recordingStop.set(true)
                recognizer?.let { engine -> runCatching { engine.stopListening() } }
                voiceUi = voiceUi.copy(mic = MicState(busy = true, message = "正在识别…"))
            }
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
                PackageManager.PERMISSION_GRANTED -> micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            capabilities?.speechConfigured == true -> startServerRecognition()
            else -> startDeviceRecognition(elder.dialect)
        }
    }

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

    // 登录后立刻拉一次能力清单。
    //
    // 首页那两条"当前走哪条语音路"的说明（助手卡片的识别路径、播报卡片的音色来源）
    // 都以 capabilities 为准。原先只有切到「设置」页才拉（见 AppShell 里的
    // AppTab.SETTINGS -> onLoadCapabilities），于是首页永远拿不到，只能按最悲观的
    // 分支渲染：明明服务端 edge-tts 可用、点播放出来的就是 zh-CN-XiaoxiaoNeural，
    // 卡片上却写"电脑端的语音合成不可用，播报会用手机自带语音，只有普通话"。
    // 文案与事实相反，恰恰违背了这一项要解决的"别让人误判音色"。
    LaunchedEffect(api?.token) {
        val client = api ?: return@LaunchedEffect
        perform({ client.getCapabilities() }) { capabilities = it }
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
                            // 播报改走服务端方言音色；不可用时 speakReply 内部会
                            // 自动降级到手机自带 TTS 并在界面上说明。
                            if (elderForAssistant.voiceEnabled && it.reply.isNotBlank()) speakReply(it.reply)
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
                    // 退出登录要把录音、播放、识别一并停掉，否则新账号登录后
                    // 可能听到上一个会话残留的语音。
                    stopSpeech()
                    micJob?.cancel()
                    micJob = null
                    recordingStop.set(true)
                    recognizer?.let { engine -> runCatching { engine.destroy() } }
                    recognizer = null
                    assistantText = ""
                    assistantResult = null
                },
                voice = voiceUi.copy(text = assistantText),
                onAssistantTextChange = { assistantText = it },
                onToggleMic = ::toggleMic,
                onReplayReply = {
                    val reply = assistantResult?.reply.orEmpty()
                    if (selectedElder?.voiceEnabled == true && reply.isNotBlank()) speakReply(reply)
                },
                onStopSpeech = ::stopSpeech,
                onPlayBroadcast = { broadcast ->
                    if (selectedElder?.voiceEnabled == true) speakReply(broadcast.text)
                },
                onMarkBroadcastPlayed = { broadcast ->
                    api?.let { client ->
                        perform({ client.markBroadcastPlayed(broadcast.id) }) { updated ->
                            dashboard = dashboard?.copy(
                                broadcasts = dashboard?.broadcasts?.map { if (it.id == updated.id) updated else it }
                                    ?: listOf(updated)
                            )
                        }
                    }
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
    voice: AssistantVoiceUi,
    onAssistantTextChange: (String) -> Unit,
    onToggleMic: () -> Unit,
    onReplayReply: () -> Unit,
    onStopSpeech: () -> Unit,
    onPlayBroadcast: (Broadcast) -> Unit,
    onMarkBroadcastPlayed: (Broadcast) -> Unit,
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
                AppTab.HOME -> HomeScreen(
                    elder = elder,
                    dashboard = dashboard,
                    observations = observations,
                    snapshot = snapshot,
                    snapshotAt = snapshotAt,
                    snapshotLoading = snapshotLoading,
                    assistantResult = assistantResult,
                    loading = loading,
                    voice = voice.copy(
                        voiceEnabled = elder.voiceEnabled,
                        speechNote = speechRouteNote(capabilities, elder.dialect),
                        broadcastNote = broadcastRouteNote(capabilities, elder.dialect),
                        // 播报列表与回传权限都按"今天、这个老人"来定。
                        broadcasts = dashboard?.broadcasts.orEmpty(),
                        canReportPlayed = user.role == "elder" || user.role == "admin",
                        // 老人把语音播报关掉时，不该再显示播报/试听按钮。
                        speech = if (elder.voiceEnabled) voice.speech else SpeechState()
                    ),
                    onRefresh = onRefreshDashboard,
                    onLoadSnapshot = onLoadSnapshot,
                    onAssistant = onAssistant,
                    onAssistantTextChange = onAssistantTextChange,
                    onToggleMic = onToggleMic,
                    onReplayReply = onReplayReply,
                    onStopSpeech = onStopSpeech,
                    onPlayBroadcast = onPlayBroadcast,
                    onMarkBroadcastPlayed = onMarkBroadcastPlayed
                )
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
    voice: AssistantVoiceUi,
    onRefresh: () -> Unit,
    onLoadSnapshot: () -> Unit,
    onAssistant: (String, String?) -> Unit,
    onAssistantTextChange: (String) -> Unit,
    onToggleMic: () -> Unit,
    onReplayReply: () -> Unit,
    onStopSpeech: () -> Unit,
    onPlayBroadcast: (Broadcast) -> Unit,
    onMarkBroadcastPlayed: (Broadcast) -> Unit
) {
    val assistantText = voice.text
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
                    Text("你好通通 · 文字与语音入口", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("可以查询天气、健康和提醒；未配置的能力会明确提示。", color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 5.dp))
                    Text(
                        voice.speechNote,
                        color = Color(0xFF37485C),
                        fontSize = 15.sp,
                        modifier = Modifier.padding(top = 6.dp)
                    )
                    OutlinedTextField(
                        assistantText,
                        onAssistantTextChange,
                        label = { Text("例如：查天气、查询提醒、每天晚上八点提醒我吃药") },
                        modifier = Modifier.fillMaxWidth().padding(top = 10.dp)
                    )
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        OutlinedButton(onClick = onToggleMic) {
                            Text(
                                when {
                                    voice.mic.recording -> "⏹ 结束录音"
                                    voice.mic.busy -> "… 识别中"
                                    else -> "🎤 语音输入"
                                }
                            )
                        }
                        Spacer(Modifier.weight(1f))
                        Button(
                            onClick = { if (assistantText.isNotBlank()) { onAssistant(assistantText.trim(), null); onAssistantTextChange("") } },
                            enabled = assistantText.isNotBlank()
                        ) { Text("发送") }
                    }
                    if (voice.mic.recording && voice.mic.seconds > 0) {
                        Text("已录 ${voice.mic.seconds} 秒", color = Color(0xFF884005), fontSize = 16.sp, modifier = Modifier.padding(top = 6.dp))
                    }
                    voice.mic.message?.let { text ->
                        Text(text, color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 6.dp))
                    }
                    // 播报状态：说清用的是电脑端音色还是手机自带语音。
                    //
                    // 这一段必须放在 assistantResult 之外。播报卡片上的"播放"只做
                    // 试听（speakReply），不产生 assistantResult；早先写在里面时，
                    // 点播放会合成、会出声，但界面一个字都不显示 —— 用户没法判断
                    // 刚才响的是不是方言音色，恰恰背离了这一项要解决的问题。
                    voice.speech.message?.let { message ->
                        Text(message, color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 6.dp))
                    }
                    voice.speech.voice?.let { used ->
                        Text("音色：$used", color = Color(0xFF37485C), fontSize = 15.sp, modifier = Modifier.padding(top = 3.dp))
                    }
                    if (voice.speech.busy) {
                        OutlinedButton(onClick = onStopSpeech, modifier = Modifier.padding(top = 6.dp)) { Text("⏹ 停止播报") }
                    }
                    assistantResult?.let { result ->
                        HorizontalDivider(Modifier.padding(vertical = 10.dp))
                        Text(result.reply, fontSize = 17.sp)
                        Text("服务：${assistantModeLabel(result.mode)} · ${assistantActionLabel(result.action)}", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 5.dp))
                        Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (voice.voiceEnabled) {
                                OutlinedButton(onClick = onReplayReply, enabled = !voice.speech.busy) { Text("🔊 重念一遍") }
                            } else {
                                Text("老人档案里关闭了语音播报，因此不会自动朗读。", color = Color(0xFF37485C), fontSize = 16.sp)
                            }
                        }
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
                    Text("启用提醒：${dashboard.reminders.count { it.enabled }} 条；今日播报：${voice.broadcasts.size} 条", modifier = Modifier.padding(top = 8.dp))
                    Text(
                        voice.broadcastNote,
                        color = Color(0xFF37485C),
                        fontSize = 15.sp,
                        modifier = Modifier.padding(top = 6.dp)
                    )
                    Text("APP运行期间每30秒轮询通知并尝试发本地通知；APP被系统终止后不保证后台提醒。", color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 8.dp))
                    if (voice.broadcasts.isEmpty()) {
                        Text("今天还没有播报记录。用药提醒到点后会生成一条。", color = Color(0xFF37485C), fontSize = 16.sp, modifier = Modifier.padding(top = 10.dp))
                    }
                    voice.broadcasts.forEach { broadcast ->
                        HorizontalDivider(Modifier.padding(vertical = 10.dp))
                        Text("${broadcastKindLabel(broadcast.kind)} · ${formatTime(broadcast.scheduledAt)}", fontSize = 16.sp, color = Color(0xFF37485C))
                        Text(broadcast.text, fontSize = 17.sp, modifier = Modifier.padding(top = 4.dp))
                        Text(
                            if (broadcast.playedAt != null) "已播报：${formatTime(broadcast.playedAt)}" else "尚未播报",
                            fontSize = 15.sp,
                            color = if (broadcast.playedAt != null) Color(0xFF1B5E20) else Color(0xFF884005),
                            modifier = Modifier.padding(top = 4.dp)
                        )
                        Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                onClick = { onPlayBroadcast(broadcast) },
                                enabled = voice.voiceEnabled && !voice.speech.busy
                            ) { Text(if (broadcast.playedAt != null) "🔊 再听一次" else "🔊 播放") }
                            if (voice.canReportPlayed && broadcast.playedAt == null) {
                                OutlinedButton(
                                    onClick = { onMarkBroadcastPlayed(broadcast) },
                                    enabled = !loading
                                ) { Text("标记已播") }
                            }
                        }
                    }
                    if (!voice.canReportPlayed) {
                        // 这不是缺陷，是契约：后端只接受老人端回传播报状态。
                        Text(
                            "「标记已播」只有老人端账号能回传（后端契约如此），当前身份只能试听。",
                            color = Color(0xFF37485C),
                            fontSize = 15.sp,
                            modifier = Modifier.padding(top = 10.dp)
                        )
                    }
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
                Text("当前方言：${dialectLabel(elder.dialect)}", modifier = Modifier.padding(top = 8.dp))
                Text(
                    "${broadcastRouteNote(capabilities, elder.dialect)}。",
                    fontSize = 16.sp,
                    color = Color(0xFF37485C),
                    modifier = Modifier.padding(top = 4.dp)
                )
                Text(
                    "${speechRouteNote(capabilities, elder.dialect)}。",
                    fontSize = 16.sp,
                    color = Color(0xFF37485C),
                    modifier = Modifier.padding(top = 4.dp)
                )
                Text("方言能力会标明可用状态；系统普通话识别不会冒充方言。", fontSize = 16.sp, color = Color(0xFF37485C), modifier = Modifier.padding(top = 4.dp))
            }
        }
        capabilities?.let { cap ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("服务能力", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("助手：${assistantModeLabel(cap.assistantMode)}")
                    Text("语音识别：${speechProviderLabel(cap.speechProvider)} · ${if (cap.speechConfigured) "可用" else "未配置"}")
                    Text("语音播报：${if (cap.ttsServerSide) "电脑端合成（支持方言音色）" else "手机自带（只有普通话）"}")
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
    // Android 13（API 33）起发通知需要运行时拿到 POST_NOTIFICATIONS，
    // 没拿到时 notify() 不会弹出任何东西。这里显式判一次：既让 lint 满意，
    // 也把"通知没来"这件事从玄学变成可解释的行为（界面上已写明是"尝试发"）。
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) {
        return
    }
    val builder = NotificationCompat.Builder(context, "laoyou-alerts")
        .setSmallIcon(android.R.drawable.ic_dialog_alert)
        .setContentTitle("老友异常通知 · ${notificationTargetLabel(notification.target)}")
        .setContentText("有新的${notificationStatusLabel(notification.status)}通知，请打开老友查看事件时间线。")
        .setPriority(NotificationCompat.PRIORITY_HIGH)
        .setAutoCancel(true)
    runCatching { NotificationManagerCompat.from(context).notify(notification.id.hashCode(), builder.build()) }
}
