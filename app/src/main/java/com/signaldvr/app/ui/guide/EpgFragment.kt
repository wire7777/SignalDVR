package com.signaldvr.app.ui.guide

import android.animation.ValueAnimator
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.KeyEvent
import android.view.animation.LinearInterpolator
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.request.target.CustomTarget
import com.bumptech.glide.request.transition.Transition
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import com.signaldvr.app.api.EpgProgram
import com.signaldvr.app.api.GuideRecordRequest
import com.signaldvr.app.ui.player.PlayerActivity
import com.signaldvr.app.util.TimeUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.launch
import java.util.Calendar
import java.util.Collections

class EpgFragment : Fragment() {

    private lateinit var timeHeader: LinearLayout
    private lateinit var channelList: RecyclerView
    private lateinit var loading: View
    private lateinit var errorText: TextView
    private lateinit var nowPlayhead: View
    private lateinit var nowPlayheadLabel: TextView

    private lateinit var detailsBackdrop: ImageView
    private lateinit var detailsArtwork: ImageView
    private lateinit var detailsChannel: TextView
    private lateinit var detailsTitle: TextView
    private lateinit var detailsMetadata: TextView
    private lateinit var detailsTime: TextView
    private lateinit var detailsDescription: TextView
    private lateinit var detailsProgress: ProgressBar
    private lateinit var detailsProgressText: TextView

    private val playheadHandler = Handler(Looper.getMainLooper())
    private val artworkHandler = Handler(Looper.getMainLooper())
    private var pendingArtworkLoad: Runnable? = null
    private var pendingArtworkKey: String? = null
    private var focusedArtworkTarget: CustomTarget<Drawable>? = null
    private var playheadAnimator: ValueAnimator? = null
    private var timelineShiftAnimator: ValueAnimator? = null
    private var timelineAutoShiftInProgress = false
    private val playheadUpdater = object : Runnable {
        override fun run() {
            // Only the first arrival flies in. After that, position the red
            // line, dot, and NOW label together without starting another
            // animator. Repeated 900 ms animations made the dot appear to
            // chase the line as time advanced.
            updateNowPlayhead(animate = firstPlayheadArrivalPending)
            updateCurrentTimeHeader()
            playheadHandler.postDelayed(this, 1_000L)
        }
    }

    private val minuteWidthDp = 7
    private val rowHeightDp = 42
    private val windowHours = 24

    private val initialGuideChannelCount = 12

    private fun dp(value: Int): Int {
        return (value * resources.displayMetrics.density)
            .toInt()
            .coerceAtLeast(1)
    }

    // Mutable collections are shared with EpgAdapter so later rows can be
    // appended without rebuilding the guide, resetting focus, or replaying
    // the NOW-line entrance animation.
    private val channels = mutableListOf<Channel>()
    private val guide = mutableMapOf<String, List<EpgProgram>>()

    // Cache full per-channel guide rows so the main guide details panel can
    // show complete season/episode metadata without repeatedly calling the API.
    private val fullGuideCache = mutableMapOf<String, List<EpgProgram>>()
    private var focusedProgramKey: String? = null

    private var sharedScrollX = 0
    private var guideWindowStartMs = 0L
    private var epgAdapter: EpgAdapter? = null

    // Preserve the original guide entrance: on the first valid playhead update,
    // the line, NOW label, and red dot animate in from their old top-left origin.
    private var firstPlayheadArrivalPending = true

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(
            R.layout.fragment_epg,
            container,
            false
        )
    }

    override fun onViewCreated(
        view: View,
        savedInstanceState: Bundle?
    ) {
        timeHeader = view.findViewById(R.id.epg_time_header)
        channelList = view.findViewById(R.id.epg_channel_list)
        loading = view.findViewById(R.id.loading)
        errorText = view.findViewById(R.id.error_text)
        nowPlayhead = view.findViewById(R.id.epg_now_playhead)
        nowPlayheadLabel = view.findViewById(R.id.epg_now_playhead_label)


        detailsBackdrop = view.findViewById(R.id.epg_details_backdrop)
        detailsArtwork = view.findViewById(R.id.epg_details_artwork)
        detailsChannel = view.findViewById(R.id.epg_details_channel)
        detailsTitle = view.findViewById(R.id.epg_details_title)
        detailsMetadata = view.findViewById(R.id.epg_details_metadata)
        detailsTime = view.findViewById(R.id.epg_details_time)
        detailsDescription = view.findViewById(R.id.epg_details_description)
        detailsProgress = view.findViewById(R.id.epg_details_progress)
        detailsProgressText = view.findViewById(R.id.epg_details_progress_text)

        firstPlayheadArrivalPending = true
        configureNowPlayhead()

        channelList.layoutManager = LinearLayoutManager(requireContext())
        channelList.setHasFixedSize(true)
        channelList.descendantFocusability = ViewGroup.FOCUS_AFTER_DESCENDANTS
        channelList.itemAnimator = null

        loadGuide()

        view.post {
            updateNowPlayhead(animate = false)
            updateCurrentTimeHeader()
            playheadHandler.removeCallbacks(playheadUpdater)
            playheadHandler.post(playheadUpdater)
        }
    }

    override fun onDestroyView() {
        playheadHandler.removeCallbacks(playheadUpdater)
        pendingArtworkLoad?.let(artworkHandler::removeCallbacks)
        pendingArtworkLoad = null
        pendingArtworkKey = null

        focusedArtworkTarget?.let { target ->
            if (::detailsBackdrop.isInitialized) {
                Glide.with(detailsBackdrop).clear(target)
            }
        }
        focusedArtworkTarget = null

        playheadAnimator?.cancel()
        playheadAnimator = null
        timelineShiftAnimator?.cancel()
        timelineShiftAnimator = null
        timelineAutoShiftInProgress = false

        // Do not request a Fragment-scoped Glide manager while the Fragment is
        // being destroyed. Glide follows the lifecycle automatically; clearing
        // the local drawables is safe and avoids a destroyed-Activity crash.
        if (::detailsBackdrop.isInitialized) {
            detailsBackdrop.setImageDrawable(null)
        }
        if (::detailsArtwork.isInitialized) {
            detailsArtwork.setImageDrawable(null)
        }

        super.onDestroyView()
    }


    private class NowPlayheadDrawable(
        density: Float
    ) : android.graphics.drawable.Drawable() {

        private val red = Color.parseColor("#FFFF3B30")
        private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = red
            strokeWidth = (2f * density).coerceAtLeast(1f)
            strokeCap = Paint.Cap.BUTT
        }
        private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = red
            style = Paint.Style.FILL
        }
        private val dotRadius = 4f * density

        override fun draw(canvas: Canvas) {
            val centerX = bounds.exactCenterX()
            canvas.drawLine(
                centerX,
                dotRadius,
                centerX,
                bounds.bottom.toFloat(),
                linePaint
            )
            canvas.drawCircle(centerX, dotRadius, dotRadius, dotPaint)
        }

        override fun setAlpha(alpha: Int) {
            linePaint.alpha = alpha
            dotPaint.alpha = alpha
            invalidateSelf()
        }

        override fun setColorFilter(colorFilter: ColorFilter?) {
            linePaint.colorFilter = colorFilter
            dotPaint.colorFilter = colorFilter
            invalidateSelf()
        }

        @Deprecated("Deprecated in Java")
        override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
    }

    private fun configureNowPlayhead() {
        val density = resources.displayMetrics.density

        // Draw the red dot and vertical line as ONE view. Because they share
        // the same canvas, the dot can never lag behind or chase the bar.
        nowPlayhead.layoutParams = nowPlayhead.layoutParams.apply {
            width = (8f * density).toInt().coerceAtLeast(1)
        }
        nowPlayhead.background = NowPlayheadDrawable(density)
        nowPlayhead.alpha = 0.90f

        // Clean white NOW label with no box, plus a shadow for readability.
        nowPlayheadLabel.apply {
            text = "Now"
            setTextColor(Color.WHITE)
            textSize = 7f
            setBackgroundColor(Color.TRANSPARENT)
            setPadding(0, 0, 0, 0)
            setShadowLayer(2f * density, 0f, 1f * density, Color.BLACK)
            alpha = 1f
        }

        // The old separate dot view is no longer used. It is explicitly hidden
        // in onViewCreated; the dot is painted inside nowPlayhead itself.
        nowPlayhead.bringToFront()
        nowPlayheadLabel.bringToFront()
    }

    private fun loadGuide() {
        loading.visibility = View.VISIBLE
        errorText.visibility = View.GONE

        lifecycleScope.launch {
            try {
                val api = ApiClient.getApi(requireContext())
                val allChannels = api.getLiveChannels()
                val initialChannels = allChannels.take(initialGuideChannelCount)

                // Fast path: query and render only the first visible group.
                // This endpoint page performs less SQL, transfers less JSON,
                // and avoids constructing 90+ rows before the guide appears.
                val initialGrid = try {
                    api.getGuideGrid(
                        offset = 0,
                        limit = initialGuideChannelCount,
                    ).ifEmpty {
                        fetchGuidePerChannel(api, initialChannels)
                    }
                } catch (e: Exception) {
                    android.util.Log.e(
                        "EpgFragment",
                        "Initial guide page failed; using per-channel fallback",
                        e,
                    )
                    fetchGuidePerChannel(api, initialChannels)
                }

                channels.clear()
                channels.addAll(initialChannels)
                guide.clear()
                fullGuideCache.clear()
                initialChannels.forEach { channel ->
                    guide[channel.number] = initialGrid[channel.number].orEmpty()
                }

                loading.visibility = View.GONE
                buildTimeHeader()
                buildGrid()

                view?.post {
                    updateNowPlayhead(animate = true)
                    updateCurrentTimeHeader()
                }

                // The guide is already visible and usable. Fetch everything
                // below the initial page quietly, then insert only new rows.
                val remainingChannels = allChannels.drop(initialGuideChannelCount)
                if (remainingChannels.isNotEmpty()) {
                    val remainingGrid = try {
                        api.getGuideGrid(
                            offset = initialGuideChannelCount,
                            limit = null,
                        ).ifEmpty {
                            fetchGuidePerChannel(api, remainingChannels)
                        }
                    } catch (e: Exception) {
                        android.util.Log.e(
                            "EpgFragment",
                            "Background guide page failed; using per-channel fallback",
                            e,
                        )
                        fetchGuidePerChannel(api, remainingChannels)
                    }

                    if (isAdded && view != null) {
                        val rows = remainingChannels.associate { channel ->
                            channel.number to remainingGrid[channel.number].orEmpty()
                        }
                        epgAdapter?.appendRows(remainingChannels, rows)
                    }
                }
            } catch (e: Exception) {
                loading.visibility = View.GONE
                errorText.visibility = View.VISIBLE
                errorText.text = "Could not load guide:\n${e.message}"
            }
        }
    }


    /**
     * Fallback for a server that hasn't picked up the new batched
     * /api/live/guide/grid route yet. Slower, but keeps the guide working
     * instead of showing blank program data.
     */
    private suspend fun fetchGuidePerChannel(
        api: com.signaldvr.app.api.SignalDvrApi,
        targetChannels: List<Channel>,
    ): Map<String, List<EpgProgram>> = kotlinx.coroutines.coroutineScope {
        val guideMap = Collections.synchronizedMap(
            mutableMapOf<String, List<EpgProgram>>()
        )

        targetChannels.map { channel ->
            async(Dispatchers.IO) {
                try {
                    guideMap[channel.number] = api.getGuide(channel.number)
                } catch (_: Exception) {
                    guideMap[channel.number] = emptyList()
                }
            }
        }.awaitAll()

        guideMap
    }


    private fun buildTimeHeader() {
        val density = resources.displayMetrics.density
        val now = Calendar.getInstance()

        val startCal = (now.clone() as Calendar).apply {
            set(Calendar.MINUTE, (now.get(Calendar.MINUTE) / 30) * 30)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }

        guideWindowStartMs = startCal.timeInMillis
        timeHeader.removeAllViews()

        var minuteOffset = 0
        while (minuteOffset < windowHours * 60) {
            val cal = (startCal.clone() as Calendar).apply {
                add(Calendar.MINUTE, minuteOffset)
            }

            val timeView = TextView(requireContext()).apply {
                text = TimeUtil.formatDisplay(cal.time)
                layoutParams = LinearLayout.LayoutParams(
                    (minuteWidthDp * 30 * density).toInt(),
                    ViewGroup.LayoutParams.MATCH_PARENT
                )
                gravity = Gravity.CENTER_VERTICAL
                setPadding((8 * density).toInt(), 0, 0, 0)
                setTextColor(Color.WHITE)
                textSize = 12f
                setBackgroundColor(Color.parseColor("#0D0D1A"))
            }

            timeHeader.addView(timeView)
            minuteOffset += 30
        }
    }

    private fun updateNowPlayhead(animate: Boolean = false) {
        if (!::nowPlayhead.isInitialized || !::timeHeader.isInitialized) return
        if (guideWindowStartMs == 0L) return

        maybeAutoAdvanceTimeline()

        val nowMs = System.currentTimeMillis()
        val elapsedMinutes = (nowMs - guideWindowStartMs) / 60_000f

        val density = resources.displayMetrics.density
        val channelColumnWidthPx = 161f * density
        val minuteOffsetPx = elapsedMinutes * minuteWidthDp * density
        val playheadCenterX = channelColumnWidthPx + minuteOffsetPx - sharedScrollX

        val gridLeft = channelColumnWidthPx
        val overlayWidth = (nowPlayhead.parent as? View)?.width?.toFloat() ?: 0f
        val gridRight = overlayWidth.takeIf { it > gridLeft }
            ?: (resources.displayMetrics.widthPixels.toFloat())
        val visible = playheadCenterX in gridLeft..gridRight

        nowPlayhead.visibility = if (visible) View.VISIBLE else View.GONE
        nowPlayheadLabel.visibility = if (visible) View.VISIBLE else View.GONE

        if (!visible) {
            playheadAnimator?.cancel()
            playheadAnimator = null
            return
        }

        val labelWidth = when {
            nowPlayheadLabel.width > 0 -> nowPlayheadLabel.width.toFloat()
            nowPlayheadLabel.measuredWidth > 0 -> nowPlayheadLabel.measuredWidth.toFloat()
            else -> 44f * density
        }

        val lineWidth = when {
            nowPlayhead.width > 0 -> nowPlayhead.width.toFloat()
            nowPlayhead.measuredWidth > 0 -> nowPlayhead.measuredWidth.toFloat()
            else -> 2f * density
        }

        fun placeAllAt(centerX: Float) {
            // One shared center coordinate is used for every element on every
            // animation frame, so the red dot can never chase the red line.
            nowPlayhead.x = centerX - lineWidth / 2f

            nowPlayheadLabel.x = (centerX - labelWidth / 2f).coerceIn(
                gridLeft,
                (gridRight - labelWidth).coerceAtLeast(gridLeft)
            )

        }

        if (!animate || !firstPlayheadArrivalPending) {
            playheadAnimator?.cancel()
            playheadAnimator = null
            placeAllAt(playheadCenterX)
            return
        }

        val currentCenterX = 0f

        playheadAnimator?.cancel()
        playheadAnimator = ValueAnimator.ofFloat(currentCenterX, playheadCenterX).apply {
            duration = 950L
            interpolator = LinearInterpolator()
            addUpdateListener { animator ->
                placeAllAt(animator.animatedValue as Float)
            }
            start()
        }

        firstPlayheadArrivalPending = false
    }


    private fun maybeAutoAdvanceTimeline() {
        if (timelineAutoShiftInProgress) return
        if (!::channelList.isInitialized || !::timeHeader.isInitialized) return
        if (guideWindowStartMs == 0L) return

        val density = resources.displayMetrics.density
        val channelColumnWidthPx = 161f * density
        val viewportWidth = (nowPlayhead.parent as? View)?.width?.toFloat()
            ?.takeIf { it > channelColumnWidthPx }
            ?: return

        val nowMinutes = (System.currentTimeMillis() - guideWindowStartMs) / 60_000f
        val nowContentX = nowMinutes * minuteWidthDp * density
        val visibleTimelineWidth = viewportWidth - channelColumnWidthPx
        val nowVisibleX = nowContentX - sharedScrollX
        val triggerX = visibleTimelineWidth * 0.80f

        if (nowVisibleX < triggerX) return

        val stepPx = (30 * minuteWidthDp * density).toInt()
        val contentWidthPx = (windowHours * 60 * minuteWidthDp * density).toInt()
        val maxScrollX = (contentWidthPx - visibleTimelineWidth.toInt()).coerceAtLeast(0)
        val targetScrollX = (sharedScrollX + stepPx).coerceAtMost(maxScrollX)

        if (targetScrollX <= sharedScrollX) return

        animateTimelineTo(targetScrollX)
    }

    private fun animateTimelineTo(targetScrollX: Int) {
        timelineShiftAnimator?.cancel()

        val startScrollX = sharedScrollX
        if (startScrollX == targetScrollX) return

        timelineAutoShiftInProgress = true
        timelineShiftAnimator = ValueAnimator.ofInt(startScrollX, targetScrollX).apply {
            duration = 420L
            interpolator = LinearInterpolator()
            addUpdateListener { animator ->
                val scrollX = animator.animatedValue as Int
                sharedScrollX = scrollX
                timeHeader.scrollTo(scrollX, 0)
                scrollVisibleProgramRowsTo(scrollX)
                updateNowPlayheadPositionOnly()
            }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) {
                    timelineAutoShiftInProgress = false
                    timelineShiftAnimator = null
                    updateCurrentTimeHeader()
                }

                override fun onAnimationCancel(animation: android.animation.Animator) {
                    timelineAutoShiftInProgress = false
                    timelineShiftAnimator = null
                }
            })
            start()
        }
    }

    private fun scrollVisibleProgramRowsTo(scrollX: Int) {
        for (index in 0 until channelList.childCount) {
            val row = channelList.getChildAt(index)
            row.findViewById<HorizontalScrollView>(R.id.epg_row_scroll)
                ?.scrollTo(scrollX, 0)
        }
    }

    private fun updateNowPlayheadPositionOnly() {
        if (!::nowPlayhead.isInitialized || guideWindowStartMs == 0L) return

        val density = resources.displayMetrics.density
        val channelColumnWidthPx = 161f * density
        val elapsedMinutes = (System.currentTimeMillis() - guideWindowStartMs) / 60_000f
        val centerX = channelColumnWidthPx +
                (elapsedMinutes * minuteWidthDp * density) - sharedScrollX

        val lineWidth = nowPlayhead.width.takeIf { it > 0 }?.toFloat() ?: 2f * density
        val labelWidth = nowPlayheadLabel.width.takeIf { it > 0 }?.toFloat() ?: 44f * density
        val gridRight = (nowPlayhead.parent as? View)?.width?.toFloat()
            ?: resources.displayMetrics.widthPixels.toFloat()

        nowPlayhead.x = centerX - lineWidth / 2f
        nowPlayheadLabel.x = (centerX - labelWidth / 2f).coerceIn(
            channelColumnWidthPx,
            (gridRight - labelWidth).coerceAtLeast(channelColumnWidthPx)
        )

    }

    private fun updateCurrentTimeHeader() {
        if (!::timeHeader.isInitialized || guideWindowStartMs == 0L) return

        val elapsedMinutes = ((System.currentTimeMillis() - guideWindowStartMs) / 60_000L)
            .toInt()
        val currentHeaderIndex = elapsedMinutes / 30

        for (index in 0 until timeHeader.childCount) {
            val header = timeHeader.getChildAt(index) as? TextView ?: continue
            val isCurrent = index == currentHeaderIndex

            header.setBackgroundColor(
                Color.parseColor(if (isCurrent) "#1A1A2E" else "#0D0D1A")
            )
            header.alpha = if (isCurrent) 1f else 0.82f
            header.textSize = if (isCurrent) 13f else 12f
        }
    }


    private fun updateDetails(channel: Channel, program: EpgProgram) {
        val focusKey = "${channel.number}|${program.id}|${program.start.orEmpty()}"
        focusedProgramKey = focusKey

        /*
         * Do not render the compact grid metadata first. The compact row can
         * contain episode=95 while season is still missing, which causes the
         * panel to flash "Episode 95" and then switch to "S6E95" after the full
         * channel guide arrives.
         *
         * Instead, render only a cached or freshly fetched full program record.
         * If the full lookup fails, fall back to the compact row once.
         */
        fullGuideCache[channel.number]?.let { cachedPrograms ->
            val fullProgram = cachedPrograms.firstOrNull { candidate ->
                candidate.id == program.id
            }

            if (fullProgram != null) {
                renderDetails(channel, fullProgram)
                return
            }
        }

        lifecycleScope.launch {
            try {
                val fullPrograms = ApiClient
                    .getApi(requireContext())
                    .getGuide(channel.number)

                fullGuideCache[channel.number] = fullPrograms

                val fullProgram = fullPrograms.firstOrNull { candidate ->
                    candidate.id == program.id
                } ?: program

                if (focusedProgramKey == focusKey) {
                    renderDetails(channel, fullProgram)
                }
            } catch (_: Exception) {
                if (focusedProgramKey == focusKey) {
                    renderDetails(channel, program)
                }
            }
        }
    }

    private fun renderDetails(channel: Channel, program: EpgProgram) {
        detailsChannel.text = "${channel.number}  ${channel.name}"
        detailsTitle.text = program.title.orEmpty().ifBlank {
            "No program information"
        }

        val metadataParts = buildList {
            formatEpisodeLabel(program)?.let(::add)
            program.episodeTitle?.takeIf { it.isNotBlank() }?.let(::add)
            program.subtitle
                ?.takeIf {
                    it.isNotBlank() &&
                            it != program.episodeTitle
                }
                ?.let(::add)
            program.category?.takeIf { it.isNotBlank() }?.let(::add)
            program.rating?.takeIf { it.isNotBlank() }?.let(::add)
            if (program.isNew == 1) add("NEW")
        }

        detailsMetadata.text = metadataParts.joinToString("  •  ")
        detailsMetadata.visibility =
            if (metadataParts.isEmpty()) View.GONE else View.VISIBLE

        detailsDescription.text = program.description
            ?.takeIf { it.isNotBlank() }
            ?: "Program description is not available."

        val start = TimeUtil.parseEpg(program.start)
        val stop = TimeUtil.parseEpg(program.stop)

        if (start != null && stop != null) {
            detailsTime.text =
                "${TimeUtil.formatDisplay(start)} – ${TimeUtil.formatDisplay(stop)}"

            val nowMs = System.currentTimeMillis()
            val durationMs = (stop.time - start.time).coerceAtLeast(1L)
            val elapsedMs = (nowMs - start.time).coerceIn(0L, durationMs)
            val isCurrent = nowMs in start.time until stop.time

            if (isCurrent) {
                val progress = ((elapsedMs * 100L) / durationMs).toInt()
                val remainingMinutes =
                    ((stop.time - nowMs + 59_999L) / 60_000L)
                        .coerceAtLeast(0L)

                detailsProgress.progress = progress
                detailsProgress.visibility = View.VISIBLE
                detailsProgressText.visibility = View.VISIBLE
                detailsProgressText.text =
                    if (remainingMinutes == 1L) {
                        "LIVE  •  1 minute left"
                    } else {
                        "LIVE  •  $remainingMinutes minutes left"
                    }
            } else {
                detailsProgress.visibility = View.INVISIBLE
                detailsProgressText.visibility = View.INVISIBLE
            }
        } else {
            detailsTime.text = ""
            detailsProgress.visibility = View.INVISIBLE
            detailsProgressText.visibility = View.INVISIBLE
        }

        scheduleFocusedArtwork(channel, program)
    }

    private fun scheduleFocusedArtwork(
        channel: Channel,
        program: EpgProgram
    ) {
        val artworkUrl = program.artwork
            ?.takeIf { it.isNotBlank() }
            ?: channel.logo?.takeIf { it.isNotBlank() }

        val artworkKey =
            "${channel.number}|${program.id}|${artworkUrl.orEmpty()}"

        if (pendingArtworkKey == artworkKey) {
            return
        }

        pendingArtworkLoad?.let(artworkHandler::removeCallbacks)
        pendingArtworkLoad = null
        pendingArtworkKey = artworkKey

        focusedArtworkTarget?.let { target ->
            Glide.with(detailsBackdrop).clear(target)
        }
        focusedArtworkTarget = null

        Glide.with(this).clear(detailsBackdrop)
        Glide.with(this).clear(detailsArtwork)

        detailsBackdrop.setImageDrawable(null)
        detailsArtwork.setImageDrawable(null)
        detailsBackdrop.visibility = View.INVISIBLE
        detailsArtwork.visibility = View.INVISIBLE

        if (artworkUrl == null) {
            return
        }

        val load = Runnable {
            if (
                !isAdded ||
                view == null ||
                pendingArtworkKey != artworkKey
            ) {
                return@Runnable
            }

            val target = object : CustomTarget<Drawable>() {
                override fun onResourceReady(
                    resource: Drawable,
                    transition: Transition<in Drawable>?
                ) {
                    if (
                        !isAdded ||
                        view == null ||
                        pendingArtworkKey != artworkKey
                    ) {
                        return
                    }

                    detailsBackdrop.scaleType = ImageView.ScaleType.CENTER_CROP
                    detailsArtwork.scaleType = ImageView.ScaleType.FIT_CENTER

                    detailsBackdrop.setImageDrawable(resource)
                    detailsArtwork.setImageDrawable(resource)
                    detailsBackdrop.visibility = View.VISIBLE
                    detailsArtwork.visibility = View.VISIBLE
                }

                override fun onLoadFailed(errorDrawable: Drawable?) {
                    if (pendingArtworkKey != artworkKey) {
                        return
                    }

                    detailsBackdrop.setImageDrawable(null)
                    detailsArtwork.setImageDrawable(null)
                    detailsBackdrop.visibility = View.INVISIBLE
                    detailsArtwork.visibility = View.INVISIBLE
                }

                override fun onLoadCleared(placeholder: Drawable?) {
                    if (pendingArtworkKey != artworkKey) {
                        return
                    }

                    detailsBackdrop.setImageDrawable(placeholder)
                    detailsArtwork.setImageDrawable(placeholder)

                    if (placeholder == null) {
                        detailsBackdrop.visibility = View.INVISIBLE
                        detailsArtwork.visibility = View.INVISIBLE
                    }
                }
            }

            focusedArtworkTarget = target

            Glide.with(this)
                .asDrawable()
                .load(artworkUrl)
                .override(640, 360)
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .dontAnimate()
                .into(target)
        }

        pendingArtworkLoad = load

        // Avoid starting artwork requests while the user is rapidly navigating.
        artworkHandler.postDelayed(load, 140L)
    }

    private fun showGuideActions(channel: Channel, program: EpgProgram) {
        val programId = program.id
        if (programId == null) {
            Toast.makeText(
                requireContext(),
                "This guide item cannot be recorded",
                Toast.LENGTH_SHORT,
            ).show()
            return
        }

        lifecycleScope.launch {
            try {
                val api = ApiClient.getApi(requireContext())
                val options = api.getGuideRecordOptions(programId)

                /*
                 * Resolve the selected grid item through the full per-channel
                 * guide before opening the details panel. The fast grid response
                 * can be compact, while the full guide includes season, episode,
                 * episode title and the rest of the program metadata.
                 */
                val fullProgram = try {
                    api.getGuide(channel.number)
                        .firstOrNull { candidate ->
                            candidate.id == programId
                        }
                        ?: program
                } catch (_: Exception) {
                    program
                }

                val content = buildGuideDetailsDialog(channel, fullProgram)
                val dialog = AlertDialog.Builder(requireContext())
                    .setView(content)
                    .setNegativeButton("Close", null)
                    .create()

                dialog.setOnShowListener {
                    val actions = content.findViewWithTag<LinearLayout>(
                        "guide_action_container"
                    )

                    fun addAction(
                        label: String,
                        action: suspend () -> Unit,
                    ) {
                        actions.addView(
                            makeGuideActionButton(label) {
                                if (label == "Watch Channel") {
                                    dialog.dismiss()
                                    onTuneFromGuide(channel)
                                    return@makeGuideActionButton
                                }

                                lifecycleScope.launch {
                                    try {
                                        action()
                                        val refreshed =
                                            api.getGuideRecordOptions(programId)

                                        epgAdapter?.updateRecordingState(
                                            programId,
                                            refreshed.recordingStatus ?: "none",
                                            refreshed.series,
                                            refreshed.seriesId,
                                        )

                                        Toast.makeText(
                                            requireContext(),
                                            when {
                                                refreshed.recordingStatus ==
                                                        "recording" -> "Recording now"
                                                refreshed.recording ->
                                                    "Recording scheduled"
                                                refreshed.series ->
                                                    "Series rule saved"
                                                else -> "Recording cancelled"
                                            },
                                            Toast.LENGTH_SHORT,
                                        ).show()
                                        dialog.dismiss()
                                    } catch (e: Exception) {
                                        Toast.makeText(
                                            requireContext(),
                                            "Recording action failed: ${e.message}",
                                            Toast.LENGTH_LONG,
                                        ).show()
                                    }
                                }
                            }
                        )
                    }

                    addAction("Watch Channel") { }

                    if (!options.recording) {
                        addAction("Record Once") {
                            api.recordGuideProgramOnce(
                                programId,
                                GuideRecordRequest("once"),
                            )
                        }
                    }

                    if (!options.series) {
                        addAction("Record Series") {
                            api.recordGuideProgramSeries(
                                programId,
                                GuideRecordRequest("series"),
                            )
                        }
                        addAction("Record New Episodes Only") {
                            api.recordGuideProgramNewEpisodes(
                                programId,
                                GuideRecordRequest("new"),
                            )
                        }
                    }

                    if (options.recording) {
                        addAction("Cancel Recording") {
                            api.cancelGuideProgramRecording(programId)
                        }
                    }

                    options.seriesId?.takeIf { options.series }?.let { seriesId ->
                        addAction("Cancel Series") {
                            api.deleteSeriesRule(seriesId)
                        }
                    }

                    actions.post {
                        actions.getChildAt(0)?.requestFocus()
                    }
                }

                dialog.show()
                dialog.window?.apply {
                    setBackgroundDrawable(
                        GradientDrawable().apply {
                            shape = GradientDrawable.RECTANGLE
                            cornerRadius = 0f
                            setColor(Color.parseColor("#F0121420"))
                            setStroke(dp(2), Color.WHITE)
                        }
                    )
                    setLayout(
                        (resources.displayMetrics.widthPixels * 0.95f).toInt(),
                        (resources.displayMetrics.heightPixels * 0.92f).toInt(),
                    )
                }
            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Could not load recording options: ${e.message}",
                    Toast.LENGTH_LONG,
                ).show()
            }
        }
    }

    private fun buildGuideDetailsDialog(
        channel: Channel,
        program: EpgProgram,
    ): View {
        val context = requireContext()

        val root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
        }

        fun makeText(
            value: String,
            size: Float,
            color: Int = Color.WHITE,
            bold: Boolean = false,
            maxLinesValue: Int = Int.MAX_VALUE,
        ): TextView {
            return TextView(context).apply {
                text = value
                textSize = size
                setTextColor(color)
                maxLines = maxLinesValue
                if (bold) {
                    setTypeface(typeface, android.graphics.Typeface.BOLD)
                }
            }
        }

        fun addSummaryText(
            parent: LinearLayout,
            value: String,
            size: Float,
            color: Int = Color.WHITE,
            bold: Boolean = false,
            marginTop: Int = 0,
        ) {
            if (value.isBlank()) return

            parent.addView(
                makeText(
                    value = value,
                    size = size,
                    color = color,
                    bold = bold,
                ),
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    topMargin = dp(marginTop)
                },
            )
        }

        fun detailRow(
            label: String,
            value: String?,
        ): View? {
            val cleanValue = value
                ?.trim()
                ?.takeIf { it.isNotBlank() }
                ?: return null

            return LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4), 0, dp(4))
                setBackgroundColor(Color.parseColor("#14000000"))

                addView(
                    makeText(
                        value = label,
                        size = 12f,
                        color = Color.parseColor("#78B9FF"),
                        bold = true,
                        maxLinesValue = 1,
                    ),
                    LinearLayout.LayoutParams(
                        dp(118),
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                    ),
                )

                addView(
                    makeText(
                        value = cleanValue,
                        size = 12f,
                        color = Color.parseColor("#E6E9F2"),
                    ),
                    LinearLayout.LayoutParams(
                        0,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        1f,
                    ),
                )
            }
        }

        val infoScroll = ScrollView(context).apply {
            isFillViewport = false
            isVerticalScrollBarEnabled = true
            overScrollMode = View.OVER_SCROLL_IF_CONTENT_SCROLLS
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f,
            )
        }

        val infoContent = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, dp(4), dp(6))
        }

        val heroRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.TOP
        }

        val artwork = ImageView(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                dp(112),
                dp(168),
            ).apply {
                marginEnd = dp(16)
            }
            scaleType = ImageView.ScaleType.FIT_CENTER
            setBackgroundColor(Color.parseColor("#0A0B10"))
            contentDescription = "Program artwork"
        }

        val summary = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f,
            )
        }

        addSummaryText(
            parent = summary,
            value = program.title.orEmpty().ifBlank { "Program" },
            size = 24f,
            bold = true,
        )

        val episodeLine = buildList {
            formatEpisodeLabel(program)?.let(::add)

            program.episodeTitle
                ?.takeIf { it.isNotBlank() }
                ?.let(::add)

            if (isEmpty()) {
                program.subtitle
                    ?.takeIf { it.isNotBlank() }
                    ?.let(::add)
            }
        }.joinToString("  •  ")

        addSummaryText(
            parent = summary,
            value = episodeLine,
            size = 15f,
            color = Color.parseColor("#D7DBE8"),
            marginTop = 3,
        )

        addSummaryText(
            parent = summary,
            value = "${channel.number}  ${channel.name}",
            size = 14f,
            color = Color.parseColor("#9BC8F0"),
            marginTop = 9,
        )

        val start = TimeUtil.parseEpg(program.start)
        val stop = TimeUtil.parseEpg(program.stop)

        if (start != null && stop != null) {
            val minutes =
                ((stop.time - start.time) / 60_000L).coerceAtLeast(1L)

            val duration = when {
                minutes < 60 -> "$minutes min"
                minutes % 60L == 0L -> "${minutes / 60} hr"
                else -> "${minutes / 60} hr ${minutes % 60} min"
            }

            addSummaryText(
                parent = summary,
                value = "${TimeUtil.formatDisplay(start)} – " +
                        "${TimeUtil.formatDisplay(stop)}  •  $duration",
                size = 14f,
                color = Color.parseColor("#C2C5D0"),
                marginTop = 3,
            )
        }

        val categoryLine = buildList {
            program.genres
                ?.takeIf { it.isNotBlank() }
                ?.let(::add)
                ?: program.category
                    ?.takeIf { it.isNotBlank() }
                    ?.let(::add)

            program.rating
                ?.takeIf { it.isNotBlank() }
                ?.let(::add)

            if (program.isNew == 1) add("NEW")

            program.originalAirDate
                ?.takeIf { it.isNotBlank() }
                ?.let { add("Aired $it") }
        }.joinToString("  •  ")

        addSummaryText(
            parent = summary,
            value = categoryLine,
            size = 13f,
            color = Color.parseColor("#E0E2E9"),
            marginTop = 6,
        )

        val technicalLine = buildList {
            program.videoProperties
                ?.takeIf { it.isNotBlank() }
                ?.let(::add)

            program.audioProperties
                ?.takeIf { it.isNotBlank() }
                ?.let(::add)
        }.joinToString("  •  ")

        addSummaryText(
            parent = summary,
            value = technicalLine,
            size = 12f,
            color = Color.parseColor("#A9ADBA"),
            marginTop = 3,
        )

        addSummaryText(
            parent = summary,
            value = program.description
                ?.takeIf { it.isNotBlank() }
                ?: "Program description is not available.",
            size = 13f,
            color = Color.parseColor("#E2E4EB"),
            marginTop = 10,
        )

        heroRow.addView(artwork)
        heroRow.addView(summary)
        infoContent.addView(heroRow)

        val detailsDivider = View(context).apply {
            setBackgroundColor(Color.parseColor("#3A3D49"))
        }

        infoContent.addView(
            detailsDivider,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(1),
            ).apply {
                topMargin = dp(10)
                bottomMargin = dp(7)
            },
        )

        val detailsColumns = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
        }

        val leftColumn = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
        }

        val rightColumn = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
        }

        detailRow("Cast", program.cast)?.let(leftColumn::addView)
        detailRow("Director", program.directors)?.let(leftColumn::addView)
        detailRow("Writers", program.writers)?.let(leftColumn::addView)
        detailRow("Genres", program.genres ?: program.category)
            ?.let(leftColumn::addView)
        detailRow("Episode", formatEpisodeLabel(program))
            ?.let(leftColumn::addView)
        detailRow("Original Air Date", program.originalAirDate)
            ?.let(leftColumn::addView)

        val runtimeMinutes =
            program.runtime.takeIf { it > 0 }?.div(60)

        detailRow(
            "Runtime",
            runtimeMinutes?.takeIf { it > 0 }?.let { "$it min" },
        )?.let(rightColumn::addView)

        detailRow("Video", program.videoProperties)
            ?.let(rightColumn::addView)
        detailRow("Audio", program.audioProperties)
            ?.let(rightColumn::addView)
        detailRow("Parental Rating", program.rating)
            ?.let(rightColumn::addView)
        detailRow("Language", program.language)
            ?.let(rightColumn::addView)
        detailRow("Show Type", program.showType)
            ?.let(rightColumn::addView)
        detailRow("Entity Type", program.entityType)
            ?.let(rightColumn::addView)

        detailsColumns.addView(
            leftColumn,
            LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f,
            ).apply {
                marginEnd = dp(12)
            },
        )

        detailsColumns.addView(
            rightColumn,
            LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f,
            ),
        )

        infoContent.addView(detailsColumns)
        infoScroll.addView(infoContent)
        root.addView(infoScroll)

        root.addView(
            TextView(context).apply {
                text = "ACTIONS"
                textSize = 13f
                setTextColor(Color.WHITE)
                setTypeface(typeface, android.graphics.Typeface.BOLD)
            },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(5)
                bottomMargin = dp(3)
            },
        )

        root.addView(
            LinearLayout(context).apply {
                tag = "guide_action_container"
                orientation = LinearLayout.VERTICAL
            },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )

        val artworkUrl = program.artwork
            ?.takeIf { it.isNotBlank() }
            ?: channel.logo?.takeIf { it.isNotBlank() }

        if (artworkUrl != null) {
            Glide.with(this)
                .load(artworkUrl)
                .fitCenter()
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .into(artwork)
        }

        return root
    }

    private fun makeGuideActionButton(
        label: String,
        onClick: () -> Unit,
    ): TextView {
        fun background(focused: Boolean) =
            GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = 0f
                setColor(
                    Color.parseColor(
                        if (focused) "#303746" else "#1B1E29"
                    )
                )
                setStroke(
                    dp(if (focused) 2 else 1),
                    Color.parseColor(
                        if (focused) "#FFFFFF" else "#3B3F4B"
                    )
                )
            }

        return TextView(requireContext()).apply {
            text = label
            textSize = 13f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), 0, dp(16), 0)
            isFocusable = true
            isFocusableInTouchMode = true
            this.background = background(false)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(31),
            ).apply {
                bottomMargin = dp(2)
            }
            setOnFocusChangeListener { view, hasFocus ->
                view.background = background(hasFocus)
            }
            setOnClickListener { onClick() }
        }
    }

    private fun formatEpisodeLabel(program: EpgProgram): String? {
        val seasonNumber = program.season
        val rawEpisode = program.episode
            ?.trim()
            .orEmpty()

        val normalizedEpisode = rawEpisode
            .uppercase()
            .replace(" ", "")

        // Preserve a server value already supplied as S6E22.
        if (normalizedEpisode.matches(Regex("""^S\d+E\d+$"""))) {
            return normalizedEpisode
        }

        val episodeNumber = rawEpisode.toIntOrNull()

        return when {
            seasonNumber > 0 && episodeNumber != null ->
                "S${seasonNumber}E${episodeNumber}"

            seasonNumber > 0 && rawEpisode.isNotBlank() ->
                "S${seasonNumber}  •  Episode $rawEpisode"

            seasonNumber > 0 ->
                "S$seasonNumber"

            rawEpisode.isNotBlank() ->
                "Episode $rawEpisode"

            else -> null
        }
    }

    private fun onTuneFromGuide(channel: Channel) {
        startActivity(
            Intent(requireContext(), PlayerActivity::class.java).apply {
                putExtra(PlayerActivity.EXTRA_CHANNEL_NUM, channel.number)
                putExtra(PlayerActivity.EXTRA_CHANNEL_NAME, channel.name)
                putExtra(PlayerActivity.EXTRA_IS_LIVE, true)
            }
        )
    }

    private fun buildGrid() {
        epgAdapter = EpgAdapter(
            ctx = requireContext(),
            channels = channels,
            guide = guide,
            minuteWidthDp = minuteWidthDp,
            rowHeightDp = rowHeightDp,
            windowHours = windowHours,
            onTune = { channel ->
                startActivity(
                    Intent(requireContext(), PlayerActivity::class.java).apply {
                        putExtra(
                            PlayerActivity.EXTRA_CHANNEL_NUM,
                            channel.number
                        )
                        putExtra(
                            PlayerActivity.EXTRA_CHANNEL_NAME,
                            channel.name
                        )
                        putExtra(
                            PlayerActivity.EXTRA_IS_LIVE,
                            true
                        )
                    }
                )
            },
            onHScroll = { scrollX ->
                sharedScrollX = scrollX
                timeHeader.scrollTo(scrollX, 0)
                updateNowPlayhead(animate = false)
            },
            getScrollX = { sharedScrollX },
            windowStartMs = guideWindowStartMs,
            onProgramFocused = { channel, program ->
                updateDetails(channel, program)
            },
            onProgramSelected = { channel, program ->
                showGuideActions(channel, program)
            }
        )
        channelList.adapter = epgAdapter

        channels.firstNotNullOfOrNull { channel ->
            guide[channel.number]
                ?.firstOrNull()
                ?.let { program -> channel to program }
        }?.let { (channel, program) ->
            updateDetails(channel, program)
        }

        channelList.requestFocus()
    }
}

class EpgAdapter(
    private val ctx: Context,
    private val channels: MutableList<Channel>,
    private val guide: MutableMap<String, List<EpgProgram>>,
    private val minuteWidthDp: Int,
    private val rowHeightDp: Int,
    private val windowHours: Int,
    private val onTune: (Channel) -> Unit,
    private val onHScroll: (Int) -> Unit,
    private val getScrollX: () -> Int,
    private val windowStartMs: Long,
    private val onProgramFocused: (Channel, EpgProgram) -> Unit,
    private val onProgramSelected: (Channel, EpgProgram) -> Unit
) : RecyclerView.Adapter<EpgAdapter.VH>() {

    private val density = ctx.resources.displayMetrics.density

    private fun dp(value: Int): Int {
        return (value * density).toInt()
    }

    private val now = Calendar.getInstance()
    private val startMs = windowStartMs
    private val totalMins = windowHours * 60

    // Keeps vertical DPAD movement locked to the same guide time column.
    // It is reset when the user moves left or right.
    private var verticalAnchorMinute: Int? = null

    // Row emphasis keeps the currently focused channel easy to follow across
    // the guide. The active row stays fully visible while surrounding rows
    // are dimmed slightly.
    private var activeRowPosition = RecyclerView.NO_POSITION

    private data class RecordingState(
        val status: String,
        val series: Boolean,
        val seriesId: Int?,
    )

    private val recordingStateOverrides = mutableMapOf<Int, RecordingState>()

    fun appendRows(
        newChannels: List<Channel>,
        newGuide: Map<String, List<EpgProgram>>,
    ) {
        if (newChannels.isEmpty()) return

        val startPosition = channels.size
        guide.putAll(newGuide)
        channels.addAll(newChannels)
        notifyItemRangeInserted(startPosition, newChannels.size)
    }

    fun updateRecordingState(
        programId: Int,
        status: String,
        series: Boolean,
        seriesId: Int?,
    ) {
        recordingStateOverrides[programId] = RecordingState(status, series, seriesId)
        val row = channels.indexOfFirst { channel ->
            guide[channel.number].orEmpty().any { it.id == programId }
        }
        if (row >= 0) notifyItemChanged(row)
    }

    private data class ProgramCellInfo(
        val startMinute: Int,
        val endMinute: Int,
        val focusView: View
    )

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val chanLogo: ImageView =
            view.findViewById(R.id.epg_chan_logo)

        val chanNumber: TextView =
            view.findViewById(R.id.epg_chan_number)

        val chanName: TextView =
            view.findViewById(R.id.epg_chan_name)

        val hScroll: HorizontalScrollView =
            view.findViewById(R.id.epg_row_scroll)

        val rowContent: LinearLayout =
            view.findViewById(R.id.epg_row_content)
    }

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int
    ): VH {
        val view = LayoutInflater
            .from(ctx)
            .inflate(
                R.layout.item_epg_row,
                parent,
                false
            )

        return VH(view)
    }

    override fun onBindViewHolder(
        holder: VH,
        position: Int
    ) {
        val channel = channels[position]
        val programs = guide[channel.number] ?: emptyList()

        applyRowEmphasis(holder, position, animate = false)

        holder.chanNumber.text = channel.number
        holder.chanName.text = channel.name

        bindChannelLogo(holder, channel)

        holder.rowContent.removeAllViews()

        var filledMins = 0

        programs.forEach { program ->
            val programStart = TimeUtil.parseEpg(program.start)
                ?: return@forEach

            val programStop = TimeUtil.parseEpg(program.stop)
                ?: return@forEach

            val cellStart = programStart.time.coerceAtLeast(startMs)
            val cellEnd = programStop.time.coerceAtMost(
                startMs + totalMins * 60_000L
            )

            if (
                cellEnd <= startMs ||
                cellStart >= startMs + totalMins * 60_000L
            ) {
                return@forEach
            }

            val offsetMins = ((cellStart - startMs) / 60_000L).toInt()
            val durationMins = ((cellEnd - cellStart) / 60_000L)
                .toInt()
                .coerceAtLeast(1)

            val isNow =
                programStart.time <= now.timeInMillis &&
                        programStop.time > now.timeInMillis

            if (offsetMins > filledMins) {
                holder.rowContent.addView(
                    makeGap(
                        dp(
                            minuteWidthDp *
                                    (offsetMins - filledMins)
                        )
                    )
                )
            }

            holder.rowContent.addView(
                makeProgram(
                    program = program,
                    channel = channel,
                    width = dp(minuteWidthDp * durationMins),
                    isNow = isNow,
                    startMinute = offsetMins,
                    endMinute = offsetMins + durationMins,
                    adapterPosition = position
                )
            )

            filledMins = offsetMins + durationMins
        }

        if (filledMins < totalMins) {
            holder.rowContent.addView(
                makeGap(
                    dp(
                        minuteWidthDp *
                                (totalMins - filledMins)
                    )
                )
            )
        }

        holder.hScroll.post {
            holder.hScroll.scrollTo(getScrollX(), 0)
        }

        holder.hScroll.setOnScrollChangeListener {
                _, scrollX, _, _, _ ->
            onHScroll(scrollX)

            val recyclerView = holder.hScroll
                .parent
                ?.parent
                ?.parent as? RecyclerView
                ?: return@setOnScrollChangeListener

            for (index in 0 until recyclerView.childCount) {
                val siblingScroll = recyclerView
                    .getChildAt(index)
                    ?.findViewById<HorizontalScrollView>(
                        R.id.epg_row_scroll
                    )

                if (
                    siblingScroll != null &&
                    siblingScroll !== holder.hScroll
                ) {
                    siblingScroll.scrollTo(scrollX, 0)
                }
            }
        }
    }

    override fun onViewRecycled(holder: VH) {
        holder.hScroll.setOnScrollChangeListener(null)
        holder.itemView.animate().cancel()
        holder.itemView.alpha = 1f

        Glide.with(holder.itemView)
            .clear(holder.chanLogo)

        holder.chanLogo.setImageDrawable(null)

        super.onViewRecycled(holder)
    }

    override fun getItemCount(): Int {
        return channels.size
    }

    private fun bindChannelLogo(
        holder: VH,
        channel: Channel
    ) {
        Glide.with(holder.itemView)
            .clear(holder.chanLogo)

        val logoUrl = channel.logo
            ?.takeIf { it.isNotBlank() }

        if (logoUrl == null) {
            holder.chanLogo.visibility = View.INVISIBLE
            holder.chanLogo.setImageDrawable(null)
            return
        }

        holder.chanLogo.visibility = View.VISIBLE

        Glide.with(holder.itemView)
            .load(logoUrl)
            .fitCenter()
            .into(holder.chanLogo)
    }


    private fun genreBaseColor(program: EpgProgram): Int {
        val source = buildString {
            append(program.category.orEmpty())
            append(' ')
            append(program.title.orEmpty())
            append(' ')
            append(program.description.orEmpty())
        }.lowercase()

        return when {
            source.contains("news") ||
                    source.contains("newscast") ||
                    source.contains("weather") -> Color.parseColor("#1E3A5F")

            source.contains("sport") ||
                    source.contains("football") ||
                    source.contains("basketball") ||
                    source.contains("baseball") ||
                    source.contains("soccer") ||
                    source.contains("hockey") ||
                    source.contains("golf") ||
                    source.contains("tennis") ||
                    source.contains("racing") -> Color.parseColor("#1E5A34")

            source.contains("movie") ||
                    source.contains("film") -> Color.parseColor("#4A2E63")

            source.contains("children") ||
                    source.contains("kids") ||
                    source.contains("child") ||
                    source.contains("animation") ||
                    source.contains("cartoon") -> Color.parseColor("#8A5A16")

            source.contains("documentary") ||
                    source.contains("science") ||
                    source.contains("nature") ||
                    source.contains("history") -> Color.parseColor("#165B5B")

            source.contains("drama") ||
                    source.contains("crime") ||
                    source.contains("mystery") -> Color.parseColor("#5A2432")

            source.contains("comedy") ||
                    source.contains("sitcom") -> Color.parseColor("#274B5D")

            source.contains("music") ||
                    source.contains("concert") -> Color.parseColor("#65304F")

            source.contains("reality") ||
                    source.contains("game show") ||
                    source.contains("talk show") -> Color.parseColor("#60462B")

            source.contains("special") ||
                    source.contains("live event") -> Color.parseColor("#6A2630")

            else -> Color.parseColor("#20212A")
        }
    }

    private fun brightenColor(color: Int, amount: Float): Int {
        val factor = amount.coerceAtLeast(1f)
        return Color.rgb(
            (Color.red(color) * factor).toInt().coerceAtMost(255),
            (Color.green(color) * factor).toInt().coerceAtMost(255),
            (Color.blue(color) * factor).toInt().coerceAtMost(255),
        )
    }

    private fun programBackground(
        program: EpgProgram,
        isNow: Boolean,
        focused: Boolean,
    ): Drawable {
        val base = if (isNow) {
            genreBaseColor(program)
        } else {
            Color.parseColor("#20212A")
        }

        val fill = when {
            focused && isNow -> brightenColor(base, 1.28f)
            focused -> Color.parseColor("#343640")
            else -> base
        }

        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = 0f
            setColor(fill)
            setStroke(dp(1), Color.parseColor("#101116"))
        }
    }

    private fun makeProgram(
        program: EpgProgram,
        channel: Channel,
        width: Int,
        isNow: Boolean,
        startMinute: Int,
        endMinute: Int,
        adapterPosition: Int
    ): View {
        val height = dp(rowHeightDp)
        val contentWidth = width
        val contentHeight = height

        val titleView = TextView(ctx).apply {
            text = program.title ?: ""
            minWidth = contentWidth
            maxWidth = contentWidth
            this.height = contentHeight
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(6), 0, dp(4), 0)
            textSize = 12f
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
            isFocusable = true
            isFocusableInTouchMode = false
            setTextColor(Color.WHITE)

            background = programBackground(
                program = program,
                isNow = isNow,
                focused = false,
            )

            // Flat, square EPG cells: no yellow outline, no glow, no scaling,
            // and no rounded corners. Focus is shown by a brighter fill.
            setOnFocusChangeListener { focusedView, hasFocus ->
                focusedView.animate().cancel()
                focusedView.scaleX = 1f
                focusedView.scaleY = 1f
                focusedView.translationZ = 0f

                focusedView.background = programBackground(
                    program = program,
                    isNow = isNow,
                    focused = hasFocus,
                )

                if (hasFocus) {
                    // Do not reorder the program cells when focus changes.
                    // Reordering breaks Android TV's left/right focus path.
                    setActiveRow(focusedView, adapterPosition)
                    onProgramFocused(channel, program)
                }
            }

            setOnClickListener {
                onProgramSelected(channel, program)
            }

            setOnKeyListener { _, keyCode, event ->
                if (event.action != KeyEvent.ACTION_DOWN) {
                    return@setOnKeyListener false
                }

                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_LEFT,
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        // A horizontal move establishes a new time column.
                        verticalAnchorMinute = null
                        false
                    }

                    KeyEvent.KEYCODE_DPAD_UP -> {
                        moveFocusVertically(
                            sourceView = this,
                            direction = -1,
                            // Anchor vertical travel to the selected cell's
                            // LEFT time edge, not its midpoint. Midpoints of
                            // long programs progressively push focus right.
                            fallbackMinute = startMinute
                        )
                    }

                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        moveFocusVertically(
                            sourceView = this,
                            direction = 1,
                            // Keep the same exact time column while moving
                            // through rows with different program durations.
                            fallbackMinute = startMinute
                        )
                    }

                    else -> false
                }
            }
        }

        val override = program.id?.let { recordingStateOverrides[it] }
        val recordingStatus = (override?.status ?: program.recordingStatus ?: "none").lowercase()
        val isSeriesRecording = override?.series ?: program.recordingSeries

        val badgeText = when {
            recordingStatus == "recording" -> "● REC"
            recordingStatus == "scheduled" && isSeriesRecording -> "⦿"
            recordingStatus == "scheduled" -> "●"
            else -> ""
        }

        val badgeView = TextView(ctx).apply {
            text = badgeText
            setTextColor(Color.WHITE)
            textSize = if (recordingStatus == "recording") 9f else 13f
            gravity = Gravity.CENTER
            setPadding(dp(4), 0, dp(4), 0)
            setBackgroundColor(
                if (recordingStatus == "recording") {
                    Color.parseColor("#CCB00020")
                } else {
                    Color.parseColor("#AA5A1A1A")
                }
            )
            visibility = if (badgeText.isBlank()) View.GONE else View.VISIBLE
            isFocusable = false
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO

            // Keep the recording badge above the focused title without
            // changing child order. The title uses translationZ while focused,
            // so the badge needs a higher Z value to remain visible.
            elevation = dp(20).toFloat()
            translationZ = dp(20).toFloat()
        }

        return FrameLayout(ctx).apply {
            clipChildren = false
            clipToPadding = false

            layoutParams = LinearLayout.LayoutParams(
                width,
                dp(rowHeightDp)
            ).apply {
                setMargins(0, 0, 0, 0)
            }

            addView(
                titleView,
                FrameLayout.LayoutParams(
                    contentWidth,
                    contentHeight
                )
            )

            addView(
                badgeView,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    dp(18),
                    Gravity.TOP or Gravity.END,
                ).apply {
                    topMargin = dp(2)
                    marginEnd = dp(4)
                }
            )

            tag = ProgramCellInfo(
                startMinute = startMinute,
                endMinute = endMinute,
                focusView = titleView
            )
        }
    }

    private fun setActiveRow(focusedView: View, adapterPosition: Int) {
        if (adapterPosition == RecyclerView.NO_POSITION) return

        activeRowPosition = adapterPosition

        val recyclerView = findRecyclerView(focusedView) ?: return

        for (index in 0 until recyclerView.childCount) {
            val holder = recyclerView.getChildViewHolder(
                recyclerView.getChildAt(index)
            ) as? VH ?: continue

            val position = holder.bindingAdapterPosition
            if (position != RecyclerView.NO_POSITION) {
                applyRowEmphasis(holder, position, animate = true)
            }
        }
    }

    private fun applyRowEmphasis(
        holder: VH,
        adapterPosition: Int,
        animate: Boolean
    ) {
        val targetAlpha = when {
            activeRowPosition == RecyclerView.NO_POSITION -> 1f
            adapterPosition == activeRowPosition -> 1f
            else -> 0.78f
        }

        holder.itemView.animate().cancel()

        if (animate) {
            holder.itemView.animate()
                .alpha(targetAlpha)
                .setDuration(140L)
                .start()
        } else {
            holder.itemView.alpha = targetAlpha
        }
    }

    private fun moveFocusVertically(
        sourceView: View,
        direction: Int,
        fallbackMinute: Int
    ): Boolean {
        val recyclerView = findRecyclerView(sourceView) ?: return false
        val sourceHolder = recyclerView.findContainingViewHolder(sourceView) ?: return false
        val sourcePosition = sourceHolder.bindingAdapterPosition

        if (sourcePosition == RecyclerView.NO_POSITION) return false

        var targetPosition = sourcePosition + direction

        // Skip channels that have no focusable guide cells in the visible window.
        // Without this, focus stops on the first channel whose guide response is
        // empty (or whose programs are all outside the current four-hour window).
        while (
            targetPosition in channels.indices &&
            !hasVisiblePrograms(targetPosition)
        ) {
            targetPosition += direction
        }

        if (targetPosition !in channels.indices) return false

        val anchorMinute = (
                verticalAnchorMinute ?: fallbackMinute
                ).coerceIn(0, (totalMins - 1).coerceAtLeast(0))

        verticalAnchorMinute = anchorMinute

        recyclerView.scrollToPosition(targetPosition)
        recyclerView.post {
            focusProgramAtMinute(
                recyclerView = recyclerView,
                adapterPosition = targetPosition,
                anchorMinute = anchorMinute,
                direction = direction
            )
        }

        return true
    }

    private fun focusProgramAtMinute(
        recyclerView: RecyclerView,
        adapterPosition: Int,
        anchorMinute: Int,
        direction: Int
    ) {
        val holder = recyclerView
            .findViewHolderForAdapterPosition(adapterPosition) as? VH

        if (holder == null) {
            recyclerView.post {
                focusProgramAtMinute(
                    recyclerView,
                    adapterPosition,
                    anchorMinute,
                    direction
                )
            }
            return
        }

        val cells = mutableListOf<ProgramCellInfo>()

        for (index in 0 until holder.rowContent.childCount) {
            val info = holder.rowContent.getChildAt(index).tag as? ProgramCellInfo
            if (info != null) cells += info
        }

        if (cells.isEmpty()) {
            // The row may have become empty between guide loading and binding.
            // Continue in the same direction instead of trapping focus.
            var nextPosition = adapterPosition + direction
            while (
                nextPosition in channels.indices &&
                !hasVisiblePrograms(nextPosition)
            ) {
                nextPosition += direction
            }

            if (nextPosition in channels.indices) {
                recyclerView.scrollToPosition(nextPosition)
                recyclerView.post {
                    focusProgramAtMinute(
                        recyclerView,
                        nextPosition,
                        anchorMinute,
                        direction
                    )
                }
            }
            return
        }

        val target = cells.firstOrNull { info ->
            anchorMinute >= info.startMinute && anchorMinute < info.endMinute
        } ?: cells.minByOrNull { info ->
            // Choose the program whose TIME RANGE is closest to the anchor.
            // Comparing against the cell center can bias focus to the right
            // when rows contain programs with different durations.
            when {
                anchorMinute < info.startMinute -> info.startMinute - anchorMinute
                anchorMinute >= info.endMinute -> anchorMinute - info.endMinute
                else -> 0
            }
        } ?: return

        // Vertical navigation must never alter the horizontal guide position.
        // Keep every row locked to the existing shared scroll offset, then
        // request focus on the program occupying the anchored time column.
        holder.hScroll.scrollTo(getScrollX(), 0)
        target.focusView.requestFocus()
    }


    private fun hasVisiblePrograms(adapterPosition: Int): Boolean {
        if (adapterPosition !in channels.indices) return false

        val channel = channels[adapterPosition]
        val programs = guide[channel.number] ?: return false
        val windowEndMs = startMs + totalMins * 60_000L

        return programs.any { program ->
            val programStart = TimeUtil.parseEpg(program.start) ?: return@any false
            val programStop = TimeUtil.parseEpg(program.stop) ?: return@any false

            programStop.time > startMs && programStart.time < windowEndMs
        }
    }

    private fun findRecyclerView(view: View): RecyclerView? {
        var parent = view.parent

        while (parent is View) {
            if (parent is RecyclerView) return parent
            parent = parent.parent
        }

        return null
    }

    private fun makeGap(width: Int): View {
        return View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(
                width,
                dp(rowHeightDp)
            ).apply {
                setMargins(2, 2, 2, 2)
            }

            setBackgroundColor(Color.parseColor("#0A0A1A"))
        }
    }
}