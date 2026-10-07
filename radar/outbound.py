"""Search queue and explicit promotion of reviewed prospects into the local Radar."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from radar import backfill, data_standard, outbound_research, privacy, scoring, service
from radar.db import connection


def matches(conn, item):
    email = item.get("email", "")
    linkedin = item.get("linkedin_person", "")
    rows = conn.execute(
        """SELECT id,name,company,email,suppressed,demo FROM leads WHERE
        (%s<>'' AND lower(email)=%s) OR
        (%s<>'' AND lower(data->>'linkedin_person')=lower(%s))""",
        (email, email, linkedin, linkedin),
    ).fetchall()
    return rows


def query_identity(query):
    return {
        "email": query["query"] if query["mode"] == "email" else "",
        "linkedin_person": query["query"] if query["mode"] == "linkedin" else "",
    }


def enqueue(conn, payload, key):
    privacy.lock(conn)
    if not key or len(key) > 150:
        raise ValueError("Chave de pesquisa obrigatória (até 150 caracteres).")
    query = outbound_research.normalize_query(payload)
    privacy.require_allowed(conn, query_identity(query), key)
    fingerprint = service.digest(query)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 8))", (key,))
    previous = conn.execute(
        "SELECT * FROM outbound_searches WHERE request_key=%s", (key,)
    ).fetchone()
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise service.Conflict("Esta chave pertence a outra pesquisa.")
        return previous
    for lead in matches(conn, query_identity(query)):
        service.require_active(lead)
    row = conn.execute(
        "INSERT INTO outbound_searches(id,request_key,fingerprint,query) VALUES (%s,%s,%s,%s) RETURNING *",
        (uuid4(), key, fingerprint, Jsonb(query)),
    ).fetchone()
    service.audit(
        conn,
        None,
        "outbound_requested",
        {"search_id": str(row["id"]), "mode": query["mode"]},
        "comercial",
    )
    return row


def detail(conn, search_id):
    row = conn.execute("SELECT * FROM outbound_searches WHERE id=%s", (search_id,)).fetchone()
    if not row:
        raise LookupError("Pesquisa não encontrada.")
    for item in row["result"].get("candidates", []):
        item["existing"] = [
            {
                "id": str(lead["id"]),
                "name": lead["name"],
                "suppressed": lead["suppressed"],
                "demo": lead["demo"],
            }
            for lead in matches(conn, item)
        ]
        promoted = conn.execute(
            "SELECT lead_id FROM outbound_promotions WHERE search_id=%s AND candidate_id=%s",
            (search_id, item["id"]),
        ).fetchone()
        item["promoted_lead_id"] = str(promoted["lead_id"]) if promoted else None
        item["possible_duplicates"] = [
            {"id": str(lead["id"]), "name": lead["name"], "suppressed": lead["suppressed"]}
            for lead in conn.execute(
                "SELECT id,name,suppressed FROM leads WHERE name<>'' AND lower(name)=lower(%s) AND lower(company)=lower(%s)",
                (item["name"], item["company"]),
            ).fetchall()
        ]
        item["fingerprint"] = service.digest(
            [
                row["id"],
                {
                    key: value
                    for key, value in item.items()
                    if key
                    not in {"existing", "possible_duplicates", "promoted_lead_id", "fingerprint"}
                },
            ]
        )
    return row


def promote(conn, search_id, candidate_id, payload):
    privacy.lock(conn)
    if not payload.get("identity_confirmed") or not payload.get("reviewer", "").strip():
        raise ValueError("Confira a pessoa e a empresa e informe o responsável pela revisão.")
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 9))", (str(search_id) + candidate_id,)
    )
    row = detail(conn, search_id)
    if row["status"] != "done":
        raise service.Conflict("A pesquisa ainda não foi concluída.")
    candidate = next(
        (item for item in row["result"].get("candidates", []) if item["id"] == candidate_id), None
    )
    if not candidate:
        raise LookupError("Candidato não encontrado nesta pesquisa.")
    privacy.require_allowed(conn, candidate)
    if candidate["fingerprint"] != payload["fingerprint"]:
        raise service.Conflict("O resultado mudou. Confira novamente antes de adicionar.")
    if candidate["promoted_lead_id"]:
        lead = service.get_lead(conn, candidate["promoted_lead_id"], lock=True)
        service.require_active(lead)
        return {"id": candidate["promoted_lead_id"], "duplicate": True, "remote_writes": 0}
    if datetime.now(UTC) - row["finished_at"] > timedelta(days=7):
        raise service.Conflict(
            "Pesquisa com mais de sete dias. Pesquise novamente antes de adicionar."
        )
    # Serializes promotion even across different searches; capture also protects email imports.
    for identity in sorted(
        {
            value.lower()
            for value in (
                candidate["email"],
                candidate["linkedin_person"],
                candidate["name"] + "|" + candidate["company"],
            )
            if value
        }
    ):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 10))", (identity,))
    known = matches(conn, candidate)
    for lead in known:
        service.require_active(service.get_lead(conn, lead["id"], lock=True))
    if len(known) > 1:
        raise service.Conflict(
            "E-mail e LinkedIn apontam para contatos diferentes. Confira a base."
        )
    if known:
        if known[0]["demo"]:
            raise service.Conflict(
                "Identificador coincide com contato demo. Confira a base antes de adicionar."
            )
        result = {"id": str(known[0]["id"]), "duplicate": True, "remote_writes": 0}
    else:
        # Exact name/company matches require explicit handling instead of multiplying contacts.
        weak = conn.execute(
            "SELECT id,name,company,suppressed FROM leads WHERE lower(name)=lower(%s) AND lower(company)=lower(%s) AND name<>''",
            (candidate["name"], candidate["company"]),
        ).fetchall()
        if weak:
            raise service.Conflict(
                "Já existe um contato com este nome e empresa. Confira o cadastro existente antes de adicionar."
            )
        data = {key: candidate[key] for key in ("website", "linkedin_person") if candidate[key]}
        result = service.capture(
            conn,
            {
                "name": candidate["name"],
                "company": candidate["company"],
                "email": candidate["email"],
                "data": data,
                "consultant": payload["reviewer"],
            },
            "outbound:" + str(search_id) + ":" + candidate_id,
            origin="Prospecção outbound",
        )
        lead = service.get_lead(conn, result["id"], lock=True)
        for proposal in candidate["proposals"]:
            # A new lead has exactly the identity/website used by the research. Do not rebase evidence.
            service.add_suggestion(conn, lead, proposal)
        result["remote_writes"] = 0
    conn.execute(
        "INSERT INTO outbound_promotions(search_id,candidate_id,lead_id,reviewer) VALUES (%s,%s,%s,%s)",
        (search_id, candidate_id, result["id"], payload["reviewer"].strip()),
    )
    service.audit(
        conn,
        result["id"],
        "outbound_added",
        {
            "search_id": str(search_id),
            "candidate_id": candidate_id,
            "identity_evidence": {
                key: candidate[key] for key in ("source_url", "quote", "identity_verified")
            },
            "duplicate": result["duplicate"],
        },
        payload["reviewer"].strip(),
    )
    return result


def run_one():
    with connection() as conn:
        privacy.lock(conn)
        conn.execute(
            "UPDATE outbound_searches SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,error='Pesquisa interrompida; nova tentativa necessária.' WHERE status='running' AND started_at<now()-interval '15 minutes'"
        )
        row = conn.execute(
            "SELECT * FROM outbound_searches WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if not row:
            return False
        claimed = conn.execute(
            "UPDATE outbound_searches SET status='running',attempts=attempts+1,started_at=now(),error='' WHERE id=%s RETURNING attempts",
            (row["id"],),
        ).fetchone()
        config, policy = scoring.active(conn), backfill.policy(conn)
        standard_config = data_standard.config(conn)
        source_config = conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[
            "value"
        ]
        suppressed = any(item["suppressed"] for item in matches(conn, query_identity(row["query"])))

    def allowed(item):
        with connection() as conn:
            return not privacy.blocked(conn, item) and not any(
                lead["suppressed"] for lead in matches(conn, item)
            )

    status, error, result = "done", "", {}
    try:
        if suppressed:
            raise ValueError("Contato na lista de exclusão. Pesquisa bloqueada.")
        result = outbound_research.run(
            row["query"],
            policy,
            config,
            source_config,
            allow=allowed,
            standard_config=standard_config,
        )
    except ValueError as exc:
        status, error = "failed", str(exc)[:300]
    except (OSError, httpx.HTTPError):
        status, error = "failed", "Fonte ou provedor indisponível. Tente novamente mais tarde."
    with connection() as conn:
        privacy.lock(conn)
        if not conn.execute("SELECT 1 FROM outbound_searches WHERE id=%s", (row["id"],)).fetchone():
            return True
        result["candidates"] = [
            item for item in result.get("candidates", []) if not privacy.blocked(conn, item)
        ]
        # A late response from an expired worker cannot replace a newer attempt.
        updated = conn.execute(
            "UPDATE outbound_searches SET status=%s,result=%s,error=%s,finished_at=now() WHERE id=%s AND status='running' AND attempts=%s RETURNING id",
            (status, Jsonb(result), error, row["id"], claimed["attempts"]),
        ).fetchone()
        if updated:
            service.audit(
                conn,
                None,
                "outbound_completed",
                {
                    "search_id": str(row["id"]),
                    "status": status,
                    "candidates": len(result.get("candidates", [])),
                },
                "radar",
            )
    return True
