from app.database import connect


def search_programs(text):

    with connect() as db:

        rows = db.execute("""
            SELECT *
            FROM programs
            WHERE
                title LIKE ?
                OR subtitle LIKE ?
                OR description LIKE ?
            ORDER BY start
            LIMIT 100
        """, (
            f"%{text}%",
            f"%{text}%",
            f"%{text}%"
        )).fetchall()

    return [dict(r) for r in rows]
