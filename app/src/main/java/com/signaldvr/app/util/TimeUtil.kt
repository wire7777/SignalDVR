package com.signaldvr.app.util

import java.text.SimpleDateFormat
import java.util.*

object TimeUtil {

    private val EPG_FORMAT = SimpleDateFormat("yyyyMMddHHmmss", Locale.US)
    private val DISPLAY_FORMAT = SimpleDateFormat("h:mm a", Locale.US)

    fun parseEpg(s: String?): Date? {
        if (s.isNullOrBlank()) return null
        return try {
            EPG_FORMAT.parse(s.take(14))
        } catch (e: Exception) {
            null
        }
    }

    fun formatDisplay(date: Date?): String {
        if (date == null) return ""
        return DISPLAY_FORMAT.format(date)
    }

    fun formatDisplay(epgString: String?): String {
        return formatDisplay(parseEpg(epgString))
    }

    /**
     * Progress 0..100 through a program based on start/stop strings.
     */
    fun progressPercent(start: String?, stop: String?): Int {
        val s = parseEpg(start) ?: return 0
        val e = parseEpg(stop)  ?: return 0
        val now   = System.currentTimeMillis()
        val total = e.time - s.time
        val elapsed = now - s.time
        if (total <= 0) return 0
        return (elapsed * 100 / total).toInt().coerceIn(0, 100)
    }

    /**
     * Minutes remaining until stop time.
     */
    fun minutesRemaining(stop: String?): Int {
        val e = parseEpg(stop) ?: return 0
        val remaining = e.time - System.currentTimeMillis()
        return (remaining / 60_000).toInt().coerceAtLeast(0)
    }

    fun nowEpg(): String {
        return EPG_FORMAT.format(Date())
    }
}
