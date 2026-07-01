import sys
import json
import urllib.parse
import urllib.request

import xbmcgui
import xbmcplugin
import xbmcaddon


ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
BASE_URL = ADDON.getSetting("server_url").rstrip("/")


def build_url(query):
    return sys.argv[0] + "?" + urllib.parse.urlencode(query)


def fetch_json(path):
    url = BASE_URL + path
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def add_dir(name, mode):
    item = xbmcgui.ListItem(label=name)
    url = build_url({"mode": mode})
    xbmcplugin.addDirectoryItem(HANDLE, url, item, True)


def add_play_item(name, url):
    item = xbmcgui.ListItem(label=name)
    item.setProperty("IsPlayable", "true")
    xbmcplugin.addDirectoryItem(HANDLE, url, item, False)


def main_menu():
    add_dir("Live TV", "live")
    add_dir("Recordings", "recordings")
    xbmcplugin.endOfDirectory(HANDLE)


def live_tv():
    channels = fetch_json("/api/kodi/live")

    for ch in channels:
        name = "{} {}".format(ch.get("channel", ""), ch.get("name", ""))

        now_title = ch.get("now_title") or "No guide data"
        next_title = ch.get("next_title") or "No guide data"

        item = xbmcgui.ListItem(label=name)
        item.setProperty("IsPlayable", "true")
        item.setInfo("video", {
            "title": name,
            "plot": "NOW: {}\n\nNEXT: {}".format(now_title, next_title),
        })

        play_url = BASE_URL + ch.get("play_url", "")
        xbmcplugin.addDirectoryItem(HANDLE, play_url, item, False)

    xbmcplugin.endOfDirectory(HANDLE)


def recordings():
    items = fetch_json("/api/kodi/recordings")

    for r in items:
        title = r.get("title") or r.get("filename") or "Recording"
        subtitle = r.get("subtitle") or ""
        channel = r.get("channel", "")
        recorded = r.get("start_time", "")
        description = r.get("description") or ""

        label = title
        if subtitle:
            label += " - " + subtitle

        item = xbmcgui.ListItem(label=label)
        item.setProperty("IsPlayable", "true")
        item.setInfo("video", {
            "title": title,
            "plot": description,
            "tvshowtitle": title,
            "episode": 0,
        })

        thumb = r.get("thumbnail")
        if thumb:
            item.setArt({
                "thumb": BASE_URL + "/thumbs/" + thumb,
                "icon": BASE_URL + "/thumbs/" + thumb,
            })

        play_url = BASE_URL + r.get("download_url", "")
        xbmcplugin.addDirectoryItem(HANDLE, play_url, item, False)

    xbmcplugin.endOfDirectory(HANDLE)

   

def router():
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
    mode = params.get("mode", "")

    if mode == "live":
        live_tv()
    elif mode == "recordings":
        recordings()
    else:
        main_menu()


if __name__ == "__main__":
    router()
