package com.signaldvr.app.ui.player.engine

import android.content.Context
import androidx.media3.ui.PlayerView

object PlayerFactory {

    const val ENGINE_MEDIA3 = "media3"

    fun create(
        context: Context,
        media3PlayerView: PlayerView,
    ): PlayerEngine {
        media3PlayerView.visibility =
            android.view.View.VISIBLE

        return Media3PlayerEngine(
            context = context,
            playerView = media3PlayerView,
        )
    }
}