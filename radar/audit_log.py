"""Bounded audit pages with an upper event ID retained while browsing."""


def page(conn, number=1, size=25, through_id=None):
    if through_id is None:
        through_id = conn.execute("SELECT coalesce(max(id),0) AS id FROM events").fetchone()["id"]
    total = conn.execute(
        "SELECT count(*) AS total FROM events WHERE id<=%s", (through_id,)
    ).fetchone()["total"]
    pages = max(1, (total + size - 1) // size)
    number = min(number, pages)
    items = conn.execute(
        """SELECT e.*,l.company FROM events e LEFT JOIN leads l ON l.id=e.lead_id
        WHERE e.id<=%s ORDER BY e.id DESC LIMIT %s OFFSET %s""",
        (through_id, size, (number - 1) * size),
    ).fetchall()
    return {
        "items": items,
        "page": number,
        "page_size": size,
        "total": total,
        "pages": pages,
        "through_id": through_id,
    }
