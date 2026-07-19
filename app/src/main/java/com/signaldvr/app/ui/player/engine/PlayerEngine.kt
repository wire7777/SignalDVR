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
     * Channel changes and recording playback continue to use playUrl() and
     * prepareUrl().
     */
    fun refreshLivePlaylist(url: String)

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

    fun seekRelative(deltaMs: Long) {
        seekTo(
            (currentPositionMs() + deltaMs)
                .coerceAtLeast(0L)
        )
    }

    fun currentPositionMs(): Long

    fun durationMs(): Long

    fun isPlaying(): Boolean

    fun setMuted(muted: Boolean)

    fun release()
}