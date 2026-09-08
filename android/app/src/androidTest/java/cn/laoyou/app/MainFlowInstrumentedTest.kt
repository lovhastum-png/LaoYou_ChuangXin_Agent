package cn.laoyou.app

import android.os.SystemClock
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiSelector
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.fail
import org.junit.runner.RunWith

/**
 * 需要测试设备通过 adb reverse 访问宿主机 127.0.0.1:8000，使用本地虚构演示账号。
 * 该用例检查真实登录、老人首页、异常列表以及通话网页内容，不注入或伪造业务数据。
 */
@RunWith(AndroidJUnit4::class)
class MainFlowInstrumentedTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun loginAndReadElderAndEventList() {
        val inputs = composeRule.onAllNodes(hasSetTextAction(), useUnmergedTree = true)
        inputs[0].performTextClearance()
        inputs[0].performTextInput("http://127.0.0.1:8000/api")
        inputs[1].performTextInput("child")
        inputs[2].performTextInput("Laoyou123!")
        composeRule.onNodeWithText("登录").performClick()

        composeRule.waitUntil(20_000) {
            composeRule.onAllNodes(hasText("健康与照护概览"), useUnmergedTree = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        dismissNotificationPermissionIfShown()
        composeRule.onNodeWithText("健康与照护概览").assertIsDisplayed()
        composeRule.onNodeWithText("李奶奶").assertIsDisplayed()
        captureEvidence("home-final.png")

        composeRule.onNodeWithText("异常").performClick()
        composeRule.waitUntil(20_000) {
            composeRule.onAllNodes(hasText("异常事件"), useUnmergedTree = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("异常事件").assertIsDisplayed()
        captureEvidence("events-final.png")

        composeRule.onNodeWithText("通话").performClick()
        composeRule.waitUntil(20_000) {
            composeRule.onAllNodes(hasText("发起视频通话"), useUnmergedTree = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("发起视频通话").performClick()
        grantMediaPermissionsIfShown()
        composeRule.waitUntil(20_000) {
            composeRule.onAllNodes(hasText("挂断"), useUnmergedTree = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        waitForWebCallContent()
        assertLocalVideoCapture()
        captureEvidence("call-final.png")
        composeRule.onNodeWithText("挂断").performClick()
    }

    private fun dismissNotificationPermissionIfShown() {
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
        val englishAllow = device.findObject(UiSelector().text("Allow"))
        if (englishAllow.exists()) {
            englishAllow.click()
            return
        }
        val chineseAllow = device.findObject(UiSelector().text("允许"))
        if (chineseAllow.exists()) chineseAllow.click()
    }

    private fun captureEvidence(name: String) {
        val directory = InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null)!!
        UiDevice.getInstance(InstrumentationRegistry.getInstrumentation()).takeScreenshot(java.io.File(directory, name))
    }

    private fun grantMediaPermissionsIfShown() {
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
        for (permissionIndex in 0 until 2) {
            var clicked = false
            for (attempt in 0 until 20) {
                val whileUsing = device.findObject(UiSelector().text("While using the app"))
                val onlyThisTime = device.findObject(UiSelector().text("Only this time"))
                val chineseWhileUsing = device.findObject(UiSelector().text("使用应用时"))
                when {
                    whileUsing.exists() -> { whileUsing.click(); clicked = true }
                    onlyThisTime.exists() -> { onlyThisTime.click(); clicked = true }
                    chineseWhileUsing.exists() -> { chineseWhileUsing.click(); clicked = true }
                }
                if (clicked) break
                SystemClock.sleep(250)
            }
        }
    }

    private fun waitForWebCallContent() {
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
        val deadline = SystemClock.uptimeMillis() + 20_000
        while (SystemClock.uptimeMillis() < deadline) {
            if (device.findObject(UiSelector().text("我的画面")).exists()) return
            if (device.findObject(UiSelector().textContains("登录")).exists()) {
                fail("通话页出现登录内容，可能重载了已清除令牌的 URL")
            }
            SystemClock.sleep(250)
        }
        val evidence = InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null)!!
        device.takeScreenshot(java.io.File(evidence, "call-failure.png"))
        device.dumpWindowHierarchy(java.io.File(evidence, "call-ui.xml"))
        val latch = java.util.concurrent.CountDownLatch(1)
        composeRule.activityRule.scenario.onActivity { activity ->
            fun findWeb(view: android.view.View): android.webkit.WebView? {
                if (view is android.webkit.WebView) return view
                if (view is android.view.ViewGroup) for (index in 0 until view.childCount) {
                    findWeb(view.getChildAt(index))?.let { return it }
                }
                return null
            }
            findWeb(activity.findViewById(android.R.id.content))?.let { web ->
                web.evaluateJavascript("JSON.stringify({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,ready:document.readyState,stage:document.querySelector('.remote-stage')?.getBoundingClientRect().height,text:document.body.innerText})") { result ->
                    java.io.File(evidence, "call-metrics.json").writeText(result)
                    latch.countDown()
                }
            } ?: latch.countDown()
        }
        latch.await(3, java.util.concurrent.TimeUnit.SECONDS)
        fail("通话页未出现“我的画面”网页内容")
    }

    private fun assertLocalVideoCapture() {
        val deadline = SystemClock.uptimeMillis() + 15_000
        while (SystemClock.uptimeMillis() < deadline) {
            val completed = java.util.concurrent.CountDownLatch(1)
            val captured = java.util.concurrent.atomic.AtomicBoolean(false)
            composeRule.activityRule.scenario.onActivity { activity ->
                fun findWeb(view: android.view.View): android.webkit.WebView? {
                    if (view is android.webkit.WebView) return view
                    if (view is android.view.ViewGroup) for (index in 0 until view.childCount) {
                        findWeb(view.getChildAt(index))?.let { return it }
                    }
                    return null
                }
                findWeb(activity.findViewById(android.R.id.content))?.evaluateJavascript(
                    "Boolean(Array.from(document.querySelectorAll('video')).some(v => v.videoWidth > 0 && v.srcObject && v.srcObject.getVideoTracks().some(t => t.readyState === 'live') && v.srcObject.getAudioTracks().some(t => t.readyState === 'live')))"
                ) { result -> captured.set(result == "true"); completed.countDown() } ?: completed.countDown()
            }
            completed.await(3, java.util.concurrent.TimeUnit.SECONDS)
            if (captured.get()) return
            SystemClock.sleep(250)
        }
        val evidence = InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null)!!
        UiDevice.getInstance(InstrumentationRegistry.getInstrumentation()).takeScreenshot(java.io.File(evidence, "capture-failure.png"))
        fail("WebView没有获得可解码视频及live音频轨道")
    }
}
