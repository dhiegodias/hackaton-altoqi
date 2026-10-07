"""Explicit human assessments and reviewable, versioned score publication.

CRM approval is never a correctness label. Simulation cannot mutate CRM data.
"""

from psycopg.types.json import Jsonb

from radar import backfill, privacy, scoring, service

SAMPLE_LIMIT = 1000


def describe(row):
    original = row["proposed_value"] if row["proposed_value"] is not None else row["value"]
    sample = {
        "id": str(row["id"]),
        "lead_id": str(row["lead_id"]),
        "name": row["name"],
        "company": row["company"],
        "field": row["field"],
        "value": original,
        "current_value": row["value"],
        "status": row["status"],
        "assessment": row["assessment"],
        "observed_at": row["observed_at"].isoformat(),
        "confidence": row["confidence"],
    }
    sample["fingerprint"] = service.digest(
        [sample["id"], original, service.assessment_identity(row["assessment"])]
    )
    review = row.get("calibration_review")
    sample["last_review_id"] = review["id"] if review else None
    sample["review"] = (
        review if review and review["sample_fingerprint"] == sample["fingerprint"] else None
    )
    sample["stale_review"] = bool(review and not sample["review"])
    return sample


def samples(conn):
    rows = conn.execute(
        """SELECT s.*,l.name,l.company,count(*) OVER() AS available,
        (SELECT jsonb_build_object('id',r.id,'judgment',r.judgment,'reviewer',r.reviewer,
          'note',r.note,'created_at',r.created_at,'sample_fingerprint',r.sample_fingerprint)
         FROM calibration_reviews r WHERE r.suggestion_id=s.id ORDER BY r.id DESC LIMIT 1)
        AS calibration_review
        FROM suggestions s JOIN leads l ON l.id=s.lead_id
        WHERE NOT l.demo AND NOT l.suppressed AND s.assessment->>'version'=%s
        ORDER BY s.observed_at DESC,s.id LIMIT %s""",
        (backfill.VERSION, SAMPLE_LIMIT),
    ).fetchall()
    items = [describe(row) for row in rows]
    return {"items": items, "available": rows[0]["available"] if rows else 0, "limit": SAMPLE_LIMIT}


def review_sample(conn, suggestion_id, payload):
    privacy.lock(conn)
    ref = conn.execute("SELECT lead_id FROM suggestions WHERE id=%s", (suggestion_id,)).fetchone()
    if not ref:
        raise LookupError("Sugestão não encontrada.")
    lead = service.get_lead(conn, ref["lead_id"], lock=True)
    service.require_active(lead)
    if lead["demo"]:
        raise ValueError("Exemplos demo não entram na calibração com dados reais.")
    row = conn.execute(
        "SELECT * FROM suggestions WHERE id=%s FOR UPDATE", (suggestion_id,)
    ).fetchone()
    if row["assessment"].get("version") != backfill.VERSION:
        raise ValueError("A sugestão não possui observações compatíveis com o cálculo atual.")
    sample = describe({**row, "name": lead["name"], "company": lead["company"]})
    if sample["fingerprint"] != payload["fingerprint"]:
        raise service.Conflict("A sugestão mudou. Confira o valor e a evidência novamente.")
    previous = conn.execute(
        "SELECT * FROM calibration_reviews WHERE suggestion_id=%s ORDER BY id DESC LIMIT 1",
        (suggestion_id,),
    ).fetchone()
    if (previous["id"] if previous else None) != payload.get("expected_review_id"):
        raise service.Conflict("Outra avaliação foi registrada. Recarregue antes de alterar.")
    reviewer, note = payload["reviewer"].strip(), payload.get("note", "").strip()
    if not reviewer:
        raise ValueError("Informe quem conferiu a sugestão.")
    if payload["judgment"] == "incorrect" and len(note) < 3:
        raise ValueError("Explique brevemente por que o valor está incorreto.")
    result = conn.execute(
        """INSERT INTO calibration_reviews(suggestion_id,judgment,reviewer,note,sample_fingerprint,sample)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,judgment,reviewer,note,created_at""",
        (suggestion_id, payload["judgment"], reviewer, note, sample["fingerprint"], Jsonb(sample)),
    ).fetchone()
    service.audit(
        conn,
        lead["id"],
        "calibration_reviewed",
        {
            "suggestion_id": str(suggestion_id),
            "field": row["field"],
            "judgment": payload["judgment"],
            "review_id": result["id"],
            "note": note,
        },
        reviewer,
    )
    return result


def metrics(items, weights, policy):
    counts = {
        "reviewed": 0,
        "passed": 0,
        "correct_passed": 0,
        "incorrect_passed": 0,
        "correct_held": 0,
        "incorrect_held": 0,
    }
    fields = {}
    cases = []
    for item in items:
        judgment = (item.get("review") or {}).get("judgment")
        if judgment not in {"correct", "incorrect"}:
            continue
        score = scoring.calculate(scoring.signals(item["assessment"]), weights)["confidence"]
        threshold = policy["field_thresholds"].get(item["field"], policy["threshold"])
        passed = score >= threshold
        field_counts = fields.setdefault(item["field"], dict.fromkeys(counts, 0))
        for target in (counts, field_counts):
            target["reviewed"] += 1
            target["passed"] += int(passed)
            target[f"{judgment}_{'passed' if passed else 'held'}"] += 1
        cases.append(
            {
                "id": item["id"],
                "field": item["field"],
                "name": item["name"],
                "company": item["company"],
                "judgment": judgment,
                "score": score,
                "threshold": threshold,
                "passed": passed,
            }
        )

    def ratios(result):
        correct = result["correct_passed"] + result["correct_held"]
        return {
            **result,
            "accuracy": round(100 * result["correct_passed"] / result["passed"], 1)
            if result["passed"]
            else None,
            "coverage": round(100 * result["passed"] / result["reviewed"], 1)
            if result["reviewed"]
            else None,
            "correct_recovery": round(100 * result["correct_passed"] / correct, 1)
            if correct
            else None,
        }

    return {
        "overall": ratios(counts),
        "fields": {key: ratios(value) for key, value in fields.items()},
        "cases": cases,
    }


def simulate(conn, weights):
    weights = scoring.validate(weights)
    current = scoring.active(conn)
    dataset = samples(conn)
    policy = backfill.policy(conn)
    pending = conn.execute(
        """SELECT s.*,l.demo,l.suppressed FROM suggestions s JOIN leads l ON l.id=s.lead_id
        WHERE s.status='pending' AND NOT l.demo AND NOT l.suppressed AND s.assessment->>'version'=%s
        ORDER BY s.id""",
        (backfill.VERSION,),
    ).fetchall()
    pending_changes = []
    for row in pending:
        updated = scoring.rescore(row, {"revision": current["revision"], "weights": weights})
        if row["confidence"] != updated["confidence"]:
            pending_changes.append(
                {"id": str(row["id"]), "before": row["confidence"], "after": updated["confidence"]}
            )
    result = {
        "active_revision": current["revision"],
        "current_weights": current["weights"],
        "proposed_weights": weights,
        "maximum": scoring.maximum(weights),
        "policy": policy,
        "sample_count": len(dataset["items"]),
        "available": dataset["available"],
        "unconfirmed": sum(
            not item["review"] or item["review"]["judgment"] == "unknown"
            for item in dataset["items"]
        ),
        "current": metrics(dataset["items"], current["weights"], policy),
        "proposed": metrics(dataset["items"], weights, policy),
        "pending_changes": pending_changes,
        "calculations": [
            {
                "id": item["id"],
                "current": scoring.calculate(
                    scoring.signals(item["assessment"]), current["weights"]
                ),
                "proposed": scoring.calculate(scoring.signals(item["assessment"]), weights),
            }
            for item in dataset["items"]
        ],
        "review_basis": [
            [item["id"], item["fingerprint"], item["review"]["id"] if item["review"] else None]
            for item in dataset["items"]
        ],
    }
    # Includes the current queue, policy and annotations; concurrent edits require a new preview.
    result["fingerprint"] = service.digest([result, backfill.preview(conn)["fingerprint"]])
    return result


def publish(conn, weights, fingerprint, reviewer, note):
    privacy.lock(conn)
    if not reviewer.strip() or not note.strip():
        raise ValueError("Informe responsável e motivo para salvar uma versão dos pesos.")
    # Same lock order as batch approval/worker: contacts, then settings, then suggestions.
    conn.execute("SELECT id FROM leads ORDER BY id FOR UPDATE").fetchall()
    backfill.policy(conn, lock=True)
    scoring.active(conn, lock=True)
    plan = simulate(conn, weights)
    if plan["fingerprint"] != fingerprint:
        raise service.Conflict("A simulação mudou. Compare novamente antes de publicar os pesos.")
    evaluation = {
        key: plan[key]
        for key in ("policy", "current", "proposed", "sample_count", "available", "review_basis")
    }
    version = conn.execute(
        """INSERT INTO scoring_versions(weights,reviewer,note,evaluation)
        VALUES (%s,%s,%s,%s) RETURNING revision,created_at""",
        (Jsonb(weights), reviewer.strip(), note.strip(), Jsonb(evaluation)),
    ).fetchone()
    config = {"revision": version["revision"], "weights": weights}
    conn.execute("UPDATE settings SET value=%s WHERE key='scoring'", (Jsonb(config),))
    rows = conn.execute(
        """SELECT s.* FROM suggestions s JOIN leads l ON l.id=s.lead_id
        WHERE s.status='pending' AND NOT l.demo AND NOT l.suppressed
        AND s.assessment->>'version'=%s FOR UPDATE OF s""",
        (backfill.VERSION,),
    ).fetchall()
    updated = 0
    for row in rows:
        item = scoring.rescore(row, config)
        conn.execute(
            "UPDATE suggestions SET confidence=%s,assessment=%s WHERE id=%s",
            (item["confidence"], Jsonb(item["assessment"]), row["id"]),
        )
        updated += 1
    service.audit(
        conn,
        None,
        "scoring_published",
        {
            "revision": version["revision"],
            "previous_revision": plan["active_revision"],
            "weights": weights,
            "pending_rescored": updated,
            "note": note.strip(),
        },
        reviewer.strip(),
    )
    return {**config, "pending_rescored": updated, "remote_writes": 0}
