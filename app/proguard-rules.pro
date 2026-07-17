# SignalDVR ProGuard Rules
-keepattributes Signature
-keepattributes *Annotation*
-keep class com.signaldvr.app.api.** { *; }
-keep class com.google.gson.** { *; }
