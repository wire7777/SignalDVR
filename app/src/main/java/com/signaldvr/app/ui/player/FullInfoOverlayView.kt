package com.signaldvr.app.ui.player

import android.content.Context
import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.util.AttributeSet
import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import com.bumptech.glide.Glide
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.NowPlaying
import com.signaldvr.app.util.TimeUtil
import java.util.Date

class FullInfoOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : FrameLayout(context, attrs, defStyleAttr) {

    private val artwork: ImageView
    private val logo: ImageView
    private val channel: TextView
    private val clock: TextView
    private val title: TextView
    private val subtitle: TextView
    private val badges: TextView
    private val time: TextView
    private val remaining: TextView
    private val progress: ProgressBar
    private val details: TextView
    private val description: TextView
    private val cast: TextView
    private val directors: TextView
    private val writers: TextView
    private val next: TextView
    private val watchButton: TextView
    private val recordButton: TextView
    private val closeButton: TextView

    private val actions: List<TextView>
    private var selectedAction = 0
    private var onWatch: (() -> Unit)? = null
    private var onRecord: (() -> Unit)? = null
    private var onClose: (() -> Unit)? = null

    init {
        LayoutInflater.from(context)
            .inflate(R.layout.view_full_info_overlay, this, true)

        artwork = findViewById(R.id.full_info_artwork)
        logo = findViewById(R.id.full_info_logo)
        channel = findViewById(R.id.full_info_channel)
        clock = findViewById(R.id.full_info_clock)
        title = findViewById(R.id.full_info_title)
        subtitle = findViewById(R.id.full_info_subtitle)
        badges = findViewById(R.id.full_info_badges)
        time = findViewById(R.id.full_info_time)
        remaining = findViewById(R.id.full_info_remaining)
        progress = findViewById(R.id.full_info_progress)
        details = findViewById(R.id.full_info_details)
        description = findViewById(R.id.full_info_description)
        cast = findViewById(R.id.full_info_cast)
        directors = findViewById(R.id.full_info_directors)
        writers = findViewById(R.id.full_info_writers)
        next = findViewById(R.id.full_info_next)
        watchButton = findViewById(R.id.full_info_watch)
        recordButton = findViewById(R.id.full_info_record)
        closeButton = findViewById(R.id.full_info_close)

        actions = listOf(watchButton, recordButton, closeButton)

        watchButton.setOnClickListener { onWatch?.invoke() }
        recordButton.setOnClickListener { onRecord?.invoke() }
        closeButton.setOnClickListener { onClose?.invoke() ?: hide() }

        visibility = View.GONE
        isFocusable = true
        isFocusableInTouchMode = true
    }

    fun show(
        data: NowPlaying,
        logoUrl: String?,
        watchLabel: String,
        onWatch: () -> Unit,
        onRecord: () -> Unit,
        onClose: () -> Unit,
    ) {
        this.onWatch = onWatch
        this.onRecord = onRecord
        this.onClose = onClose

        bind(data, logoUrl)
        watchButton.text = watchLabel
        selectedAction = 0
        updateActionSelection()

        visibility = View.VISIBLE
        alpha = 0f
        animate().alpha(1f).setDuration(180L).start()
        requestFocus()
    }

    fun hide() {
        animate()
            .alpha(0f)
            .setDuration(140L)
            .withEndAction { visibility = View.GONE }
            .start()
    }

    fun isShowing(): Boolean = visibility == View.VISIBLE

    fun handleKey(keyCode: Int): Boolean {
        return when (keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT -> {
                selectedAction = (selectedAction - 1).coerceAtLeast(0)
                updateActionSelection()
                true
            }

            KeyEvent.KEYCODE_DPAD_RIGHT -> {
                selectedAction = (selectedAction + 1).coerceAtMost(actions.lastIndex)
                updateActionSelection()
                true
            }

            KeyEvent.KEYCODE_DPAD_CENTER,
            KeyEvent.KEYCODE_ENTER -> {
                actions[selectedAction].performClick()
                true
            }

            KeyEvent.KEYCODE_BACK,
            KeyEvent.KEYCODE_INFO,
            KeyEvent.KEYCODE_GUIDE,
            KeyEvent.KEYCODE_MENU -> {
                onClose?.invoke() ?: hide()
                true
            }

            else -> true
        }
    }

    private fun bind(data: NowPlaying, logoUrl: String?) {
        val current = data.current

        channel.text = "${data.channel}  ${data.name}"
        clock.text = TimeUtil.formatDisplay(Date())
        title.text = current?.title?.takeIf { it.isNotBlank() } ?: "No guide data"
        subtitle.text = current?.episodeTitle
            ?.takeIf { it.isNotBlank() }
            ?: current?.subtitle.orEmpty()

        if (!logoUrl.isNullOrBlank()) {
            Glide.with(this).load(logoUrl).fitCenter().into(logo)
            logo.visibility = View.VISIBLE
        } else {
            logo.setImageDrawable(null)
            logo.visibility = View.GONE
        }

        val rawArtwork = current?.artwork
            ?.trim()
            ?.takeIf {
                it.isNotBlank() &&
                        !it.equals("true", ignoreCase = true) &&
                        !it.equals("false", ignoreCase = true)
            }

        val baseUrl = ApiClient
            .getBaseUrl(context)
            .trimEnd('/')

        val artworkUrl = when {
            rawArtwork == null -> null

            rawArtwork.startsWith("http://") ||
                    rawArtwork.startsWith("https://") -> rawArtwork

            rawArtwork.startsWith("/") -> baseUrl + rawArtwork

            else -> "$baseUrl/${rawArtwork.trimStart('/')}"
        }

        android.util.Log.d(
            "SignalDVR-Artwork",
            "raw=$rawArtwork base=$baseUrl final=$artworkUrl"
        )

        Glide.with(this).clear(artwork)

        if (artworkUrl != null) {
            artwork.visibility = View.VISIBLE

            Glide.with(this)
                .load(artworkUrl)
                .diskCacheStrategy(
                    com.bumptech.glide.load.engine.DiskCacheStrategy.ALL
                )
                .skipMemoryCache(false)
                .thumbnail(0.25f)
                .fitCenter()
                .listener(
                    object :
                        com.bumptech.glide.request.RequestListener<
                                android.graphics.drawable.Drawable
                                > {

                        override fun onLoadFailed(
                            error: com.bumptech.glide.load.engine.GlideException?,
                            model: Any?,
                            target: com.bumptech.glide.request.target.Target<
                                    android.graphics.drawable.Drawable
                                    >,
                            isFirstResource: Boolean,
                        ): Boolean {
                            android.util.Log.e(
                                "SignalDVR-Artwork",
                                "FAILED url=$model",
                                error,
                            )
                            return false
                        }

                        override fun onResourceReady(
                            resource: android.graphics.drawable.Drawable,
                            model: Any,
                            target: com.bumptech.glide.request.target.Target<
                                    android.graphics.drawable.Drawable
                                    >?,
                            dataSource: com.bumptech.glide.load.DataSource,
                            isFirstResource: Boolean,
                        ): Boolean {
                            android.util.Log.d(
                                "SignalDVR-Artwork",
                                "LOADED url=$model source=$dataSource",
                            )
                            return false
                        }
                    }
                )
                .into(artwork)

        } else {
            artwork.setImageDrawable(null)
            artwork.visibility = View.INVISIBLE
        }
        val metadata = mutableListOf<String>()
        if (current?.isNew == 1) metadata += "NEW"
        if (current != null && current.season > 0 && !current.episode.isNullOrBlank()) {
            metadata += "S${current.season} E${current.episode}"
        }
        current?.rating?.takeIf { it.isNotBlank() }?.let { metadata += it.uppercase() }
        current?.genres
            ?.split(",", "|")
            ?.map { it.trim() }
            ?.filter { it.isNotBlank() }
            ?.take(2)
            ?.forEach { metadata += it.uppercase() }
        capabilityBadges(current?.videoProperties, current?.audioProperties)
            .forEach { metadata += it }
        badges.text = buildBadgesText(
            metadata.distinct(),
            current?.recordingStatus,
        )

        time.text = buildTimeRange(current?.start, current?.stop)
        progress.progress = if (current == null) 0 else {
            TimeUtil.progressPercent(current.start, current.stop)
        }

        val minutesLeft = current?.let { TimeUtil.minutesRemaining(it.stop) } ?: -1
        remaining.text = when {
            minutesLeft < 0 -> ""
            minutesLeft >= 60 -> "${minutesLeft / 60}h ${minutesLeft % 60}m left"
            else -> "$minutesLeft min left"
        }

        details.text = buildList {
            current?.showType?.takeIf { it.isNotBlank() }?.let { add(it) }
            current?.runtime?.takeIf { it > 0 }?.let { runtimeSeconds ->
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
            current?.year?.takeIf { it > 0 }?.let { add(it.toString()) }
            current?.originalAirDate?.takeIf { it.isNotBlank() }?.let { add("Aired ${formatAirDate(it)}") }
            current?.language?.takeIf { it.isNotBlank() }?.let { add(it) }
        }.joinToString("  •  ")

        description.text = current?.description
            ?.takeIf { it.isNotBlank() }
            ?: "No description available."

        cast.text = labeledPeople("CAST", current?.cast)
        directors.text = labeledPeople("DIRECTOR", current?.directors)
        writers.text = labeledPeople("WRITER", current?.writers)

        next.text = data.next?.title
            ?.takeIf { it.isNotBlank() }
            ?.let {
                val nextTime = TimeUtil.formatDisplay(data.next.start)
                if (nextTime.isNotBlank()) "NEXT  $nextTime  •  $it" else "NEXT  $it"
            }
            ?: ""
    }

    private fun labeledPeople(label: String, value: String?): String {
        val clean = value?.takeIf { it.isNotBlank() } ?: return ""
        return "$label  $clean"
    }

    private fun updateActionSelection() {
        actions.forEachIndexed { index, view ->
            view.setBackgroundResource(
                if (index == selectedAction) {
                    R.drawable.bg_channel_focused
                } else {
                    R.drawable.bg_channel_normal
                }
            )
            view.setTextColor(
                if (index == selectedAction) 0xFFFFFF66.toInt()
                else 0xFFFFFFFF.toInt()
            )
        }
    }

    private fun buildBadgesText(
        metadata: List<String>,
        recordingStatus: String?,
    ): CharSequence {
        val builder = SpannableStringBuilder()

        val status = when (recordingStatus?.lowercase()) {
            "recording" -> "REC" to 0xFFFF5252.toInt()
            "recorded" -> "RECORDED" to 0xFF66BB6A.toInt()
            "scheduled" -> "SCHEDULED" to 0xFF64B5F6.toInt()
            else -> null
        }

        if (status != null) {
            val start = builder.length
            builder.append(status.first)
            val end = builder.length
            builder.setSpan(
                ForegroundColorSpan(status.second),
                start,
                end,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
            builder.setSpan(
                StyleSpan(Typeface.BOLD),
                start,
                end,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        }

        metadata.forEach { badge ->
            if (builder.isNotEmpty()) builder.append("   ")
            builder.append(badge)
        }

        return builder
    }

    private fun capabilityBadges(videoProperties: String?, audioProperties: String?): List<String> {
        val video = videoProperties.orEmpty().lowercase()
        val audio = audioProperties.orEmpty().lowercase()
        val result = mutableListOf<String>()

        when {
            "2160" in video || "uhd" in video || "4k" in video -> result += "4K"
            "1080" in video || "720" in video || "hd" in video -> result += "HD"
        }
        if ("5.1" in audio || "surround" in audio || "dolby" in audio || "ac3" in audio) {
            result += "5.1"
        }
        if ("cc" in video || "caption" in video || "closed" in video) {
            result += "CC"
        }
        return result
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
    private fun buildTimeRange(start: String?, stop: String?): String {
        val startLabel = TimeUtil.formatDisplay(start)
        val stopLabel = TimeUtil.formatDisplay(stop)
        return when {
            startLabel.isNotBlank() && stopLabel.isNotBlank() -> "$startLabel – $stopLabel"
            startLabel.isNotBlank() -> startLabel
            else -> ""
        }
    }
}
