"""Erasure must remove copies, block replay and defeat in-flight research."""

import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from radar import privacy, research, service, worker

PERSON = {
    "name": "Marina Privacidade",
    "company": "Empresa Privacidade",
    "email": "marina@privacidade.invalid",
    "data": {
        "website": "https://privacidade.invalid",
        "linkedin_person": "https://www.linkedin.com/in/marina-privacidade",
    },
}


def capture(database, payload=None, key="privacy-first"):
    with database() as conn:
        return service.capture(conn, payload or PERSON, key)["id"]


def test_erasure_removes_operational_copies_and_blocks_all_known_identity_paths(
    database, api_client
):
    lead_id = capture(database)
    other = capture(
        database, {"name": "Rafael Preservado", "company": "Empresa Independente"}, "other"
    )
    with database() as conn:
        lead = service.get_lead(conn, lead_id)
        doc = research.document(
            PERSON["data"]["website"],
            "<title>Empresa Privacidade</title><p>Marina Privacidade, Diretora.</p>",
            lead,
        )
        suggestion = service.add_suggestion(conn, lead, research.role_candidates(lead, doc)[0])
        service.review(conn, suggestion["id"], "approve", "Equipe")
        service.enqueue(conn, [lead_id])
        conn.execute(
            "INSERT INTO calibration_reviews(suggestion_id,judgment,reviewer,sample_fingerprint,sample) VALUES (%s,%s,%s,%s,%s)",
            (
                suggestion["id"],
                "correct",
                "Equipe",
                "sample",
                Jsonb({"name": PERSON["name"], "email": PERSON["email"]}),
            ),
        )
        search_id = uuid4()
        conn.execute(
            "INSERT INTO outbound_searches(id,request_key,fingerprint,query,status,result) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                search_id,
                "search-private",
                "fp",
                Jsonb({"query": PERSON["email"]}),
                "done",
                Jsonb({"candidates": [PERSON]}),
            ),
        )
        conn.execute(
            "INSERT INTO outbound_promotions(search_id,candidate_id,lead_id,reviewer) VALUES (%s,%s,%s,%s)",
            (search_id, "candidate", lead_id, "Equipe"),
        )
        conn.execute(
            "INSERT INTO scoring_versions(weights,reviewer,note,evaluation) VALUES ('{}','Equipe','Avaliação de exemplo',%s)",
            (
                Jsonb(
                    {"current": {"cases": [{"name": PERSON["name"], "company": PERSON["company"]}]}}
                ),
            ),
        )
        service.audit(
            conn, None, "outbound_completed", {"search_id": str(search_id), "candidate": PERSON}
        )
    plan = api_client.get(f"/api/leads/{lead_id}/erasure-preview").json()
    assert plan["counts"]["calibracoes"] == 1 and plan["counts"]["pesquisas_outbound"] == 1
    declined = api_client.post(
        f"/api/leads/{lead_id}/erase",
        json={"fingerprint": plan["fingerprint"], "confirmation": "NÃO"},
    )
    assert declined.status_code == 400
    result = api_client.post(
        f"/api/leads/{lead_id}/erase",
        json={"fingerprint": plan["fingerprint"], "confirmation": "ELIMINAR"},
    )
    assert (
        result.status_code == 200
        and result.json()["deleted"]
        and result.json()["remote_writes"] == 0
    )
    assert api_client.get(f"/api/leads/{lead_id}").status_code == 404
    with database() as conn:
        assert service.get_lead(conn, other)["name"] == "Rafael Preservado"
        for table in (
            "leads",
            "suggestions",
            "events",
            "jobs",
            "requests",
            "syncs",
            "calibration_reviews",
            "outbound_searches",
            "outbound_promotions",
            "erasure_blocks",
            "erasure_receipts",
            "scoring_versions",
        ):
            dump = json.dumps(
                conn.execute(f"SELECT row_to_json(t) AS row FROM {table} t").fetchall(),
                ensure_ascii=False,
                default=str,
            ).casefold()
            for original in (
                PERSON["name"],
                PERSON["email"],
                PERSON["data"]["linkedin_person"],
                lead_id,
            ):
                assert original.casefold() not in dump, (table, original)
        assert conn.execute("SELECT count(*) AS n FROM erasure_blocks").fetchone()["n"] >= 4
    for payload, key in [
        (PERSON, "privacy-first"),
        ({"email": PERSON["email"]}, "new-key"),
        ({"name": PERSON["name"], "company": PERSON["company"]}, "name-key"),
        (
            {
                "company": "Nova Empresa",
                "data": {"linkedin_person": PERSON["data"]["linkedin_person"]},
            },
            "social-key",
        ),
        (
            {
                "company": "Outra Empresa",
                "data": {
                    "linkedin_person": "https://br.linkedin.com/in/marina-privacidade/?trk=public"
                },
            },
            "alternate-social-key",
        ),
    ]:
        response = api_client.post("/api/captures", json=payload, headers={"Idempotency-Key": key})
        assert response.status_code == 410 and response.json()["erased"]
    assert api_client.post(
        "/api/privacy/check",
        json={"items": [PERSON, {"name": "Rafael Preservado", "company": "Empresa Independente"}]},
    ).json() == {"blocked": [0]}


def test_stale_preview_does_not_erase_changed_contact(database):
    lead_id = capture(database)
    with database() as conn:
        plan = privacy.preview(conn, lead_id)
        service.enqueue(conn, [lead_id])
    with database() as conn, pytest.raises(service.Conflict, match="mudaram"):
        privacy.erase(conn, lead_id, plan["fingerprint"], "ELIMINAR")
    with database() as conn:
        assert service.get_lead(conn, lead_id)["email"] == PERSON["email"]


def test_restart_does_not_repopulate_empty_erased_database(database, monkeypatch):
    from fastapi.testclient import TestClient

    from radar.api import app

    lead_id = capture(database)
    with database() as conn:
        plan = privacy.preview(conn, lead_id)
        privacy.erase(conn, lead_id, plan["fingerprint"], "ELIMINAR")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("RADAR_ACCESS_TOKEN", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/leads").json() == []
        assert client.get("/api/privacy/overview").json()["erasure_count"] == 1


def test_worker_cannot_recreate_contact_erased_while_network_request_was_in_flight(
    database, monkeypatch
):
    lead_id = capture(database)
    with database() as conn:
        conn.execute(
            "UPDATE settings SET value=%s WHERE key='sources'",
            (Jsonb({"website": True, "backfill": True}),),
        )
        service.enqueue(conn, [lead_id])
    called = []

    def fetch(url):
        called.append(url)
        with database() as conn:
            plan = privacy.preview(conn, lead_id)
            privacy.erase(conn, lead_id, plan["fingerprint"], "ELIMINAR")
        return url, "<title>Empresa Privacidade</title><p>Marina Privacidade, Diretora.</p>"

    monkeypatch.setattr(research.sources, "fetch_public", fetch)
    assert worker.run_one()
    assert len(called) == 1
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM leads").fetchone()
        assert not conn.execute("SELECT 1 FROM suggestions").fetchone()
        assert not conn.execute("SELECT 1 FROM jobs").fetchone()
        assert conn.execute("SELECT count(*) AS n FROM erasure_receipts").fetchone()["n"] == 1
