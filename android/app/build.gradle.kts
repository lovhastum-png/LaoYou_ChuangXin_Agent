// 注意：脚本里不能写 `java.util.Properties` —— `java` 会被 JavaPluginExtension
// 的访问器抢占，必须显式 import。
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// 预置服务地址：打包时把它写进 APK，装好打开就带出来，不用再手填。
// 取值顺序：-PlaoyouBaseUrl=... > android/local.properties 的 laoyou.baseUrl > 空。
// 放在 local.properties 是因为它已被 .gitignore 排除，地址属于本机配置；
// 留空则保持原来的行为（登录页空着，用户必须自己填）。
val laoyouLocalProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { stream -> load(stream) }
}
val laoyouDefaultBaseUrl: String =
    (project.findProperty("laoyouBaseUrl") as? String)?.trim()?.takeIf { it.isNotEmpty() }
        ?: laoyouLocalProperties.getProperty("laoyou.baseUrl", "").trim()

android {
    namespace = "cn.laoyou.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "cn.laoyou.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField(
            "String",
            "DEFAULT_BASE_URL",
            "\"${laoyouDefaultBaseUrl.replace("\\", "\\\\").replace("\"", "\\\"")}\""
        )
    }

    signingConfigs.getByName("debug") {
        storeFile = rootProject.file("../runtime/android-debug.keystore")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        // 预置服务地址通过 BuildConfig.DEFAULT_BASE_URL 传给登录页；
        // AGP 8 起默认关闭 BuildConfig 生成，必须显式打开。
        buildConfig = true
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.08.00")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:rules:1.6.1")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
}
