package com.signaldvr.app.ui.player

import android.content.Context
import android.content.Intent
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Recording

object PlaybackLauncher {

    suspend fun createRecordingIntent(
        context: Context,
        recording: Recording,
    ): Result<Intent> {
        val recordingId = recording.id

        if (recordingId == null || recordingId <= 0) {
            return Result.failure(
                IllegalArgumentException("Recording ID is missing")
            )
        }

        return try {
            val vod = ApiClient
                .getApi(context)
                .getRecordingVod(
                    recordingId = recordingId,
                    profile = "media3",
                )

            if (!vod.ok || vod.playlistUrl.isNullOrBlank()) {
                Result.failure(
                    IllegalStateException(
                        vod.error ?: "Unable to prepare recording"
                    )
                )
            } else {
                Result.success(
                    Intent(context, PlayerActivity::class.java).apply {
                        putExtra(
                            PlayerActivity.EXTRA_PLAY_URL,
                            vod.playlistUrl,
                        )
                        putExtra(
                            PlayerActivity.EXTRA_TITLE,
                            recording.title ?: "Recording",
                        )
                        putExtra(
                            PlayerActivity.EXTRA_CHANNEL_NUM,
                            recording.channel ?: "",
                        )
                        putExtra(
                            PlayerActivity.EXTRA_CHANNEL_NAME,
                            recording.channel ?: "",
                        )
                        putExtra(
                            PlayerActivity.EXTRA_IS_LIVE,
                            false,
                        )
                        putExtra(
                            PlayerActivity.EXTRA_RECORDING_ID,
                            recordingId,
                        )
                    }
                )
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun createLiveIntent(
        context: Context,
        channel: String,
        channelName: String = channel,
        title: String = "Live TV",
    ): Intent {
        return Intent(context, PlayerActivity::class.java).apply {
            putExtra(PlayerActivity.EXTRA_TITLE, title)
            putExtra(PlayerActivity.EXTRA_CHANNEL_NUM, channel)
            putExtra(PlayerActivity.EXTRA_CHANNEL_NAME, channelName)
            putExtra(PlayerActivity.EXTRA_IS_LIVE, true)
            putExtra(PlayerActivity.EXTRA_RECORDING_ID, -1)
        }
    }
}