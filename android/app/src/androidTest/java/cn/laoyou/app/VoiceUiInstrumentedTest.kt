package cn.laoyou.app

import android.Manifest
import android.os.SystemClock
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiSelector
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 安卓端语音界面的插桩用例（2026-10-03 随"三项语音能力"一起加入）。
 *
 * 需要一台能访问后端的设备/模拟器，以及已经启动的老友后端：
 *
 *     ./gradlew :app:connectedDebugAndroidTest \
 *       -Pandroid.testInstrumentationRunnerArguments.class=cn.laoyou.app.VoiceUiInstrumentedTest \
 *       -Pandroid.testInstrumentationRunnerArguments.baseUrl=http://10.0.2.2:8000/api
 *
 * 为什么必须用插桩而不是 `adb shell input tap`：实测无头模拟器里
 * 注入的触摸事件**进不了 Compose 的 Button**（EditText 能拿到焦点，按钮
 * 的 clickable 完全不响应，DPAD/TAB 导航也不行）。而插桩走的是语义动作
 * （performClick → ACTION_CLICK），与真实触摸管线无关，因此在模拟器里可靠。
 *
 * 用例只做"看得到的断言"，不去伪造业务数据；后端没配 QWEN_API_KEY 时
 * 应当仍然全绿 —— 因为此时界面应当**如实说明**用的是设备识别/手机自带语音，
 * 而不是假装方言可用。这条"不假装"正是用例要守住的行为。
 */
@RunWith(AndroidJUnit4::class)
class VoiceUiInstrumentedTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @get:Rule
    val permissionRule: GrantPermissionRule = GrantPermissionRule.grant(
        Manifest.permission.CAMERA,
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.POST_NOTIFICATIONS
    )

    /** 文本是否存在；substring=true 允许部分匹配，避免因文案微调而误报。 */
    private fun exists(text: String, substring: Boolean = true): Boolean =
        composeRule.onAllNodes(hasText(text, substring = substring), useUnmergedTree = true)
            .fetchSemanticsNodes().isNotEmpty()

    /** 等某个文本出现，返回是否等到。 */
    private fun waitText(text: String, timeoutMs: Long = 15_000, substring: Boolean = true): Boolean {
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        while (SystemClock.uptimeMillis() < deadline) {
            if (exists(text, substring)) return true
            SystemClock.sleep(250)
        }
        return false
    }

    /** 在若干候选文案里等第一个出现的，返回它；都不出现返回 null。 */
    private fun waitAny(vararg candidates: String, timeoutMs: Long = 15_000): String? {
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        while (SystemClock.uptimeMillis() < deadline) {
            candidates.firstOrNull { exists(it) }?.let { return it }
            SystemClock.sleep(250)
        }
        return null
    }

    /**
     * 点一个节点。用 performClick（语义动作），不依赖系统注入。
     *
     * 必须先 `performScrollTo()`：首页外层是 `verticalScroll` 的 `Column`，
     * 视口之外的子项**仍然在语义树里**（所以 `waitText` 找得到），但
     * `performClick` 会把触摸注入到节点中心的**屏幕坐标**上 —— 首屏之下
     * 就是屏幕外，事件静默失效，表现为"点了没反应、也不报错"。
     * 实测助手卡片与播报卡片都在首屏之下，不滚动根本点不动。
     *
     * 没有可滚动祖先时（例如底部导航栏的「设置」）`performScrollTo` 会抛异常，
     * 这里吞掉即可 —— 那些节点本来就可见。
     */
    private fun clickText(text: String, substring: Boolean = true) {
        val node = composeRule.onNodeWithText(text, substring = substring)
        runCatching { node.performScrollTo() }
        node.performClick()
    }

    private fun login() {
        val inputs = composeRule.onAllNodes(
            androidx.compose.ui.test.hasSetTextAction(), useUnmergedTree = true
        )
        val baseUrl = InstrumentationRegistry.getArguments().getString("baseUrl")
            // 模拟器里 10.0.2.2 是宿主机回环的固定别名；真机用 adb reverse 时传 127.0.0.1。
            ?: "http://10.0.2.2:8000/api"
        inputs[0].performTextClearance()
        inputs[0].performTextInput(baseUrl)
        inputs[1].performTextInput("child")
        inputs[2].performTextInput("Laoyou123!")
        composeRule.onNodeWithText("登录").performClick()
        dismissSystemDialogIfShown(30_000)
        assertTrue(
            "登录后没有进入首页，请确认后端已启动、baseUrl 正确",
            waitText("健康与照护概览", 30_000)
        )
        dismissSystemDialogIfShown()
    }

    private fun dismissSystemDialogIfShown(timeoutMs: Long = 0) {
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        do {
            for (label in listOf("Allow", "允许", "确定", "OK")) {
                val button = device.findObject(UiSelector().text(label))
                if (button.exists()) {
                    button.click()
                    return
                }
            }
            if (timeoutMs == 0L) return
            SystemClock.sleep(100)
        } while (SystemClock.uptimeMillis() < deadline)
    }

    private fun captureEvidence(name: String) {
        val directory = InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null)!!
        UiDevice.getInstance(InstrumentationRegistry.getInstrumentation()).takeScreenshot(java.io.File(directory, name))
    }

    @Test
    fun voiceInputAndBroadcastPlaybackAreHonestAboutCapabilities() {
        login()

        // ---- 助手卡片：新增了麦克风按钮与"当前走哪条识别路"的说明 ----
        assertTrue("助手卡片应有语音输入按钮", waitText("语音输入"))
        // 任务要求之一：没配服务端方言识别时，界面要说清是设备识别、只认普通话。
        val routeNote = waitAny(
            "手机自带的语音识别",
            "录音后发给电脑端识别",
            timeoutMs = 5_000
        )
        assertTrue("助手卡片应说明当前识别路径", routeNote != null)
        println("[断言] 识别路径说明：$routeNote")
        captureEvidence("voice-01-assistant.png")

        // ---- 点麦克风：必须有明确反馈，不能静默 ----
        clickText("语音输入")
        val micFeedback = waitAny(
            "正在听",
            "请说话",
            "正在录音",
            "正在启动语音识别",
            "没有可用的语音识别服务",
            "没有麦克风权限",
            timeoutMs = 20_000
        )
        assertTrue(
            "点麦克风后没有任何状态提示，说明按钮没有生效或失败被吞掉了",
            micFeedback != null
        )
        println("[断言] 点麦克风后的反馈：$micFeedback")
        captureEvidence("voice-02-mic.png")

        // ---- 播报卡片：列表、播放按钮、以及角色边界说明 ----
        assertTrue("应有「提醒与播报」卡片", waitText("提醒与播报", 10_000))
        assertTrue("应显示今日播报条数", waitText("今日播报", 10_000))
        // 任务要求之一：播报要说清用的是服务端方言音色还是手机自带语音。
        val ttsNote = waitAny(
            "播报用电脑端合成",
            "播报会用手机自带语音",
            timeoutMs = 5_000
        )
        assertTrue("播报卡片应说明用的是哪一套语音", ttsNote != null)
        println("[断言] 播报路径说明：$ttsNote")

        // child 角色不能回传播报状态（后端契约只放行 elder/admin），
        // 界面必须把这件事说出来，而不是给一个点了会 403 的按钮。
        assertTrue(
            "子女账号应看到「标记已播只有老人端能回传」的说明",
            waitText("只有老人端", 5_000)
        )
        captureEvidence("voice-03-broadcast.png")
    }

    @Test
    fun broadcastPlaybackUsesServerVoiceWhenAvailable() {
        login()

        val playButton = waitAny("播放", "再听一次", timeoutMs = 10_000)
        assertTrue("今日应有可播放的播报（否则先造一条到点的用药提醒）", playButton != null)
        clickText(playButton!!)

        // 合成成功时应用会把响应头 X-Voice 显示出来（音色：xxx）。
        // 这一步是"手机真的用上了服务端方言音色"的唯一证据。
        val outcome = waitAny(
            "音色：",
            "电脑端语音不可用",
            timeoutMs = 40_000
        )
        assertTrue(
            "点了播放但既没有音色信息也没有降级说明，说明播报链路没有反馈",
            outcome != null
        )
        println("[断言] 播报结果：$outcome")

        // 一致性防线：既然真的用服务端音色念出来了，卡片上就不能同时写着
        // "电脑端的语音合成不可用" —— 文案与事实相反。
        // 这曾经真发生过：capabilities 只在切到"设置"页时才拉，首页拿到 null，
        // 于是首页按最悲观分支渲染，一边播着 zh-CN-XiaoxiaoNeural 一边说不可用。
        if (outcome != null && outcome.startsWith("音色：")) {
            assertTrue(
                "已经用服务端音色播出来了，播报卡片却仍写「电脑端的语音合成不可用」" +
                    "（说明首页没有加载能力清单）",
                !exists("电脑端的语音合成不可用")
            )
        }
        captureEvidence("voice-04-playback.png")
    }

    @Test
    fun settingsReportsSpeechAndTtsProvider() {
        login()
        composeRule.onNodeWithText("设置", substring = true).performClick()
        assertTrue("设置页应显示服务能力", waitText("服务能力", 15_000))
        assertTrue(
            "设置页应分别报出语音识别与语音播报的来源",
            waitText("语音识别：", 5_000) && waitText("语音播报：", 5_000)
        )
        // 方言名应当显示成「粤语」而不是 yue-HK。
        assertTrue("设置页应显示可读的方言名", waitText("当前方言：", 5_000))
        captureEvidence("voice-05-settings.png")
    }
}
