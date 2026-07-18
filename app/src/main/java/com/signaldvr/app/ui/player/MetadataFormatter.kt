package com.signaldvr.app.ui.player

import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale

/**
 * Shared formatting for guide and player metadata.
 * Backend runtime values are seconds.
 */
object MetadataFormatter {

    private val inputAirDateFormatter: DateTimeFormatter =
        DateTimeFormatter.ISO_LOCAL_DATE

    private val outputAirDateFormatter: DateTimeFormatter =
        DateTimeFormatter.ofPattern("MMMM d, yyyy", Locale.US)

    fun formatDetails(
        showType: String?,
        runtimeSeconds: Int?,
        year: Int?,
        originalAirDate: String?,
        language: String? = null,
    ): String = buildList {
        showType
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?.let(::add)

        runtimeSeconds
            ?.takeIf { it > 0 }
            ?.let { add(formatRuntime(it)) }

        year
            ?.takeIf { it > 0 }
            ?.let { add(it.toString()) }

        originalAirDate
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?.let { add("Aired ${formatAirDate(it)}") }

        language
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?.let(::add)
    }.distinct().joinToString("  •  ")

    fun formatRuntime(runtimeSeconds: Int): String {
        if (runtimeSeconds <= 0) return ""

        val totalMinutes = runtimeSeconds / 60
        val hours = totalMinutes / 60
        val minutes = totalMinutes % 60

        return when {
            hours > 0 && minutes > 0 -> "${hours} hr ${minutes} min"
            hours > 0 -> "${hours} hr"
            totalMinutes > 0 -> "${totalMinutes} min"
            else -> "Less than 1 min"
        }
    }

    fun formatAirDate(value: String): String {
        val cleanValue = value.trim()
        if (cleanValue.isEmpty()) return ""

        return try {
            LocalDate.parse(cleanValue, inputAirDateFormatter)
                .format(outputAirDateFormatter)
        } catch (_: DateTimeParseException) {
            cleanValue
        }
    }
}
