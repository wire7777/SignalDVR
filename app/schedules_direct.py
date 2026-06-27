from app import database


def update():
    username = database.get_setting("sd_username", "")
    postal_code = database.get_setting("sd_postal_code", "")
    lineup = database.get_setting("sd_lineup", "")
    days = database.get_setting("guide_days", "14")

    raise RuntimeError(
        "Schedules Direct importer is not implemented yet. "
        f"username={username or '-'} "
        f"postal_code={postal_code or '-'} "
        f"lineup={lineup or '-'} "
        f"days={days}"
    )
