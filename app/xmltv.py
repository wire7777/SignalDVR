from app import database
from app import xmltv
from app import schedules_direct


def update_guide():

    source = database.get_setting(
        "guide_source",
        "xmltv"
    )

    if source == "xmltv":
        return xmltv.update()

    elif source == "schedules_direct":
        return schedules_direct.update()

    raise RuntimeError(
        f"Unknown guide source: {source}"
    )