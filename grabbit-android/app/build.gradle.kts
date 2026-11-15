import java.net.URI
import java.security.MessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// youtubedl-android bundles Python 3.12, ffmpeg 7.1 and QuickJS as native libraries (GPL-3.0).
val youtubedlAndroid = "0.18.1"
// The yt-dlp the app ships with. Keep it the same as grabbit-web/pyproject.toml; the app updates itself after that.
val ytdlpVersion = "2026.10.26"
val ytdlpSha256 = "e51e69e89489f199530f13d32a4342da3ddb2cf34842f364603a066db05afbf3"

// The release workflow passes the tag's version (-PgrabbitVersion=1.2.0); 1.2.3 becomes versionCode 10203.
val grabbitVersion = (findProperty("grabbitVersion") as String?)?.removePrefix("v") ?: "1.2.0"
val grabbitVersionCode = grabbitVersion.split('.').map { it.takeWhile(Char::isDigit).toIntOrNull() ?: 0 }
    .let { (it + listOf(0, 0, 0)).take(3) }.let { (major, minor, patch) -> major * 10000 + minor * 100 + patch }

// Release signing, from the environment (GitHub secrets in the release workflow; see
// packaging/make_release_key.sh). Without them, release builds come out unsigned.
val releaseKeystore = System.getenv("GRABBIT_KEYSTORE")?.takeIf { it.isNotBlank() && file(it).isFile }
val releaseKeystorePassword = System.getenv("GRABBIT_KEYSTORE_PASSWORD")

android {
    namespace = "com.grabbit.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.grabbit.android"
        minSdk = 29 // Android 10: saving to Downloads through MediaStore needs no storage permission
        targetSdk = 35
        versionCode = grabbitVersionCode
        versionName = grabbitVersion
    }

    splits.abi {
        isEnable = true
        reset()
        include("arm64-v8a", "armeabi-v7a", "x86_64")
        isUniversalApk = false
    }

    // youtubedl-android runs python/ffmpeg/qjs straight out of nativeLibraryDir,
    // so the .so files must be extracted at install time.
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    signingConfigs {
        if (releaseKeystore != null && !releaseKeystorePassword.isNullOrEmpty()) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = releaseKeystorePassword
                keyAlias = System.getenv("GRABBIT_KEY_ALIAS")?.takeIf { it.isNotBlank() } ?: "grabbit"
                keyPassword = releaseKeystorePassword // PKCS12 keystores use one password for both
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
            // Shrinks the Compose/icon code from ~66 MB of dex to a few MB.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    jvmToolchain(17)
}

// ---------------------------------------------------------------------------------------------
// The download engine: grabbit-web's own Python modules plus the Android bridge, as app assets.
// ---------------------------------------------------------------------------------------------

abstract class SyncEngineTask : DefaultTask() {
    @get:InputFiles
    abstract val webModules: ConfigurableFileCollection

    @get:InputFile
    abstract val bridge: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun sync() {
        val root = outputDir.get().asFile.resolve("engine")
        root.deleteRecursively()
        val app = root.resolve("app").apply { mkdirs() }
        val digest = MessageDigest.getInstance("SHA-256")
        webModules.files.sortedBy { it.name }.forEach { f ->
            f.copyTo(app.resolve(f.name))
            digest.update(f.name.toByteArray())
            digest.update(f.readBytes())
        }
        val b = bridge.get().asFile
        b.copyTo(root.resolve(b.name))
        digest.update(b.readBytes())
        root.resolve("VERSION").writeText(digest.digest().joinToString("") { "%02x".format(it) })
    }
}

abstract class FetchYtDlpTask : DefaultTask() {
    @get:Input
    abstract val version: Property<String>

    @get:Input
    abstract val sha256: Property<String>

    @get:Internal
    abstract val cacheDir: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun fetch() {
        val ver = version.get()
        val cached = cacheDir.get().asFile.resolve("yt-dlp-$ver")
        fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        if (!cached.isFile || hash(cached.readBytes()) != sha256.get()) {
            cached.parentFile.mkdirs()
            val url = "https://github.com/yt-dlp/yt-dlp/releases/download/$ver/yt-dlp"
            logger.lifecycle("Downloading $url")
            val bytes = URI(url).toURL().openStream().use { it.readBytes() }
            check(hash(bytes) == sha256.get()) { "yt-dlp $ver doesn't match its pinned SHA-256" }
            cached.writeBytes(bytes)
        }
        val out = outputDir.get().asFile.resolve("ytdlp")
        out.deleteRecursively()
        out.mkdirs()
        cached.copyTo(out.resolve("yt-dlp-$ver.zip"))
    }
}

val webApp = rootProject.layout.projectDirectory.dir("../grabbit-web/app")
val syncEngine = tasks.register<SyncEngineTask>("syncEngine") {
    webModules.from(
        listOf("__init__", "config", "cookies", "compat", "formats", "jobs", "resolve", "ytdl", "security")
            .map { webApp.file("$it.py") }
    )
    bridge.set(rootProject.layout.projectDirectory.file("engine/grabbit_engine.py"))
    outputDir.set(layout.buildDirectory.dir("generated/engineAssets"))
}
val fetchYtDlp = tasks.register<FetchYtDlpTask>("fetchYtDlp") {
    version.set(ytdlpVersion)
    sha256.set(ytdlpSha256)
    cacheDir.set(rootProject.layout.projectDirectory.dir(".gradle/yt-dlp"))
    outputDir.set(layout.buildDirectory.dir("generated/ytdlpAssets"))
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(syncEngine, SyncEngineTask::outputDir)
        variant.sources.assets?.addGeneratedSourceDirectory(fetchYtDlp, FetchYtDlpTask::outputDir)
    }
}

dependencies {
    implementation("io.github.junkfood02.youtubedl-android:library:$youtubedlAndroid")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:$youtubedlAndroid")

    val composeBom = platform("androidx.compose:compose-bom:2025.10.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")
    implementation("io.coil-kt.coil3:coil-compose:3.3.0")
    implementation("io.coil-kt.coil3:coil-network-okhttp:3.3.0")

    testImplementation("junit:junit:4.13.2")
}
