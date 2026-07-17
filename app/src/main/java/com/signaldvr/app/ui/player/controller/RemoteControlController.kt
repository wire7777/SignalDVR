package com.signaldvr.app.ui.player.controller

import android.view.KeyEvent

class RemoteControlController(
    private val playerUiController: PlayerUiController,
    private val actions: Actions
) {
    data class Actions(
        val onTogglePlayPause: () -> Unit,
        val onPlay: () -> Unit,
        val onPause: () -> Unit,
        val onRewind10: () -> Unit,
        val onFastForward10: () -> Unit,
        val onShowGuide: () -> Unit,
        val onFinish: () -> Unit
    )

    fun handleKeyDown(
        keyCode: Int,
        isDvrBarVisible: Boolean
    ): Boolean? {
        if (isDvrBarVisible) {
            playerUiController.scheduleDvrDismiss()

            when (keyCode) {
                KeyEvent.KEYCODE_BACK -> {
                    playerUiController.hideDvrBar()
                    return true
                }

                KeyEvent.KEYCODE_DPAD_UP -> {
                    playerUiController.hideDvrBar()
                    actions.onShowGuide()
                    return true
                }

                KeyEvent.KEYCODE_DPAD_LEFT,
                KeyEvent.KEYCODE_DPAD_RIGHT,
                KeyEvent.KEYCODE_DPAD_CENTER,
                KeyEvent.KEYCODE_ENTER -> return null
            }
        }

        return when (keyCode) {
            KeyEvent.KEYCODE_DPAD_CENTER,
            KeyEvent.KEYCODE_ENTER -> {
                playerUiController.showDvrBar()
                true
            }

            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> {
                actions.onTogglePlayPause()
                true
            }

            KeyEvent.KEYCODE_MEDIA_PLAY -> {
                actions.onPlay()
                true
            }

            KeyEvent.KEYCODE_MEDIA_PAUSE,
            KeyEvent.KEYCODE_MEDIA_STOP -> {
                actions.onPause()
                true
            }

            KeyEvent.KEYCODE_DPAD_LEFT,
            KeyEvent.KEYCODE_MEDIA_REWIND -> {
                actions.onRewind10()
                true
            }

            KeyEvent.KEYCODE_DPAD_RIGHT,
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                actions.onFastForward10()
                true
            }

            KeyEvent.KEYCODE_INFO,
            KeyEvent.KEYCODE_GUIDE,
            KeyEvent.KEYCODE_MENU -> {
                actions.onShowGuide()
                true
            }

            KeyEvent.KEYCODE_BACK -> {
                actions.onFinish()
                true
            }

            else -> null
        }
    }
}
