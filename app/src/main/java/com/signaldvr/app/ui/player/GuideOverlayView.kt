package com.signaldvr.app.ui.player

import android.content.Context
import android.util.AttributeSet
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

class GuideOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : FrameLayout(context, attrs, defStyle) {

    private val channelLogo: ImageView
    private val tvChannelNum: TextView
    private val tvChannelName: TextView
    private val tvCurrentTitle: TextView
    private val tvTimeRange: TextView
    private val progressBar: ProgressBar
    private val tvRemaining: TextView
    private val tvNext: TextView
    private val tvClock: TextView
    private val tvStatus: TextView

    private var dismissRunnable: Runnable? = null

    init {
        LayoutInflater.from(context).inflate(R.layout.view_guide_overlay, this, true)

        channelLogo = findViewById(R.id.overlay_channel_logo)
        tvChannelNum = findViewById(R.id.overlay_channel_num)
        tvChannelName = findViewById(R.id.overlay_channel_name)
        tvCurrentTitle = findViewById(R.id.overlay_current_title)
        tvTimeRange = findViewById(R.id.overlay_current_time)
        progressBar = findViewById(R.id.overlay_progress)
        tvRemaining = findViewById(R.id.overlay_time_remaining)
        tvNext = findViewById(R.id.overlay_next_title)
        tvClock = findViewById(R.id.overlay_clock)
        tvStatus = findViewById(R.id.overlay_status_icon)

        visibility = View.GONE
    }

    fun show(
        data: NowPlaying,
        logoUrl: String? = null,
        autoDismissMs: Long = 7000L,
    ) {
        tvChannelNum.text = data.channel
        tvChannelName.text = data.name
        loadChannelLogo(logoUrl)

        val cur = data.current
        if (cur != null) {
            tvCurrentTitle.text = cur.title ?: "No guide data"
            tvTimeRange.text = buildTimeRange(cur.start, cur.stop)
            progressBar.progress = TimeUtil.progressPercent(cur.start, cur.stop)

            val remainingMinutes = TimeUtil.minutesRemaining(cur.stop)
                .coerceAtLeast(0)

            tvRemaining.text = if (remainingMinutes >= 60) {
                "${remainingMinutes / 60}h ${remainingMinutes % 60}m left"
            } else {
                "$remainingMinutes min left"
            }
        } else {
            tvCurrentTitle.text = "No guide data"
            tvTimeRange.text = ""
            progressBar.progress = 0
            tvRemaining.text = ""
        }

        val next = data.next
        tvNext.text = if (!next?.title.isNullOrBlank()) {
            "NEXT  ${next?.title}  •  ${TimeUtil.formatDisplay(next?.start)}"
        } else {
            ""
        }

        tvClock.text = TimeUtil.formatDisplay(Date())
        tvStatus.text = "▶"

        present(autoDismissMs)
    }

    fun showBasic(
        chNum: String,
        chName: String,
        autoDismissMs: Long = 7000L,
    ) {
        loadChannelLogo(null)

        tvChannelNum.text = chNum
        tvChannelName.text = chName
        tvCurrentTitle.text = "Live"
        tvTimeRange.text = ""
        progressBar.progress = 0
        tvRemaining.text = ""
        tvNext.text = ""
        tvClock.text = TimeUtil.formatDisplay(Date())
        tvStatus.text = "▶"

        present(autoDismissMs)
    }

    fun showPlayState(playing: Boolean) {
        tvStatus.text = if (playing) "▶" else "⏸"
        present(3000L)
    }

    fun showSeek(seconds: Int) {
        tvStatus.text = if (seconds < 0) {
            "⏪  ${-seconds}s"
        } else {
            "⏩  ${seconds}s"
        }

        present(2000L)
    }

    private fun loadChannelLogo(rawUrl: String?) {
        Glide.with(this).clear(channelLogo)

        val finalUrl = when {
            rawUrl.isNullOrBlank() -> null
            rawUrl.startsWith("http://", ignoreCase = true) -> rawUrl
            rawUrl.startsWith("https://", ignoreCase = true) -> rawUrl
            else -> {
                val baseUrl = ApiClient.getBaseUrl(context).trimEnd('/')
                "$baseUrl/${rawUrl.trimStart('/')}"
            }
        }

        if (finalUrl == null) {
            channelLogo.setImageDrawable(null)
            channelLogo.visibility = View.GONE
            return
        }

        channelLogo.visibility = View.VISIBLE

        Glide.with(this)
            .load(finalUrl)
            .fitCenter()
            .into(channelLogo)
    }

    private fun present(dismissAfterMs: Long) {
        cancelDismiss()

        visibility = View.VISIBLE
        alpha = 1f

        dismissRunnable = Runnable {
            animate()
                .alpha(0f)
                .setDuration(500L)
                .withEndAction {
                    visibility = View.GONE
                    alpha = 1f
                }
                .start()
        }

        postDelayed(dismissRunnable, dismissAfterMs)
    }

    private fun cancelDismiss() {
        dismissRunnable?.let(::removeCallbacks)
        dismissRunnable = null

        animate().cancel()
        alpha = 1f
    }

    private fun buildTimeRange(start: String?, stop: String?): String {
        val startText = TimeUtil.formatDisplay(start)
        val stopText = TimeUtil.formatDisplay(stop)

        return if (startText.isNotEmpty() && stopText.isNotEmpty()) {
            "$startText – $stopText"
        } else {
            ""
        }
    }

    override fun onDetachedFromWindow() {
        /*
         * Do not call Glide.with(this) here. This callback can run after the
         * hosting Activity has already been destroyed, and asking Glide for
         * an Activity-scoped RequestManager at that point throws:
         *
         * "You cannot start a load for a destroyed activity"
         *
         * Glide automatically releases view/activity-scoped requests with
         * the destroyed lifecycle. Clearing the drawable locally is safe.
         */
        cancelDismiss()
        channelLogo.setImageDrawable(null)
        super.onDetachedFromWindow()
    }
}