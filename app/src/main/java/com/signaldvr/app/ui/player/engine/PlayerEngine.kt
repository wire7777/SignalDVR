package com.signaldvr.app.ui.player.engine

interface PlayerEngine {

    interface Listener {
        fun onOpening()
        fun onPlaying()
        fun onPaused()
        fun onStopped()
        fun onEndReached()
        fun onError(message: String)
    }

    fun setListener(listener: Listener?)

    fun attach()

    /**
     * Load and start playback immediately.
     */
    fun playUrl(url: String)

    /**
     * Load media without starting playback.
     *
     * Used for recording resume so the engine can receive a seek position
     * before audio/video output begins.
     */
    fun prepareUrl(url: String)

    /**
     * Refresh a live or delayed-live HLS playlist after a server-side DVR
     * seek without stopping and clearing the entire ExoPlayer instance.
     *
     * When jumpToLiveEdge is true, the player must explicitly move to the
     * newest available position in the live playlist.
     *
     * Channel changes and recording playback continue to use playUrl() and
     * prepareUrl().
     */
    fun refreshLivePlaylist(
        url: String,
        jumpToLiveEdge: Boolean = false,
        keepPaused: Boolean = false,
    )

    /**
     * Fully rebuild the current live media source after Media3 reports READY
     * but playback remains frozen. This keeps the ExoPlayer instance and
     * PlayerView, but tears down the wedged HLS/decoder pipeline.
     */
    fun forceReloadLivePlaylist(
        url: String,
        jumpToLiveEdge: Boolean = true,
    )

    fun play()

    fun pause()

    /**
     * DVR-style pause for live streams.
     *
     * A player engine may stop its active media instance so that live HLS
     * buffering cannot advance while paused.
     */
    fun pauseLiveDvr() {
        pause()
    }

    /**
     * Resume a DVR-paused live stream.
     */
    fun resumeLiveDvr() {
        play()
    }

    fun stop()

    fun seekTo(positionMs: Long)

    /**
     * Attempt a relative seek inside the media timeline Media3 already has.
     *
     * Returns false when the requested position falls outside the currently
     * loaded HLS window, allowing SignalDVR to fall back to its server-side
     * delayed-live playlist seek.
     */
    fun trySeekRelative(deltaMs: Long): Boolean {
        return false
    }

    fun seekRelative(deltaMs: Long) {
        seekTo(
            (currentPositionMs() + deltaMs)
                .coerceAtLeast(0L)
        )
    }

    fun currentPositionMs(): Long

    fun durationMs(): Long

    fun isPlaying(): Boolean

    /**
     * Monotonic elapsed-realtime timestamp of the most recent video frame
     * delivered to the renderer. Returns 0 until a frame has rendered.
     */
    fun lastRenderedVideoFrameRealtimeMs(): Long

    fun setMuted(muted: Boolean)

    /** Enable or disable closed-caption text tracks. */
    fun setClosedCaptionsEnabled(enabled: Boolean)

    fun release()
}