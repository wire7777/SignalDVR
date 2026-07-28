package com.signaldvr.app.ui.player.controller

import android.content.Context
import android.os.Handler
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.ui.player.engine.PlayerEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class PlaybackController(
    private val context: Context,
    private val playerEngine: PlayerEngine,
    private val handler: Handler
) {
    companion object {
        private const val LIVE_SEEK_DEBOUNCE_MS = 350L
    }

    var state = PlaybackState()
        private set

    private var pendingLiveDeltaSeconds = 0
    private var pendingLiveSeekRunnable: Runnable? = null

    fun updateState(newState: PlaybackState) {
        state = newState
    }

    fun updatePlaybackState(
        isLive: Boolean,
        isRecordingPlayback: Boolean,
        currentUrl: String,
        liveUrl: String,
        behindLiveSeconds: Int,
        isReconnecting: Boolean
    ): PlaybackState {
        state = PlaybackState(
            isLive = isLive,
            isRecordingPlayback = isRecordingPlayback,
            currentUrl = currentUrl,
            liveUrl = liveUrl,
            behindLiveSeconds = behindLiveSeconds,
            isPlaying = playerEngine.isPlaying(),
            isReconnecting = isReconnecting
        )

        return state
    }

    fun positionLabel(): String {
        return state.positionLabel()
    }

    suspend fun startLiveStream(
        channelNum: String,
        onError: (String) -> Unit,
        onStarted: (String) -> Unit
    ) {
        try {
            val resp = ApiClient
                .getApi(context)
                .startLiveStream(channelNum)

            if (!resp.ok) {
                handler.post {
                    onError(
                        "Could not start stream"
                    )
                }
                return
            }

            val url =
                resp.streamUrl
                    ?: resp.hlsUrl
                    ?: resp.rawUrl
                    ?: ""

            if (url.isBlank()) {
                handler.post {
                    onError(
                        "No stream URL from server"
                    )
                }
                return
            }

            state = state.copy(
                isLive = true,
                isRecordingPlayback = false,
                currentUrl = url,
                liveUrl = url,
                behindLiveSeconds = 0
            )

            handler.post {
                onStarted(url)
            }
        } catch (e: Exception) {
            handler.post {
                onError(
                    "Cannot reach server: ${e.message}"
                )
            }
        }
    }

    fun startRecordingPlayback(
        url: String,
        onStarted: (String) -> Unit
    ) {
        state = state.copy(
            isLive = false,
            isRecordingPlayback = true,
            currentUrl = url,
            liveUrl = url,
            behindLiveSeconds = 0
        )

        playStream(url)
        onStarted(url)
    }

    fun playStream(url: String) {
        if (url.isBlank()) {
            return
        }

        state = state.copy(
            currentUrl = url
        )

        playerEngine.playUrl(url)
    }

    fun reloadStream(
        url: String,
        delayMs: Long = 0L
    ) {
        if (url.isBlank()) {
            return
        }

        if (delayMs <= 0L) {
            playStream(url)
        } else {
            handler.postDelayed({
                playStream(url)
            }, delayMs)
        }
    }

    fun recoverPlayback(
        currentUrl: String,
        isReconnecting: Boolean,
        onReconnectStarted: () -> Unit,
        onReconnectFailed: () -> Unit
    ) {
        if (
            isReconnecting ||
            currentUrl.isBlank()
        ) {
            return
        }

        onReconnectStarted()

        handler.postDelayed({
            try {
                reloadStream(
                    url = currentUrl,
                    delayMs = 150L
                )
            } catch (_: Exception) {
                onReconnectFailed()
            }
        }, 1500)
    }

    fun play() {
        playerEngine.play()
    }

    fun pause() {
        playerEngine.pause()
    }

    fun stop() {
        playerEngine.stop()
    }

    fun togglePlayPause(
        isRecordingPlayback: Boolean,
        onPaused: () -> Unit,
        onPlaying: () -> Unit,
        onError: (String) -> Unit
    ) {
        if (playerEngine.isPlaying()) {
            if (isRecordingPlayback) {
                playerEngine.pause()

                state = state.copy(
                    isPlaying = false
                )

                onPaused()
                return
            }

            /*
             * Stop VLC immediately and preserve its exact position inside
             * delayed_live.m3u8.
             */
            playerEngine.pauseLiveDvr()

            state = state.copy(
                isPlaying = false
            )

            onPaused()

            /*
             * Freeze the server playlist in the background.
             */
            CoroutineScope(
                Dispatchers.IO
            ).launch {
                try {
                    ApiClient
                        .getApi(context)
                        .timeshiftPause()
                } catch (e: Exception) {
                    handler.post {
                        onError(
                            "Pause sync failed: ${e.message}"
                        )
                    }
                }
            }

            return
        }

        if (isRecordingPlayback) {
            playerEngine.play()

            state = state.copy(
                isPlaying = true
            )

            onPlaying()
            return
        }

        /*
         * Reopen VLC while the delayed playlist is still frozen. Then
         * unfreeze the server shortly afterward so new segments continue
         * from the same DVR position.
         */
        playerEngine.resumeLiveDvr()

        state = state.copy(
            isPlaying = true
        )

        onPlaying()

        CoroutineScope(
            Dispatchers.IO
        ).launch {
            try {
                ApiClient
                    .getApi(context)
                    .timeshiftResume()
            } catch (e: Exception) {
                handler.post {
                    onError(
                        "Resume sync failed: ${e.message}"
                    )
                }
            }
        }
    }

    fun seekRecordingRelative(seconds: Int) {
        playerEngine.seekRelative(
            seconds * 1000L
        )
    }

    fun currentPositionMs(): Long {
        return playerEngine.currentPositionMs()
    }

    fun durationMs(): Long {
        return playerEngine.durationMs()
    }

    fun scheduleLiveRelativeSeek(
        deltaSeconds: Int,
        onRelativeSeek: (Int) -> Unit
    ) {
        pendingLiveDeltaSeconds += deltaSeconds

        pendingLiveSeekRunnable?.let {
            handler.removeCallbacks(it)
        }

        val runnable = Runnable {
            val delta = pendingLiveDeltaSeconds
            pendingLiveDeltaSeconds = 0
            pendingLiveSeekRunnable = null

            if (delta != 0) {
                onRelativeSeek(delta)
            }
        }

        pendingLiveSeekRunnable = runnable
        handler.postDelayed(runnable, LIVE_SEEK_DEBOUNCE_MS)
    }

    fun cancelPendingLiveSeek() {
        pendingLiveSeekRunnable?.let {
            handler.removeCallbacks(it)
        }

        pendingLiveSeekRunnable = null
        pendingLiveDeltaSeconds = 0
    }


    suspend fun toggleRecording(
        channelNum: String,
        isRecording: Boolean,
        onRecordingStarted: () -> Unit,
        onRecordingStopped: () -> Unit,
        onError: (String) -> Unit
    ) {
        try {
            if (isRecording) {
                ApiClient
                    .getApi(context)
                    .recordStop()

                handler.post {
                    onRecordingStopped()
                }
            } else {
                val resp = ApiClient
                    .getApi(context)
                    .recordStart(channelNum)

                if (resp.ok == true) {
                    handler.post {
                        onRecordingStarted()
                    }
                } else {
                    handler.post {
                        onError(
                            resp.error
                                ?: "Could not start recording"
                        )
                    }
                }
            }
        } catch (e: Exception) {
            handler.post {
                onError(
                    "Recording error: ${e.message}"
                )
            }
        }
    }

    suspend fun recordRestOfShow(
        channelNum: String,
        onScheduled: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        try {
            val resp = ApiClient
                .getApi(context)
                .recordRestOfShow(channelNum)

            handler.post {
                if (resp.ok) {
                    onScheduled(
                        resp.message
                            ?: "Scheduled to record the rest of this show"
                    )
                } else {
                    onError(
                        resp.error
                            ?: "Could not schedule recording"
                    )
                }
            }
        } catch (e: Exception) {
            handler.post {
                onError(
                    "Record rest failed: ${e.message}"
                )
            }
        }
    }

    suspend fun scheduleCurrentShow(
        channelNum: String,
        onScheduled: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        try {
            val resp = ApiClient
                .getApi(context)
                .recordScheduleShow(channelNum)

            handler.post {
                if (resp.ok) {
                    onScheduled(
                        resp.message
                            ?: "Show scheduled"
                    )
                } else {
                    onError(
                        resp.error
                            ?: "Could not schedule show"
                    )
                }
            }
        } catch (e: Exception) {
            handler.post {
                onError(
                    "Schedule failed: ${e.message}"
                )
            }
        }
    }


    suspend fun getGuideRecordOptions(
        programId: Int,
        onLoaded: (com.signaldvr.app.api.GuideRecordOptionsResponse) -> Unit,
        onError: (String) -> Unit
    ) {
        try {
            val response = ApiClient
                .getApi(context)
                .getGuideRecordOptions(programId)

            handler.post {
                if (response.ok) {
                    onLoaded(response)
                } else {
                    onError(response.error ?: "Could not load recording options")
                }
            }
        } catch (e: Exception) {
            handler.post {
                onError("Recording options failed: ${e.message}")
            }
        }
    }

    suspend fun recordGuideEpisode(
        programId: Int,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        runGuideRecordAction(
            action = {
                ApiClient.getApi(context).recordGuideProgramOnce(
                    programId,
                    com.signaldvr.app.api.GuideRecordRequest("once")
                )
            },
            defaultSuccess = "Episode scheduled",
            onSuccess = onSuccess,
            onError = onError
        )
    }

    suspend fun recordGuideSeries(
        programId: Int,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        runGuideRecordAction(
            action = {
                ApiClient.getApi(context).recordGuideProgramSeries(
                    programId,
                    com.signaldvr.app.api.GuideRecordRequest("series")
                )
            },
            defaultSuccess = "Series recording scheduled",
            onSuccess = onSuccess,
            onError = onError
        )
    }

    suspend fun recordGuideNewEpisodes(
        programId: Int,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        runGuideRecordAction(
            action = {
                ApiClient.getApi(context).recordGuideProgramNewEpisodes(
                    programId,
                    com.signaldvr.app.api.GuideRecordRequest("new")
                )
            },
            defaultSuccess = "New episodes will be recorded",
            onSuccess = onSuccess,
            onError = onError
        )
    }

    suspend fun cancelGuideEpisode(
        programId: Int,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        runGuideRecordAction(
            action = {
                ApiClient.getApi(context).cancelGuideProgramRecording(programId)
            },
            defaultSuccess = "Episode recording cancelled",
            onSuccess = onSuccess,
            onError = onError
        )
    }

    suspend fun cancelGuideSeries(
        seriesId: Int,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        runGuideRecordAction(
            action = {
                ApiClient.getApi(context).deleteSeriesRule(seriesId)
            },
            defaultSuccess = "Series recording cancelled",
            onSuccess = onSuccess,
            onError = onError
        )
    }

    private suspend fun runGuideRecordAction(
        action: suspend () -> com.signaldvr.app.api.GuideRecordActionResponse,
        defaultSuccess: String,
        onSuccess: (String) -> Unit,
        onError: (String) -> Unit
    ) {
        try {
            val response = action()

            handler.post {
                if (response.ok) {
                    onSuccess(response.message ?: defaultSuccess)
                } else {
                    onError(response.error ?: "Recording action failed")
                }
            }
        } catch (e: Exception) {
            handler.post {
                onError("Recording action failed: ${e.message}")
            }
        }
    }

    suspend fun refreshRecordingStatus(
        onRecording: () -> Unit,
        onNotRecording: () -> Unit,
        onError: (String) -> Unit
    ) {
        try {
            val resp = ApiClient
                .getApi(context)
                .recordStatus()

            handler.post {
                if (
                    resp.ok == true &&
                    resp.recording == true
                ) {
                    onRecording()
                } else {
                    onNotRecording()
                }
            }
        } catch (e: Exception) {
            handler.post {
                onError(
                    e.message
                        ?: "Recording status error"
                )
            }
        }
    }

    suspend fun jumpLive(
        isRecordingPlayback: Boolean,
        onNotAvailable: () -> Unit,
        onError: (String) -> Unit,
        onLive: (String) -> Unit
    ) {
        if (isRecordingPlayback) {
            handler.post {
                onNotAvailable()
            }
            return
        }

        try {
            val resp = ApiClient
                .getApi(context)
                .playbackLive()

            if (
                !resp.ok ||
                resp.playlistUrl.isNullOrBlank()
            ) {
                handler.post {
                    onError(
                        resp.error
                            ?: "Could not jump live"
                    )
                }
                return
            }

            val url = resp.playlistUrl

            state = state.copy(
                isLive = true,
                currentUrl = url,
                liveUrl = url,
                behindLiveSeconds = 0
            )

            handler.post {
                playerEngine.refreshLivePlaylist(
                    url = url,
                    jumpToLiveEdge = true
                )
                onLive(url)
            }
        } catch (e: Exception) {
            handler.post {
                onError(
                    "Jump live failed: ${e.message}"
                )
            }
        }
    }

    suspend fun seekLiveRelative(
        deltaSeconds: Int,
        onError: (String) -> Unit,
        onSeek: (String, Int, Boolean) -> Unit
    ) {
        try {
            val resp = ApiClient
                .getApi(context)
                .playbackSeek(deltaSeconds)

            if (!resp.ok || resp.playlistUrl.isNullOrBlank()) {
                handler.post {
                    onError(resp.error ?: "Seek failed")
                }
                return
            }

            val url = resp.playlistUrl
            val live = resp.live == true
            val secondsBehind = (resp.secondsBehind ?: 0).coerceAtLeast(0)

            state = state.copy(
                isLive = live,
                currentUrl = url,
                liveUrl = if (live) url else state.liveUrl,
                behindLiveSeconds = secondsBehind
            )

            handler.post {
                playerEngine.refreshLivePlaylist(url)
                onSeek(url, secondsBehind, live)
            }
        } catch (e: Exception) {
            handler.post {
                onError("Seek failed: ${e.message}")
            }
        }
    }


    fun release() {
        playerEngine.release()
    }
}