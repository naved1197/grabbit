# youtubedl-android maps yt-dlp's JSON with Jackson (reflection) and loads its native pieces by name.
-keep class com.yausername.** { *; }
-keep class com.fasterxml.jackson.** { *; }
-dontwarn com.fasterxml.jackson.**
-dontwarn org.apache.commons.**

# Grabbit's engine models are read with kotlinx.serialization (its rules ship with the library);
# keep the names readable in crash reports.
-keepnames class com.grabbit.android.** { *; }
