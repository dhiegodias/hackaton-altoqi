"""Bounded browsing and SQL aggregates for large imported bases."""

from radar.qualification import qualify
from radar.validation import CORE_FIELDS, FIELDS


def page(conn, number=1, size=50, query="", filter_by="all"):
    term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = "NOT l.suppressed AND (%s='' OR l.company ILIKE %s OR l.name ILIKE %s OR l.email ILIKE %s OR l.data->>'segment' ILIKE %s)"
    params = [query, term, term, term, term]
    if filter_by == "pending":
        where += (
            " AND EXISTS (SELECT 1 FROM suggestions s WHERE s.lead_id=l.id AND s.status='pending')"
        )
    if filter_by == "priority":
        # Preserve domain rules, including expiring qualification; don't duplicate them in SQL.
        ids = []
        total = 0
        with conn.cursor(name="priority_contacts") as cursor:
            cursor.execute(
                "SELECT l.* FROM leads l WHERE " + where + " ORDER BY l.created_at,l.id", params
            )
            for lead in cursor:
                if qualify(lead)["group"].startswith("ICP"):
                    if (number - 1) * size <= total < number * size:
                        ids.append(lead["id"])
                    total += 1
        selected = conn.execute(
            "SELECT * FROM leads WHERE id=ANY(%s) ORDER BY created_at,id", (ids,)
        ).fetchall()
    else:
        total = conn.execute("SELECT count(*) AS n FROM leads l WHERE " + where, params).fetchone()[
            "n"
        ]
        selected = conn.execute(
            "SELECT l.* FROM leads l WHERE "
            + where
            + " ORDER BY l.created_at,l.id LIMIT %s OFFSET %s",
            (*params, size, (number - 1) * size),
        ).fetchall()
    ids = [lead["id"] for lead in selected]
    counts = {
        row["lead_id"]: row
        for row in conn.execute(
            "SELECT lead_id,count(*) FILTER(WHERE status='pending') AS pending,count(*) FILTER(WHERE status='pending' AND previous_value<>'null'::jsonb) AS conflicts FROM suggestions WHERE lead_id=ANY(%s) GROUP BY lead_id",
            (ids,),
        ).fetchall()
    }
    for lead in selected:
        lead.update({k: counts.get(lead["id"], {}).get(k, 0) for k in ("pending", "conflicts")})
    return {
        "items": selected,
        "total": total,
        "page": number,
        "page_size": size,
        "pages": max(1, (total + size - 1) // size),
    }


def coverage(conn):
    projections = []
    sums = {}
    for source in ("data", "baseline"):
        expressions = []
        for i, key in enumerate(CORE_FIELDS):
            condition = f"{source}->>'{key}' IS NOT NULL AND {source}->>'{key}'<>''"
            projections.append(f"count(*) FILTER (WHERE {condition}) AS {source}_{i}")
            expressions.append(f"(CASE WHEN {condition} THEN 1 ELSE 0 END)")
        sums[source] = "+".join(expressions)
        projections.append(
            f"coalesce(avg(round(100.0*({sums[source]})/{len(CORE_FIELDS)})),0) AS {source}_average"
        )
    row = conn.execute(
        "SELECT count(*) AS total,count(*) FILTER(WHERE demo) AS demo_count,"
        + ",".join(projections)
        + " FROM leads WHERE NOT suppressed"
    ).fetchone()
    return {
        "total": row["total"],
        "demo_count": row["demo_count"],
        "completeness": round(row["data_average"]),
        "baseline_completeness": round(row["baseline_average"]),
        "fields": [
            {
                "field": key,
                "label": FIELDS[key],
                "before": row[f"baseline_{i}"],
                "after": row[f"data_{i}"],
            }
            for i, key in enumerate(CORE_FIELDS)
        ],
    }
