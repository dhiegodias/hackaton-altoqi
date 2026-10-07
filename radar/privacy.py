"""Transactional local erasure and keyed, minimal anti-reimport markers.

HMAC markers are pseudonymous personal data, not anonymous records. The secret
stays outside the database. Backups/external CRMs require their own deletion flow.
"""

import hashlib
import hmac
import json
import os
from urllib.parse import unquote
from uuid import uuid4

from psycopg.types.json import Jsonb

from radar import data_standard as standard
from radar.validation import normalize_email, validate_value


class ErasedContact(ValueError):
    pass


def lock(conn):
    # Acquire before contact/policy locks in every writer. Work done outside the
    # transaction must recheck at commit; erasure never waits for an HTTP fetch.
    conn.execute("SELECT pg_advisory_xact_lock(907202612)")


def identities(payload, key=""):
    data = {**payload.get("intake_data", {}), **payload.get("data", {})}
    result = {}
    for field, value in [
        ("email", payload.get("email")),
        ("linkedin", payload.get("linkedin_person") or data.get("linkedin_person")),
        ("hubspot", payload.get("hubspot_id")),
        ("request", key),
    ]:
        if value:
            try:
                if field == "email":
                    value = normalize_email(value)
                elif field == "linkedin":
                    value = unquote(validate_value("linkedin_person", value))
            except ValueError:
                # Preserve other usable identities; input validation follows this check.
                pass
            result[field] = str(value).strip().casefold().rstrip("/")
    if payload.get("name") and payload.get("company"):
        result["name_company"] = (
            standard.normal(payload["name"]) + "|" + standard.normal(payload["company"])
        )
    return result


def configured():
    secret = os.getenv("RADAR_ERASURE_KEY", "")
    return len(secret) >= 32 and not secret.startswith("generate-")


def fingerprints(values):
    secret = os.getenv("RADAR_ERASURE_KEY", "")
    if not configured():
        raise ValueError(
            "Configure RADAR_ERASURE_KEY com pelo menos 32 caracteres no servidor para eliminar dados e preservar o bloqueio de reimportação."
        )
    return [
        (kind, hmac.new(secret.encode(), (kind + ":" + value).encode(), hashlib.sha256).hexdigest())
        for kind, value in values.items()
    ]


def blocked(conn, payload, key=""):
    if not conn.execute("SELECT 1 FROM erasure_blocks LIMIT 1").fetchone():
        return False
    return any(
        conn.execute(
            "SELECT 1 FROM erasure_blocks WHERE kind=%s AND fingerprint=%s", item
        ).fetchone()
        for item in fingerprints(identities(payload, key))
    )


def require_allowed(conn, payload, key=""):
    if blocked(conn, payload, key):
        raise ErasedContact(
            "Contato eliminado a pedido. Reimportação, captura e pesquisa bloqueadas."
        )


def matches_record(value, lead):
    if isinstance(value, dict):
        if str(value.get("id", "")) == str(lead["id"]) or str(value.get("lead_id", "")) == str(
            lead["id"]
        ):
            return True
        if value.get("email") and str(value["email"]).casefold() == lead["email"].casefold():
            return True
        if (
            value.get("name")
            and value.get("company")
            and standard.normal(value["name"]) == standard.normal(lead["name"])
            and standard.normal(value["company"]) == standard.normal(lead["company"])
        ):
            return True
        return any(matches_record(v, lead) for v in value.values())
    if isinstance(value, list):
        return any(matches_record(v, lead) for v in value)
    if isinstance(value, str):
        needles = [str(lead["id"]), lead.get("email", ""), lead["data"].get("linkedin_person", "")]
        return any(needle and needle.casefold() in value.casefold() for needle in needles)
    return False


def related(conn, lead):
    versions = [
        row["revision"]
        for row in conn.execute("SELECT revision,evaluation FROM scoring_versions").fetchall()
        if matches_record(row["evaluation"], lead)
    ]
    searches = set(
        row["search_id"]
        for row in conn.execute(
            "SELECT search_id FROM outbound_promotions WHERE lead_id=%s", (lead["id"],)
        ).fetchall()
    )
    for row in conn.execute("SELECT id,query,result FROM outbound_searches").fetchall():
        if matches_record(row, lead) or (
            lead["name"]
            and standard.normal(lead["name"])
            in standard.normal(json.dumps(row["result"], ensure_ascii=False))
        ):
            searches.add(row["id"])
    requests = [
        row["key"]
        for row in conn.execute("SELECT key,result FROM requests").fetchall()
        if matches_record(row, lead)
    ]
    events = [
        row["id"]
        for row in conn.execute("SELECT id,lead_id,action,detail FROM events").fetchall()
        if row["lead_id"] == lead["id"]
        or matches_record(row["detail"], lead)
        or row["detail"].get("search_id") in {str(s) for s in searches}
        or (row["action"] == "scoring_published" and row["detail"].get("revision") in versions)
    ]
    return {
        "searches": sorted(searches),
        "requests": requests,
        "events": events,
        "versions": versions,
    }


def preview(conn, lead_id):
    from radar import transfers
    from radar.service import digest, get_lead

    lead = get_lead(conn, lead_id)
    refs = related(conn, lead)
    refs["transfers"] = transfers.privacy_impact(conn, lead_id)
    counts = {
        "contatos": 1,
        "pesquisas_outbound": len(refs["searches"]),
        "recibos_de_captura": len(refs["requests"]),
        "eventos": len(refs["events"]),
        "avaliacoes_historicas": len(refs["versions"]),
        "arquivos_de_listas": len(refs["transfers"]),
    }
    for table in ("suggestions", "jobs", "syncs"):
        counts[table] = conn.execute(
            f"SELECT count(*) AS n FROM {table} WHERE lead_id=%s", (lead_id,)
        ).fetchone()["n"]
    counts["calibracoes"] = conn.execute(
        "SELECT count(*) AS n FROM calibration_reviews WHERE suggestion_id IN (SELECT id FROM suggestions WHERE lead_id=%s)",
        (lead_id,),
    ).fetchone()["n"]
    return {
        "counts": counts,
        "fingerprint": digest([lead, refs, counts]),
        "scope": "Banco operacional deste Radar; não inclui HubSpot, backups, arquivos já baixados ou dispositivos offline. Arquivos temporários de listas pendentes e exportações armazenadas serão removidos.",
        "blocked_identifiers": list(identities(lead)),
        "configured": configured(),
    }


def erase(conn, lead_id, fingerprint, confirmation):
    from radar import transfers
    from radar.service import Conflict, get_lead

    if confirmation != "ELIMINAR":
        raise ValueError("Digite ELIMINAR para confirmar a eliminação definitiva local.")
    lock(conn)
    lead = get_lead(conn, lead_id, lock=True)
    plan = preview(conn, lead_id)
    if plan["fingerprint"] != fingerprint:
        raise Conflict("Os dados mudaram. Confira novamente a prévia de eliminação.")
    refs = related(conn, lead)
    markers = fingerprints(identities(lead))
    for key in refs["requests"]:
        markers.extend(fingerprints({"request": key}))
    for marker in markers:
        conn.execute(
            "INSERT INTO erasure_blocks(kind,fingerprint) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            marker,
        )
    # Remove copies in audit/training/search data as well as the visible contact.
    transfers.erase_copies(conn, lead_id)
    conn.execute(
        "DELETE FROM calibration_reviews WHERE suggestion_id IN (SELECT id FROM suggestions WHERE lead_id=%s)",
        (lead_id,),
    )
    conn.execute(
        "DELETE FROM outbound_promotions WHERE lead_id=%s OR search_id=ANY(%s::uuid[])",
        (lead_id, refs["searches"]),
    )
    conn.execute("DELETE FROM outbound_searches WHERE id=ANY(%s::uuid[])", (refs["searches"],))
    conn.execute("DELETE FROM requests WHERE key=ANY(%s)", (refs["requests"],))
    conn.execute("DELETE FROM events WHERE id=ANY(%s)", (refs["events"],))
    conn.execute(
        "UPDATE scoring_versions SET evaluation='{}',note='Avaliação histórica removida na eliminação de dados pessoais.' WHERE revision=ANY(%s)",
        (refs["versions"],),
    )
    for table in ("suggestions", "jobs", "syncs"):
        conn.execute(f"DELETE FROM {table} WHERE lead_id=%s", (lead_id,))
    conn.execute("DELETE FROM leads WHERE id=%s", (lead_id,))
    receipt = uuid4()
    conn.execute(
        "INSERT INTO erasure_receipts(id,counts) VALUES (%s,%s)", (receipt, Jsonb(plan["counts"]))
    )
    return {
        "receipt": str(receipt),
        "deleted": True,
        "counts": plan["counts"],
        "remote_writes": 0,
        "scope": plan["scope"],
    }
