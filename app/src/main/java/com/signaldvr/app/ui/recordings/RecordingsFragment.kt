package com.signaldvr.app.ui.recordings

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
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
import com.signaldvr.app.api.Recording
import com.signaldvr.app.ui.player.PlaybackLauncher
import com.signaldvr.app.util.TimeUtil
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

class RecordingsFragment : Fragment() {

    private lateinit var recycler: RecyclerView
    private lateinit var loading: View
    private lateinit var error: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(
        R.layout.fragment_recordings,
        container,
        false,
    )

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        recycler = view.findViewById(R.id.recordings_recycler)
        loading = view.findViewById(R.id.loading)
        error = view.findViewById(R.id.error_text)

        recycler.layoutManager = LinearLayoutManager(requireContext())
        recycler.setHasFixedSize(true)
        recycler.itemAnimator = null
        recycler.descendantFocusability = ViewGroup.FOCUS_AFTER_DESCENDANTS

        loadRecordings()
    }

    private fun loadRecordings() {
        loading.visibility = View.VISIBLE
        error.visibility = View.GONE

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val recordings = ApiClient
                    .getApi(requireContext())
                    .getRecordings()

                loading.visibility = View.GONE

                if (recordings.isEmpty()) {
                    error.visibility = View.VISIBLE
                    error.text = "No recordings found"
                    return@launch
                }

                recycler.adapter = RecordingAdapter(
                    items = recordings,
                    onClick = ::launchRecording,
                )

                recycler.post {
                    recycler.requestFocus()
                    recycler.findViewHolderForAdapterPosition(0)
                        ?.itemView
                        ?.requestFocus()
                }
            } catch (e: Exception) {
                loading.visibility = View.GONE
                error.visibility = View.VISIBLE
                error.text = "Could not load recordings:\n${e.message}"
            }
        }
    }

    private fun launchRecording(recording: Recording) {
        viewLifecycleOwner.lifecycleScope.launch {
            val result = PlaybackLauncher.createRecordingIntent(
                context = requireContext(),
                recording = recording,
            )

            result.onSuccess { intent ->
                error.visibility = View.GONE
                startActivity(intent)
            }

            result.onFailure { throwable ->
                error.visibility = View.VISIBLE
                error.text = "Unable to play recording:\n" +
                        (throwable.message ?: "Unknown error")
            }
        }
    }
}

class RecordingAdapter(
    private val items: List<Recording>,
    private val onClick: (Recording) -> Unit,
) : RecyclerView.Adapter<RecordingAdapter.VH>() {

    private val density: Float
        get() = currentDensity

    private var currentDensity = 1f

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val thumbnail: ImageView = view.findViewById(R.id.rec_thumbnail)
        val title: TextView = view.findViewById(R.id.rec_title)
        val episode: TextView = view.findViewById(R.id.rec_episode)
        val metadata: TextView = view.findViewById(R.id.rec_metadata)
        val time: TextView = view.findViewById(R.id.rec_time)
        val description: TextView = view.findViewById(R.id.rec_description)
        val status: TextView = view.findViewById(R.id.rec_status)
        val progress: ProgressBar = view.findViewById(R.id.rec_processing_progress)

        init {
            currentDensity = view.resources.displayMetrics.density
            view.isFocusable = true
            view.isFocusableInTouchMode = true
            view.background = cardBackground(false)

            view.setOnClickListener {
                val position = bindingAdapterPosition
                if (position != RecyclerView.NO_POSITION) {
                    onClick(items[position])
                }
            }

            view.setOnFocusChangeListener { focusedView, hasFocus ->
                focusedView.animate().cancel()
                focusedView.scaleX = 1f
                focusedView.scaleY = 1f
                focusedView.translationZ = 0f
                focusedView.background = cardBackground(hasFocus)
            }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_recording, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val recording = items[position]

        holder.title.text = recording.title
            ?.takeIf { it.isNotBlank() }
            ?: recording.filename
                ?.takeIf { it.isNotBlank() }
                    ?: "Recording"

        val episodeParts = mutableListOf<String>()
        recording.season?.trim()?.takeIf { it.isNotBlank() && it != "0" }?.let {
            episodeParts += "S${it.padStart(2, '0')}"
        }
        recording.episode?.trim()?.takeIf { it.isNotBlank() && it != "0" }?.let {
            episodeParts += "E${it.padStart(2, '0')}"
        }

        val episodeLabel = buildList {
            if (episodeParts.isNotEmpty()) add(episodeParts.joinToString(""))
            recording.episodeTitle?.takeIf { it.isNotBlank() }?.let(::add)
            if (isEmpty()) {
                recording.subtitle?.takeIf { it.isNotBlank() }?.let(::add)
            }
        }.joinToString("  •  ")

        holder.episode.text = episodeLabel
        holder.episode.visibility =
            if (episodeLabel.isBlank()) View.GONE else View.VISIBLE

        val metadataText = buildList {
            recording.category?.takeIf { it.isNotBlank() }?.let(::add)
            recording.channel?.takeIf { it.isNotBlank() }?.let { add("CH $it") }
            recording.originalAirDate?.takeIf { it.isNotBlank() }?.let {
                add("Aired $it")
            }
            formatDuration(recording)?.let(::add)
            formatFileSize(recording.sizeBytes)?.let(::add)
        }.joinToString("  •  ")

        holder.metadata.text = metadataText
        holder.metadata.visibility =
            if (metadataText.isBlank()) View.GONE else View.VISIBLE

        holder.time.text = TimeUtil.formatDisplay(recording.startTime)

        holder.description.text = recording.description
            ?.takeIf { it.isNotBlank() }
            ?: "No program description available."

        bindStatus(holder, recording)
        bindArtwork(holder, recording)
    }

    override fun onViewRecycled(holder: VH) {
        Glide.with(holder.itemView).clear(holder.thumbnail)
        holder.thumbnail.setImageDrawable(null)
        super.onViewRecycled(holder)
    }

    override fun getItemCount(): Int = items.size

    private fun bindStatus(holder: VH, recording: Recording) {
        val processing = recording.processingStatus
            ?.trim()
            ?.lowercase(Locale.US)
            .orEmpty()

        val statusText = when {
            processing == "ready" || recording.vodReady == 1 -> "READY"
            processing == "processing" -> {
                recording.processingStep
                    ?.takeIf { it.isNotBlank() }
                    ?.uppercase(Locale.US)
                    ?: "PROCESSING"
            }
            processing == "error" -> "ERROR"
            processing == "legacy" -> "RECORDED"
            !recording.status.isNullOrBlank() ->
                recording.status.uppercase(Locale.US)
            else -> "RECORDED"
        }

        holder.status.text = statusText
        holder.status.setTextColor(Color.WHITE)
        holder.status.background = statusBackground(processing)

        val showProgress =
            processing == "processing" &&
                    recording.processingPercent in 1..99

        holder.progress.visibility =
            if (showProgress) View.VISIBLE else View.GONE

        if (showProgress) {
            holder.progress.progress = recording.processingPercent
        }
    }

    private fun bindArtwork(holder: VH, recording: Recording) {
        val baseUrl = ApiClient
            .getBaseUrl(holder.itemView.context)
            .trimEnd('/')

        val artworkUrl = when {
            !recording.artwork.isNullOrBlank() ->
                absoluteUrl(baseUrl, recording.artwork)

            !recording.thumbnail.isNullOrBlank() ->
                absoluteThumbnailUrl(baseUrl, recording.thumbnail)

            !recording.programId.isNullOrBlank() ->
                "$baseUrl/api/program-artwork/${recording.programId}"

            else -> null
        }

        Glide.with(holder.itemView)
            .clear(holder.thumbnail)

        if (artworkUrl == null) {
            holder.thumbnail.setImageResource(android.R.color.black)
            return
        }

        Glide.with(holder.itemView)
            .load(artworkUrl)
            .placeholder(android.R.color.black)
            .error(android.R.color.black)
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .centerCrop()
            .into(holder.thumbnail)
    }

    private fun absoluteUrl(baseUrl: String, value: String): String {
        return when {
            value.startsWith("http://") || value.startsWith("https://") -> value
            value.startsWith("/") -> baseUrl + value
            else -> "$baseUrl/$value"
        }
    }

    private fun absoluteThumbnailUrl(baseUrl: String, value: String): String {
        return when {
            value.startsWith("http://") || value.startsWith("https://") -> value
            value.startsWith("/") -> baseUrl + value
            else -> "$baseUrl/thumbs/$value"
        }
    }

    private fun formatDuration(recording: Recording): String? {
        val start = recording.startTime ?: return null
        val end = recording.endTime ?: return null

        return runCatching {
            val formatter = DateTimeFormatter.ISO_LOCAL_DATE_TIME
            val minutes = Duration.between(
                LocalDateTime.parse(start, formatter),
                LocalDateTime.parse(end, formatter),
            ).toMinutes().coerceAtLeast(1)

            when {
                minutes < 60 -> "$minutes min"
                minutes % 60L == 0L -> "${minutes / 60} hr"
                else -> "${minutes / 60} hr ${minutes % 60} min"
            }
        }.getOrNull()
    }

    private fun formatFileSize(bytes: Long): String? {
        if (bytes <= 0L) return null

        val mib = bytes / (1024.0 * 1024.0)
        return if (mib >= 1024.0) {
            String.format(Locale.US, "%.1f GB", mib / 1024.0)
        } else {
            String.format(Locale.US, "%.0f MB", mib)
        }
    }

    private fun cardBackground(focused: Boolean): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = 0f
            setColor(
                Color.parseColor(
                    if (focused) "#303342" else "#181A24"
                )
            )
            setStroke(
                dp(1),
                Color.parseColor(
                    if (focused) "#50566C" else "#292C38"
                )
            )
        }
    }

    private fun statusBackground(processing: String): GradientDrawable {
        val color = when (processing) {
            "ready" -> "#315B3C"
            "processing" -> "#70541F"
            "error" -> "#722A32"
            else -> "#3A3D49"
        }

        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = 0f
            setColor(Color.parseColor(color))
        }
    }

    private fun dp(value: Int): Int =
        (value * density).toInt().coerceAtLeast(1)
}