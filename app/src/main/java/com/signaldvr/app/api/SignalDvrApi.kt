package com.signaldvr.app.api

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface SignalDvrApi {

    @GET("api/kodi/live")
    suspend fun getLiveChannels(): List<Channel>

    @GET("api/kodi/nowplaying/{channel}")
    suspend fun getNowPlaying(
        @Path("channel") channel: String,
    ): NowPlaying

    @GET("api/kodi/stream/{channel}")
    suspend fun getDirectStream(
        @Path("channel") channel: String,
    ): DirectStreamResponse

    @GET("api/kodi/timeshift/start/{channel}")
    suspend fun startTimeshift(
        @Path("channel") channel: String,
    ): TimeshiftResponse

    @GET("api/kodi/recordings")
    suspend fun getRecordings(): List<Recording>

    @GET("api/kodi/guide/{channel}")
    suspend fun getGuide(
        @Path("channel") channel: String,
    ): List<EpgProgram>


    @GET("api/guide/program/{programId}/record-options")
    suspend fun getGuideRecordOptions(
        @Path("programId") programId: Int,
    ): GuideRecordOptionsResponse

    @POST("api/guide/program/{programId}/record-once")
    suspend fun recordGuideProgramOnce(
        @Path("programId") programId: Int,
        @Body options: GuideRecordRequest,
    ): GuideRecordActionResponse

    @POST("api/guide/program/{programId}/record-series")
    suspend fun recordGuideProgramSeries(
        @Path("programId") programId: Int,
        @Body options: GuideRecordRequest,
    ): GuideRecordActionResponse

    @POST("api/guide/program/{programId}/record-new")
    suspend fun recordGuideProgramNewEpisodes(
        @Path("programId") programId: Int,
        @Body options: GuideRecordRequest,
    ): GuideRecordActionResponse

    @DELETE("api/guide/program/{programId}/record")
    suspend fun cancelGuideProgramRecording(
        @Path("programId") programId: Int,
    ): GuideRecordActionResponse

    @DELETE("api/series/{seriesId}")
    suspend fun deleteSeriesRule(
        @Path("seriesId") seriesId: Int,
    ): GuideRecordActionResponse

    @POST("api/kodi/record/start/{channel}")
    suspend fun recordStart(
        @Path("channel") channel: String,
    ): RecordStatus

    @POST("api/kodi/record/stop")
    suspend fun recordStop(): RecordStatus

    @GET("api/kodi/record/status")
    suspend fun recordStatus(): RecordStatus

    @GET("api/record/options/{channel}")
    suspend fun getRecordOptions(
        @Path("channel") channel: String,
    ): RecordOptionsResponse

    @POST("api/record/rest/{channel}")
    suspend fun recordRestOfShow(
        @Path("channel") channel: String,
    ): RecordActionResponse

    @POST("api/record/schedule/{channel}")
    suspend fun recordScheduleShow(
        @Path("channel") channel: String,
    ): RecordActionResponse

    @GET("api/recordings/{recordingId}/vod")
    suspend fun getRecordingVod(
        @Path("recordingId") recordingId: Int,
        @Query("profile") profile: String,
    ): RecordingVodResponse

    @GET("api/playback/seek")
    suspend fun playbackSeek(
        @Query("seconds") seconds: Int,
    ): SeekResponse

    @GET("api/playback/live")
    suspend fun playbackLive(): SeekResponse

    @POST("api/timeshift/pause")
    suspend fun timeshiftPause(): TimeshiftStatus

    @POST("api/timeshift/resume")
    suspend fun timeshiftResume(): TimeshiftStatus

    @POST("api/timeshift/replay/{seconds}")
    suspend fun timeshiftReplay(
        @Path("seconds") seconds: Int,
    ): TimeshiftStatus

    @POST("api/timeshift/skip/{seconds}")
    suspend fun timeshiftSkip(
        @Path("seconds") seconds: Int,
    ): TimeshiftStatus

    @POST("api/timeshift/live")
    suspend fun timeshiftLive(): TimeshiftStatus

    @GET("api/library")
    suspend fun getLibrary(): LibraryResponse

    @GET("api/program_catalog/{programId}/playlist")
    suspend fun getProgramPlaylist(
        @Path("programId") programId: Int,
    ): ProgramPlaylistResponse

    @POST("api/program_catalog/{programId}/save")
    suspend fun saveProgram(
        @Path("programId") programId: Int,
    ): SaveProgramResponse

    @DELETE("api/program_catalog/{programId}/delete")
    suspend fun deleteProgram(
        @Path("programId") programId: Int,
    ): BasicResponse

    @DELETE("api/recordings/{recordingId}/delete")
    suspend fun deleteRecording(
        @Path("recordingId") recordingId: Int,
    ): BasicResponse

    @GET("api/android/settings")
    suspend fun getAndroidSettings(): AndroidSettingsResponse
}