package com.signaldvr.app.ui.recordings

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Recording
import com.signaldvr.app.ui.player.PlaybackLauncher
import com.signaldvr.app.util.TimeUtil
import kotlinx.coroutines.launch

class RecordingsFragment : Fragment() {

    private lateinit var recycler: RecyclerView
    private lateinit var loading: View
    private lateinit var error: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        return inflater.inflate(
            R.layout.fragment_recordings,
            container,
            false,
        )
    }

    override fun onViewCreated(
        view: View,
        savedInstanceState: Bundle?,
    ) {
        recycler = view.findViewById(R.id.recordings_recycler)
        loading = view.findViewById(R.id.loading)
        error = view.findViewById(R.id.error_text)

        recycler.layoutManager =
            LinearLayoutManager(requireContext())

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
                    onClick = { recording ->
                        launchRecording(recording)
                    },
                )

                recycler.requestFocus()

            } catch (e: Exception) {
                loading.visibility = View.GONE
                error.visibility = View.VISIBLE
                error.text =
                    "Could not load recordings:\n${e.message}"
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
                error.text =
                    "Unable to play recording:\n" +
                            (throwable.message ?: "Unknown error")
            }
        }
    }
}

class RecordingAdapter(
    private val items: List<Recording>,
    private val onClick: (Recording) -> Unit,
) : RecyclerView.Adapter<RecordingAdapter.VH>() {

    inner class VH(
        view: View,
    ) : RecyclerView.ViewHolder(view) {

        val title: TextView =
            view.findViewById(R.id.rec_title)

        val subtitle: TextView =
            view.findViewById(R.id.rec_subtitle)

        val channel: TextView =
            view.findViewById(R.id.rec_channel)

        val time: TextView =
            view.findViewById(R.id.rec_time)

        val thumbnail: ImageView =
            view.findViewById(R.id.rec_thumbnail)

        init {
            view.isFocusable = true

            view.setOnClickListener {
                val position = bindingAdapterPosition

                if (position != RecyclerView.NO_POSITION) {
                    onClick(items[position])
                }
            }

            view.setOnFocusChangeListener { focusedView, hasFocus ->
                focusedView.setBackgroundResource(
                    if (hasFocus) {
                        R.drawable.bg_channel_focused
                    } else {
                        R.drawable.bg_channel_normal
                    }
                )
            }
        }
    }

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int,
    ): VH {
        val view = LayoutInflater
            .from(parent.context)
            .inflate(
                R.layout.item_recording,
                parent,
                false,
            )

        return VH(view)
    }

    override fun onBindViewHolder(
        holder: VH,
        position: Int,
    ) {
        val recording = items[position]

        holder.title.text =
            recording.title
                ?: recording.filename
                        ?: "Recording"

        holder.subtitle.text =
            recording.subtitle
                ?: recording.description
                        ?: ""

        holder.channel.text =
            recording.channel ?: ""

        holder.time.text =
            TimeUtil.formatDisplay(recording.startTime)

        val baseUrl =
            ApiClient.getBaseUrl(holder.itemView.context)

        val thumbnailUrl =
            if (!recording.thumbnail.isNullOrBlank()) {
                "$baseUrl/thumbs/${recording.thumbnail}"
            } else {
                null
            }

        if (thumbnailUrl != null) {
            Glide.with(holder.itemView.context)
                .load(thumbnailUrl)
                .placeholder(android.R.color.black)
                .centerCrop()
                .into(holder.thumbnail)
        } else {
            holder.thumbnail.setImageResource(
                android.R.color.black
            )
        }
    }

    override fun getItemCount(): Int = items.size
}