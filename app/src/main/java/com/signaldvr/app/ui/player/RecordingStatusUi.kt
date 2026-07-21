package com.signaldvr.app.ui

import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan

object RecordingStatusUi {

    data class Style(
        val badgeText: String,
        val buttonText: String,
        val guideText: String,
        val color: Int,
    )

    fun style(status: String?): Style? {
        return when (status?.trim()?.lowercase()) {
            "recording" -> Style(
                badgeText = "REC",
                buttonText = "RECORDING",
                guideText = "● REC",
                color = 0xFFFF5252.toInt(),
            )

            "recorded" -> Style(
                badgeText = "RECORDED",
                buttonText = "RECORDED",
                guideText = "● DONE",
                color = 0xFF66BB6A.toInt(),
            )

            "scheduled" -> Style(
                badgeText = "SCHEDULED",
                buttonText = "SCHEDULED",
                guideText = "● SCH",
                color = 0xFF64B5F6.toInt(),
            )

            "series" -> Style(
                badgeText = "SERIES",
                buttonText = "RECORD SERIES",
                guideText = "● SER",
                color = 0xFFBA68C8.toInt(),
            )

            "series_new" -> Style(
                badgeText = "NEW EPISODES",
                buttonText = "RECORD NEW",
                guideText = "● NEW",
                color = 0xFFFFB74D.toInt(),
            )

            else -> null
        }
    }

    fun buildBadges(
        status: String?,
        metadata: List<String>,
        separator: String = "   ",
    ): CharSequence {
        val builder = SpannableStringBuilder()
        val style = style(status)

        if (style != null) {
            val start = builder.length
            builder.append(style.badgeText)
            val end = builder.length

            builder.setSpan(
                ForegroundColorSpan(style.color),
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
            if (builder.isNotEmpty()) builder.append(separator)
            builder.append(badge)
        }

        return builder
    }
}