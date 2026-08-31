from . import manager as stream_manager
from . import delayed_live
from app import live_segments


class PlaybackManager:
    def status(self):
        active = stream_manager.get_active()
        if not active:
            return None
        return {"active": active, "delayed_live": delayed_live.status()}

    def jump_live(self):
        active = stream_manager.get_active()
        if not active:
            return None

        delayed_live.stop_delayed_live()
        segment = live_segments.sync_active_segment(active)

        return {
            "playlist": active["playlist_url"],
            "seconds_behind": 0,
            "live": True,
            "mode": "live",
            "active": active,
            "live_program_segment": segment,
        }

    def seek(self, seconds):
        """
        Server-authoritative relative DVR seek.

        Positive seconds fast-forward and negative seconds rewind. The active
        delayed-live cursor is adjusted on the server, avoiding client drift.
        """
        active = stream_manager.get_active()
        if not active:
            return None

        result = delayed_live.seek_relative(
            session_dir=active["session_dir"],
            seconds=int(seconds),
            output_name="delayed_live.m3u8",
        )

        if result.get("live"):
            segment = live_segments.sync_active_segment(active)
            return {
                "playlist": active["playlist_url"],
                "segment": None,
                "seconds": int(seconds),
                "seconds_behind": 0,
                "live": True,
                "mode": "live",
                "active": active,
                "live_program_segment": segment,
            }

        return {
            "playlist": result["playlist"],
            "segment": None,
            "seconds": int(seconds),
            "seconds_behind": result["seconds_behind"],
            "live": False,
            "mode": "delayed_live",
            "active": active,
        }

    def start_delayed_live(self, seconds_behind=30):
        active = stream_manager.get_active()
        if not active:
            return None

        result = delayed_live.start_delayed_live(
            session_dir=active["session_dir"],
            seconds_behind=seconds_behind,
            output_name="delayed_live.m3u8",
        )
        return {**result, "active": active}

    def delayed_live_status(self):
        active = stream_manager.get_active()
        if not active:
            return None
        return {"active_stream": active, "delayed_live": delayed_live.status()}


manager = PlaybackManager()
