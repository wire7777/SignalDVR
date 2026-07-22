package com.signaldvr.app.api

import com.google.gson.annotations.SerializedName

data class Channel(
    @SerializedName("channel")
    val number: String,

    @SerializedName("name")
    val name: String,

    @SerializedName("now_title")
    val nowTitle: String?,

    @SerializedName("next_title")
    val nextTitle: String?,

    @SerializedName("logo")
    val logo: String? = null,
)

data class NowPlaying(
    @SerializedName("channel") val channel: String,
    @SerializedName("name") val name: String,
    @SerializedName("current") val current: Program?,
    @SerializedName("next") val next: Program?,
)

data class Program(
    @SerializedName("id")
    val id: Int? = null,

    @SerializedName("title")
    val title: String? = null,

    @SerializedName("subtitle")
    val subtitle: String? = null,

    @SerializedName("episode_title")
    val episodeTitle: String? = null,

    @SerializedName("description")
    val description: String? = null,

    @SerializedName("start")
    val start: String? = null,

    @SerializedName("stop")
    val stop: String? = null,

    @SerializedName("channel")
    val channel: String? = null,

    @SerializedName("category")
    val category: String? = null,

    @SerializedName("season")
    val season: Int = 0,

    @SerializedName("episode")
    val episode: String? = null,

    @SerializedName("is_new")
    val isNew: Int = 0,

    @SerializedName("originalairdate")
    val originalAirDate: String? = null,

    @SerializedName("show_type")
    val showType: String? = null,

    @SerializedName("entity_type")
    val entityType: String? = null,

    @SerializedName("genres")
    val genres: String? = null,

    @SerializedName("rating")
    val rating: String? = null,

    @SerializedName("runtime")
    val runtime: Int = 0,

    @SerializedName("year")
    val year: Int = 0,

    @SerializedName("language")
    val language: String? = null,

    @SerializedName("video_properties")
    val videoProperties: String? = null,

    @SerializedName("audio_properties")
    val audioProperties: String? = null,

    @SerializedName("cast")
    val cast: String? = null,

    @SerializedName("directors")
    val directors: String? = null,

    @SerializedName("writers")
    val writers: String? = null,

    @SerializedName("artwork")
    val artwork: String? = null,

    @SerializedName("programid")
    val programId: String? = null,

    @SerializedName("station_id")
    val stationId: String? = null,

    @SerializedName("recording_status")
    val recordingStatus: String? = null,
)

data class TimeshiftResponse(
    @SerializedName("ok") val ok: Boolean,
    @SerializedName("hls_url") val hlsUrl: String?,
    @SerializedName("raw_url") val rawUrl: String?,
    @SerializedName("file_url") val fileUrl: String?,
    @SerializedName("stream_url") val streamUrl: String?,
    @SerializedName("file") val file: String?,
    @SerializedName("error") val error: String?,
)

data class Recording(
    @SerializedName("id") val id: Int?,
    @SerializedName("title") val title: String?,
    @SerializedName("subtitle") val subtitle: String?,
    @SerializedName("description") val description: String?,
    @SerializedName("channel") val channel: String?,
    @SerializedName("start_time") val startTime: String?,
    @SerializedName("filename") val filename: String?,
    @SerializedName("thumbnail") val thumbnail: String?,
    @SerializedName("download_url") val downloadUrl: String?,
    @SerializedName("play_url") val playUrl: String?,
)

data class EpgProgram(
    @SerializedName("id") val id: Int? = null,
    @SerializedName("title") val title: String?,
    @SerializedName("subtitle") val subtitle: String?,
    @SerializedName("description") val description: String?,
    @SerializedName("start") val start: String?,
    @SerializedName("stop") val stop: String?,
    @SerializedName("channel") val channel: String?,
    @SerializedName("category") val category: String? = null,
    @SerializedName("episode") val episode: String? = null,
    @SerializedName("rating") val rating: String? = null,
    @SerializedName("is_new") val isNew: Int? = null,
    @SerializedName("artwork") val artwork: String? = null,
    @SerializedName("recording_status") val recordingStatus: String? = null,
    @SerializedName("recording_series") val recordingSeries: Boolean = false,
    @SerializedName("recording_series_id") val recordingSeriesId: Int? = null,
)

data class DirectStreamResponse(
    @SerializedName("url") val url: String,
    @SerializedName("channel") val channel: String,
    @SerializedName("name") val name: String,
)

data class TimeshiftPosition(
    @SerializedName("duration_ms") val durationMs: Long,
    @SerializedName("size_bytes") val sizeBytes: Long,
    @SerializedName("file") val file: String?,
    @SerializedName("running") val running: Boolean,
)

data class TimeshiftStatus(
    @SerializedName("channel") val channel: String?,
    @SerializedName("position") val position: Int?,
    @SerializedName("paused") val paused: Boolean?,
    @SerializedName("hls_exists") val hlsExists: Boolean?,
    @SerializedName("running") val running: Boolean?,
)

data class RecordStatus(
    @SerializedName("recording") val recording: Boolean,
    @SerializedName("filename") val filename: String?,
    @SerializedName("ok") val ok: Boolean?,
    @SerializedName("error") val error: String?,
)

data class RecordOptionsResponse(
    @SerializedName("ok") val ok: Boolean,
    @SerializedName("channel") val channel: String?,
    @SerializedName("program") val program: Program?,
    @SerializedName("options") val options: List<String>?,
    @SerializedName("error") val error: String?,
)

data class RecordActionResponse(
    @SerializedName("ok") val ok: Boolean,
    @SerializedName("mode") val mode: String?,
    @SerializedName("channel") val channel: String?,
    @SerializedName("title") val title: String?,
    @SerializedName("start") val start: String?,
    @SerializedName("stop") val stop: String?,
    @SerializedName("message") val message: String?,
    @SerializedName("error") val error: String?,
)

data class SeekResponse(
    @SerializedName("ok") val ok: Boolean,
    @SerializedName("playlist_url") val playlistUrl: String?,
    @SerializedName("playlist") val playlist: String?,
    @SerializedName("segment") val segment: String?,
    @SerializedName("seconds") val seconds: Int?,
    @SerializedName("seconds_behind") val secondsBehind: Int?,
    @SerializedName("live") val live: Boolean?,
    @SerializedName("mode") val mode: String?,
    @SerializedName("error") val error: String?,
)

data class BasicResponse(
    @SerializedName("ok") val ok: Boolean? = null,
    @SerializedName("error") val error: String? = null,
    @SerializedName("message") val message: String? = null,
)


data class LibraryProgram(

    @SerializedName("id")
    val id: Int? = null,

    @SerializedName("title")
    val title: String? = null,

    @SerializedName("subtitle")
    val subtitle: String? = null,

    @SerializedName("description")
    val description: String? = null,

    @SerializedName("category")
    val category: String? = null,

    @SerializedName("channel")
    val channel: String? = null,

    @SerializedName("channel_name")
    val channelName: String? = null,

    @SerializedName("guide_name")
    val guideName: String? = null,

    @SerializedName("filename")
    val filename: String? = null,

    @SerializedName("type")
    val type: String? = null,

    @SerializedName("status")
    val status: String? = null,

    @SerializedName("saved")
    val saved: Boolean? = null,

    @SerializedName("playable")
    val playable: Boolean = false,

    @SerializedName("play_error")
    val playError: String? = null,

    @SerializedName("auto_expire")
    val autoExpire: Boolean = false,

    @SerializedName("start")
    val start: String? = null,

    @SerializedName("stop")
    val stop: String? = null,

    @SerializedName("start_time")
    val startTime: String? = null,

    @SerializedName("stop_time")
    val stopTime: String? = null,

    @SerializedName("created_at")
    val createdAt: String? = null,

    @SerializedName("updated_at")
    val updatedAt: String? = null,

    @SerializedName("ended_at")
    val endedAt: String? = null,

    @SerializedName("first_segment")
    val firstSegment: String? = null,

    @SerializedName("last_segment")
    val lastSegment: String? = null,

    @SerializedName("segment_count")
    val segmentCount: Int = 0,

    @SerializedName("file_path")
    val filePath: String? = null,

    @SerializedName("program_id")
    val programId: String? = null,

    @SerializedName("playlist_url")
    val playlistUrl: String? = null,

    @SerializedName("play_url")
    val playUrl: String? = null,

    @SerializedName("can_resume")
    val canResume: Boolean = false,

    @SerializedName("resume_url")
    val resumeUrl: String? = null,

    @SerializedName("resume_position_seconds")
    val resumePositionSeconds: Double = 0.0,

    @SerializedName("resume_duration_seconds")
    val resumeDurationSeconds: Double = 0.0,

    @SerializedName("resume_completed")
    val resumeCompleted: Boolean = false,

    @SerializedName("resume_updated_at")
    val resumeUpdatedAt: String? = null,

    @SerializedName("has_resume")
    val hasResume: Boolean = false,

    @SerializedName("thumbnail")
    val thumbnail: String? = null,

    // Recording post-processing state. These fields affect only
    // Library presentation; playback APIs and PlayerActivity are unchanged.
    @SerializedName("processing_status")
    val processingStatus: String? = null,

    @SerializedName("processing_percent")
    val processingPercent: Int = 0,

    @SerializedName("processing_step")
    val processingStep: String? = null,

    @SerializedName("processing_error")
    val processingError: String? = null,

    @SerializedName("vod_ready")
    val vodReady: Boolean = false,

    @SerializedName("processed_at")
    val processedAt: String? = null,

    // =========================================================
    // Guide Metadata (Metadata 2.0)
    // =========================================================

    @SerializedName("season")
    val season: Int = 0,

    @SerializedName("episode")
    val episode: String? = null,

    @SerializedName("episode_title")
    val episodeTitle: String? = null,

    @SerializedName("is_new")
    val isNew: Boolean = false,

    @SerializedName("is_repeat")
    val isRepeat: Boolean = false,

    @SerializedName("originalairdate")
    val originalAirDate: String? = null,

    @SerializedName("show_type")
    val showType: String? = null,

    @SerializedName("entity_type")
    val entityType: String? = null,

    @SerializedName("genres")
    val genres: String? = null,

    @SerializedName("rating")
    val rating: String? = null,

    @SerializedName("runtime")
    val runtime: Int = 0,

    @SerializedName("year")
    val year: Int = 0,

    @SerializedName("language")
    val language: String? = null,

    @SerializedName("video_properties")
    val videoProperties: String? = null,

    @SerializedName("audio_properties")
    val audioProperties: String? = null,

    @SerializedName("artwork")
    val artwork: String? = null,
)

data class BackgroundDvrStatusResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("phase")
    val phase: Int = 0,

    @SerializedName("active_program")
    val activeProgram: BackgroundDvrProgram? = null,

    @SerializedName("error")
    val error: String? = null,
)

data class BackgroundDvrProgram(
    @SerializedName("id") val id: Int? = null,
    @SerializedName("title") val title: String? = null,
    @SerializedName("subtitle") val subtitle: String? = null,
    @SerializedName("episode_title") val episodeTitle: String? = null,
    @SerializedName("description") val description: String? = null,
    @SerializedName("category") val category: String? = null,
    @SerializedName("channel") val channel: String? = null,
    @SerializedName("guide_name") val guideName: String? = null,
    @SerializedName("status") val status: String? = null,
    @SerializedName("saved") val saved: Int = 0,
    @SerializedName("auto_expire") val autoExpire: Int = 0,
    @SerializedName("start_time") val startTime: String? = null,
    @SerializedName("stop_time") val stopTime: String? = null,
    @SerializedName("created_at") val createdAt: String? = null,
    @SerializedName("updated_at") val updatedAt: String? = null,
    @SerializedName("ended_at") val endedAt: String? = null,
    @SerializedName("first_segment") val firstSegment: String? = null,
    @SerializedName("last_segment") val lastSegment: String? = null,
    @SerializedName("segment_count") val segmentCount: Int = 0,
    @SerializedName("file_path") val filePath: String? = null,
    @SerializedName("program_id") val programId: String? = null,
    @SerializedName("season") val season: Int = 0,
    @SerializedName("episode") val episode: String? = null,
    @SerializedName("is_new") val isNew: Int = 0,
    @SerializedName("originalairdate") val originalAirDate: String? = null,
    @SerializedName("show_type") val showType: String? = null,
    @SerializedName("entity_type") val entityType: String? = null,
    @SerializedName("genres") val genres: String? = null,
    @SerializedName("rating") val rating: String? = null,
    @SerializedName("runtime") val runtime: Int = 0,
    @SerializedName("year") val year: Int = 0,
    @SerializedName("language") val language: String? = null,
    @SerializedName("video_properties") val videoProperties: String? = null,
    @SerializedName("audio_properties") val audioProperties: String? = null,
    @SerializedName("artwork") val artwork: String? = null,
)

data class LibraryResponse(
    @SerializedName("ok")
    val ok: Boolean? = null,

    @SerializedName("continue_watching")
    val continueWatching: List<LibraryProgram>? = null,

    @SerializedName("live")
    val live: List<LibraryProgram>? = null,

    @SerializedName("buffered")
    val buffered: List<LibraryProgram>? = null,

    @SerializedName("saved")
    val saved: List<LibraryProgram>? = null,

    @SerializedName("recorded")
    val recorded: List<LibraryProgram>? = null,

    @SerializedName("error")
    val error: String? = null,
)

data class LibraryItem(
    @SerializedName("id") val id: Int? = null,
    @SerializedName("title") val title: String? = null,
    @SerializedName("filename") val filename: String? = null,
    @SerializedName("channel") val channel: String? = null,
    @SerializedName("type") val type: String? = null,
    @SerializedName("status") val status: String? = null,
    @SerializedName("description") val description: String? = null,
)

data class ProgramPlaylistResponse(
    @SerializedName("ok") val ok: Boolean? = null,
    @SerializedName("playlist_url") val playlistUrl: String? = null,
    @SerializedName("error") val error: String? = null,
)

data class SaveProgramResponse(
    @SerializedName("ok") val ok: Boolean? = null,
    @SerializedName("saved") val saved: Boolean? = null,
    @SerializedName("error") val error: String? = null,
)

data class AndroidSettingsResponse(
    @SerializedName("ok") val ok: Boolean = false,
    @SerializedName("player_engine") val playerEngine: String = "media3",
    @SerializedName("error") val error: String? = null,
)

data class RecordingVodResponse(
    @SerializedName("ok") val ok: Boolean = false,
    @SerializedName("recording_id") val recordingId: Int? = null,
    @SerializedName("title") val title: String? = null,
    @SerializedName("filename") val filename: String? = null,
    @SerializedName("profile") val profile: String? = null,
    @SerializedName("playlist_url") val playlistUrl: String? = null,
    @SerializedName("duration_seconds") val durationSeconds: Double? = null,
    @SerializedName("cached") val cached: Boolean? = null,
    @SerializedName("segment_count") val segmentCount: Int? = null,
    @SerializedName("error") val error: String? = null,
)

data class GuideRecordRequest(
    @SerializedName("type")
    val type: String? = null,
)

data class GuideRecordOptionsResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("program_id")
    val programId: Int? = null,

    @SerializedName("recording")
    val recording: Boolean = false,

    @SerializedName("series")
    val series: Boolean = false,

    @SerializedName("can_record")
    val canRecord: Boolean = true,

    @SerializedName("recording_status")
    val recordingStatus: String? = null,

    @SerializedName("schedule_id")
    val scheduleId: Int? = null,

    @SerializedName("series_id")
    val seriesId: Int? = null,

    @SerializedName("error")
    val error: String? = null,
)

data class GuideRecordActionResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("message")
    val message: String? = null,

    @SerializedName("recording_status")
    val recordingStatus: String? = null,

    @SerializedName("schedule_id")
    val scheduleId: Int? = null,

    @SerializedName("series_id")
    val seriesId: Int? = null,

    @SerializedName("error")
    val error: String? = null,
)

data class SeriesRecordingRule(
    @SerializedName("id")
    val id: Int,

    @SerializedName("seriesid")
    val seriesId: String? = null,

    @SerializedName("title")
    val title: String? = null,

    @SerializedName("channel")
    val channel: String? = null,

    @SerializedName("only_new")
    val onlyNew: Int = 0,

    @SerializedName("enabled")
    val enabled: Int = 1,

    @SerializedName("priority")
    val priority: Int = 50,

    @SerializedName("start_padding")
    val startPadding: Int = 2,

    @SerializedName("end_padding")
    val endPadding: Int = 5,

    @SerializedName("keep_last")
    val keepLast: Int = 0,

    @SerializedName("any_channel")
    val anyChannel: Int = 0,
)

data class SeriesRecordingListResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("count")
    val count: Int = 0,

    @SerializedName("series")
    val series: List<SeriesRecordingRule> = emptyList(),

    @SerializedName("error")
    val error: String? = null,
)

data class SeriesRecordingUpdateRequest(
    @SerializedName("title")
    val title: String? = null,

    @SerializedName("channel")
    val channel: String? = null,

    @SerializedName("only_new")
    val onlyNew: Int? = null,

    @SerializedName("enabled")
    val enabled: Int? = null,

    @SerializedName("priority")
    val priority: Int? = null,

    @SerializedName("start_padding")
    val startPadding: Int? = null,

    @SerializedName("end_padding")
    val endPadding: Int? = null,

    @SerializedName("keep_last")
    val keepLast: Int? = null,

    @SerializedName("any_channel")
    val anyChannel: Int? = null,
)

data class SeriesRecordingUpdateResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("message")
    val message: String? = null,

    @SerializedName("series")
    val series: SeriesRecordingRule? = null,

    @SerializedName("error")
    val error: String? = null,
)

data class ScheduledRecording(
    @SerializedName("id")
    val id: Int,

    @SerializedName("channel")
    val channel: String? = null,

    @SerializedName("title")
    val title: String? = null,

    @SerializedName("subtitle")
    val subtitle: String? = null,

    @SerializedName("start")
    val start: String? = null,

    @SerializedName("stop")
    val stop: String? = null,

    @SerializedName("status")
    val status: String? = null,

    @SerializedName("created_at")
    val createdAt: String? = null,

    @SerializedName("priority")
    val priority: Int = 50,

    @SerializedName("start_padding")
    val startPadding: Int = 0,

    @SerializedName("end_padding")
    val endPadding: Int = 0,

    @SerializedName("series_id")
    val seriesId: Int = 0,

    @SerializedName("description")
    val description: String? = null,

    @SerializedName("category")
    val category: String? = null,

    @SerializedName("episode")
    val episode: String? = null,

    @SerializedName("programid")
    val programId: String? = null,

    @SerializedName("seriesid")
    val guideSeriesId: String? = null,

    @SerializedName("originalairdate")
    val originalAirDate: String? = null,
)

data class ScheduledRecordingListResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("count")
    val count: Int = 0,

    @SerializedName("scheduled")
    val scheduled: List<ScheduledRecording> = emptyList(),

    @SerializedName("error")
    val error: String? = null,
)

data class ScheduledRecordingItemResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("scheduled")
    val scheduled: ScheduledRecording? = null,

    @SerializedName("error")
    val error: String? = null,
)

data class ScheduledRecordingActionResponse(
    @SerializedName("ok")
    val ok: Boolean = false,

    @SerializedName("schedule_id")
    val scheduleId: Int? = null,

    @SerializedName("message")
    val message: String? = null,

    @SerializedName("error")
    val error: String? = null,
)