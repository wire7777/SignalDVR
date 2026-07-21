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
        val onRewind: (seconds: Int) -> Unit,
        val onFastForward: (seconds: Int) -> Unit,
        val onShowGuide: () -> Unit,
        val onFinish: () -> Unit
    )

    fun handleKeyDown(
        keyCode: Int,
        repeatCount: Int,
        isDvrBarVisible: Boolean
    ): Boolean? {
        if (isDvrBarVisible) {
            playerUiController.scheduleDvrDismiss()

            return when (keyCode) {
                KeyEvent.KEYCODE_BACK -> {
                    playerUiController.hideDvrBar()
                    true
                }

                KeyEvent.KEYCODE_DPAD_UP -> {
                    playerUiController.hideDvrBar()
                    actions.onShowGuide()
                    true
                }

                // While the DVR controls are visible, allow Android's normal
                // focus system to move among 30/10/Pause/10/30/Live/REC and
                // allow OK/Enter to click the selected button.
                KeyEvent.KEYCODE_DPAD_LEFT,
                KeyEvent.KEYCODE_DPAD_RIGHT,
                KeyEvent.KEYCODE_DPAD_CENTER,
                KeyEvent.KEYCODE_ENTER -> null

                // Physical media keys remain direct playback controls even
                // when the on-screen DVR controls are visible.
                KeyEvent.KEYCODE_MEDIA_REWIND -> {
                    actions.onRewind(acceleratedSeekSeconds(repeatCount))
                    true
                }

                KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                    actions.onFastForward(acceleratedSeekSeconds(repeatCount))
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

                else -> null
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

            // When the DVR bar is hidden, DPAD left/right are direct seek
            // controls and repeated key events accelerate the seek distance.
            KeyEvent.KEYCODE_DPAD_LEFT,
            KeyEvent.KEYCODE_MEDIA_REWIND -> {
                actions.onRewind(acceleratedSeekSeconds(repeatCount))
                true
            }

            KeyEvent.KEYCODE_DPAD_RIGHT,
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                actions.onFastForward(acceleratedSeekSeconds(repeatCount))
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

    private fun acceleratedSeekSeconds(repeatCount: Int): Int {
        return when {
            repeatCount >= 12 -> 120
            repeatCount >= 8 -> 60
            repeatCount >= 4 -> 30
            else -> 10
        }
    }
}