package com.signaldvr.app.ui.player

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min

class TimelineView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val titlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFFFFF.toInt()
        textSize = 26f
        isFakeBoldText = true
    }

    private val subtitlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xB3FFFFFF.toInt()
        textSize = 20f
    }

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xCCFFFFFF.toInt()
        textSize = 22f
    }

    private val rightLabelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xCCFFFFFF.toInt()
        textSize = 22f
        textAlign = Paint.Align.RIGHT
        isFakeBoldText = true
    }

    private val liveLabelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF00E676.toInt()
        textSize = 22f
        textAlign = Paint.Align.RIGHT
        isFakeBoldText = true
    }

    private val statusPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFCC00.toInt()
        textSize = 22f
        textAlign = Paint.Align.CENTER
        isFakeBoldText = true
    }

    private val previewPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF4FC3F7.toInt()
        textSize = 23f
        textAlign = Paint.Align.CENTER
        isFakeBoldText = true
    }

    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x2BFFFFFF
        strokeCap = Paint.Cap.ROUND
    }

    private val bufferPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x55FFFFFF
        strokeCap = Paint.Cap.ROUND
    }

    private val progressPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFCC00.toInt()
        strokeCap = Paint.Cap.ROUND
    }

    private val previewProgressPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF4FC3F7.toInt()
        strokeCap = Paint.Cap.ROUND
    }

    private val remainingPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x22FFFFFF
        strokeCap = Paint.Cap.ROUND
    }

    private val liveEdgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF00E676.toInt()
        strokeCap = Paint.Cap.ROUND
    }

    private val liveGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x3300E676
    }

    private val playheadGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x44FFFFCC.toInt()
    }

    private val previewGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x554FC3F7.toInt()
    }

    private val playheadPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFFFFFF.toInt()
    }

    private val liveDotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF00E676.toInt()
    }

    private val liveTickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF00E676.toInt()
        strokeCap = Paint.Cap.ROUND
    }

    private var programTitle = "Now Playing"
    private var programSubtitle = ""

    private var isLive = true
    private var behindLiveSeconds = 0
    private var isRecordingPlayback = false

    private var playbackPositionMs = 0L
    private var playbackDurationMs = 0L

    private var isPreviewing = false
    private var previewBehindLiveSeconds = 0

    private var displayedFraction = 1f
    private var targetFraction = 1f
    private var animator: ValueAnimator? = null

    private val timeFormatter = SimpleDateFormat("h:mm a", Locale.getDefault())

    fun setProgramInfo(title: String, subtitle: String = "") {
        programTitle = title.ifBlank { "Now Playing" }
        programSubtitle = subtitle
        invalidate()
    }

    fun setPlayback(
        isLive: Boolean,
        behindLiveSeconds: Int,
        isRecordingPlayback: Boolean
    ) {
        this.isLive = isLive
        this.behindLiveSeconds = max(0, behindLiveSeconds)
        this.isRecordingPlayback = isRecordingPlayback

        if (!isRecordingPlayback) {
            if (!isPreviewing) animateTo(livePositionFraction()) else invalidate()
        } else {
            animateTo(playbackPositionFraction())
        }
    }

    fun setMediaProgress(positionMs: Long, durationMs: Long) {
        playbackPositionMs = max(0L, positionMs)
        playbackDurationMs = max(0L, durationMs)
        isRecordingPlayback = true

        if (!isPreviewing) {
            animateTo(playbackPositionFraction())
        } else {
            invalidate()
        }
    }

    fun beginPreview(currentBehindLiveSeconds: Int) {
        if (isRecordingPlayback) return

        isPreviewing = true
        previewBehindLiveSeconds = max(0, currentBehindLiveSeconds)
        animateTo(previewPositionFraction())
    }

    fun adjustPreview(deltaSeconds: Int): Int {
        if (isRecordingPlayback) return 0
        if (!isPreviewing) {
            beginPreview(behindLiveSeconds)
        }

        previewBehindLiveSeconds = (previewBehindLiveSeconds + deltaSeconds)
            .coerceIn(0, VISIBLE_WINDOW_SECONDS)
        animateTo(previewPositionFraction())
        return previewBehindLiveSeconds
    }

    fun cancelPreview() {
        isPreviewing = false
        animateTo(if (isRecordingPlayback) playbackPositionFraction() else livePositionFraction())
    }

    fun confirmPreview(): Int {
        val selectedSeconds = previewBehindLiveSeconds
        isPreviewing = false
        behindLiveSeconds = selectedSeconds
        animateTo(livePositionFraction())
        return selectedSeconds
    }

    override fun onDetachedFromWindow() {
        animator?.cancel()
        animator = null
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val widthF = width.toFloat()
        val heightF = height.toFloat()
        if (widthF <= 0f || heightF <= 0f) return

        val horizontalPadding = 30f
        val startX = horizontalPadding
        val endX = widthF - horizontalPadding
        val trackWidth = endX - startX
        val titleY = 24f
        val subtitleY = 46f
        val labelY = 72f
        val trackY = heightF - 22f
        val playheadX = startX + (trackWidth * displayedFraction.coerceIn(0f, 1f))
        val pulse = livePulse()

        drawProgramInfo(canvas, startX, titleY, subtitleY)
        drawLabels(canvas, startX, endX, labelY, pulse)
        drawStatus(canvas, startX, endX, labelY)
        drawTimeline(canvas, startX, endX, trackY, playheadX, pulse)

        // Keep live pulse alive. Playback mode is updated by PlayerActivity from VLC time.
        if (!isRecordingPlayback) {
            postInvalidateOnAnimation()
        }
    }

    private fun drawProgramInfo(canvas: Canvas, startX: Float, titleY: Float, subtitleY: Float) {
        canvas.drawText(programTitle, startX, titleY, titlePaint)
        if (programSubtitle.isNotBlank()) {
            canvas.drawText(programSubtitle, startX, subtitleY, subtitlePaint)
        }
    }

    private fun drawLabels(canvas: Canvas, startX: Float, endX: Float, labelY: Float, pulse: Float) {
        labelPaint.textAlign = Paint.Align.LEFT

        if (isRecordingPlayback) {
            canvas.drawText(formatDuration(playbackPositionMs), startX, labelY, labelPaint)
            rightLabelPaint.color = 0xCCFFFFFF.toInt()
            canvas.drawText(formatDuration(playbackDurationMs), endX, labelY, rightLabelPaint)
        } else {
            canvas.drawText(leftTimeLabel(), startX, labelY, labelPaint)
            liveLabelPaint.color = 0xFF00E676.toInt()
            canvas.drawCircle(endX - 58f, labelY - 7f, 5f + (pulse * 2f), liveDotPaint)
            canvas.drawText("LIVE", endX, labelY, liveLabelPaint)
        }
    }

    private fun drawStatus(canvas: Canvas, startX: Float, endX: Float, labelY: Float) {
        val text = if (isRecordingPlayback) {
            if (playbackDurationMs > 0L) {
                "${formatDuration(playbackPositionMs)} / ${formatDuration(playbackDurationMs)}"
            } else {
                "Playback"
            }
        } else {
            when {
                isPreviewing -> "Preview: ${behindLabel(previewBehindLiveSeconds)} behind live"
                isLive || behindLiveSeconds <= 0 -> "Watching live"
                else -> "${behindLabel(behindLiveSeconds)} behind live"
            }
        }

        canvas.drawText(text, (startX + endX) / 2f, labelY, if (isPreviewing) previewPaint else statusPaint)
    }

    private fun drawTimeline(
        canvas: Canvas,
        startX: Float,
        endX: Float,
        trackY: Float,
        playheadX: Float,
        pulse: Float
    ) {
        val stroke = 10f
        val liveEdgeWidth = 30f
        val liveEdgeStartX = endX - liveEdgeWidth

        trackPaint.strokeWidth = stroke
        bufferPaint.strokeWidth = stroke
        progressPaint.strokeWidth = stroke
        previewProgressPaint.strokeWidth = stroke
        remainingPaint.strokeWidth = stroke
        liveEdgePaint.strokeWidth = stroke
        liveTickPaint.strokeWidth = 4f

        canvas.drawLine(startX, trackY, endX, trackY, trackPaint)

        if (isRecordingPlayback) {
            canvas.drawLine(startX, trackY, endX, trackY, remainingPaint)
            canvas.drawLine(startX, trackY, playheadX, trackY, progressPaint)
        } else {
            canvas.drawLine(startX, trackY, endX, trackY, bufferPaint)
            if (playheadX < liveEdgeStartX) {
                canvas.drawLine(playheadX, trackY, liveEdgeStartX, trackY, remainingPaint)
            }

            val liveGlowRadius = 18f + (pulse * 14f)
            canvas.drawCircle(endX, trackY, liveGlowRadius, liveGlowPaint)
            canvas.drawLine(liveEdgeStartX, trackY, endX, trackY, liveEdgePaint)
            canvas.drawLine(endX, trackY - 16f, endX, trackY + 16f, liveTickPaint)

            canvas.drawLine(
                startX,
                trackY,
                playheadX,
                trackY,
                if (isPreviewing) previewProgressPaint else progressPaint
            )
        }

        val glowRadius = when {
            isPreviewing -> 24f + (pulse * 8f)
            !isRecordingPlayback && (isLive || behindLiveSeconds <= 0) -> 20f + (pulse * 10f)
            else -> 20f
        }

        val glowRect = RectF(
            playheadX - glowRadius,
            trackY - glowRadius,
            playheadX + glowRadius,
            trackY + glowRadius
        )
        val headRect = RectF(playheadX - 10f, trackY - 10f, playheadX + 10f, trackY + 10f)
        canvas.drawOval(glowRect, if (isPreviewing) previewGlowPaint else playheadGlowPaint)
        canvas.drawOval(headRect, playheadPaint)
    }

    private fun animateTo(newFraction: Float) {
        val clamped = newFraction.coerceIn(0f, 1f)
        if (targetFraction == clamped) {
            invalidate()
            return
        }

        targetFraction = clamped
        animator?.cancel()
        animator = ValueAnimator.ofFloat(displayedFraction, targetFraction).apply {
            duration = 180L
            interpolator = DecelerateInterpolator()
            addUpdateListener { valueAnimator ->
                displayedFraction = valueAnimator.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    private fun livePositionFraction(): Float {
        if (isLive || behindLiveSeconds <= 0) return 1f

        val fractionBehind = behindLiveSeconds / VISIBLE_WINDOW_SECONDS.toFloat()
        return min(1f, max(0f, 1f - fractionBehind))
    }

    private fun playbackPositionFraction(): Float {
        val duration = playbackDurationMs
        if (duration <= 0L) return 0f

        return (playbackPositionMs.toFloat() / duration.toFloat()).coerceIn(0f, 1f)
    }

    private fun previewPositionFraction(): Float {
        val fractionBehind = previewBehindLiveSeconds / VISIBLE_WINDOW_SECONDS.toFloat()
        return min(1f, max(0f, 1f - fractionBehind))
    }

    private fun leftTimeLabel(): String {
        val now = System.currentTimeMillis()
        val bufferStart = now - (VISIBLE_WINDOW_SECONDS.toLong() * 1000L)
        return timeFormatter.format(Date(bufferStart))
    }

    private fun behindLabel(secondsBehind: Int): String {
        val safeSeconds = max(0, secondsBehind)
        val minutes = safeSeconds / 60
        val seconds = safeSeconds % 60
        return if (minutes > 0) {
            if (seconds > 0) "${minutes}m ${seconds}s" else "${minutes}m"
        } else {
            "${seconds}s"
        }
    }

    private fun formatDuration(ms: Long): String {
        val totalSeconds = max(0L, ms / 1000L)
        val hours = totalSeconds / 3600L
        val minutes = (totalSeconds % 3600L) / 60L
        val seconds = totalSeconds % 60L

        return if (hours > 0) {
            "%d:%02d:%02d".format(hours, minutes, seconds)
        } else {
            "%d:%02d".format(minutes, seconds)
        }
    }

    private fun livePulse(): Float {
        val cycleMs = 1400L
        val progress = (System.currentTimeMillis() % cycleMs).toFloat() / cycleMs.toFloat()
        return ((1f - cos(progress * Math.PI.toFloat() * 2f)) / 2f).coerceIn(0f, 1f)
    }

    companion object {
        private const val VISIBLE_WINDOW_SECONDS = 30 * 60
    }
}
