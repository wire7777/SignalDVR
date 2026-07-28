package com.signaldvr.app.ui.player
import com.signaldvr.app.ui.player.controller.PlaybackController
import com.signaldvr.app.ui.player.controller.PlayerUiController
import com.signaldvr.app.ui.player.controller.RemoteControlController
import com.signaldvr.app.ui.player.engine.PlayerEngine
import com.signaldvr.app.ui.player.engine.PlayerFactory
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.View
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.signaldvr.app.R
import com.signaldvr.app.MainActivity
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.media3.ui.PlayerView
import com.bumptech.glide.Glide
import com.bumptech.glide.request.target.CustomTarget
import com.bumptech.glide.request.transition.Transition
import android.graphics.Bitmap
import android.graphics.drawable.Drawable
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL



class PlayerActivity : AppCompatActivity() {
    private lateinit var media3PlayerView: PlayerView
    private lateinit var loadingBar: ProgressBar
    private lateinit var loadingText: TextView
    private lateinit var overlay: GuideOverlayView
    private lateinit var quickGuide: QuickGuideOverlayView
    private lateinit var fullInfo: FullInfoOverlayView
    private lateinit var dvrBar: View

    private lateinit var btnRew60: TextView
    private lateinit var btnRew30: TextView
    private lateinit var btnRew10: TextView
    private lateinit var btnPlayPause: TextView
    private lateinit var btnFf10: TextView
    private lateinit var btnFf30: TextView
    private lateinit var btnFf60: TextView
    private lateinit var btnLive: TextView
    private lateinit var btnRecord: TextView
    private lateinit var tvPosition: TextView
    private lateinit var timelineView: TimelineView

    private lateinit var playerEngine: PlayerEngine
    private lateinit var playbackController: PlaybackController
    private lateinit var playerUiController: PlayerUiController
    private lateinit var remoteControlController: RemoteControlController


    private var liveUrl = ""
    private var currentUrl = ""
    private var channelNum = ""
    private var channelName = ""

    private var isRecording = false
    private var isRecordingPlayback = false
    private var isLiveMode = true
    private var behindLiveSeconds = 0
    private var reconnecting = false
    private var liveBuffering = false
    private var liveReconnectAttempts = 0
    private var userPaused = false
    private var playbackControllerReady = false

    /*
     * Every live DVR seek/jump receives a new generation number.
     * A response from an older request is ignored so an earlier rewind
     * cannot arrive after a newer fast-forward or Jump Live operation.
     */
    private var liveSeekGeneration = 0L
    private var liveSeekJob: Job? = null

    private var quickGuideChannels: List<Channel> = emptyList()
    private var quickGuideRequestToken = 0

    private var recordingId = -1
    private var resumeUrlPath = ""
    private var resumePositionMs = 0L
    private var resumeApplied = false
    private var playbackCompleted = false
    private var lastResumeSaveMs = 0L
    private var resumeSeekAttempts = 0

    private val handler = Handler(Looper.getMainLooper())

    /*
     * Media3 can remain stuck on an HLS source after the backend watchdog
     * restarts FFmpeg. While live TV is buffering, periodically replace the
     * current media item with a cache-busted copy of the same playlist.
     *
     * The backend normally recovers in roughly 15-30 seconds, so several
     * bounded retries are allowed. Recording/VOD playback is never reloaded
     * by this watchdog.
     */
    private val liveReconnectRunnable = object : Runnable {
        override fun run() {
            if (
                !liveBuffering ||
                isRecordingPlayback ||
                userPaused ||
                currentUrl.isBlank() ||
                !playbackControllerReady ||
                isFinishing ||
                isDestroyed
            ) {
                return
            }

            if (liveReconnectAttempts >= LIVE_RECONNECT_MAX_ATTEMPTS) {
                reconnecting = false
                playerUiController.showLoading("Still reconnecting...")
                return
            }

            liveReconnectAttempts += 1
            reconnecting = true

            playerUiController.showLoading(
                "Reconnecting... ($liveReconnectAttempts/$LIVE_RECONNECT_MAX_ATTEMPTS)"
            )

            /*
             * refreshLivePlaylist() keeps the same ExoPlayer instance but
             * replaces the HLS media item and adds a changing query value.
             * This forces a new manifest request after FFmpeg recovery.
             */
            playerEngine.refreshLivePlaylist(currentUrl)

            handler.removeCallbacks(this)
            handler.postDelayed(this, LIVE_RECONNECT_DELAY_MS)
        }
    }

    private val playbackProgressRunnable = object : Runnable {
        override fun run() {
            updateMediaProgress()
            handler.postDelayed(this, 500L)
        }
    }


    private val resumeSaveRunnable = object : Runnable {
        override fun run() {
            saveResumeProgress(completed = false)
            handler.postDelayed(this, 10_000L)
        }
    }


    companion object {
        const val EXTRA_CHANNEL_NUM = "channel_num"
        const val EXTRA_CHANNEL_NAME = "channel_name"
        const val EXTRA_PLAY_URL = "play_url"
        const val EXTRA_IS_LIVE = "is_live"
        const val EXTRA_TITLE = "title"
        const val EXTRA_RECORDING_ID = "recording_id"
        const val EXTRA_RESUME_URL = "resume_url"

        private const val LIVE_RECONNECT_DELAY_MS = 12_000L
        private const val LIVE_RECONNECT_MAX_ATTEMPTS = 6
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_player)

        bindViews()
        playerUiController = PlayerUiController(
            context = this,
            handler = handler,
            dvrBar = dvrBar,
            defaultFocusView = btnPlayPause,
            loadingBar = loadingBar,
            loadingText = loadingText,
            recordButton = btnRecord,
            positionText = tvPosition,
            timelineView = timelineView
        )
        remoteControlController = RemoteControlController(
            playerUiController = playerUiController,
            actions = RemoteControlController.Actions(
                onTogglePlayPause = { togglePlayPause() },
                onPlay = { if (playbackControllerReady) playbackController.play() },
                onPause = { if (playbackControllerReady) playbackController.pause() },
                onRewind = { seconds -> rewindSeconds(seconds) },
                onFastForward = { seconds -> fastForwardSeconds(seconds) },
                onShowGuide = { showGuideOverlay() },
                onFinish = { exitPlayer() }
            )
        )
        setupButtons()
        loadPlayerEngineAndStart()
    }

    private fun loadPlayerEngineAndStart() {
        playerUiController.showLoading("Loading player settings...")

        lifecycleScope.launch {

            playerEngine = PlayerFactory.create(
                context = this@PlayerActivity,
                media3PlayerView = media3PlayerView
            )

            playerEngine.setListener(object : PlayerEngine.Listener {
                override fun onOpening() {
                    runOnUiThread {
                        playerUiController.showLoadingDelayed("Buffering...")

                        if (!isRecordingPlayback && !userPaused) {
                            liveBuffering = true
                            handler.removeCallbacks(liveReconnectRunnable)
                            handler.postDelayed(
                                liveReconnectRunnable,
                                LIVE_RECONNECT_DELAY_MS
                            )
                        }
                    }
                }

                override fun onPlaying() {
                    runOnUiThread {
                        liveBuffering = false
                        liveReconnectAttempts = 0
                        reconnecting = false
                        handler.removeCallbacks(liveReconnectRunnable)
                        playerUiController.clearTransientMessages()
                        window.decorView.keepScreenOn = true
                        playerUiController.showPauseIcon()

                        if (isRecordingPlayback) {
                            startPlaybackProgressUpdates()
                            updateMediaProgress()

                            if (!resumeApplied) {
                                resumeApplied = true

                                if (resumePositionMs >= 3_000L) {
                                    playerUiController.showShortToast(
                                        "Resuming at ${formatResumeTime(resumePositionMs)}"
                                    )
                                }
                            }

                            revealRecordingVideo()
                        } else {
                            playerUiController.hideLoading()
                            updatePositionLabel()
                        }
                    }
                }

                override fun onPaused() {
                    runOnUiThread {
                        liveBuffering = false
                        handler.removeCallbacks(liveReconnectRunnable)
                        playerUiController.showPlayIcon()
                    }
                }

                override fun onStopped() = Unit

                override fun onEndReached() {
                    runOnUiThread {
                        liveBuffering = false
                        handler.removeCallbacks(liveReconnectRunnable)
                        if (isRecordingPlayback) {
                            playbackCompleted = true
                            stopPlaybackProgressUpdates()
                            handler.removeCallbacks(resumeSaveRunnable)

                            lifecycleScope.launch {
                                saveResumeProgressNow(completed = true)
                                exitPlayer()
                            }
                        } else {
                            /*
                             * A live HLS playlist can occasionally signal an end
                             * while it refreshes. Reopen the current playlist
                             * unless the user intentionally paused playback.
                             */
                            if (userPaused) {
                                reconnecting = false
                                playerUiController.hideLoading()
                                playerUiController.showPlayIcon()
                                playerEngine.pause()
                            } else if (!reconnecting && currentUrl.isNotBlank()) {
                                reconnecting = true
                                playerUiController.showLoading("Reconnecting...")

                                handler.postDelayed({
                                    if (!userPaused) {
                                        playerEngine.playUrl(currentUrl)
                                    }
                                }, 500)
                            }
                        }
                    }
                }

                override fun onError(message: String) {
                    runOnUiThread {
                        liveBuffering = false
                        handler.removeCallbacks(liveReconnectRunnable)
                        playbackController.recoverPlayback(
                            currentUrl = currentUrl,
                            isReconnecting = reconnecting,
                            onReconnectStarted = {
                                reconnecting = true
                                playerUiController.showLoading("Reconnecting...")
                            },
                            onReconnectFailed = {
                                reconnecting = false
                                playerUiController.showLongToast(message)
                            }
                        )
                    }
                }
            })

            playerEngine.attach()

            playbackController = PlaybackController(
                context = this@PlayerActivity,
                playerEngine = playerEngine,
                handler = handler
            )

            playbackControllerReady = true
            //playerUiController.showShortToast("Using Media3 / ExoPlayer")

            startRequestedPlayback()
        }
    }

    private fun startRequestedPlayback() {
        channelNum = intent.getStringExtra(EXTRA_CHANNEL_NUM) ?: ""
        channelName = intent.getStringExtra(EXTRA_CHANNEL_NAME) ?: ""
        recordingId = intent.getIntExtra(EXTRA_RECORDING_ID, -1)
        resumeUrlPath =
            intent.getStringExtra(EXTRA_RESUME_URL)
                ?.trim()
                .orEmpty()

        /*
         * Backward compatibility for recording launches that do not yet
         * include EXTRA_RESUME_URL.
         */
        if (resumeUrlPath.isBlank() && recordingId > 0) {
            resumeUrlPath =
                "/api/recordings/$recordingId/resume"
        }

        val requestedIsLive =
            intent.getBooleanExtra(EXTRA_IS_LIVE, false)

        val timelineTitle =
            intent.getStringExtra(EXTRA_TITLE)?.takeIf { it.isNotBlank() }
                ?: channelName.takeIf { it.isNotBlank() }
                ?: "Live TV"

        val timelineSubtitle =
            if (channelNum.isNotBlank()) channelNum else ""

        playerUiController.setTimelineInfo(
            timelineTitle,
            timelineSubtitle
        )

        if (requestedIsLive) {
            updateTimelineChannelIdentity()
        }

        val directUrl = intent.getStringExtra(EXTRA_PLAY_URL)

        /*
         * Live Now must take priority over any program playlist URL or
         * live-program database ID supplied by the Library.
         */
        if (requestedIsLive) {
            recordingId = -1
            resumeUrlPath = ""
            isRecordingPlayback = false
            isLiveMode = true
            updatePlaybackModeControls()
            startLiveStream()
            return
        }

        if (!directUrl.isNullOrBlank()) {
            startRecordingPlayback(directUrl)
            return
        }

        recordingId = -1
        resumeUrlPath = ""
        isRecordingPlayback = false
        isLiveMode = true
        updatePlaybackModeControls()
        startLiveStream()
    }

    private fun bindViews() {
        media3PlayerView = findViewById(R.id.media3_player_view)
        loadingBar = findViewById(R.id.loading_bar)
        loadingText = findViewById(R.id.loading_text)
        overlay = findViewById(R.id.guide_overlay)
        quickGuide = findViewById(R.id.quick_guide_overlay)
        fullInfo = findViewById(R.id.full_info_overlay)
        dvrBar = findViewById(R.id.dvr_bar)

        btnRew60 = findViewById(R.id.btn_rew60)
        btnRew30 = findViewById(R.id.btn_rew30)
        btnRew10 = findViewById(R.id.btn_rew10)
        btnPlayPause = findViewById(R.id.btn_play_pause)
        btnFf10 = findViewById(R.id.btn_ff10)
        btnFf30 = findViewById(R.id.btn_ff30)
        btnFf60 = findViewById(R.id.btn_ff60)
        btnLive = findViewById(R.id.btn_live)
        btnRecord = findViewById(R.id.btn_record)
        tvPosition = findViewById(R.id.tv_position)
        timelineView = findViewById(R.id.timelineView)
    }

    private fun updateTimelineChannelIdentity() {
        playerUiController.setTimelineChannel(
            logo = null,
            channelName = channelName,
            channelNumber = channelNum
        )

        lifecycleScope.launch {
            try {
                if (quickGuideChannels.isEmpty()) {
                    quickGuideChannels = ApiClient
                        .getApi(this@PlayerActivity)
                        .getLiveChannels()
                }

                val channel = quickGuideChannels.firstOrNull {
                    it.number == channelNum
                } ?: return@launch

                val logoUrl = channel.logo
                if (logoUrl.isNullOrBlank()) {
                    return@launch
                }

                Glide.with(this@PlayerActivity)
                    .asBitmap()
                    .load(logoUrl)
                    .into(object : CustomTarget<Bitmap>() {
                        override fun onResourceReady(
                            resource: Bitmap,
                            transition: Transition<in Bitmap>?
                        ) {
                            if (!isFinishing && !isDestroyed && channelNum == channel.number) {
                                playerUiController.setTimelineChannel(
                                    logo = resource,
                                    channelName = channel.name,
                                    channelNumber = channel.number
                                )
                            }
                        }

                        override fun onLoadCleared(placeholder: Drawable?) = Unit
                    })
            } catch (_: Exception) {
                // Keep the clean text-only identity if logo loading fails.
            }
        }
    }


    private fun startLiveStream() {
        playerEngine.setMuted(false)
        playerUiController.showLoading("Tuning to $channelName...")

        lifecycleScope.launch {
            playbackController.startLiveStream(
                channelNum = channelNum,
                onError = { message ->
                    showFatalError(message)
                },
                onStarted = { url ->
                    liveUrl = url
                    currentUrl = liveUrl
                    behindLiveSeconds = 0
                    isLiveMode = true

                    playbackController.playStream(currentUrl)
                    updatePositionLabel()
                    refreshRecordingStatus()

                    lifecycleScope.launch {
                        delay(1200)
                        showGuideOverlay()
                    }
                }
            )
        }
    }


    private fun startRecordingPlayback(url: String) {
        isRecordingPlayback = true
        isLiveMode = false
        liveUrl = url
        currentUrl = url
        behindLiveSeconds = 0
        resumeApplied = false
        resumePositionMs = 0L
        resumeSeekAttempts = 0
        playbackCompleted = false
        lastResumeSaveMs = 0L

        updatePlaybackModeControls()
        playerUiController.hideRecordButton()
        playerUiController.clearPositionLabel()

        media3PlayerView.visibility = View.INVISIBLE

        playerEngine.setMuted(true)
        playerUiController.showLoading("Opening recording...")
        startPlaybackProgressUpdates()

        handler.removeCallbacks(resumeSaveRunnable)
        handler.postDelayed(resumeSaveRunnable, 10_000L)

        lifecycleScope.launch {
            // Load resume before the player is allowed to start. This prevents
            // the first frame/audio from playing and then jumping forward.
            resumePositionMs = loadResumePositionMs()

            playerEngine.prepareUrl(url)

            if (resumePositionMs >= 3_000L) {
                playerEngine.seekTo(resumePositionMs)
            }

            currentUrl = url
            liveUrl = url
            playerEngine.play()
        }
    }


    private fun setupButtons() {
        playerUiController.configureDvrButtons(
            btnRew60 = btnRew60,
            btnRew30 = btnRew30,
            btnRew10 = btnRew10,
            btnPlayPause = btnPlayPause,
            btnFf10 = btnFf10,
            btnFf30 = btnFf30,
            btnFf60 = btnFf60,
            btnLive = btnLive,
            btnRecord = btnRecord,
            onRew60 = { rewindSeconds(60) },
            onRew30 = { rewindSeconds(30) },
            onRew10 = { rewindSeconds(10) },
            onPlayPause = { togglePlayPause() },
            onFf10 = { fastForwardSeconds(10) },
            onFf30 = { fastForwardSeconds(30) },
            onFf60 = { fastForwardSeconds(60) },
            onLive = { jumpLive() },
            onRecord = { showRecordMenu() }
        )
    }


    private fun updatePlaybackModeControls() {
        /*
         * LIVE only applies to live TV and delayed-live playback.
         * Hide it for completed recordings and DVR Library playback.
         */
        btnLive.visibility = if (isRecordingPlayback) {
            View.GONE
        } else {
            View.VISIBLE
        }

        /*
         * Keep remote focus navigation valid when LIVE is removed.
         */
        if (isRecordingPlayback) {
            btnFf30.nextFocusRightId = btnRecord.id
            btnRecord.nextFocusLeftId = btnFf30.id
        } else {
            btnFf30.nextFocusRightId = btnLive.id
            btnLive.nextFocusLeftId = btnFf30.id
            btnLive.nextFocusRightId = btnRecord.id
            btnRecord.nextFocusLeftId = btnLive.id
        }
    }

    private fun rewindSeconds(seconds: Int) {
        showSeekMovementIndicator(direction = "REW", deltaSeconds = -seconds)

        if (isRecordingPlayback) {
            playbackController.seekRecordingRelative(-seconds)
            updateMediaProgress()
            playerUiController.scheduleDvrDismiss()
            return
        }

        playbackController.scheduleLiveRelativeSeek(
            deltaSeconds = -seconds,
            onRelativeSeek = { deltaSeconds ->
                performLiveRelativeSeek(deltaSeconds)
            }
        )

        playerUiController.scheduleDvrDismiss()
    }

    private fun fastForwardSeconds(seconds: Int) {
        showSeekMovementIndicator(direction = "FF", deltaSeconds = seconds)

        if (isRecordingPlayback) {
            playbackController.seekRecordingRelative(seconds)
            updateMediaProgress()
            playerUiController.scheduleDvrDismiss()
            return
        }

        playbackController.scheduleLiveRelativeSeek(
            deltaSeconds = seconds,
            onRelativeSeek = { deltaSeconds ->
                performLiveRelativeSeek(deltaSeconds)
            }
        )

        playerUiController.scheduleDvrDismiss()
    }

    private fun showSeekMovementIndicator(direction: String, deltaSeconds: Int) {
        playerUiController.showDvrBar()

        val stepSeconds = kotlin.math.abs(deltaSeconds)
        val location = if (isRecordingPlayback) {
            val durationMs = playbackController.durationMs().coerceAtLeast(0L)
            val projectedMs = (
                    playbackController.currentPositionMs() + (deltaSeconds * 1000L)
                    ).coerceIn(0L, if (durationMs > 0L) durationMs else Long.MAX_VALUE)

            formatSeekPosition(projectedMs)
        } else {
            val projectedBehind = (behindLiveSeconds - deltaSeconds).coerceAtLeast(0)
            if (projectedBehind == 0) "LIVE" else "${formatSeekSeconds(projectedBehind)} behind"
        }

        playerUiController.updatePositionLabel("$direction ${stepSeconds}s  •  $location")
        playerUiController.scheduleDvrDismiss()
    }

    private fun formatSeekPosition(positionMs: Long): String {
        return formatSeekSeconds((positionMs / 1000L).coerceAtLeast(0L).toInt())
    }

    private fun formatSeekSeconds(totalSeconds: Int): String {
        val safeSeconds = totalSeconds.coerceAtLeast(0)
        val hours = safeSeconds / 3600
        val minutes = (safeSeconds % 3600) / 60
        val seconds = safeSeconds % 60

        return if (hours > 0) {
            "%d:%02d:%02d".format(hours, minutes, seconds)
        } else {
            "%d:%02d".format(minutes, seconds)
        }
    }

    private fun performLiveRelativeSeek(deltaSeconds: Int) {
        val generation = ++liveSeekGeneration

        /*
         * Rapid remote presses are combined by PlaybackController.
         * Only the newest server response may update playback.
         */
        liveSeekJob?.cancel()
        playerUiController.showLoadingDelayed("Seeking...")

        liveSeekJob = lifecycleScope.launch {
            playbackController.seekLiveRelative(
                deltaSeconds = deltaSeconds,
                onError = seekError@ { message ->
                    if (generation != liveSeekGeneration) {
                        return@seekError
                    }

                    liveSeekJob = null
                    playerUiController.hideLoading()
                    playerUiController.showLongToast(message)
                },
                onSeek = seekSuccess@ { url, behindSeconds, live ->
                    if (generation != liveSeekGeneration) {
                        return@seekSuccess
                    }

                    liveSeekJob = null
                    playerUiController.hideLoading()

                    isLiveMode = live
                    behindLiveSeconds = behindSeconds
                    currentUrl = url

                    if (live) {
                        liveUrl = url
                    }

                    updatePositionLabel()
                    playerUiController.scheduleDvrDismiss()
                }
            )
        }
    }



    private fun startPlaybackProgressUpdates() {
        handler.removeCallbacks(playbackProgressRunnable)
        handler.post(playbackProgressRunnable)
    }

    private fun stopPlaybackProgressUpdates() {
        handler.removeCallbacks(playbackProgressRunnable)
    }

    private fun updateMediaProgress() {
        if (!isRecordingPlayback || !playbackControllerReady) return

        val position = playbackController.currentPositionMs()
        val duration = playbackController.durationMs()

        playerUiController.updateMediaTimeline(position, duration)
    }

    private fun togglePlayPause() {
        playbackController.togglePlayPause(
            isRecordingPlayback = isRecordingPlayback,
            onPaused = {
                userPaused = true
                reconnecting = false
                playerUiController.hideLoading()
                playerUiController.showPlayIcon()
                playerUiController.setTimelinePaused(true)
            },
            onPlaying = {
                userPaused = false
                playerUiController.showPauseIcon()
                playerUiController.setTimelinePaused(false)
            },
            onError = { message ->
                playerUiController.showLongToast(message)
            }
        )

        playerUiController.scheduleDvrDismiss()
    }

    private fun jumpLive() {
        /*
         * Invalidate both a queued debounce seek and any network request that
         * is still returning. This prevents an old rewind response from
         * switching the player back to delayed_live.m3u8 after Fast Forward
         * has already reached LIVE.
         */
        playbackController.cancelPendingLiveSeek()

        val generation = ++liveSeekGeneration
        liveSeekJob?.cancel()

        playerUiController.showLoadingDelayed("Jumping Live...")

        liveSeekJob = lifecycleScope.launch {
            playbackController.jumpLive(
                isRecordingPlayback = isRecordingPlayback,
                onNotAvailable = liveUnavailable@ {
                    if (generation != liveSeekGeneration) {
                        return@liveUnavailable
                    }

                    liveSeekJob = null
                    playerUiController.hideLoading()
                    playerUiController.showShortToast("Live only while watching TV")
                },
                onError = liveError@ { message ->
                    if (generation != liveSeekGeneration) {
                        return@liveError
                    }

                    liveSeekJob = null
                    playerUiController.hideLoading()
                    playerUiController.showLongToast(message)
                },
                onLive = liveSuccess@ { url ->
                    if (generation != liveSeekGeneration) {
                        return@liveSuccess
                    }

                    liveSeekJob = null
                    playerUiController.hideLoading()
                    liveUrl = url
                    currentUrl = liveUrl
                    isLiveMode = true
                    behindLiveSeconds = 0

                    updatePositionLabel()
                    playerUiController.scheduleDvrDismiss()
                }
            )
        }
    }

    private fun updatePositionLabel() {
        playbackController.updatePlaybackState(
            isLive = isLiveMode,
            isRecordingPlayback = isRecordingPlayback,
            currentUrl = currentUrl,
            liveUrl = liveUrl,
            behindLiveSeconds = behindLiveSeconds,
            isReconnecting = reconnecting
        )

        playerUiController.updatePositionLabel(playbackController.positionLabel())
        playerUiController.updateTimeline(
            isLive = isLiveMode,
            behindLiveSeconds = behindLiveSeconds,
            isRecordingPlayback = isRecordingPlayback
        )
    }


    private fun showRecordMenu() {
        if (isRecording) {
            AlertDialog.Builder(this)
                .setTitle("Recording")
                .setMessage("Stop the current recording?")
                .setPositiveButton("Stop Recording") { _, _ -> toggleRecord() }
                .setNegativeButton("Cancel", null)
                .show()
            return
        }

        val options = arrayOf(
            "Record from now",
            "Record rest of this show",
            "Schedule this show"
        )

        AlertDialog.Builder(this)
            .setTitle("Record Options")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> toggleRecord()
                    1 -> recordRestOfShow()
                    2 -> scheduleCurrentShow()
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun recordRestOfShow() {
        lifecycleScope.launch {
            playbackController.recordRestOfShow(
                channelNum = channelNum,
                onScheduled = { message ->
                    playerUiController.showShortToast(message)
                    playerUiController.scheduleDvrDismiss()
                    refreshRecordingStatus()
                },
                onError = { message ->
                    playerUiController.showLongToast(message)
                    playerUiController.scheduleDvrDismiss()
                }
            )
        }
    }

    private fun scheduleCurrentShow() {
        lifecycleScope.launch {
            playbackController.scheduleCurrentShow(
                channelNum = channelNum,
                onScheduled = { message ->
                    playerUiController.showShortToast(message)
                    playerUiController.scheduleDvrDismiss()
                },
                onError = { message ->
                    playerUiController.showLongToast(message)
                    playerUiController.scheduleDvrDismiss()
                }
            )
        }
    }

    private fun toggleRecord() {
        lifecycleScope.launch {
            playbackController.toggleRecording(
                channelNum = channelNum,
                isRecording = isRecording,
                onRecordingStarted = {
                    updateRecordingUi(true)
                    playerUiController.showShortToast("Recording started")
                    playerUiController.scheduleDvrDismiss()
                },
                onRecordingStopped = {
                    updateRecordingUi(false)
                    playerUiController.showShortToast("Recording stopped")
                    playerUiController.scheduleDvrDismiss()
                },
                onError = { message ->
                    playerUiController.showLongToast(message)
                    playerUiController.scheduleDvrDismiss()
                }
            )
        }
    }

    private fun updateRecordingUi(recording: Boolean) {
        isRecording = recording

        if (recording) {
            playerUiController.showRecordingActive()
        } else {
            playerUiController.showRecordingInactive()
        }
    }

    private fun refreshRecordingStatus() {
        if (isRecordingPlayback || channelNum.isBlank()) return

        lifecycleScope.launch {
            playbackController.refreshRecordingStatus(
                onRecording = {
                    updateRecordingUi(true)
                },
                onNotRecording = {
                    updateRecordingUi(false)
                },
                onError = {
                    // Do not interrupt playback if status sync fails.
                }
            )
        }
    }

    private fun showGuideOverlay() {
        if (channelNum.isBlank()) return

        lifecycleScope.launch {
            try {
                val api = ApiClient.getApi(this@PlayerActivity)

                /*
                 * Load the channel list here when necessary so the mini guide
                 * has the current station logo even before Quick Guide opens.
                 */
                if (quickGuideChannels.isEmpty()) {
                    quickGuideChannels = api.getLiveChannels()
                }

                val currentChannel = quickGuideChannels.firstOrNull {
                    it.number == channelNum
                }

                val data = api.getNowPlaying(channelNum)

                overlay.show(
                    data = data,
                    logoUrl = currentChannel?.logo,
                    autoDismissMs = 7000L,
                )
            } catch (_: Exception) {
                overlay.showBasic(
                    chNum = channelNum,
                    chName = channelName,
                    autoDismissMs = 7000L,
                )
            }
        }
    }

    private fun openQuickGuide() {
        if (
            isRecordingPlayback ||
            channelNum.isBlank() ||
            !playbackControllerReady
        ) {
            return
        }

        playerUiController.hideDvrBar()

        lifecycleScope.launch {
            try {
                if (quickGuideChannels.isEmpty()) {
                    quickGuideChannels = ApiClient
                        .getApi(this@PlayerActivity)
                        .getLiveChannels()
                }

                if (quickGuideChannels.isEmpty()) {
                    playerUiController.showLongToast(
                        "No channels are available"
                    )
                    return@launch
                }

                quickGuide.show(
                    channels = quickGuideChannels,
                    playingChannel = channelNum,
                    onSelectionChanged = { channel ->
                        loadQuickGuideProgram(channel)
                    },
                    autoDismissMs = 12_000L,
                )

            } catch (e: Exception) {
                playerUiController.showLongToast(
                    "Unable to open Quick Guide: ${e.message}"
                )
            }
        }
    }

    private fun loadQuickGuideProgram(channel: Channel) {
        val requestToken = ++quickGuideRequestToken

        lifecycleScope.launch {
            try {
                val data = ApiClient
                    .getApi(this@PlayerActivity)
                    .getNowPlaying(channel.number)

                if (
                    requestToken == quickGuideRequestToken &&
                    quickGuide.isShowing() &&
                    quickGuide.selectedChannel()?.number == channel.number
                ) {
                    quickGuide.updateProgram(data)
                }

            } catch (_: Exception) {
                /*
                 * The channel row already contains basic now/next data.
                 * Keep browsing responsive if detailed guide data fails.
                 */
            }
        }
    }


    private fun openFullInfoFromQuickGuide() {
        val selected = quickGuide.selectedChannel() ?: return

        lifecycleScope.launch {
            try {
                val data = ApiClient
                    .getApi(this@PlayerActivity)
                    .getNowPlaying(selected.number)

                quickGuide.hide()
                overlay.visibility = View.GONE

                fullInfo.show(
                    data = data,
                    logoUrl = selected.logo,
                    watchLabel = if (selected.number == channelNum) {
                        "WATCH LIVE"
                    } else {
                        "TUNE ${selected.number}"
                    },
                    onWatch = {
                        fullInfo.hide()
                        if (selected.number != channelNum) {
                            tuneChannelFromInfo(selected)
                        }
                    },
                    onRecord = {
                        fullInfo.hide()
                        lifecycleScope.launch {
                            playbackController.scheduleCurrentShow(
                                channelNum = selected.number,
                                onScheduled = { message ->
                                    playerUiController.showShortToast(message)
                                },
                                onError = { message ->
                                    playerUiController.showLongToast(message)
                                }
                            )
                        }
                    },
                    onClose = { fullInfo.hide() },
                )
            } catch (e: Exception) {
                playerUiController.showLongToast(
                    "Unable to load program information: ${e.message}"
                )
            }
        }
    }

    private fun tuneChannelFromInfo(selected: Channel) {
        channelNum = selected.number
        channelName = selected.name

        recordingId = -1
        resumeUrlPath = ""
        isRecordingPlayback = false
        isLiveMode = true
        updatePlaybackModeControls()
        behindLiveSeconds = 0
        userPaused = false
        reconnecting = false
        liveUrl = ""
        currentUrl = ""

        playerUiController.setTimelineInfo(
            selected.nowTitle?.takeIf { it.isNotBlank() } ?: selected.name,
            selected.number,
        )
        updateTimelineChannelIdentity()

        playerEngine.stop()
        startLiveStream()
    }

    private fun tuneQuickGuideSelection() {
        val selected = quickGuide.selectedChannel() ?: return

        if (selected.number == channelNum) {
            quickGuide.hide()
            return
        }

        quickGuide.hide()
        overlay.visibility = View.GONE

        channelNum = selected.number
        channelName = selected.name

        recordingId = -1
        isRecordingPlayback = false
        isLiveMode = true
        updatePlaybackModeControls()
        behindLiveSeconds = 0
        userPaused = false
        reconnecting = false
        liveUrl = ""
        currentUrl = ""

        playerUiController.setTimelineInfo(
            selected.nowTitle
                ?.takeIf { it.isNotBlank() }
                ?: selected.name,
            selected.number,
        )
        updateTimelineChannelIdentity()

        playerEngine.stop()
        startLiveStream()
    }

    override fun onKeyDown(
        keyCode: Int,
        event: KeyEvent?,
    ): Boolean {
        if (fullInfo.isShowing()) {
            return fullInfo.handleKey(keyCode)
        }

        /*
         * Quick Guide owns remote input while visible.
         * Browsing does not change playback until OK is pressed.
         */
        if (quickGuide.isShowing()) {
            return when (keyCode) {
                KeyEvent.KEYCODE_DPAD_UP -> {
                    quickGuide.moveSelection(-1)
                    true
                }

                KeyEvent.KEYCODE_DPAD_DOWN -> {
                    quickGuide.moveSelection(1)
                    true
                }

                KeyEvent.KEYCODE_DPAD_CENTER,
                KeyEvent.KEYCODE_ENTER -> {
                    tuneQuickGuideSelection()
                    true
                }

                KeyEvent.KEYCODE_INFO -> {
                    openFullInfoFromQuickGuide()
                    true
                }

                KeyEvent.KEYCODE_BACK,
                KeyEvent.KEYCODE_GUIDE,
                KeyEvent.KEYCODE_MENU -> {
                    quickGuide.hide()
                    true
                }

                /*
                 * Left/right program browsing is reserved for Quick Guide v2.
                 */
                KeyEvent.KEYCODE_DPAD_LEFT -> true

                KeyEvent.KEYCODE_DPAD_RIGHT -> {
                    openFullInfoFromQuickGuide()
                    true
                }

                else -> true
            }
        }

        /*
         * Open Quick Guide while watching live TV.
         */
        if (
            !isRecordingPlayback &&
            (
                    keyCode == KeyEvent.KEYCODE_DPAD_UP ||
                            keyCode == KeyEvent.KEYCODE_INFO ||
                            keyCode == KeyEvent.KEYCODE_GUIDE ||
                            keyCode == KeyEvent.KEYCODE_MENU
                    )
        ) {
            openQuickGuide()
            return true
        }

        return remoteControlController.handleKeyDown(
            keyCode = keyCode,
            repeatCount = event?.repeatCount ?: 0,
            isDvrBarVisible = dvrBar.visibility == View.VISIBLE,
        ) ?: super.onKeyDown(keyCode, event)
    }

    override fun onResume() {
        super.onResume()

        if (!playbackControllerReady) return

        playbackController.play()

        if (isRecordingPlayback) {
            startPlaybackProgressUpdates()

            if (!playbackCompleted) {
                handler.removeCallbacks(resumeSaveRunnable)
                handler.postDelayed(resumeSaveRunnable, 10_000L)
            }
        } else {
            refreshRecordingStatus()
        }
    }

    override fun onPause() {
        super.onPause()

        liveBuffering = false
        handler.removeCallbacks(liveReconnectRunnable)
        stopPlaybackProgressUpdates()
        handler.removeCallbacks(resumeSaveRunnable)

        if (isRecordingPlayback && !playbackCompleted) {
            saveResumeProgress(completed = false)
        }

        if (playbackControllerReady) {
            playbackController.pause()
        }
    }

    override fun onDestroy() {
        liveSeekJob?.cancel()
        liveSeekJob = null
        quickGuide.hide()
        super.onDestroy()

        playerUiController.release()
        handler.removeCallbacks(resumeSaveRunnable)
        handler.removeCallbacks(liveReconnectRunnable)
        handler.removeCallbacksAndMessages(null)

        if (playbackControllerReady) {
            playbackController.release()
        } else if (::playerEngine.isInitialized) {
            playerEngine.release()
        }
    }

    private fun revealRecordingVideo(delayMs: Long = 0L) {
        val reveal = {
            if (
                !isFinishing &&
                !isDestroyed &&
                isRecordingPlayback
            ) {
                playerEngine.setMuted(false)

                media3PlayerView.visibility = View.VISIBLE
                playerUiController.hideLoading()
            }
        }

        if (delayMs > 0L) {
            handler.postDelayed(reveal, delayMs)
        } else {
            reveal()
        }
    }

    private suspend fun loadResumePositionMs(): Long {
        if (resumeUrlPath.isBlank()) {
            return 0L
        }

        return withContext(Dispatchers.IO) {
            var connection: HttpURLConnection? = null

            try {
                val url = URL(resolveResumeUrl())

                connection = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 10_000
                    readTimeout = 10_000
                    setRequestProperty("Accept", "application/json")
                }

                if (connection.responseCode !in 200..299) {
                    return@withContext 0L
                }

                val body = connection.inputStream
                    .bufferedReader()
                    .use { it.readText() }

                val json = JSONObject(body)

                if (!json.optBoolean("ok", false)) {
                    return@withContext 0L
                }

                val seconds = json.optDouble(
                    "resume_position_seconds",
                    0.0
                )

                (seconds * 1000.0)
                    .toLong()
                    .coerceAtLeast(0L)
            } catch (_: Exception) {
                0L
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun saveResumeProgress(completed: Boolean) {
        if (
            !isRecordingPlayback ||
            resumeUrlPath.isBlank() ||
            !playbackControllerReady
        ) {
            return
        }

        val now = System.currentTimeMillis()

        if (!completed && now - lastResumeSaveMs < 8_000L) {
            return
        }

        lastResumeSaveMs = now

        lifecycleScope.launch {
            saveResumeProgressNow(completed)
        }
    }

    private suspend fun saveResumeProgressNow(completed: Boolean) {
        if (
            !isRecordingPlayback ||
            resumeUrlPath.isBlank() ||
            !playbackControllerReady
        ) {
            return
        }

        val positionMs = playbackController.currentPositionMs()
        val durationMs = playbackController.durationMs()

        withContext(Dispatchers.IO) {
            var connection: HttpURLConnection? = null

            try {
                val url = URL(resolveResumeUrl())

                val json = JSONObject().apply {
                    put(
                        "position_seconds",
                        positionMs.toDouble() / 1000.0
                    )
                    put(
                        "duration_seconds",
                        durationMs.toDouble() / 1000.0
                    )
                    put("completed", completed)
                }

                connection = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 10_000
                    readTimeout = 10_000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("Accept", "application/json")
                }

                connection.outputStream
                    .bufferedWriter()
                    .use { writer ->
                        writer.write(json.toString())
                    }

                connection.responseCode
            } catch (_: Exception) {
                // Resume saving must never interrupt playback.
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun resolveResumeUrl(): String {
        val path = resumeUrlPath.trim()

        if (
            path.startsWith("http://", ignoreCase = true) ||
            path.startsWith("https://", ignoreCase = true)
        ) {
            return path
        }

        val baseUrl =
            ApiClient
                .getBaseUrl(this@PlayerActivity)
                .trimEnd('/')

        return if (path.startsWith("/")) {
            "$baseUrl$path"
        } else {
            "$baseUrl/$path"
        }
    }

    private fun formatResumeTime(positionMs: Long): String {
        val totalSeconds = (positionMs / 1000L).coerceAtLeast(0L)
        val hours = totalSeconds / 3600L
        val minutes = (totalSeconds % 3600L) / 60L
        val seconds = totalSeconds % 60L

        return if (hours > 0L) {
            "%d:%02d:%02d".format(hours, minutes, seconds)
        } else {
            "%d:%02d".format(minutes, seconds)
        }
    }

    private fun exitPlayer() {
        /*
         * Normally MainActivity remains below PlayerActivity. If the player
         * is ever the task root, recreate MainActivity before finishing so
         * Back does not return to the Shield home screen.
         */
        if (isTaskRoot) {
            val intent = Intent(this, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                            Intent.FLAG_ACTIVITY_SINGLE_TOP
                )
            }

            startActivity(intent)
        }

        finish()
    }

    private fun showFatalError(msg: String) {
        playerUiController.hideLoading()
        playerUiController.showLongToast(msg)
        exitPlayer()
    }
}