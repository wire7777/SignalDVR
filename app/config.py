from pathlib import Path

BASE = Path.home() / "andresdvr"

RECORDINGS = BASE / "recordings"
LIVEBUFFER = BASE / "livebuffer"
LOGS = BASE / "logs"
THUMBNAILS = BASE / "thumbnails"
GUIDE = BASE / "guide"
GUIDE_XML = GUIDE / "guide.xml"
GUIDE_URL = "http://192.168.2.13:8089/api/xmltv"
HDHR_DEVICE = "1077144F"

PORT = 8088