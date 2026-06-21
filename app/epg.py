from app.database import connect
from app import config
import xml.etree.ElementTree as ET
import urllib.request
import gzip
import shutil


def download_guide():
    config.GUIDE.mkdir(parents=True, exist_ok=True)

    if not config.GUIDE_URL:
        raise RuntimeError("GUIDE_URL is empty in config.py")

    tmp_file = config.GUIDE / "guide_download"

    urllib.request.urlretrieve(config.GUIDE_URL, tmp_file)

    if str(config.GUIDE_URL).endswith(".gz"):
        with gzip.open(tmp_file, "rb") as src:
            with open(config.GUIDE_XML, "wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        shutil.move(tmp_file, config.GUIDE_XML)

    return config.GUIDE_XML


def import_xmltv(filename):
    tree = ET.parse(filename)
    root = tree.getroot()

    with connect() as db:
        db.execute("DELETE FROM programs")

        for p in root.findall("programme"):
            db.execute("""
                INSERT INTO programs
                (
                    channel,
                    title,
                    subtitle,
                    description,
                    start,
                    stop,
                    category
                )
                VALUES (?,?,?,?,?,?,?)
            """, (
                p.attrib.get("channel", ""),
                p.findtext("title", ""),
                p.findtext("sub-title", ""),
                p.findtext("desc", ""),
                p.attrib.get("start", ""),
                p.attrib.get("stop", ""),
                p.findtext("category", "")
            ))

        db.commit()


def update_guide():
    guide_file = download_guide()
    import_xmltv(guide_file)