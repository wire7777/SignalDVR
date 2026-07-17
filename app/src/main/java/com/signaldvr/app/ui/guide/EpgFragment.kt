package com.signaldvr.app.ui.guide

import android.content.Context
import android.content.Intent
import android.graphics.Color
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
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import com.signaldvr.app.api.EpgProgram
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
    private lateinit var nowPlayheadDot: View

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
    private val playheadUpdater = object : Runnable {
        override fun run() {
            updateNowPlayhead(animate = true)
            updateCurrentTimeHeader()
            playheadHandler.postDelayed(this, 1_000L)
        }
    }

    private val minuteWidthDp = 7
    private val rowHeightDp = 72
    private val windowHours = 4

    private var channels: List<Channel> = emptyList()
    private var guide: Map<String, List<EpgProgram>> = emptyMap()
    private var sharedScrollX = 0
    private var guideWindowStartMs = 0L

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

        if (::detailsBackdrop.isInitialized) {
            Glide.with(this).clear(detailsBackdrop)
        }
        if (::detailsArtwork.isInitialized) {
            Glide.with(this).clear(detailsArtwork)
        }

        super.onDestroyView()
    }


    private fun configureNowPlayhead() {
        val density = resources.displayMetrics.density

        // Thin, slightly translucent live-time line so program titles remain readable.
        nowPlayhead.layoutParams = nowPlayhead.layoutParams.apply {
            width = (2f * density).toInt().coerceAtLeast(1)
        }
        nowPlayhead.setBackgroundColor(Color.parseColor("#FFFF3B30"))
        nowPlayhead.alpha = 0.90f

        // Clean white NOW label with no box, plus a shadow for readability.
        nowPlayheadLabel.apply {
            text = "NOW"
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.TRANSPARENT)
            setPadding(0, 0, 0, 0)
            setShadowLayer(2f * density, 0f, 1f * density, Color.BLACK)
            alpha = 1f
        }

        // Add a small red dot centered where the header meets the vertical line.
        val overlayParent = nowPlayhead.parent as? ViewGroup ?: return
        val dotSize = (8f * density).toInt().coerceAtLeast(1)

        nowPlayheadDot = View(requireContext()).apply {
            layoutParams = ViewGroup.LayoutParams(dotSize, dotSize)
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#FFFF3B30"))
            }
            elevation = 4f * density
            visibility = View.GONE
        }

        overlayParent.addView(nowPlayheadDot)
        nowPlayheadDot.bringToFront()
        nowPlayheadLabel.bringToFront()
    }

    private fun loadGuide() {
        loading.visibility = View.VISIBLE
        errorText.visibility = View.GONE

        lifecycleScope.launch {
            try {
                channels = ApiClient
                    .getApi(requireContext())
                    .getLiveChannels()

                val guideMap = Collections.synchronizedMap(
                    mutableMapOf<String, List<EpgProgram>>()
                )

                channels.map { channel ->
                    async(Dispatchers.IO) {
                        try {
                            guideMap[channel.number] = ApiClient
                                .getApi(requireContext())
                                .getGuide(channel.number)
                        } catch (_: Exception) {
                            guideMap[channel.number] = emptyList()
                        }
                    }
                }.awaitAll()

                guide = guideMap
                loading.visibility = View.GONE

                // Warm Glide's memory/disk cache before the user moves through the grid.
                preloadGuideArtwork()

                buildTimeHeader()
                buildGrid()
            } catch (e: Exception) {
                loading.visibility = View.GONE
                errorText.visibility = View.VISIBLE
                errorText.text = "Could not load guide:\n${e.message}"
            }
        }
    }


    private fun preloadGuideArtwork() {
        if (!isAdded) return

        // Preload artwork for the first visible group of channels/programs.
        // The same size/options are used in updateDetails(), allowing Glide
        // to reuse the exact cached resource immediately on focus.
        channels.take(10).forEach { channel ->
            guide[channel.number]
                .orEmpty()
                .take(5)
                .forEach { program ->
                    val artworkUrl = program.artwork
                        ?.takeIf { it.isNotBlank() }
                        ?: channel.logo?.takeIf { it.isNotBlank() }

                    if (artworkUrl != null) {
                        Glide.with(this)
                            .load(artworkUrl)
                            .override(400, 264)
                            .diskCacheStrategy(DiskCacheStrategy.ALL)
                            .dontAnimate()
                            .fitCenter()
                            .preload(400, 264)
                    }
                }
        }
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

        val nowMs = System.currentTimeMillis()
        val elapsedMinutes = (nowMs - guideWindowStartMs) / 60_000f

        val density = resources.displayMetrics.density
        val channelColumnWidthPx = 161f * density
        val minuteOffsetPx = elapsedMinutes * minuteWidthDp * density
        val playheadX = channelColumnWidthPx + minuteOffsetPx - sharedScrollX

        val gridLeft = channelColumnWidthPx
        val gridRight = timeHeader.width.toFloat() + channelColumnWidthPx
        val visible = playheadX in gridLeft..gridRight

        nowPlayhead.visibility = if (visible) View.VISIBLE else View.GONE
        nowPlayheadLabel.visibility = if (visible) View.VISIBLE else View.GONE
        if (::nowPlayheadDot.isInitialized) {
            nowPlayheadDot.visibility = if (visible) View.VISIBLE else View.GONE
        }

        if (!visible) {
            nowPlayhead.animate().cancel()
            nowPlayheadLabel.animate().cancel()
            if (::nowPlayheadDot.isInitialized) nowPlayheadDot.animate().cancel()
            return
        }

        val labelWidth = when {
            nowPlayheadLabel.width > 0 -> nowPlayheadLabel.width.toFloat()
            nowPlayheadLabel.measuredWidth > 0 -> nowPlayheadLabel.measuredWidth.toFloat()
            else -> 44f * density
        }
        val labelTargetX = (playheadX - labelWidth / 2f).coerceIn(
            gridLeft,
            (gridRight - labelWidth).coerceAtLeast(gridLeft)
        )

        val dotWidth = if (::nowPlayheadDot.isInitialized) {
            when {
                nowPlayheadDot.width > 0 -> nowPlayheadDot.width.toFloat()
                nowPlayheadDot.measuredWidth > 0 -> nowPlayheadDot.measuredWidth.toFloat()
                else -> 8f * density
            }
        } else {
            0f
        }

        val dotHeight = if (::nowPlayheadDot.isInitialized) {
            when {
                nowPlayheadDot.height > 0 -> nowPlayheadDot.height.toFloat()
                nowPlayheadDot.measuredHeight > 0 -> nowPlayheadDot.measuredHeight.toFloat()
                else -> 8f * density
            }
        } else {
            0f
        }

        // Treat playheadX as the shared CENTER point for the red line and dot.
        // Use absolute x/y coordinates instead of translation values so their
        // different XML/layout origins can never cause them to drift apart.
        val lineWidth = when {
            nowPlayhead.width > 0 -> nowPlayhead.width.toFloat()
            nowPlayhead.measuredWidth > 0 -> nowPlayhead.measuredWidth.toFloat()
            else -> 2f * density
        }

        val lineTargetX = playheadX - lineWidth / 2f
        val dotTargetX = playheadX - dotWidth / 2f
        val dotTargetY = nowPlayhead.top.toFloat() - dotHeight / 2f

        if (animate) {
            nowPlayhead.animate()
                .x(lineTargetX)
                .setDuration(950L)
                .setInterpolator(LinearInterpolator())
                .start()

            nowPlayheadLabel.animate()
                .x(labelTargetX)
                .setDuration(950L)
                .setInterpolator(LinearInterpolator())
                .start()

            if (::nowPlayheadDot.isInitialized) {
                nowPlayheadDot.animate()
                    .x(dotTargetX)
                    .y(dotTargetY)
                    .setDuration(950L)
                    .setInterpolator(LinearInterpolator())
                    .start()
            }
        } else {
            nowPlayhead.animate().cancel()
            nowPlayheadLabel.animate().cancel()
            nowPlayhead.x = lineTargetX
            nowPlayheadLabel.x = labelTargetX

            if (::nowPlayheadDot.isInitialized) {
                nowPlayheadDot.animate().cancel()
                nowPlayheadDot.x = dotTargetX
                nowPlayheadDot.y = dotTargetY
            }
        }
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
        detailsChannel.text = "${channel.number}  ${channel.name}"
        detailsTitle.text = program.title.orEmpty().ifBlank { "No program information" }

        val metadataParts = buildList {
            program.subtitle?.takeIf { it.isNotBlank() }?.let { add(it) }
            program.category?.takeIf { it.isNotBlank() }?.let { add(it) }
            program.rating?.takeIf { it.isNotBlank() }?.let { add(it) }
            program.episode?.takeIf { it.isNotBlank() }?.let { add(it) }
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
                    ((stop.time - nowMs + 59_999L) / 60_000L).coerceAtLeast(0L)

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

        val artworkUrl = program.artwork
            ?.takeIf { it.isNotBlank() }
            ?: channel.logo?.takeIf { it.isNotBlank() }

        if (artworkUrl == null) {
            Glide.with(this).clear(detailsBackdrop)
            Glide.with(this).clear(detailsArtwork)

            detailsBackdrop.setImageDrawable(null)
            detailsArtwork.setImageDrawable(null)

            detailsBackdrop.visibility = View.INVISIBLE
            detailsArtwork.visibility = View.INVISIBLE
        } else {
            detailsBackdrop.visibility = View.VISIBLE
            detailsArtwork.visibility = View.VISIBLE

            // Large cinematic background. Glide reuses the same downloaded
            // source as the poster, so this does not require another network
            // download after the image enters cache.
            Glide.with(this)
                .load(artworkUrl)
                // Small backdrop decode keeps navigation fast on Android TV.
                .override(640, 360)
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .dontAnimate()
                .centerCrop()
                .into(detailsBackdrop)

            // Sharp foreground artwork. Keep this immediate while navigating.
            Glide.with(this)
                .load(artworkUrl)
                .override(400, 264)
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .dontAnimate()
                .fitCenter()
                .into(detailsArtwork)
        }
    }

    private fun buildGrid() {
        channelList.adapter = EpgAdapter(
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
            }
        )

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
    private val channels: List<Channel>,
    private val guide: Map<String, List<EpgProgram>>,
    private val minuteWidthDp: Int,
    private val rowHeightDp: Int,
    private val windowHours: Int,
    private val onTune: (Channel) -> Unit,
    private val onHScroll: (Int) -> Unit,
    private val getScrollX: () -> Int,
    private val windowStartMs: Long,
    private val onProgramFocused: (Channel, EpgProgram) -> Unit
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
        val contentWidth = (width - 4).coerceAtLeast(1)
        val contentHeight = (height - 4).coerceAtLeast(1)

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

            setBackgroundResource(
                if (isNow) {
                    R.drawable.bg_epg_now
                } else {
                    R.drawable.bg_epg_cell
                }
            )

            // Premium TV-style focus animation. Keep the scale subtle so
            // neighboring cells do not jump or become difficult to read.
            pivotX = contentWidth / 2f
            pivotY = contentHeight / 2f

            setOnFocusChangeListener { focusedView, hasFocus ->
                focusedView.animate().cancel()

                focusedView.setBackgroundResource(
                    when {
                        hasFocus -> R.drawable.bg_epg_focused
                        isNow -> R.drawable.bg_epg_now
                        else -> R.drawable.bg_epg_cell
                    }
                )

                focusedView.animate()
                    .scaleX(if (hasFocus) 1.025f else 1.0f)
                    .scaleY(if (hasFocus) 1.06f else 1.0f)
                    .translationZ(if (hasFocus) dp(8).toFloat() else 0f)
                    .setDuration(if (hasFocus) 140L else 110L)
                    .setInterpolator(
                        android.view.animation.DecelerateInterpolator()
                    )
                    .start()

                if (hasFocus) {
                    focusedView.bringToFront()
                    setActiveRow(focusedView, adapterPosition)
                    onProgramFocused(channel, program)
                }
            }

            setOnClickListener {
                onTune(channel)
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

        return FrameLayout(ctx).apply {
            clipChildren = false
            clipToPadding = false

            layoutParams = LinearLayout.LayoutParams(
                width,
                height
            ).apply {
                setMargins(2, 2, 2, 2)
            }

            addView(
                titleView,
                FrameLayout.LayoutParams(
                    contentWidth,
                    contentHeight
                )
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