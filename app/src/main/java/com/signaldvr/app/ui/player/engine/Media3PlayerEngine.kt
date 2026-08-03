package com.signaldvr.app.ui.player.engine

import android.content.Context
import android.media.MediaFormat
import android.os.SystemClock
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionParameters
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector
import androidx.media3.exoplayer.video.VideoFrameMetadataListener
import androidx.media3.ui.PlayerView

@OptIn(UnstableApi::class)
class Media3PlayerEngine(
    context: Context,
    private val playerView: PlayerView,
) : PlayerEngine {

    companion object {
        private const val TAG = "SignalDVR-Media3"
    }

    private var listener: PlayerEngine.Listener? = null
    private var listenerAttached = false
    private var muted = false
    private var liveRefreshGeneration = 0L
    private var pendingJumpToLive = false

    @Volatile
    private var lastRenderedVideoFrameMs = 0L

    /*
     * Allow Media3 to try another decoder if the preferred hardware decoder
     * cannot initialize the MPEG-2 video or AC-3 audio stream.
     */
    private val renderersFactory =
        DefaultRenderersFactory(context)
            .setEnableDecoderFallback(true)

    /*
     * Prefer the English audio track when multiple broadcast audio tracks
     * are present.
     */
    private val trackSelector =
        DefaultTrackSelector(context).apply {
            parameters = buildUponParameters()
                .setPreferredAudioLanguage("en")
                .setPreferredTextLanguage("en")
                .setSelectUndeterminedTextLanguage(false)
                .build()
        }

    private val loadControl =
        DefaultLoadControl.Builder()
            .setBufferDurationsMs(
                10_000,  // Minimum buffer
                45_000,  // Maximum buffer
                1_200,   // Buffer before initial playback
                5_000,   // Buffer required after a rebuffer
            )
            .build()

    private val audioAttributes =
        AudioAttributes.Builder()
            .setUsage(C.USAGE_MEDIA)
            .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
            .build()

    private val player: ExoPlayer =
        ExoPlayer.Builder(
            context,
            renderersFactory,
        )
            .setTrackSelector(trackSelector)
            .setLoadControl(loadControl)
            .setAudioAttributes(
                audioAttributes,
                true,
            )
            .build()

    override fun setListener(
        listener: PlayerEngine.Listener?
    ) {
        this.listener = listener
    }

    override fun attach() {
        playerView.player = player
        playerView.useController = false
        playerView.keepScreenOn = true

        player.setVideoFrameMetadataListener(
            object : VideoFrameMetadataListener {
                override fun onVideoFrameAboutToBeRendered(
                    presentationTimeUs: Long,
                    releaseTimeNs: Long,
                    format: Format,
                    mediaFormat: MediaFormat?,
                ) {
                    /*
                     * This callback represents real video output. Media3's
                     * currentPosition can continue advancing while the decoder
                     * or Surface is wedged, so position alone cannot prove that
                     * playback recovered.
                     */
                    lastRenderedVideoFrameMs = SystemClock.elapsedRealtime()
                }
            }
        )

        if (listenerAttached) {
            return
        }

        listenerAttached = true

        player.addListener(
            object : Player.Listener {

                override fun onPlaybackStateChanged(
                    playbackState: Int
                ) {
                    when (playbackState) {
                        Player.STATE_BUFFERING -> {
                            Log.d(TAG, "Playback buffering")
                            listener?.onOpening()
                        }

                        Player.STATE_READY -> {
                            if (pendingJumpToLive) {
                                pendingJumpToLive = false

                                Log.d(
                                    TAG,
                                    "Jumping to live edge after playlist refresh"
                                )

                                player.seekToDefaultPosition()
                            }

                            Log.d(
                                TAG,
                                "Playback ready; " +
                                        "isPlaying=${player.isPlaying}"
                            )

                            if (player.isPlaying) {
                                listener?.onPlaying()
                            }
                        }

                        Player.STATE_ENDED -> {
                            Log.d(TAG, "Playback ended")
                            listener?.onEndReached()
                        }

                        Player.STATE_IDLE -> {
                            Log.d(TAG, "Playback idle")
                        }
                    }
                }

                override fun onIsPlayingChanged(
                    isPlaying: Boolean
                ) {
                    Log.d(
                        TAG,
                        "isPlaying changed: $isPlaying"
                    )

                    if (isPlaying) {
                        listener?.onPlaying()
                    } else if (
                        player.playbackState == Player.STATE_READY &&
                        !player.playWhenReady
                    ) {
                        /*
                         * Do not treat transient READY/isPlaying=false during
                         * HLS recovery as an intentional user pause.
                         */
                        listener?.onPaused()
                    }
                }

                override fun onTracksChanged(
                    tracks: Tracks
                ) {
                    Log.d(
                        TAG,
                        "Tracks changed: " +
                                "${tracks.groups.size} groups"
                    )

                    var audioTrackFound = false
                    var selectedAudioTrackFound = false

                    tracks.groups.forEachIndexed {
                            groupIndex,
                            group ->

                        for (
                        trackIndex in
                        0 until group.length
                        ) {
                            val format =
                                group.getTrackFormat(
                                    trackIndex
                                )

                            val selected =
                                group.isTrackSelected(
                                    trackIndex
                                )

                            val supported =
                                group.isTrackSupported(
                                    trackIndex
                                )

                            val isAudio =
                                group.type == C.TRACK_TYPE_AUDIO

                            if (isAudio) {
                                audioTrackFound = true

                                if (selected) {
                                    selectedAudioTrackFound =
                                        true
                                }
                            }

                            Log.d(
                                TAG,
                                "group=$groupIndex " +
                                        "track=$trackIndex " +
                                        "type=${trackTypeName(group.type)} " +
                                        "mime=${format.sampleMimeType} " +
                                        "codecs=${format.codecs} " +
                                        "language=${format.language} " +
                                        "channels=${format.channelCount} " +
                                        "sampleRate=${format.sampleRate} " +
                                        "bitrate=${format.bitrate} " +
                                        "selected=$selected " +
                                        "supported=$supported"
                            )
                        }
                    }

                    when {
                        !audioTrackFound -> {
                            Log.e(
                                TAG,
                                "No audio track was detected"
                            )
                        }

                        !selectedAudioTrackFound -> {
                            Log.e(
                                TAG,
                                "Audio tracks exist, but none " +
                                        "was selected"
                            )
                        }

                        else -> {
                            Log.d(
                                TAG,
                                "An audio track is selected"
                            )
                        }
                    }
                }

                override fun onAudioAttributesChanged(
                    audioAttributes: AudioAttributes
                ) {
                    Log.d(
                        TAG,
                        "Audio attributes changed: " +
                                "usage=${audioAttributes.usage}, " +
                                "contentType=" +
                                audioAttributes.contentType
                    )
                }

                override fun onVolumeChanged(
                    volume: Float
                ) {
                    Log.d(
                        TAG,
                        "Player volume changed: $volume"
                    )
                }

                override fun onPlayerError(
                    error: PlaybackException
                ) {
                    Log.e(
                        TAG,
                        "Media3 playback error",
                        error,
                    )

                    listener?.onError(
                        error.message
                            ?: "Media3 playback error"
                    )
                }
            }
        )
    }

    override fun playUrl(url: String) {
        if (url.isBlank()) {
            listener?.onError(
                "Playback URL is empty"
            )
            return
        }

        Log.d(
            TAG,
            "Opening playback URL: $url"
        )

        lastRenderedVideoFrameMs = 0L

        /*
         * Clear any old media and force Media3 to inspect the new MPEG-TS/HLS
         * tracks again.
         */
        player.stop()
        player.clearMediaItems()

        val mediaItem =
            MediaItem.Builder()
                .setUri(url)
                .build()

        player.setMediaItem(
            mediaItem,
            true,
        )

        applyAudioSettings()

        player.prepare()
        player.playWhenReady = true
    }

    override fun prepareUrl(url: String) {
        if (url.isBlank()) {
            listener?.onError(
                "Playback URL is empty"
            )
            return
        }

        Log.d(
            TAG,
            "Preparing playback URL: $url"
        )

        lastRenderedVideoFrameMs = 0L

        player.playWhenReady = false
        player.stop()
        player.clearMediaItems()

        val mediaItem =
            MediaItem.Builder()
                .setUri(url)
                .build()

        player.setMediaItem(
            mediaItem,
            true,
        )

        applyAudioSettings()
        player.prepare()
    }

    override fun refreshLivePlaylist(
        url: String,
        jumpToLiveEdge: Boolean,
        keepPaused: Boolean,
    ) {
        if (url.isBlank()) {
            listener?.onError(
                "Live playlist URL is empty"
            )
            return
        }

        /*
         * The SignalDVR backend rewrites delayed_live.m3u8 after a DVR seek.
         * Add a changing query value so Media3, HTTP caches, and proxies must
         * request the updated playlist instead of reusing an older response.
         */
        val refreshUrl =
            if (url.contains("?")) {
                "$url&sdvr_refresh=${++liveRefreshGeneration}"
            } else {
                "$url?sdvr_refresh=${++liveRefreshGeneration}"
            }

        Log.d(
            TAG,
            "Refreshing live playlist: $refreshUrl"
        )

        lastRenderedVideoFrameMs = 0L

        /*
         * Keep the same ExoPlayer instance. Do not call stop() and do not
         * clearMediaItems(). Replacing the media source is enough to force a
         * fresh HLS manifest request while avoiding the heaviest teardown
         * path used for channel changes.
         */
        /*
         * Capture the desired transport state before replacing the media item.
         * When a seek starts from pause, keepPaused is authoritative and must
         * force playWhenReady=false before prepare() completes. Pausing after
         * prepare() is too late because Media3 may already begin playback.
         */
        val shouldResume =
            !keepPaused &&
                    (
                            player.playWhenReady ||
                                    player.isPlaying
                            )

        val mediaItem =
            MediaItem.Builder()
                .setUri(refreshUrl)
                .setMimeType(MimeTypes.APPLICATION_M3U8)
                .build()

        pendingJumpToLive = jumpToLiveEdge

        /*
         * Apply the requested transport state before the new media source is
         * prepared. This prevents a paused DVR seek from briefly starting and
         * then continuing to play.
         */
        player.playWhenReady = shouldResume

        player.setMediaItem(
            mediaItem,
            true,
        )

        applyAudioSettings()
        player.prepare()

        if (keepPaused) {
            player.pause()
        }
    }

    override fun forceReloadLivePlaylist(
        url: String,
        jumpToLiveEdge: Boolean,
    ) {
        if (url.isBlank()) {
            listener?.onError("Live playlist URL is empty")
            return
        }

        val refreshUrl =
            if (url.contains("?")) {
                "$url&sdvr_refresh=${++liveRefreshGeneration}"
            } else {
                "$url?sdvr_refresh=${++liveRefreshGeneration}"
            }

        Log.w(TAG, "Force reloading live pipeline: $refreshUrl")

        lastRenderedVideoFrameMs = 0L

        val mediaItem =
            MediaItem.Builder()
                .setUri(refreshUrl)
                .setMimeType(MimeTypes.APPLICATION_M3U8)
                .build()

        pendingJumpToLive = jumpToLiveEdge

        // A simple media-item replacement is not enough after a long network
        // outage: Media3 can continue downloading segments while its decoder
        // or render pipeline remains wedged. Stop and clear the media source
        // so prepare() creates a clean HLS/decoder pipeline.
        player.playWhenReady = false
        player.stop()
        player.clearMediaItems()
        player.setMediaItem(mediaItem, true)
        applyAudioSettings()
        player.prepare()
        player.playWhenReady = true
    }

    override fun play() {
        Log.d(TAG, "Play requested")

        applyAudioSettings()
        player.play()
    }

    override fun pause() {
        Log.d(TAG, "Pause requested")
        player.pause()
    }

    /*
     * Media3 uses the standard pause/play path for live HLS playback.
     */
    override fun pauseLiveDvr() {
        pause()
    }

    override fun resumeLiveDvr() {
        play()
    }

    override fun stop() {
        Log.d(TAG, "Stop requested")
        player.stop()
        listener?.onStopped()
    }

    override fun seekTo(positionMs: Long) {
        val target =
            positionMs.coerceAtLeast(0L)

        Log.d(
            TAG,
            "Seek requested: $target ms"
        )

        player.seekTo(target)
    }

    override fun trySeekRelative(deltaMs: Long): Boolean {
        val duration = player.duration

        if (
            duration <= 0L ||
            duration == C.TIME_UNSET ||
            player.currentTimeline.isEmpty
        ) {
            return false
        }

        val current = player.currentPosition.coerceAtLeast(0L)
        val target = current + deltaMs

        /*
         * Do not clamp an out-of-window request. Returning false is important:
         * the controller can then ask the SignalDVR backend to build the
         * correct delayed-live playlist instead of silently seeking to the
         * beginning or end of the currently loaded HLS window.
         */
        if (target < 0L || target > duration) {
            return false
        }

        Log.d(TAG, "Fast in-window seek: $current ms -> $target ms")
        player.seekTo(target)
        return true
    }

    override fun currentPositionMs(): Long {
        return player.currentPosition
            .coerceAtLeast(0L)
    }

    override fun durationMs(): Long {
        val duration = player.duration

        return if (
            duration > 0L &&
            duration != C.TIME_UNSET
        ) {
            duration
        } else {
            0L
        }
    }

    override fun isPlaying(): Boolean {
        return player.isPlaying
    }

    override fun lastRenderedVideoFrameRealtimeMs(): Long {
        return lastRenderedVideoFrameMs
    }

    override fun setMuted(muted: Boolean) {
        this.muted = muted
        applyAudioSettings()
    }

    private fun applyAudioSettings() {
        player.volume =
            if (muted) {
                0f
            } else {
                1f
            }

        /*
         * Ensure the audio renderer has not accidentally been disabled by a
         * previous track-selection state.
         */
        val currentParameters:
                TrackSelectionParameters =
            player.trackSelectionParameters

        player.trackSelectionParameters =
            currentParameters
                .buildUpon()
                .setPreferredAudioLanguage("en")
                .setTrackTypeDisabled(
                    C.TRACK_TYPE_AUDIO,
                    false,
                )
                .build()

        Log.d(
            TAG,
            "Audio settings applied: " +
                    "muted=$muted, " +
                    "volume=${player.volume}"
        )
    }

    private fun trackTypeName(
        trackType: Int
    ): String {
        return when (trackType) {
            C.TRACK_TYPE_AUDIO -> "audio"
            C.TRACK_TYPE_VIDEO -> "video"
            C.TRACK_TYPE_TEXT -> "text"
            C.TRACK_TYPE_IMAGE -> "image"
            C.TRACK_TYPE_METADATA -> "metadata"
            else -> "unknown-$trackType"
        }
    }

    override fun release() {
        Log.d(TAG, "Releasing Media3 player")

        playerView.player = null
        player.release()

        listener = null
        listenerAttached = false
    }
}