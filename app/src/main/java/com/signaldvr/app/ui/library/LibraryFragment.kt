package com.signaldvr.app.ui.library

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.LibraryProgram
import com.signaldvr.app.ui.player.PlayerActivity
import kotlinx.coroutines.launch

class LibraryFragment : Fragment() {

    private lateinit var rowsRecycler: RecyclerView
    private lateinit var loading: View
    private lateinit var errorText: TextView
    private lateinit var summaryText: TextView

    private var adapter: LibrarySectionAdapter? = null
    private var firstLoad = true

    /*
     * Android TV focus restoration.
     *
     * Keep the identity of the last focused card so returning from playback
     * and the five-second library refresh do not send focus to the far-left
     * card.
     */
    private var lastFocusedSectionKind: String? = null
    private var lastFocusedProgramId: Int? = null
    private var lastFocusedFilename: String? = null

    private val handler = Handler(Looper.getMainLooper())

    private val refreshRunnable = object : Runnable {
        override fun run() {
            loadLibrary(showLoading = false)
            handler.postDelayed(this, 5000)
        }
    }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, b: Bundle?): View {
        return inflater.inflate(R.layout.fragment_library, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        rowsRecycler = view.findViewById(R.id.library_rows_recycler)
        loading = view.findViewById(R.id.loading)
        errorText = view.findViewById(R.id.error_text)
        summaryText = view.findViewById(R.id.library_summary)

        rowsRecycler.layoutManager = LinearLayoutManager(requireContext())

        adapter = LibrarySectionAdapter(
            onWatch = { program -> watchProgram(program) },
            onSave = { program -> saveProgram(program) },
            onOptions = { program -> showProgramOptions(program) },
            onProgramFocused = { sectionKind, program ->
                lastFocusedSectionKind = sectionKind
                lastFocusedProgramId = program.id
                lastFocusedFilename = program.filename
            }
        )

        rowsRecycler.adapter = adapter
        loadLibrary(showLoading = true)
    }

    override fun onResume() {
        super.onResume()

        /*
         * Restore focus after PlayerActivity closes. The adapter also restores
         * focus after each background refresh.
         */
        rowsRecycler.post {
            adapter?.restoreFocus(
                sectionKind = lastFocusedSectionKind,
                programId = lastFocusedProgramId,
                filename = lastFocusedFilename
            )
        }

        handler.removeCallbacks(refreshRunnable)
        handler.postDelayed(refreshRunnable, 5000)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refreshRunnable)
    }

    private fun loadLibrary(showLoading: Boolean) {
        if (showLoading) {
            loading.visibility = View.VISIBLE
            errorText.visibility = View.GONE
        }

        lifecycleScope.launch {
            try {
                val data = ApiClient.getApi(requireContext()).getLibrary()

                loading.visibility = View.GONE
                errorText.visibility = View.GONE

                val live = data.live.orEmpty()
                val buffered = data.buffered.orEmpty()
                val saved = data.saved.orEmpty()
                val recorded = data.recorded.orEmpty()

                summaryText.text =
                    "Live ${live.size}  ·  Buffered ${buffered.size}  ·  Saved ${saved.size}  ·  Recorded ${recorded.size}"

                val sections = listOf(
                    LibrarySection("🔴 LIVE NOW", "live", live),
                    LibrarySection("🟡 BUFFERED", "buffered", buffered),
                    LibrarySection("🟢 SAVED", "saved", saved),
                    LibrarySection("🔵 RECORDED", "recorded", recorded)
                )

                adapter?.submitSections(sections)

                rowsRecycler.post {
                    adapter?.restoreFocus(
                        sectionKind = lastFocusedSectionKind,
                        programId = lastFocusedProgramId,
                        filename = lastFocusedFilename
                    )
                }

                if (firstLoad) {
                    firstLoad = false
                    rowsRecycler.post {
                        rowsRecycler.requestFocus()
                    }
                }

            } catch (e: Exception) {
                loading.visibility = View.GONE
                errorText.visibility = View.VISIBLE
                errorText.text =
                    "Could not load DVR Library:\n${e.message}\n\nCheck Settings → Server URL"
            }
        }
    }

    private fun watchProgram(program: LibraryProgram) {
        lifecycleScope.launch {
            try {
                val isLive =
                    program.type.equals("live", ignoreCase = true)

                val isRecording =
                    program.type.equals("recording", ignoreCase = true)

                /*
                 * Live Now must bypass program-playlist and recording-resume
                 * APIs. Launch the same live playback path used by the guide.
                 */
                if (isLive) {
                    val channel = program.channel.orEmpty()

                    if (channel.isBlank()) {
                        Toast.makeText(
                            requireContext(),
                            "Live channel is missing",
                            Toast.LENGTH_LONG
                        ).show()
                        return@launch
                    }

                    val intent = Intent(
                        requireContext(),
                        PlayerActivity::class.java
                    ).apply {
                        putExtra(
                            PlayerActivity.EXTRA_TITLE,
                            program.title ?: "Live TV"
                        )
                        putExtra(
                            PlayerActivity.EXTRA_CHANNEL_NUM,
                            channel
                        )
                        putExtra(
                            PlayerActivity.EXTRA_CHANNEL_NAME,
                            program.channelName
                                ?: program.guideName
                                ?: channel
                        )
                        putExtra(
                            PlayerActivity.EXTRA_IS_LIVE,
                            true
                        )
                        putExtra(
                            PlayerActivity.EXTRA_RECORDING_ID,
                            -1
                        )

                        /*
                         * Do not set EXTRA_PLAY_URL for live playback.
                         */
                    }

                    startActivity(intent)
                    return@launch
                }

                val api = ApiClient.getApi(requireContext())
                val id = program.id
                val filename = program.filename

                val url: String = when {
                    isRecording && id != null -> {
                        Toast.makeText(
                            requireContext(),
                            "Preparing recording...",
                            Toast.LENGTH_SHORT
                        ).show()

                        val vod = api.getRecordingVod(
                            recordingId = id,
                            profile = "media3"
                        )

                        if (
                            !vod.ok ||
                            vod.playlistUrl.isNullOrBlank()
                        ) {
                            Toast.makeText(
                                requireContext(),
                                vod.error
                                    ?: "Unable to prepare recording",
                                Toast.LENGTH_LONG
                            ).show()
                            return@launch
                        }

                        vod.playlistUrl
                    }

                    id != null -> {
                        val playlist =
                            api.getProgramPlaylist(id)

                        if (
                            playlist.ok != true ||
                            playlist.playlistUrl.isNullOrBlank()
                        ) {
                            Toast.makeText(
                                requireContext(),
                                playlist.error
                                    ?: "Unable to play program",
                                Toast.LENGTH_LONG
                            ).show()
                            return@launch
                        }

                        playlist.playlistUrl
                    }

                    !filename.isNullOrBlank() -> {
                        val baseUrl =
                            ApiClient
                                .getBaseUrl(requireContext())
                                .trimEnd('/')

                        "$baseUrl/play/$filename"
                    }

                    else -> {
                        Toast.makeText(
                            requireContext(),
                            "No playable media found",
                            Toast.LENGTH_LONG
                        ).show()
                        return@launch
                    }
                }

                val intent = Intent(
                    requireContext(),
                    PlayerActivity::class.java
                ).apply {
                    putExtra(
                        PlayerActivity.EXTRA_PLAY_URL,
                        url
                    )
                    putExtra(
                        PlayerActivity.EXTRA_TITLE,
                        program.title ?: "DVR Library"
                    )
                    putExtra(
                        PlayerActivity.EXTRA_CHANNEL_NUM,
                        program.channel ?: ""
                    )
                    putExtra(
                        PlayerActivity.EXTRA_CHANNEL_NAME,
                        program.channelName
                            ?: program.guideName
                            ?: program.channel
                            ?: ""
                    )
                    putExtra(
                        PlayerActivity.EXTRA_IS_LIVE,
                        false
                    )
                    putExtra(
                        PlayerActivity.EXTRA_RECORDING_ID,
                        if (isRecording) {
                            program.id ?: -1
                        } else {
                            -1
                        }
                    )
                }

                startActivity(intent)

            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Playback failed: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    private fun showProgramOptions(program: LibraryProgram) {
        val title = program.title ?: "Program"
        val actions = mutableListOf<String>()

        actions.add("▶ Watch")

        if (program.saved != true && program.id != null && program.type != "recording") {
            actions.add("💾 Save")
        }

        actions.add("🗑 Delete")

        AlertDialog.Builder(requireContext())
            .setTitle(title)
            .setItems(actions.toTypedArray()) { _, which ->
                when (actions[which]) {
                    "▶ Watch" -> watchProgram(program)
                    "💾 Save" -> saveProgram(program)
                    "🗑 Delete" -> confirmDelete(program)
                }
            }
            .show()
    }

    private fun confirmDelete(program: LibraryProgram) {
        AlertDialog.Builder(requireContext())
            .setTitle("Delete?")
            .setMessage(program.title ?: "Delete this item?")
            .setPositiveButton("Delete") { _, _ -> deleteProgram(program) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun deleteProgram(program: LibraryProgram) {
        val id = program.id ?: return

        lifecycleScope.launch {
            try {
                val result = if (program.type == "recording") {
                    ApiClient.getApi(requireContext()).deleteRecording(id)
                } else {
                    ApiClient.getApi(requireContext()).deleteProgram(id)
                }

                if (result.ok == true) {
                    Toast.makeText(requireContext(), "Deleted", Toast.LENGTH_SHORT).show()
                    loadLibrary(showLoading = false)
                } else {
                    Toast.makeText(
                        requireContext(),
                        result.error ?: "Delete failed",
                        Toast.LENGTH_LONG
                    ).show()
                }

            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Delete failed: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    private fun saveProgram(program: LibraryProgram) {
        val id = program.id ?: return

        lifecycleScope.launch {
            try {
                val result = ApiClient.getApi(requireContext()).saveProgram(id)

                if (result.ok == true) {
                    Toast.makeText(requireContext(), "Saved", Toast.LENGTH_SHORT).show()
                    loadLibrary(showLoading = false)
                } else {
                    Toast.makeText(
                        requireContext(),
                        result.error ?: "Save failed",
                        Toast.LENGTH_LONG
                    ).show()
                }

            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Save failed: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }
}

data class LibrarySection(
    val title: String,
    val kind: String,
    val programs: List<LibraryProgram>
)

class LibrarySectionAdapter(
    private val onWatch: (LibraryProgram) -> Unit,
    private val onSave: (LibraryProgram) -> Unit,
    private val onOptions: (LibraryProgram) -> Unit,
    private val onProgramFocused: (String, LibraryProgram) -> Unit,
) : RecyclerView.Adapter<LibrarySectionAdapter.SectionVH>() {

    private val sections = mutableListOf<LibrarySection>()

    /*
     * Keep references only to currently bound horizontal rows. This lets the
     * fragment restore focus to the same card after playback or a refresh.
     */
    private val boundRows = mutableMapOf<String, RecyclerView>()

    init {
        setHasStableIds(true)
    }

    override fun getItemId(position: Int): Long {
        return sections[position].kind.hashCode().toLong()
    }

    fun submitSections(newSections: List<LibrarySection>) {
        val oldSections = sections.toList()

        val diff = DiffUtil.calculateDiff(
            object : DiffUtil.Callback() {
                override fun getOldListSize(): Int = oldSections.size
                override fun getNewListSize(): Int = newSections.size

                override fun areItemsTheSame(
                    oldItemPosition: Int,
                    newItemPosition: Int
                ): Boolean {
                    return oldSections[oldItemPosition].kind ==
                            newSections[newItemPosition].kind
                }

                override fun areContentsTheSame(
                    oldItemPosition: Int,
                    newItemPosition: Int
                ): Boolean {
                    return oldSections[oldItemPosition] ==
                            newSections[newItemPosition]
                }
            }
        )

        sections.clear()
        sections.addAll(newSections)
        diff.dispatchUpdatesTo(this)
    }

    fun restoreFocus(
        sectionKind: String?,
        programId: Int?,
        filename: String?
    ): Boolean {
        if (sectionKind.isNullOrBlank()) {
            return false
        }

        val row = boundRows[sectionKind] ?: return false
        val programAdapter =
            row.adapter as? LibraryProgramAdapter ?: return false

        return programAdapter.restoreFocus(
            recyclerView = row,
            programId = programId,
            filename = filename
        )
    }

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int
    ): SectionVH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_library_section, parent, false)

        return SectionVH(view)
    }

    override fun onBindViewHolder(
        holder: SectionVH,
        position: Int
    ) {
        holder.bind(sections[position])
    }

    override fun onViewRecycled(holder: SectionVH) {
        holder.boundKind?.let { kind ->
            if (boundRows[kind] === holder.recyclerView) {
                boundRows.remove(kind)
            }
        }

        super.onViewRecycled(holder)
    }

    override fun getItemCount(): Int = sections.size

    inner class SectionVH(
        view: View
    ) : RecyclerView.ViewHolder(view) {

        private val title: TextView =
            view.findViewById(R.id.library_section_title)

        private val empty: TextView =
            view.findViewById(R.id.library_section_empty)

        val recyclerView: RecyclerView =
            view.findViewById(R.id.library_section_recycler)

        var boundKind: String? = null
            private set

        init {
            recyclerView.layoutManager = LinearLayoutManager(
                view.context,
                LinearLayoutManager.HORIZONTAL,
                false
            )

            /*
             * The child cards, not the row container, should own focus.
             */
            recyclerView.isFocusable = false
            recyclerView.isFocusableInTouchMode = false
            recyclerView.descendantFocusability =
                ViewGroup.FOCUS_AFTER_DESCENDANTS

            recyclerView.itemAnimator = null
            recyclerView.setHasFixedSize(true)
        }

        fun bind(section: LibrarySection) {
            boundKind?.let { oldKind ->
                if (oldKind != section.kind &&
                    boundRows[oldKind] === recyclerView
                ) {
                    boundRows.remove(oldKind)
                }
            }

            boundKind = section.kind
            boundRows[section.kind] = recyclerView
            title.text = section.title

            if (section.programs.isEmpty()) {
                empty.visibility = View.VISIBLE
                recyclerView.visibility = View.GONE
                empty.text = "Nothing here yet."
                return
            }

            empty.visibility = View.GONE
            recyclerView.visibility = View.VISIBLE

            val existing =
                recyclerView.adapter as? LibraryProgramAdapter

            if (existing == null) {
                recyclerView.adapter = LibraryProgramAdapter(
                    kind = section.kind,
                    onWatch = onWatch,
                    onSave = onSave,
                    onOptions = onOptions,
                    onFocused = { program ->
                        onProgramFocused(section.kind, program)
                    }
                ).also { childAdapter ->
                    childAdapter.submitPrograms(section.programs)
                }
            } else {
                existing.kind = section.kind
                existing.onFocused = { program ->
                    onProgramFocused(section.kind, program)
                }
                existing.submitPrograms(section.programs)
            }
        }
    }
}

class LibraryProgramAdapter(
    var kind: String,
    private val onWatch: (LibraryProgram) -> Unit,
    private val onSave: (LibraryProgram) -> Unit,
    private val onOptions: (LibraryProgram) -> Unit,
    var onFocused: (LibraryProgram) -> Unit,
) : RecyclerView.Adapter<LibraryProgramAdapter.ProgramVH>() {

    private val programs = mutableListOf<LibraryProgram>()

    init {
        setHasStableIds(true)
    }

    override fun getItemId(position: Int): Long {
        return stableKey(programs[position]).hashCode().toLong()
    }

    private fun stableKey(program: LibraryProgram): String {
        return buildString {
            append(program.type.orEmpty())
            append('|')
            append(program.id ?: -1)
            append('|')
            append(program.filename.orEmpty())
            append('|')
            append(program.channel.orEmpty())
            append('|')
            append(program.startTime.orEmpty())
        }
    }

    fun submitPrograms(newPrograms: List<LibraryProgram>) {
        val oldPrograms = programs.toList()

        val diff = DiffUtil.calculateDiff(
            object : DiffUtil.Callback() {
                override fun getOldListSize(): Int = oldPrograms.size
                override fun getNewListSize(): Int = newPrograms.size

                override fun areItemsTheSame(
                    oldItemPosition: Int,
                    newItemPosition: Int
                ): Boolean {
                    return stableKey(oldPrograms[oldItemPosition]) ==
                            stableKey(newPrograms[newItemPosition])
                }

                override fun areContentsTheSame(
                    oldItemPosition: Int,
                    newItemPosition: Int
                ): Boolean {
                    return oldPrograms[oldItemPosition] ==
                            newPrograms[newItemPosition]
                }
            }
        )

        programs.clear()
        programs.addAll(newPrograms)
        diff.dispatchUpdatesTo(this)
    }

    fun restoreFocus(
        recyclerView: RecyclerView,
        programId: Int?,
        filename: String?
    ): Boolean {
        val position = programs.indexOfFirst { program ->
            when {
                programId != null && programId > 0 ->
                    program.id == programId

                !filename.isNullOrBlank() ->
                    program.filename == filename

                else -> false
            }
        }

        if (position == RecyclerView.NO_POSITION || position < 0) {
            return false
        }

        val layoutManager =
            recyclerView.layoutManager as? LinearLayoutManager
                ?: return false

        layoutManager.scrollToPositionWithOffset(position, 0)

        recyclerView.post {
            val holder =
                recyclerView.findViewHolderForAdapterPosition(position)

            if (holder != null) {
                holder.itemView.requestFocus()
            } else {
                recyclerView.post {
                    recyclerView
                        .findViewHolderForAdapterPosition(position)
                        ?.itemView
                        ?.requestFocus()
                }
            }
        }

        return true
    }

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int
    ): ProgramVH {
        val view = LayoutInflater.from(parent.context)
            .inflate(
                R.layout.item_library_program_card,
                parent,
                false
            )

        return ProgramVH(view)
    }

    override fun onBindViewHolder(
        holder: ProgramVH,
        position: Int
    ) {
        holder.bind(programs[position], kind)
    }

    override fun getItemCount(): Int = programs.size

    inner class ProgramVH(
        view: View
    ) : RecyclerView.ViewHolder(view) {

        private val icon: TextView =
            view.findViewById(R.id.library_item_icon)

        private val badge: TextView =
            view.findViewById(R.id.library_item_badge)

        private val title: TextView =
            view.findViewById(R.id.library_item_title)

        private val meta: TextView =
            view.findViewById(R.id.library_item_meta)

        private val description: TextView =
            view.findViewById(R.id.library_item_description)

        private val hint: TextView =
            view.findViewById(R.id.library_item_hint)

        init {
            view.isFocusable = true
            view.isFocusableInTouchMode = true

            view.setOnClickListener {
                val position = bindingAdapterPosition

                if (position != RecyclerView.NO_POSITION) {
                    onFocused(programs[position])
                    onWatch(programs[position])
                }
            }

            view.setOnLongClickListener {
                val position = bindingAdapterPosition

                if (position != RecyclerView.NO_POSITION) {
                    onFocused(programs[position])
                    onOptions(programs[position])
                }

                true
            }

            view.setOnFocusChangeListener { focusedView, hasFocus ->
                focusedView.setBackgroundResource(
                    if (hasFocus) {
                        R.drawable.bg_channel_focused
                    } else {
                        R.drawable.bg_channel_normal
                    }
                )

                if (hasFocus) {
                    val position = bindingAdapterPosition

                    if (position != RecyclerView.NO_POSITION) {
                        onFocused(programs[position])
                    }
                }
            }
        }

        fun bind(
            program: LibraryProgram,
            kind: String
        ) {
            icon.text = iconFor(program)

            badge.text = when {
                program.isNew -> "NEW"
                program.isRepeat -> "REPEAT"
                else -> kind.uppercase()
            }

            title.text = program.title ?: "Unknown Program"

            val channel =
                program.channelName
                    ?: program.guideName
                    ?: program.channel
                    ?: ""

            val time =
                buildTimeLabel(
                    program.startTime,
                    program.stopTime
                )

            val metadata = mutableListOf<String>()

            if (channel.isNotBlank()) {
                metadata += channel
            }

            if (time.isNotBlank()) {
                metadata += time
            }

            if (
                program.season > 0 &&
                !program.episode.isNullOrBlank()
            ) {
                metadata += "S${program.season}E${program.episode}"
            } else if (!program.episode.isNullOrBlank()) {
                metadata += "Episode ${program.episode}"
            }

            if (!program.episodeTitle.isNullOrBlank()) {
                metadata += program.episodeTitle!!
            }

            if (program.isNew) {
                metadata += "⭐ NEW"
            } else if (program.isRepeat) {
                metadata += "🔁 Repeat"
            }

            if (!program.showType.isNullOrBlank()) {
                metadata += program.showType!!
            }

            if (program.year > 0) {
                metadata += program.year.toString()
            }

            if (!program.rating.isNullOrBlank()) {
                metadata += program.rating!!
            }

            if (!program.genres.isNullOrBlank()) {
                metadata += program.genres!!
            }

            if (!program.originalAirDate.isNullOrBlank()) {
                metadata += "Aired ${program.originalAirDate}"
            }

            if (program.runtime > 0) {
                val minutes = program.runtime / 60
                metadata += if (minutes > 0) {
                    "${minutes} min"
                } else {
                    "${program.runtime} sec"
                }
            }

            if (program.segmentCount > 0) {
                metadata += "${program.segmentCount} segments"
            }

            meta.text = metadata.joinToString("  •  ")

            description.text =
                when {
                    !program.description.isNullOrBlank() ->
                        program.description!!

                    !program.subtitle.isNullOrBlank() ->
                        program.subtitle!!

                    else ->
                        ""
                }

            hint.text = "OK: Watch\nHold OK: Options"
        }

        private fun iconFor(
            program: LibraryProgram
        ): String {
            val category =
                (program.category ?: "").lowercase()

            val programTitle =
                (program.title ?: "").lowercase()

            return when {
                "baseball" in category ||
                        "baseball" in programTitle -> "⚾"

                "sports" in category -> "🏟️"

                "news" in category ||
                        "news" in programTitle -> "📰"

                "movie" in category ||
                        "movie" in programTitle -> "🎬"

                "music" in category -> "🎵"
                "weather" in category -> "🌦️"
                program.type == "recording" -> "📼"
                else -> "📺"
            }
        }

        private fun buildTimeLabel(
            start: String?,
            stop: String?
        ): String {
            val startLabel = formatTime(start)
            val stopLabel = formatTime(stop)

            return when {
                startLabel.isNotBlank() &&
                        stopLabel.isNotBlank() ->
                    "$startLabel → $stopLabel"

                startLabel.isNotBlank() ->
                    startLabel

                else -> ""
            }
        }

        private fun formatTime(
            value: String?
        ): String {
            val raw = value?.take(14) ?: return ""

            if (raw.length < 12) {
                return ""
            }

            return try {
                val hour =
                    raw.substring(8, 10).toInt()

                val minute =
                    raw.substring(10, 12)

                val amPm =
                    if (hour >= 12) "PM" else "AM"

                val twelveHour = when {
                    hour == 0 -> 12
                    hour > 12 -> hour - 12
                    else -> hour
                }

                "$twelveHour:$minute $amPm"
            } catch (_: Exception) {
                ""
            }
        }
    }
}