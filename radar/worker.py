import logging
import os
import time

import httpx
from psycopg.types.json import Jsonb

from radar import (
    backfill,
    data_standard,
    outbound,
    privacy,
    research,
    scoring,
    sources,
    standard_sources,
)
from radar.db import connection, initialize
from radar.service import add_suggestion, audit, enqueue, get_lead

log = logging.getLogger("radar.worker")


def run_one():
    with connection() as conn:
        privacy.lock(conn)
        # Recupera trabalho abandonado. Coleta tem timeouts menores que o lease.
        conn.execute(
            "UPDATE jobs SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END, result='{\"error\":\"Lease expirou; worker reiniciado\"}' WHERE status='running' AND started_at<now()-interval '15 minutes'"
        )
        job = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' AND available_at<=now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if not job:
            return False
        conn.execute(
            "UPDATE jobs SET status='running',started_at=now(),attempts=attempts+1 WHERE id=%s",
            (job["id"],),
        )
        lead = get_lead(conn, job["lead_id"])
        config = conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()["value"]
        research_policy = backfill.policy(conn)
        score_config = scoring.active(conn)
        standard_config = data_standard.config(conn)
    proposals, notes = [], []
    research_result = None
    if lead["demo"]:
        from radar.demo import demo_proposals

        proposals = demo_proposals(lead)
        notes.append("Pesquisa simulada com fontes fictícias, sem acesso externo.")
    elif not lead["suppressed"]:
        for source in ("registry", "website"):
            identifier = data_standard.inputs(lead).get(
                "cnpj" if source == "registry" else "website"
            )
            if not config.get(source) or not identifier:
                continue
            try:
                if source == "registry":
                    found, registry_notes = standard_sources.registry(lead)
                    notes.extend(registry_notes)
                    proposals.extend(
                        research.registry_candidates(
                            {**lead, "data": data_standard.inputs(lead)}, found
                        )
                        if config.get("backfill")
                        else found
                    )
                else:
                    if not config.get("backfill"):
                        found, url, content = sources.website_evidence(identifier)
                        doc = research.document(url, content, lead)
                        found.extend(standard_sources.phone_candidates(lead, doc))
                        proposals.extend(found)
                        if config.get("ai"):
                            proposals.extend(sources.ai_evidence(url, content))
            except (ValueError, OSError, httpx.HTTPError) as exc:
                # Não persiste corpos HTTP ou tokens nas mensagens de erro.
                notes.append(
                    f"{source}: {str(exc)[:220]}"
                    if isinstance(exc, ValueError)
                    else f"{source}: fonte indisponível ({type(exc).__name__}). Tente novamente."
                )
        if config.get("backfill"):
            try:
                found, research_result = research.collect(
                    lead,
                    research_policy,
                    score_config=score_config,
                    use_ai=config.get("ai", False),
                    standard_config=standard_config,
                )
                proposals.extend(found)
                notes.extend(research_result["notes"])
                # A site CNPJ is only a lookup hint. Registry identity must confirm it.
                if config.get("registry") and not data_standard.inputs(lead).get("cnpj"):
                    numbers = {p["value"] for p in found if p["field"] == "cnpj"}
                    if len(numbers) == 1:
                        registered, registry_notes = standard_sources.registry(
                            lead, cnpj=numbers.pop()
                        )
                        proposals.extend(research.registry_candidates(lead, registered))
                        notes.extend(registry_notes)
            except (ValueError, OSError, httpx.HTTPError) as exc:
                notes.append(
                    str(exc)[:220]
                    if isinstance(exc, ValueError)
                    else f"Backfill indisponível ({type(exc).__name__})."
                )
        if not any(config.values()):
            notes.append("Ative fontes em Fontes e regras para iniciar uma coleta pública.")
        if not lead["data"].get("cnpj") and not lead["data"].get("website"):
            notes.append("Informe CNPJ ou site oficial para identificar a empresa com segurança.")
    with connection() as conn:
        privacy.lock(conn)
        if not conn.execute("SELECT 1 FROM leads WHERE id=%s", (job["lead_id"],)).fetchone():
            return True
        lead = get_lead(conn, job["lead_id"], lock=True)
        current = conn.execute(
            "SELECT status FROM jobs WHERE id=%s FOR UPDATE", (job["id"],)
        ).fetchone()
        if lead["suppressed"] or current["status"] != "running":
            return True
        count = 0
        for proposal in proposals:
            try:
                count += bool(add_suggestion(conn, lead, proposal))
            except ValueError as exc:
                notes.append(str(exc))
        applied = (
            backfill.apply(conn, None, [str(lead["id"])], automatic=True)
            if config.get("backfill") and not lead["demo"]
            else {"applied": 0}
        )
        outcome = {
            "suggestions": count,
            "notes": notes,
            "backfill": research_result,
            "auto_filled": applied["applied"],
        }
        conn.execute(
            "UPDATE jobs SET status='done',result=%s,finished_at=now() WHERE id=%s",
            (Jsonb(outcome), job["id"]),
        )
        conn.execute("UPDATE leads SET last_scanned_at=now() WHERE id=%s", (lead["id"],))
        audit(conn, lead["id"], "scanned", outcome, "radar")
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    initialize()
    interval = max(0, int(os.getenv("RADAR_SCAN_INTERVAL_SECONDS", "0")))
    last_schedule = time.monotonic()
    while True:
        try:
            if interval and time.monotonic() - last_schedule >= interval:
                with connection() as conn:
                    # Um agendador por banco; advisory lock é transacional.
                    if conn.execute(
                        "SELECT pg_try_advisory_xact_lock(907202611) AS acquired"
                    ).fetchone()["acquired"]:
                        enqueue(conn)
                last_schedule = time.monotonic()
            enriched = run_one()
            prospected = outbound.run_one()
            if not enriched and not prospected:
                time.sleep(2)
        except Exception:
            log.exception("Falha no worker; lease permite recuperação da tarefa.")
            time.sleep(5)


if __name__ == "__main__":
    main()
