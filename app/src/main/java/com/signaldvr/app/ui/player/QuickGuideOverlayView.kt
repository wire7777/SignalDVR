package com.signaldvr.app.ui.player

import android.content.Context
import android.util.AttributeSet
import android.graphics.drawable.GradientDrawable
import android.view.LayoutInflater
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import com.signaldvr.app.api.NowPlaying
import com.signaldvr.app.ui.RecordingStatusUi
import com.signaldvr.app.util.TimeUtil
import java.util.Date

class QuickGuideOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : FrameLayout(context, attrs, defStyleAttr) {

    private val channelNumber: TextView
    private val channelName: TextView
    private val title: TextView
    private val subtitle: TextView
    private val badges: TextView
    private val timeRange: TextView
    private val remaining: TextView
    private val details: TextView
    private val description: TextView
    private val credits: TextView
    private val nextProgram: TextView
    private val clock: TextView
    private val progress: ProgressBar
    private val channelList: LinearLayout
    private val artwork: ImageView

    private var channels: List<Channel> = emptyList()
    private var selectedIndex = 0
    private var playingChannel: String = ""
    private var onSelectionChanged: ((Channel) -> Unit)? = null
    private var dismissRunnable: Runnable? = null

    init {
        LayoutInflater.from(context)
            .inflate(R.layout.view_quick_guide_overlay, this, true)

        channelNumber = findViewById(R.id.quick_guide_channel_number)
        channelName = findViewById(R.id.quick_guide_channel_name)
        title = findViewById(R.id.quick_guide_title)
        subtitle = findViewById(R.id.quick_guide_subtitle)
        badges = findViewById(R.id.quick_guide_badges)
        timeRange = findViewById(R.id.quick_guide_time)
        remaining = findViewById(R.id.quick_guide_remaining)
        details = findViewById(R.id.quick_guide_details)
        description = findViewById(R.id.quick_guide_description)
        credits = findViewById(R.id.quick_guide_credits)
        nextProgram = findViewById(R.id.quick_guide_next)
        clock = findViewById(R.id.quick_guide_clock)
        progress = findViewById(R.id.quick_guide_progress)
        channelList = findViewById(R.id.quick_guide_channel_list)
        artwork = findViewById(R.id.quick_guide_artwork)

        visibility = View.GONE
        isFocusable = true
        isFocusableInTouchMode = true
    }

    fun show(
        channels: List<Channel>,
        playingChannel: String,
        onSelectionChanged: (Channel) -> Unit,
        autoDismissMs: Long = 12_000L,
    ) {
        this.channels = channels
        this.playingChannel = playingChannel
        this.onSelectionChanged = onSelectionChanged

        selectedIndex = channels.indexOfFirst {
            it.number == playingChannel
        }.takeIf { it >= 0 } ?: 0

        renderChannelList()
        updateHeaderFromChannel(channels.getOrNull(selectedIndex))

        visibility = View.VISIBLE
        alpha = 0f
        translationX = width.coerceAtLeast(1).toFloat()

        animate()
            .alpha(1f)
            .translationX(0f)
            .setDuration(180L)
            .start()

        requestFocus()
        notifySelectionChanged()
        scheduleDismiss(autoDismissMs)
    }

    fun isShowing(): Boolean = visibility == View.VISIBLE

    fun selectedChannel(): Channel? = channels.getOrNull(selectedIndex)

    fun moveSelection(delta: Int) {
        if (channels.isEmpty()) return

        selectedIndex = (selectedIndex + delta)
            .coerceIn(0, channels.lastIndex)

        renderChannelList()
        updateHeaderFromChannel(channels[selectedIndex])
        notifySelectionChanged()
        scheduleDismiss(12_000L)
    }

    fun updateProgram(data: NowPlaying) {
        channelNumber.text = data.channel
        channelName.text = data.name

        val current = data.current

        if (current == null) {
            title.text = "No guide data"
            subtitle.text = ""
            badges.text = ""
            timeRange.text = ""
            remaining.text = ""
            details.text = ""
            description.text = ""
            credits.text = ""
            progress.progress = 0
        } else {
            title.text = current.title
                ?.takeIf { it.isNotBlank() }
                ?: "No guide data"

            subtitle.text = current.episodeTitle
                ?.takeIf { it.isNotBlank() }
                ?: current.subtitle.orEmpty()

            val metadata = mutableListOf<String>()

            if (current.isNew == 1) {
                metadata += "NEW"
            } else if (!current.originalAirDate.isNullOrBlank()) {
                metadata += "REPEAT"
            }

            if (
                current.season > 0 &&
                !current.episode.isNullOrBlank()
            ) {
                metadata += "S${current.season} E${current.episode}"
            }

            current.rating
                ?.takeIf { it.isNotBlank() }
                ?.let { metadata += it.uppercase() }

            current.genres
                ?.split(",", "|")
                ?.map { it.trim() }
                ?.firstOrNull { it.isNotBlank() }
                ?.let { metadata += it.uppercase() }

            capabilityBadges(
                current.videoProperties,
                current.audioProperties,
            ).forEach {
                metadata += it
            }

            badges.text = RecordingStatusUi.buildBadges(
                current.recordingStatus,
                metadata.distinct(),
            )

            timeRange.text = buildTimeRange(
                current.start,
                current.stop,
            )

            progress.progress = TimeUtil.progressPercent(
                current.start,
                current.stop,
            )

            val minutesLeft =
                TimeUtil.minutesRemaining(current.stop)

            remaining.text = when {
                minutesLeft < 0 -> ""

                minutesLeft >= 60 ->
                    "${minutesLeft / 60}h " +
                        "${minutesLeft % 60}m left"

                else ->
                    "$minutesLeft min left"
            }

            details.text = buildList {
                current.showType
                    ?.takeIf { it.isNotBlank() }
                    ?.let { add(it) }

                current.runtime
                    .takeIf { it > 0 }
                    ?.let { runtimeSeconds ->
                        val totalMinutes = runtimeSeconds / 60
                        val hours = totalMinutes / 60
                        val minutes = totalMinutes % 60

                        add(
                            when {
                                hours > 0 && minutes > 0 ->
                                    "${hours} hr ${minutes} min"

                                hours > 0 ->
                                    "${hours} hr"

                                else ->
                                    "${totalMinutes} min"
                            }
                        )
                    }

                current.year
                    .takeIf { it > 0 }
                    ?.let { add(it.toString()) }

                current.originalAirDate
                    ?.takeIf { it.isNotBlank() }
                    ?.let { add("Aired ${formatAirDate(it)}") }
            }.joinToString("  •  ")

            description.text = current.description
                ?.takeIf { it.isNotBlank() }
                ?: "No description available."

            credits.text = buildList {
                current.cast
                    ?.takeIf { it.isNotBlank() }
                    ?.let {
                        add(
                            "Starring ${shortenPeople(it, 3)}"
                        )
                    }

                current.directors
                    ?.takeIf { it.isNotBlank() }
                    ?.let {
                        add(
                            "Directed by ${shortenPeople(it, 2)}"
                        )
                    }

                current.writers
                    ?.takeIf { it.isNotBlank() }
                    ?.let {
                        add(
                            "Written by ${shortenPeople(it, 2)}"
                        )
                    }
            }.joinToString("\n")
        }

        nextProgram.text = data.next?.title
            ?.takeIf { it.isNotBlank() }
            ?.let {
                val nextTime =
                    TimeUtil.formatDisplay(data.next.start)

                if (nextTime.isNotBlank()) {
                    "NEXT  $nextTime  •  $it"
                } else {
                    "NEXT  $it"
                }
            }
            ?: ""

        clock.text = TimeUtil.formatDisplay(Date())

        loadArtwork(data)
    }

    fun showLoading(channel: Channel) {
        Glide.with(this).clear(artwork)
        artwork.setImageDrawable(null)
        artwork.visibility = View.GONE

        channelNumber.text = channel.number
        channelName.text = channel.name
        title.text = channel.nowTitle ?: "Loading guide…"
        subtitle.text = ""
        badges.text = ""
        timeRange.text = ""
        remaining.text = ""
        details.text = ""
        description.text = ""
        credits.text = ""

        nextProgram.text = channel.nextTitle
            ?.takeIf { it.isNotBlank() }
            ?.let { "NEXT  $it" }
            ?: ""

        progress.progress = 0
        clock.text = TimeUtil.formatDisplay(Date())
    }

    fun hide() {
        dismissRunnable?.let {
            removeCallbacks(it)
        }

        dismissRunnable = null

        animate()
            .alpha(0f)
            .translationX(
                width.coerceAtLeast(1).toFloat()
            )
            .setDuration(160L)
            .withEndAction {
                visibility = View.GONE
                translationX = 0f
            }
            .start()
    }

    private fun notifySelectionChanged() {
        selectedChannel()?.let { channel ->
            showLoading(channel)
            onSelectionChanged?.invoke(channel)
        }
    }

    private fun updateHeaderFromChannel(
        channel: Channel?,
    ) {
        if (channel == null) return

        channelNumber.text = channel.number
        channelName.text = channel.name
        clock.text = TimeUtil.formatDisplay(Date())
    }

    private fun renderChannelList() {
        channelList.post {
            renderMeasuredChannelList()
        }
    }

    private fun renderMeasuredChannelList() {
        channelList.removeAllViews()

        if (channels.isEmpty()) {
            val empty = TextView(context).apply {
                text = "No channels"
                textSize = 16f
                setTextColor(0xFFBBBBBB.toInt())
                gravity =
                    android.view.Gravity.CENTER_VERTICAL

                setPadding(
                    dpToPx(16),
                    dpToPx(10),
                    dpToPx(16),
                    dpToPx(10),
                )
            }

            channelList.addView(empty)
            return
        }

        /*
         * Keep all three rows inside the fixed bottom channel panel.
         *
         * Three 28dp rows = 84dp total, leaving a small safety allowance
         * inside the 90dp panel. The old 30dp rows also added 1dp top and
         * bottom margins, requiring 96dp and clipping the third row.
         */
        val rowHeight = dpToPx(28)
        val visibleRows = minOf(3, channels.size)
        val halfWindow = 1

        val maxFirstIndex =
            (channels.size - visibleRows)
                .coerceAtLeast(0)

        val firstIndex =
            (selectedIndex - halfWindow)
                .coerceIn(0, maxFirstIndex)

        val lastIndex =
            (firstIndex + visibleRows - 1)
                .coerceAtMost(channels.lastIndex)

        for (index in firstIndex..lastIndex) {
            val channel = channels[index]
            val selected = index == selectedIndex
            val playing =
                channel.number == playingChannel

            val row = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity =
                    android.view.Gravity.CENTER_VERTICAL

                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    rowHeight,
                )

                setPadding(
                    dpToPx(8),
                    0,
                    dpToPx(8),
                    0,
                )

                /*
                 * Draw the row backgrounds here so the Quick Guide rows are
                 * square even if the shared channel drawables use rounded
                 * corners elsewhere in the app.
                 */
                background = buildSquareRowBackground(selected)
            }

            val logoView = ImageView(context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    dpToPx(28),
                    dpToPx(22),
                ).apply {
                    marginEnd = dpToPx(7)
                }

                scaleType =
                    ImageView.ScaleType.FIT_CENTER

                adjustViewBounds = true

                contentDescription =
                    "${channel.number} " +
                        "${channel.name} logo"
            }

            val label = TextView(context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    0,
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    1f,
                )

                text = buildString {
                    if (selected) {
                        append("▶ ")
                    }

                    append(channel.number)
                    append("  ")
                    append(channel.name)

                    if (playing) {
                        append("  • LIVE")
                    }
                }

                textSize =
                    if (selected) 14.5f else 13.5f

                setTextColor(
                    if (selected) {
                        0xFFFFFF66.toInt()
                    } else {
                        0xFFFFFFFF.toInt()
                    }
                )

                maxLines = 1
                isSingleLine = true

                ellipsize =
                    android.text.TextUtils.TruncateAt.END

                gravity =
                    android.view.Gravity.CENTER_VERTICAL
            }

            row.addView(logoView)
            row.addView(label)
            channelList.addView(row)

            if (!channel.logo.isNullOrBlank()) {
                Glide.with(this)
                    .load(channel.logo)
                    .fitCenter()
                    .into(logoView)
            } else {
                logoView.setImageDrawable(null)
            }
        }
    }

    private fun loadArtwork(data: NowPlaying) {
        val rawArtwork = data.current?.artwork
            ?.trim()
            ?.takeIf {
                it.isNotBlank() &&
                    !it.equals(
                        "true",
                        ignoreCase = true,
                    ) &&
                    !it.equals(
                        "false",
                        ignoreCase = true,
                    )
            }

        val baseUrl = ApiClient
            .getBaseUrl(context)
            .trimEnd('/')

        val artworkUrl = when {
            rawArtwork == null ->
                null

            rawArtwork.startsWith("http://") ||
                rawArtwork.startsWith("https://") ->
                rawArtwork

            rawArtwork.startsWith("/") ->
                baseUrl + rawArtwork

            else ->
                "$baseUrl/${rawArtwork.trimStart('/')}"
        }

        Glide.with(this).clear(artwork)

        if (artworkUrl == null) {
            artwork.setImageDrawable(null)
            artwork.visibility = View.GONE
            return
        }

        artwork.visibility = View.VISIBLE

        Glide.with(this)
            .load(artworkUrl)
            .diskCacheStrategy(
                DiskCacheStrategy.ALL
            )
            .skipMemoryCache(false)
            .fitCenter()
            .into(artwork)
    }

    private fun buildSquareRowBackground(
        selected: Boolean,
    ): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = 0f

            setColor(
                if (selected) {
                    // Slightly deeper SignalDVR blue for a calmer TV focus.
                    0xFF1565C0.toInt()
                } else {
                    // Neutral dark navy that blends into the overlay.
                    0xE618212E.toInt()
                }
            )

            // No outline. Selection is shown by fill and yellow text only.
        }
    }

    private fun dpToPx(dp: Int): Int {
        return (
            dp * resources.displayMetrics.density
        ).toInt()
    }

    private fun scheduleDismiss(delayMs: Long) {
        dismissRunnable?.let {
            removeCallbacks(it)
        }

        dismissRunnable = Runnable {
            hide()
        }.also {
            postDelayed(it, delayMs)
        }
    }


    private fun capabilityBadges(
        videoProperties: String?,
        audioProperties: String?,
    ): List<String> {
        val video =
            videoProperties.orEmpty().lowercase()

        val audio =
            audioProperties.orEmpty().lowercase()

        val result = mutableListOf<String>()

        when {
            "2160" in video ||
                "uhd" in video ||
                "4k" in video ->
                result += "4K"

            "1080" in video ||
                "720" in video ||
                "hd" in video ->
                result += "HD"
        }

        if (
            "5.1" in audio ||
            "surround" in audio ||
            "dolby" in audio ||
            "ac3" in audio
        ) {
            result += "5.1"
        }

        if (
            "cc" in video ||
            "caption" in video ||
            "closed" in video
        ) {
            result += "CC"
        }

        return result
    }

    private fun shortenPeople(
        value: String,
        limit: Int,
    ): String {
        val people = value
            .split(",", "|", ";")
            .map { it.trim() }
            .filter { it.isNotBlank() }

        if (people.size <= limit) {
            return people.joinToString(", ")
        }

        return people
            .take(limit)
            .joinToString(", ") +
            " +${people.size - limit}"
    }

    private fun formatAirDate(value: String): String {
        val parts = value.trim().split("-")

        if (parts.size != 3) {
            return value
        }

        val year = parts[0].toIntOrNull()
            ?: return value

        val month = parts[1].toIntOrNull()
            ?: return value

        val day = parts[2].toIntOrNull()
            ?: return value

        val monthName = listOf(
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ).getOrNull(month - 1)
            ?: return value

        return "$monthName $day, $year"
    }
    private fun buildTimeRange(
        start: String?,
        stop: String?,
    ): String {
        val startLabel =
            TimeUtil.formatDisplay(start)

        val stopLabel =
            TimeUtil.formatDisplay(stop)

        return when {
            startLabel.isNotBlank() &&
                stopLabel.isNotBlank() ->
                "$startLabel – $stopLabel"

            startLabel.isNotBlank() ->
                startLabel

            else ->
                ""
        }
    }
}