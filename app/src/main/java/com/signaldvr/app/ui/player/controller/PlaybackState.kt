package com.signaldvr.app.ui.player.controller

data class PlaybackState(
    val isLive: Boolean = true,
    val isRecordingPlayback: Boolean = false,
    val currentUrl: String = "",
    val liveUrl: String = "",
    val behindLiveSeconds: Int = 0,
    val isPlaying: Boolean = false,
    val isReconnecting: Boolean = false
) {
    fun positionLabel(): String {
        if (isRecordingPlayback) return ""

        if (isLive || behindLiveSeconds <= 0) return "● LIVE"

        if (behindLiveSeconds < 60) return "◀ ${behindLiveSeconds}s"

        val minutes = behindLiveSeconds / 60
        val seconds = behindLiveSeconds % 60

        return if (seconds == 0) {
            "◀ ${minutes}m"
        } else {
            "◀ ${minutes}m ${seconds}s"
        }
    }
}