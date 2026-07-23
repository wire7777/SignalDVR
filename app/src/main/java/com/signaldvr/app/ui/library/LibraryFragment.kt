package com.signaldvr.app.ui.library

import android.animation.ObjectAnimator
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import android.view.animation.AccelerateDecelerateInterpolator
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import androidx.annotation.DrawableRes
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.BackgroundDvrProgram
import com.signaldvr.app.api.LibraryProgram
import com.signaldvr.app.ui.player.PlayerActivity
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class LibraryFragment : Fragment() {

    private lateinit var rowsRecycler: RecyclerView
    private lateinit var loading: View
    private lateinit var errorText: TextView
    private lateinit var summaryText: TextView

    private var adapter: LibrarySectionAdapter? = null
    private var firstLoad = true

    /*
     * Only one Library API request may run at a time. A queued refresh runs
     * immediately after the active request finishes, preventing an older
     * response from overwriting newer Library data.
     */
    private var loadJob: Job? = null
    private var refreshQueued = false

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
            /*
             * Never mutate the nested RecyclerViews while the user is
             * navigating the library. Even a correct DiffUtil update can make
             * Android TV's focus search re-anchor a horizontal row to its
             * start. Defer the refresh until focus is outside the library.
             */
            if (!rowsRecycler.hasFocus()) {
                loadLibrary(
                    showLoading = false,
                    restoreFocusAfterLoad = false
                )
            }

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
    }

    override fun onResume() {
        super.onResume()

        /*
         * Refresh immediately whenever the Library becomes visible. This is
         * especially important after returning from playback because a
         * recording may have completed or changed processing state while
         * PlayerActivity was open.
         *
         * Focus is restored only after the fresh data has been submitted,
         * avoiding a race between focus restoration and RecyclerView layout.
         */
        loadLibrary(
            showLoading = firstLoad,
            restoreFocusAfterLoad = !firstLoad
        )

        handler.removeCallbacks(refreshRunnable)
        handler.postDelayed(refreshRunnable, 5000)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refreshRunnable)

        /*
         * Do not allow a response started for an invisible Library screen to
         * arrive later and replace fresher data after the user returns.
         */
        loadJob?.cancel()
        loadJob = null
        refreshQueued = false
    }

    private fun loadLibrary(
        showLoading: Boolean,
        restoreFocusAfterLoad: Boolean = false
    ) {
        if (loadJob?.isActive == true) {
            refreshQueued = true
            return
        }
        if (showLoading) {
            loading.visibility = View.VISIBLE
            errorText.visibility = View.GONE
        }

        loadJob = viewLifecycleOwner.lifecycleScope.launch {
            try {
                val api = ApiClient.getApi(requireContext())
                val data = api.getLibrary()

                // Background DVR status is supplemental. A temporary status
                // failure must not prevent the rest of the Library loading.
                val activeProgram = try {
                    api.getBackgroundDvrStatus()
                        .activeProgram
                        ?.takeIf { it.status.equals("active", ignoreCase = true) }
                        ?.toLibraryProgram()
                } catch (_: Exception) {
                    null
                }

                loading.visibility = View.GONE
                errorText.visibility = View.GONE

                val continueWatching = data.continueWatching.orEmpty()
                val live = data.live.orEmpty()
                val buffered = data.buffered.orEmpty()
                val saved = data.saved.orEmpty()
                val recorded = data.recorded.orEmpty()

                summaryText.text =
                    "Continue ${continueWatching.size}  ·  Recording ${if (activeProgram != null) 1 else 0}  ·  Background DVR ${buffered.size}  ·  Saved ${saved.size}  ·  Recorded ${recorded.size}"

                val sections = buildList {
                    if (continueWatching.isNotEmpty()) {
                        add(
                            LibrarySection(
                                "CONTINUE WATCHING",
                                "continue_watching",
                                R.drawable.ic_home_library,
                                continueWatching
                            )
                        )
                    }

                    if (activeProgram != null) {
                        add(
                            LibrarySection(
                                "CURRENTLY RECORDING",
                                "currently_recording",
                                R.drawable.ic_library_buffered,
                                listOf(activeProgram)
                            )
                        )
                    }

                    add(LibrarySection("BACKGROUND DVR", "buffered", R.drawable.ic_library_buffered, buffered))
                    add(LibrarySection("LIVE NOW", "live", R.drawable.ic_home_live_tv, live))
                    add(LibrarySection("SAVED", "saved", R.drawable.ic_library_saved, saved))
                    add(LibrarySection("RECORDED", "recorded", R.drawable.ic_home_recordings, recorded))
                }

                adapter?.submitSections(sections)

                when {
                    firstLoad -> {
                        firstLoad = false
                        rowsRecycler.post {
                            rowsRecycler.requestFocus()
                        }
                    }

                    restoreFocusAfterLoad -> {
                        rowsRecycler.post {
                            adapter?.restoreFocus(
                                sectionKind = lastFocusedSectionKind,
                                programId = lastFocusedProgramId,
                                filename = lastFocusedFilename,
                                alignToStart = false
                            )
                        }
                    }
                }

            } catch (e: Exception) {
                loading.visibility = View.GONE
                errorText.visibility = View.VISIBLE
                errorText.text =
                    "Could not load DVR Library:\n${e.message}\n\nCheck Settings → Server URL"
            } finally {
                loadJob = null

                if (refreshQueued && isResumed) {
                    refreshQueued = false
                    rowsRecycler.post {
                        loadLibrary(
                            showLoading = false,
                            restoreFocusAfterLoad = false
                        )
                    }
                }
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
                 * The Library status is informational and does not alter the
                 * playback engine. While a newly completed recording is still
                 * being prepared, avoid starting the existing on-demand VOD
                 * request a second time. Legacy recordings continue through
                 * the original playback fallback below.
                 */
                if (isRecording) {
                    when (program.processingStatus?.lowercase()) {
                        "pending", "processing" -> {
                            val step = program.processingStep
                                ?.takeIf { it.isNotBlank() }
                                ?: "Preparing playback"
                            val percent = program.processingPercent
                                .coerceIn(0, 100)

                            Toast.makeText(
                                requireContext(),
                                if (percent > 0) {
                                    "$step — $percent%"
                                } else {
                                    step
                                },
                                Toast.LENGTH_SHORT
                            ).show()
                            return@launch
                        }

                        "failed" -> {
                            Toast.makeText(
                                requireContext(),
                                program.processingError
                                    ?.takeIf { it.isNotBlank() }
                                    ?: "Recording preparation failed",
                                Toast.LENGTH_LONG
                            ).show()
                            return@launch
                        }
                    }
                }

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
                    putExtra(
                        PlayerActivity.EXTRA_RESUME_URL,
                        program.resumeUrl
                            ?.takeIf {
                                program.canResume &&
                                        it.isNotBlank()
                            }
                            ?: ""
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
        val isActive = program.status.equals("active", ignoreCase = true)
        val actions = mutableListOf<String>()

        actions.add(if (isActive) "Watch from Beginning" else "Watch")

        if (isActive && !program.channel.isNullOrBlank()) {
            actions.add("Join Live")
        }

        if (program.saved != true && program.id != null && program.type != "recording") {
            actions.add("Save")
        }

        actions.add("Details")
        actions.add(if (program.type == "recording") "Delete Recording" else "Delete")

        AlertDialog.Builder(requireContext())
            .setTitle(title)
            .setItems(actions.toTypedArray()) { _, which ->
                when (actions[which]) {
                    "Watch", "Watch from Beginning" -> watchProgram(program)
                    "Join Live" -> joinLive(program)
                    "Save" -> saveProgram(program)
                    "Details" -> showProgramDetails(program)
                    "Delete", "Delete Recording" -> confirmDelete(program)
                }
            }
            .show()
    }

    private fun joinLive(program: LibraryProgram) {
        val channel = program.channel.orEmpty()
        if (channel.isBlank()) {
            Toast.makeText(requireContext(), "Live channel is missing", Toast.LENGTH_LONG).show()
            return
        }

        val intent = Intent(requireContext(), PlayerActivity::class.java).apply {
            putExtra(PlayerActivity.EXTRA_CHANNEL_NUM, channel)
            putExtra(
                PlayerActivity.EXTRA_CHANNEL_NAME,
                program.channelName ?: program.guideName ?: channel
            )
            putExtra(PlayerActivity.EXTRA_TITLE, program.title ?: "Live TV")
            putExtra(PlayerActivity.EXTRA_IS_LIVE, true)
            putExtra(PlayerActivity.EXTRA_RECORDING_ID, -1)
            putExtra(PlayerActivity.EXTRA_RESUME_URL, "")
        }
        startActivity(intent)
    }

    private fun showProgramDetails(program: LibraryProgram) {
        val details = buildList {
            val channel = program.channelName ?: program.guideName ?: program.channel
            if (!channel.isNullOrBlank()) add(channel)

            if (!program.episodeTitle.isNullOrBlank()) add(program.episodeTitle!!)

            if (program.season > 0 && !program.episode.isNullOrBlank()) {
                add("Season ${program.season}, Episode ${program.episode}")
            } else if (!program.episode.isNullOrBlank()) {
                add("Episode ${program.episode}")
            }

            if (!program.genres.isNullOrBlank()) add(program.genres!!)
            if (!program.rating.isNullOrBlank()) add("Rating: ${program.rating}")
            if (!program.originalAirDate.isNullOrBlank()) {
                add("Original air date: ${program.originalAirDate}")
            }
            if (program.runtime > 0) add("Runtime: ${program.runtime / 60} min")
            if (program.segmentCount > 0) add("Segments: ${program.segmentCount}")

            if (!program.description.isNullOrBlank()) {
                add("")
                add(program.description!!)
            }
        }.joinToString("\n")

        AlertDialog.Builder(requireContext())
            .setTitle(program.title ?: "Program Details")
            .setMessage(details.ifBlank { "No additional information is available." })
            .setPositiveButton("Close", null)
            .show()
    }

    private fun confirmDelete(program: LibraryProgram) {
        val isRecording = program.type.equals("recording", ignoreCase = true)
        AlertDialog.Builder(requireContext())
            .setTitle(if (isRecording) "Delete recording?" else "Delete item?")
            .setMessage(
                if (isRecording) {
                    "Permanently delete ${program.title ?: "this recording"} and its video files?"
                } else {
                    program.title ?: "Delete this item?"
                }
            )
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

    private fun BackgroundDvrProgram.toLibraryProgram(): LibraryProgram {
        return LibraryProgram(
            id = id,
            title = title,
            subtitle = subtitle,
            description = description,
            category = category,
            channel = channel,
            channelName = guideName,
            guideName = guideName,
            type = "program",
            status = status,
            saved = saved != 0,
            playable = id != null && segmentCount > 0,
            autoExpire = autoExpire != 0,
            startTime = startTime,
            stopTime = stopTime,
            createdAt = createdAt,
            updatedAt = updatedAt,
            endedAt = endedAt,
            firstSegment = firstSegment,
            lastSegment = lastSegment,
            segmentCount = segmentCount,
            filePath = filePath,
            programId = programId,
            season = season,
            episode = episode,
            episodeTitle = episodeTitle,
            isNew = isNew != 0,
            originalAirDate = originalAirDate,
            showType = showType,
            entityType = entityType,
            genres = genres,
            rating = rating,
            runtime = runtime,
            year = year,
            language = language,
            videoProperties = videoProperties,
            audioProperties = audioProperties,
            artwork = artwork,
        )
    }
}

data class LibrarySection(
    val title: String,
    val kind: String,
    @DrawableRes val iconRes: Int,
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
        filename: String?,
        alignToStart: Boolean
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
            filename = filename,
            alignToStart = alignToStart
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

        private val sectionIcon: ImageView =
            view.findViewById(R.id.library_section_icon)

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
            sectionIcon.setImageResource(section.iconRes)
            sectionIcon.contentDescription = section.title
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
                    childAdapter.submitPrograms(
                        recyclerView = recyclerView,
                        newPrograms = section.programs
                    )
                }
            } else {
                existing.kind = section.kind
                existing.onFocused = { program ->
                    onProgramFocused(section.kind, program)
                }
                existing.submitPrograms(
                    recyclerView = recyclerView,
                    newPrograms = section.programs
                )
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

    fun submitPrograms(
        recyclerView: RecyclerView,
        newPrograms: List<LibraryProgram>
    ) {
        val oldPrograms = programs.toList()

        /*
         * A five-second API refresh can change metadata such as updated_at,
         * processing percentage, or resume state. DiffUtil then rebinds the
         * focused card. On Android TV, RecyclerView may use that layout pass
         * to anchor the focused child at the beginning of the horizontal row,
         * which looks like focus suddenly moving left.
         *
         * Save both the horizontal layout state and the exact focused item
         * before dispatching the diff, then restore them after RecyclerView
         * finishes the update.
         */
        val layoutManager =
            recyclerView.layoutManager as? LinearLayoutManager

        val savedLayoutState =
            layoutManager?.onSaveInstanceState()

        val focusedItemView =
            recyclerView.findFocus()
                ?.let { focused ->
                    recyclerView.findContainingItemView(focused)
                }

        val focusedPosition =
            focusedItemView?.let { itemView ->
                recyclerView.getChildAdapterPosition(itemView)
            } ?: RecyclerView.NO_POSITION

        val focusedKey =
            if (focusedPosition in oldPrograms.indices) {
                stableKey(oldPrograms[focusedPosition])
            } else {
                null
            }

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

        recyclerView.post {
            if (savedLayoutState != null) {
                layoutManager?.onRestoreInstanceState(savedLayoutState)
            }

            val newFocusedPosition =
                focusedKey?.let { key ->
                    programs.indexOfFirst {
                        stableKey(it) == key
                    }
                } ?: RecyclerView.NO_POSITION

            if (newFocusedPosition != RecyclerView.NO_POSITION) {
                recyclerView.post {
                    recyclerView
                        .findViewHolderForAdapterPosition(
                            newFocusedPosition
                        )
                        ?.itemView
                        ?.requestFocus()
                }
            }
        }
    }

    fun restoreFocus(
        recyclerView: RecyclerView,
        programId: Int?,
        filename: String?,
        alignToStart: Boolean
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

        val currentHolder =
            recyclerView.findViewHolderForAdapterPosition(position)

        if (currentHolder != null) {
            if (!currentHolder.itemView.hasFocus()) {
                currentHolder.itemView.requestFocus()
            }
            return true
        }

        if (alignToStart) {
            layoutManager.scrollToPositionWithOffset(position, 0)
        } else {
            // Bring the card into view only when needed; do not snap it left.
            layoutManager.scrollToPosition(position)
        }

        recyclerView.post {
            recyclerView
                .findViewHolderForAdapterPosition(position)
                ?.itemView
                ?.requestFocus()
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

    override fun onViewRecycled(holder: ProgramVH) {
        holder.recycle()
        super.onViewRecycled(holder)
    }

    private data class ActiveProgress(
        val percent: Int,
        val elapsedMinutes: Int,
        val totalMinutes: Int,
    )

    inner class ProgramVH(
        view: View
    ) : RecyclerView.ViewHolder(view) {

        private val artwork: ImageView =
            view.findViewById(R.id.library_item_artwork)

        private val channelLogo: ImageView =
            view.findViewById(R.id.library_item_channel_logo)

        private val icon: ImageView =
            view.findViewById(R.id.library_item_icon)

        private val badge: TextView =
            view.findViewById(R.id.library_item_badge)

        private val title: TextView =
            view.findViewById(R.id.library_item_title)

        private val meta: TextView =
            view.findViewById(R.id.library_item_meta)

        private val description: TextView =
            view.findViewById(R.id.library_item_description)

        private val resumeProgress: ProgressBar =
            view.findViewById(R.id.library_item_resume_progress)

        private val resumeText: TextView =
            view.findViewById(R.id.library_item_resume_text)

        private val hint: TextView =
            view.findViewById(R.id.library_item_hint)

        private var recordingPulse: ObjectAnimator? = null

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
                    if (hasFocus) R.drawable.bg_library_card_focused
                    else R.drawable.bg_library_card_normal
                )

                /*
                 * Keep the card at its measured size. Scaling a focused child
                 * inside a horizontal RecyclerView clipped the artwork and the
                 * yellow selection border at the row edges. Selection is now
                 * communicated by a thin outline, yellow title, subtle elevation,
                 * and brightness without changing layout bounds.
                 */
                focusedView.animate()
                    .scaleX(1.0f)
                    .scaleY(1.0f)
                    .translationZ(if (hasFocus) 6f else 0f)
                    .alpha(if (hasFocus) 1.0f else 0.90f)
                    .setDuration(110L)
                    .start()

                title.setTextColor(
                    if (hasFocus) Color.rgb(255, 204, 0)
                    else Color.WHITE
                )
                hint.setTextColor(
                    if (hasFocus) Color.WHITE
                    else Color.rgb(189, 189, 189)
                )
                hint.text = if (hasFocus) "▶ OK Watch   •   Hold OK Options" else "OK Watch"

                artwork.animate()
                    .alpha(if (hasFocus) 1.0f else 0.88f)
                    .setDuration(110L)
                    .start()

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
            bindArtwork(program)
            bindChannelLogo(program)

            icon.visibility = View.GONE

            val processingStatus =
                program.processingStatus?.lowercase()

            badge.text = when {
                program.type.equals("recording", ignoreCase = true) &&
                        processingStatus == "ready" ->
                    "● READY"

                program.type.equals("recording", ignoreCase = true) &&
                        processingStatus in setOf("pending", "processing") -> {
                    val percent = program.processingPercent.coerceIn(0, 100)
                    if (percent > 0) "● PROCESSING $percent%" else "● PROCESSING"
                }

                program.type.equals("recording", ignoreCase = true) &&
                        processingStatus == "failed" ->
                    "● FAILED"

                program.status.equals("recording", ignoreCase = true) ||
                        program.status.equals("active", ignoreCase = true) ->
                    "● RECORDING"

                program.isNew -> "NEW"
                program.isRepeat -> "REPEAT"
                else -> kind.uppercase()
            }

            badge.setTextColor(
                when {
                    program.type.equals("recording", ignoreCase = true) &&
                            processingStatus == "ready" ->
                        Color.rgb(76, 217, 100)

                    program.type.equals("recording", ignoreCase = true) &&
                            processingStatus in setOf("pending", "processing") ->
                        Color.rgb(255, 204, 0)

                    program.type.equals("recording", ignoreCase = true) &&
                            processingStatus == "failed" ->
                        Color.rgb(255, 69, 58)

                    program.status.equals("recording", ignoreCase = true) ||
                            program.status.equals("active", ignoreCase = true) ->
                        Color.rgb(255, 69, 58)

                    else -> Color.rgb(255, 204, 0)
                }
            )

            val isActiveBadge =
                kind == "currently_recording" ||
                        program.status.equals("recording", ignoreCase = true) ||
                        program.status.equals("active", ignoreCase = true)
            updateRecordingPulse(isActiveBadge)

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
                metadata += "NEW"
            } else if (program.isRepeat) {
                metadata += "Repeat"
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

            if (program.type.equals("recording", ignoreCase = true)) {
                when (processingStatus) {
                    "pending", "processing" -> {
                        val step = program.processingStep
                            ?.takeIf { it.isNotBlank() }
                            ?: "Preparing playback"
                        val percent = program.processingPercent.coerceIn(0, 100)
                        metadata += if (percent > 0) {
                            "$step $percent%"
                        } else {
                            step
                        }
                    }

                    "ready" -> metadata += "Ready to play"
                    "failed" -> metadata += "Preparation failed"
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

            val isActiveRecording =
                kind == "currently_recording" ||
                        program.status.equals("active", ignoreCase = true)

            val activeProgress = if (isActiveRecording) {
                calculateActiveProgress(program.startTime, program.stopTime)
            } else {
                null
            }

            val showResume =
                kind == "continue_watching" &&
                        program.hasResume &&
                        !program.resumeCompleted &&
                        program.resumePositionSeconds > 0.0

            when {
                activeProgress != null -> {
                    resumeProgress.visibility = View.VISIBLE
                    resumeProgress.progress = activeProgress.percent
                    resumeText.visibility = View.VISIBLE
                    val remaining =
                        (activeProgress.totalMinutes - activeProgress.elapsedMinutes)
                            .coerceAtLeast(0)
                    resumeText.text =
                        "${activeProgress.elapsedMinutes} min recorded  •  $remaining min remaining"
                }

                showResume -> {
                    val duration = program.resumeDurationSeconds
                    val position = program.resumePositionSeconds
                    val percent = if (duration > 0.0) {
                        ((position / duration) * 100.0)
                            .toInt()
                            .coerceIn(1, 99)
                    } else {
                        0
                    }

                    resumeProgress.visibility = View.VISIBLE
                    resumeProgress.progress = percent
                    resumeText.visibility = View.VISIBLE
                    resumeText.text = buildString {
                        append("Resume at ")
                        append(formatDuration(position))
                        if (percent > 0) {
                            append("  ·  ")
                            append(percent)
                            append('%')
                        }
                    }
                }

                else -> {
                    resumeProgress.visibility = View.GONE
                    resumeProgress.progress = 0
                    resumeText.visibility = View.GONE
                    resumeText.text = ""
                }
            }

            hint.text = when {
                program.type.equals("recording", ignoreCase = true) &&
                        processingStatus in setOf("pending", "processing") ->
                    "Preparing playback…\nHold OK: Options"

                program.type.equals("recording", ignoreCase = true) &&
                        processingStatus == "failed" ->
                    "Not ready\nHold OK: Options"

                else -> "OK: Watch\nHold OK: Options"
            }
        }

        fun recycle() {
            recordingPulse?.cancel()
            recordingPulse = null
            badge.alpha = 1.0f
            Glide.with(itemView).clear(artwork)
            Glide.with(itemView).clear(channelLogo)
        }

        private fun updateRecordingPulse(active: Boolean) {
            recordingPulse?.cancel()
            recordingPulse = null
            badge.alpha = 1.0f

            if (!active) return

            recordingPulse = ObjectAnimator.ofFloat(badge, View.ALPHA, 1.0f, 0.48f, 1.0f).apply {
                duration = 1200L
                repeatCount = ObjectAnimator.INFINITE
                interpolator = AccelerateDecelerateInterpolator()
                start()
            }
        }

        private fun bindArtwork(program: LibraryProgram) {
            /*
             * Program artwork must be the show/episode image, never the
             * station logo. The logo has its own ImageView on the card.
             *
             * Prefer SignalDVR's artwork endpoint because it already applies
             * the Schedules Direct -> TVMaze fallback and cache. Older library
             * rows may only contain thumbnail/artwork paths, so keep those as
             * secondary fallbacks while explicitly rejecting logo paths.
             */
            val programArtworkUrl = program.programId
                ?.trim()
                ?.takeIf { it.isNotBlank() }
                ?.let { absoluteUrl("/api/program-artwork/$it") }

            val storedArtwork = sequenceOf(program.thumbnail, program.artwork)
                .mapNotNull { it?.trim()?.takeIf(String::isNotBlank) }
                .firstOrNull { path ->
                    !path.contains("/static/logos/", ignoreCase = true) &&
                            !path.contains("/logos/", ignoreCase = true)
                }
                ?.let(::absoluteUrl)

            val fallbackRequest = storedArtwork?.let { fallbackUrl ->
                Glide.with(itemView)
                    .load(fallbackUrl)
                    .fitCenter()
            }

            val request = Glide.with(itemView)
                .load(programArtworkUrl ?: storedArtwork)
                .fitCenter()
                .placeholder(R.drawable.bg_library_artwork_placeholder)

            if (programArtworkUrl != null && fallbackRequest != null) {
                request.error(fallbackRequest)
            } else {
                request.error(R.drawable.bg_library_artwork_placeholder)
            }

            request.into(artwork)
        }

        private fun bindChannelLogo(program: LibraryProgram) {
            val channel = program.channel?.trim().orEmpty()
            if (channel.isBlank()) {
                channelLogo.visibility = View.GONE
                Glide.with(itemView).clear(channelLogo)
                return
            }

            val logoPath = "/static/logos/${channel.replace('.', '_')}.png"
            channelLogo.visibility = View.VISIBLE
            Glide.with(itemView)
                .load(absoluteUrl(logoPath))
                .fitCenter()
                .error(android.R.color.transparent)
                .into(channelLogo)
        }

        private fun absoluteUrl(value: String?): String? {
            val raw = value?.trim().orEmpty()
            if (raw.isBlank()) return null
            if (raw.startsWith("http://") || raw.startsWith("https://")) return raw

            val base = ApiClient.getBaseUrl(itemView.context).trimEnd('/')
            return if (raw.startsWith('/')) "$base$raw" else "$base/$raw"
        }

        @DrawableRes
        private fun iconFor(
            program: LibraryProgram
        ): Int {
            val category =
                (program.category ?: "").lowercase()

            val programTitle =
                (program.title ?: "").lowercase()

            return when {
                "baseball" in category ||
                        "baseball" in programTitle ||
                        "sports" in category -> R.drawable.ic_library_sports

                "news" in category ||
                        "news" in programTitle -> R.drawable.ic_library_news

                "movie" in category ||
                        "movie" in programTitle -> R.drawable.ic_library_movie

                "music" in category -> R.drawable.ic_library_music
                "weather" in category -> R.drawable.ic_library_weather
                program.type == "recording" -> R.drawable.ic_home_recordings
                else -> R.drawable.ic_home_live_tv
            }
        }

        private fun calculateActiveProgress(
            startValue: String?,
            stopValue: String?,
        ): ActiveProgress? {
            if (startValue.isNullOrBlank() || stopValue.isNullOrBlank()) return null

            return try {
                val parser = SimpleDateFormat("yyyyMMddHHmmss", Locale.US)
                parser.isLenient = false
                val start = parser.parse(startValue)?.time ?: return null
                val stop = parser.parse(stopValue)?.time ?: return null
                val totalMs = (stop - start).coerceAtLeast(1L)
                val elapsedMs = (Date().time - start).coerceIn(0L, totalMs)

                ActiveProgress(
                    percent = ((elapsedMs.toDouble() / totalMs.toDouble()) * 100.0)
                        .toInt()
                        .coerceIn(1, 100),
                    elapsedMinutes = (elapsedMs / 60_000L).toInt(),
                    totalMinutes = ((totalMs + 59_999L) / 60_000L).toInt(),
                )
            } catch (_: Exception) {
                null
            }
        }

        private fun formatDuration(secondsValue: Double): String {
            val totalSeconds = secondsValue.toLong().coerceAtLeast(0L)
            val hours = totalSeconds / 3600L
            val minutes = (totalSeconds % 3600L) / 60L
            val seconds = totalSeconds % 60L

            return if (hours > 0L) {
                "%d:%02d:%02d".format(hours, minutes, seconds)
            } else {
                "%d:%02d".format(minutes, seconds)
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