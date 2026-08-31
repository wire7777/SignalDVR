import gzip
import shutil
import urllib.request
import xml.etree.ElementTree as ET

from app import config
from app import database


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
        tmp_file.unlink(missing_ok=True)
    else:
        shutil.move(tmp_file, config.GUIDE_XML)

    return config.GUIDE_XML


def import_xmltv(filename):
    tree = ET.parse(filename)
    root = tree.getroot()

    with database.connect() as db:
        db.execute("DELETE FROM programs")

        for p in root.findall("programme"):
            db.execute("""
                INSERT INTO programs
                (
                    channel, title, subtitle, description,
                    start, stop, category, episode, rating, is_new
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                p.attrib.get("channel", ""),
                p.findtext("title", ""),
                p.findtext("sub-title", ""),
                p.findtext("desc", ""),
                p.attrib.get("start", ""),
                p.attrib.get("stop", ""),
                p.findtext("category", ""),
                p.findtext("episode-num", ""),
                p.findtext("rating/value", ""),
                1 if p.find("new") is not None else 0,
            ))

        db.commit()


def update():
    guide_file = download_guide()
    import_xmltv(guide_file)

    created = database.apply_series_rules()
    print(f"Created {created} scheduled recordings from series rules.", flush=True)

    return guide_file