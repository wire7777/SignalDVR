package com.signaldvr.app.ui.player.controller

import android.content.Context
import android.os.Handler
import android.view.View
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import com.signaldvr.app.R
import com.signaldvr.app.ui.player.TimelineView

class PlayerUiController(
    private val context: Context,
    private val handler: Handler,
    private val dvrBar: View,
    private val defaultFocusView: TextView,
    private val loadingBar: ProgressBar,
    private val loadingText: TextView,
    private val recordButton: TextView,
    private val positionText: TextView,
    private val timelineView: TimelineView
) {
    private var dvrDismiss: Runnable? = null
    private var loadingRunnable: Runnable? = null
    private var currentToast: Toast? = null


    fun applyDvrButtonFocus(vararg buttons: TextView) {
        buttons.forEach { button ->
            button.setOnFocusChangeListener { view, hasFocus ->
                view.setBackgroundResource(
                    if (hasFocus) R.drawable.bg_epg_focused
                    else R.drawable.bg_channel_normal
                )
            }
        }
    }


    fun configureDvrButtons(
        btnRew60: TextView,
        btnRew30: TextView,
        btnRew10: TextView,
        btnPlayPause: TextView,
        btnFf10: TextView,
        btnFf30: TextView,
        btnFf60: TextView,
        btnLive: TextView,
        btnRecord: TextView,
        onRew60: () -> Unit,
        onRew30: () -> Unit,
        onRew10: () -> Unit,
        onPlayPause: () -> Unit,
        onFf10: () -> Unit,
        onFf30: () -> Unit,
        onFf60: () -> Unit,
        onLive: () -> Unit,
        onRecord: () -> Unit
    ) {
        applyDvrButtonFocus(
            btnRew60,
            btnRew30,
            btnRew10,
            btnPlayPause,
            btnFf10,
            btnFf30,
            btnFf60,
            btnLive,
            btnRecord
        )

        btnRew60.setOnClickListener { onRew60() }
        btnRew30.setOnClickListener { onRew30() }
        btnRew10.setOnClickListener { onRew10() }

        btnPlayPause.setOnClickListener { onPlayPause() }

        btnFf10.setOnClickListener { onFf10() }
        btnFf30.setOnClickListener { onFf30() }
        btnFf60.setOnClickListener { onFf60() }

        btnLive.setOnClickListener { onLive() }
        btnRecord.setOnClickListener { onRecord() }
    }

    fun showDvrBar() {
        dvrBar.visibility = View.VISIBLE
        defaultFocusView.requestFocus()
        scheduleDvrDismiss()
    }

    fun hideDvrBar() {
        dvrBar.visibility = View.GONE
    }

    fun scheduleDvrDismiss() {
        dvrDismiss?.let { handler.removeCallbacks(it) }
        dvrDismiss = Runnable { hideDvrBar() }
        handler.postDelayed(dvrDismiss!!, 5000)
    }

    fun showLoading(message: String) {
        cancelPendingLoading()
        loadingBar.visibility = View.VISIBLE
        loadingText.visibility = View.VISIBLE
        loadingText.text = message
    }

    fun showLoadingDelayed(message: String, delayMs: Long = 750L) {
        cancelPendingLoading()
        loadingRunnable = Runnable {
            loadingBar.visibility = View.VISIBLE
            loadingText.visibility = View.VISIBLE
            loadingText.text = message
        }
        handler.postDelayed(loadingRunnable!!, delayMs)
    }

    fun hideLoading() {
        cancelPendingLoading()
        loadingBar.visibility = View.GONE
        loadingText.visibility = View.GONE
    }

    private fun cancelPendingLoading() {
        loadingRunnable?.let { handler.removeCallbacks(it) }
        loadingRunnable = null
    }

    fun clearTransientMessages() {
        hideLoading()
        currentToast?.cancel()
        currentToast = null
    }

    fun showPlayIcon() {
        defaultFocusView.text = "▶"
    }

    fun showPauseIcon() {
        defaultFocusView.text = "⏸"
    }

    fun hideRecordButton() {
        recordButton.visibility = View.GONE
    }

    fun updatePositionLabel(label: String) {
        positionText.text = label
    }

    fun clearPositionLabel() {
        positionText.text = ""
        timelineView.setPlayback(isLive = false, behindLiveSeconds = 0, isRecordingPlayback = true)
    }


    fun setTimelineInfo(title: String, subtitle: String) {
        timelineView.setProgramInfo(title, subtitle)
    }

    fun updateTimeline(
        isLive: Boolean,
        behindLiveSeconds: Int,
        isRecordingPlayback: Boolean
    ) {
        timelineView.setPlayback(
            isLive = isLive,
            behindLiveSeconds = behindLiveSeconds,
            isRecordingPlayback = isRecordingPlayback
        )
    }


    fun updateMediaTimeline(positionMs: Long, durationMs: Long) {
        timelineView.setMediaProgress(positionMs, durationMs)

        if (durationMs > 0L) {
            val remainingMs = (durationMs - positionMs).coerceAtLeast(0L)
            positionText.text =
                "${formatDuration(positionMs)}   •   -${formatDuration(remainingMs)}"
        } else {
            positionText.text = formatDuration(positionMs)
        }
    }

    private fun formatDuration(ms: Long): String {
        val totalSeconds = kotlin.math.max(0L, ms / 1000L)
        val hours = totalSeconds / 3600L
        val minutes = (totalSeconds % 3600L) / 60L
        val seconds = totalSeconds % 60L

        return if (hours > 0) {
            "%d:%02d:%02d".format(hours, minutes, seconds)
        } else {
            "%d:%02d".format(minutes, seconds)
        }
    }


    fun beginTimelinePreview(currentBehindLiveSeconds: Int) {
        timelineView.beginPreview(currentBehindLiveSeconds)
        showDvrBar()
    }

    fun adjustTimelinePreview(deltaSeconds: Int): Int {
        val selectedBehindLiveSeconds = timelineView.adjustPreview(deltaSeconds)
        scheduleDvrDismiss()
        return selectedBehindLiveSeconds
    }

    fun cancelTimelinePreview() {
        timelineView.cancelPreview()
        scheduleDvrDismiss()
    }

    fun confirmTimelinePreview(): Int {
        val selectedBehindLiveSeconds = timelineView.confirmPreview()
        scheduleDvrDismiss()
        return selectedBehindLiveSeconds
    }

    fun showRecordingActive() {
        recordButton.text = "⏹ STOP"
        recordButton.setTextColor(0xFFFFFFFF.toInt())
    }

    fun showRecordingInactive() {
        recordButton.text = "⏺ REC"
        recordButton.setTextColor(0xFFFF4444.toInt())
    }

    fun showShortToast(message: String) {
        currentToast?.cancel()
        currentToast = Toast.makeText(context, message, Toast.LENGTH_SHORT)
        currentToast?.show()
    }

    fun showLongToast(message: String) {
        currentToast?.cancel()
        currentToast = Toast.makeText(context, message, Toast.LENGTH_LONG)
        currentToast?.show()
    }

    fun release() {
        dvrDismiss?.let { handler.removeCallbacks(it) }
        dvrDismiss = null
        cancelPendingLoading()
        currentToast?.cancel()
        currentToast = null
    }
}