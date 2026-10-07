"""Actual PostgreSQL workflows and HTTP CRM failure/recovery scenarios."""

import csv
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
import pytest
from psycopg.types.json import Jsonb

from radar import data_standard, hubspot, sources, standard_sources, worker
from radar import qualification as qualification_module
from radar.importer import import_rows
from radar.qualification import qualify
from radar.service import (
    Conflict,
    add_suggestion,
    capture,
    enqueue,
    get_lead,
    review,
    suppress,
)


def lead_payload(**changes):
    payload = {
        "name": "Marina Exemplo",
        "email": "marina@empresa-ficticia.invalid",
        "company": "Construtora Horizonte Fictícia",
        "hubspot_id": "contact-7",
        "data": {
            "role": "Engenheiro",
            "cnpj": "12345678000195",
            "website": "https://empresa-ficticia.invalid",
        },
    }
    payload.update(changes)
    return payload


def make_lead(database, **changes):
    with database() as conn:
        payload = lead_payload(**changes)
        lead_id = capture(conn, payload, "capture-first")["id"]
        # A pre-existing CRM snapshot, not a newly verified import. These scenarios
        # exercise replacements and remote drift from an already-populated base.
        conn.execute(
            "UPDATE leads SET data=%s,baseline=%s WHERE id=%s",
            (Jsonb(payload["data"]), Jsonb(payload["data"]), lead_id),
        )
        return lead_id


def propose(database, lead_id, field="role", value="Coordenador", source="website"):
    with database() as conn:
        lead = get_lead(conn, lead_id)
        if field == "role":
            url = (
                "https://caubr.gov.br/perfil"
                if source == "council"
                else "https://empresa-ficticia.invalid/equipe"
            )
            html = f"<title>{lead['company']}</title><p>{lead['name']}, {value}.</p>"
            proposals = standard_sources.from_url(
                lead, url, data_standard.config(conn), fetch=lambda url: (url, html)
            )
            proposal = next(item for item in proposals if item["field"] == "role")
            proposal["source_kind"] = source
        else:
            proposal = {
                "field": field,
                "value": value,
                "source_kind": source,
                "source_url": "https://empresa-ficticia.invalid/equipe",
                "evidence": f"A fonte da empresa fictícia informa {field}: {value}.",
                "confidence": 85,
            }
        return add_suggestion(conn, lead, proposal)["id"]


def approve(database, suggestion_id, **kwargs):
    with database() as conn:
        return review(conn, suggestion_id, "approve", "revisora-teste", **kwargs)


def plan_for(database, lead_id):
    with database() as conn:
        return hubspot.preview(conn, lead_id)


def exported_rows(api_client):
    from test_transfers import drain

    response = api_client.get("/api/export.csv")
    assert response.status_code == 202
    job = drain(api_client, response.json()["id"])
    response = api_client.get(job["download_url"])
    assert response.status_code == 200
    return list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))


def test_shared_token_requires_login_and_rejects_cross_origin_writes(api_client, monkeypatch):
    monkeypatch.setenv("RADAR_ACCESS_TOKEN", "test-placeholder-not-a-secret")
    assert api_client.get("/api/leads").status_code == 401
    assert api_client.get("/api/config").json()["auth_required"] is True
    assert api_client.post("/api/session", json={"token": "wrong"}).status_code == 401
    logged_in = api_client.post("/api/session", json={"token": "test-placeholder-not-a-secret"})
    assert logged_in.status_code == 200
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert api_client.get("/api/leads").status_code == 200
    external_write = api_client.post(
        "/api/captures",
        headers={"Origin": "https://external.invalid", "Idempotency-Key": "csrf"},
        json={"company": "Não deve ser criada"},
    )
    assert external_write.status_code == 403
    assert api_client.get("/api/leads").json() == []


def test_reconnected_capture_reuses_persisted_receipt_after_new_connection(database):
    payload = lead_payload()
    with database() as conn:
        first = capture(conn, payload, "phone-event-1")
    with database() as conn:
        replay = capture(conn, payload, "phone-event-1")
        assert replay == first
        assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 1
        assert (
            conn.execute("SELECT count(*) AS n FROM events WHERE action='created'").fetchone()["n"]
            == 1
        )
    with pytest.raises(Conflict, match="outro conteúdo"), database() as conn:
        capture(conn, dict(payload, name="Outra pessoa"), "phone-event-1")
    with database() as conn:
        assert get_lead(conn, first["id"])["name"] == "Marina Exemplo"


def test_ten_simultaneous_offline_retries_use_independent_transactions(database):
    barrier = threading.Barrier(10)

    def send(_):
        with database() as conn:
            barrier.wait(timeout=10)
            return capture(conn, lead_payload(), "phone-burst-1")

    with ThreadPoolExecutor(max_workers=10) as pool:
        receipts = list(pool.map(send, range(10)))
    assert all(item == receipts[0] for item in receipts)
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM requests").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 1


def test_http_capture_returns_same_receipt_and_409_on_changed_content(api_client):
    payload = {
        "name": "Marina Exemplo",
        "company": "Empresa Fictícia",
        "email": "marina@empresa-ficticia.invalid",
    }
    headers = {"Idempotency-Key": "mobile-http-event"}
    first = api_client.post("/api/captures", json=payload, headers=headers)
    replay = api_client.post("/api/captures", json=payload, headers=headers)
    collision = api_client.post(
        "/api/captures", json=dict(payload, name="Outra pessoa"), headers=headers
    )
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert collision.status_code == 409
    assert len(api_client.get("/api/leads").json()) == 1


def test_same_company_cnpj_does_not_merge_distinct_people(database):
    with database() as conn:
        first = capture(conn, lead_payload(hubspot_id=None), "person-1")
        second = capture(
            conn,
            lead_payload(
                name="Rafael Exemplo", email="rafael@empresa-ficticia.invalid", hubspot_id=None
            ),
            "person-2",
        )
        assert first["id"] != second["id"]
        assert (
            get_lead(conn, first["id"])["intake_data"]["cnpj"]
            == get_lead(conn, second["id"])["intake_data"]["cnpj"]
        )
        assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 2


def test_reimport_creates_review_instead_of_overwriting_current_crm_data(database):
    mapping = {"Nome": "name", "Email": "email", "Cargo": "role"}
    rows = [
        {
            "Nome": "Marina Exemplo",
            "Email": "marina@empresa-ficticia.invalid",
            "Cargo": "Engenheiro",
        }
    ]
    with database() as conn:
        original = import_rows(conn, rows, mapping, "batch-1")
    with database() as conn:
        replayed = import_rows(conn, rows, mapping, "batch-1")
        assert replayed["created"] == 0
        assert replayed["duplicates"] == 1
        assert replayed["ids"] == original["ids"]
        assert replayed["errors"] == []
    with database() as conn:
        again = import_rows(conn, [dict(rows[0], Cargo="Diretor")], mapping, "batch-2")
        lead = get_lead(conn, original["ids"][0])
        assert again["duplicates"] == 1
        assert "role" not in lead["data"]
        assert "role" not in lead["baseline"]
        assert lead["intake_data"]["role"] == "Diretor"
        assert conn.execute("SELECT value,status FROM suggestions").fetchone() == {
            "value": "Diretor",
            "status": "pending",
        }
        assert hubspot.preview(conn, lead["id"])["approved"] == {}


def test_pending_fields_cannot_be_synchronized_and_replacement_requires_explicit_choice(database):
    lead_id = make_lead(database)
    suggestion_id = propose(database, lead_id)
    plan = plan_for(database, lead_id)
    assert plan["approved"] == plan["properties"] == {}
    with pytest.raises(ValueError, match="Aprove"), database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"])
    with pytest.raises(Conflict, match="substituição"):
        approve(database, suggestion_id)
    with database() as conn:
        assert get_lead(conn, lead_id)["data"]["role"] == "Engenheiro"
    approve(database, suggestion_id, allow_replace=True)
    plan = plan_for(database, lead_id)
    assert plan["approved"] == {"role": "Coordenador"}
    assert plan["properties"] == {"jobtitle": "Coordenador"}


def test_csv_exports_only_reviewed_changes_and_omits_suppressed_contact(database, api_client):
    lead_id = make_lead(database)
    role_id = propose(database, lead_id)
    propose(database, lead_id, "website", "https://empresa-ficticia.invalid/novo")
    assert exported_rows(api_client) == []
    without_permission = api_client.post(
        f"/api/suggestions/{role_id}/review", json={"action": "approve"}
    )
    assert without_permission.status_code == 409
    accepted = api_client.post(
        f"/api/suggestions/{role_id}/review", json={"action": "approve", "allow_replace": True}
    )
    assert accepted.status_code == 200
    rows = exported_rows(api_client)
    assert len(rows) == 1
    assert rows[0]["role"] == "Coordenador"
    assert rows[0]["website"] == rows[0]["cnpj"] == ""
    response = api_client.post(f"/api/leads/{lead_id}/suppress")
    assert response.status_code == 200
    assert exported_rows(api_client) == []


def test_two_sources_are_preserved_and_a_racing_review_has_one_winner(database):
    lead_id = make_lead(database)
    first = propose(database, lead_id, value="Coordenador", source="website")
    second = propose(database, lead_id, value="Diretor", source="council")
    barrier = threading.Barrier(2)

    def decide(suggestion_id):
        try:
            with database() as conn:
                barrier.wait(timeout=10)
                review(conn, suggestion_id, "approve", "review-racer", allow_replace=True)
                return "approved"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, [first, second]))
    assert sorted(outcomes) == ["approved", "conflict"]
    with database() as conn:
        rows = conn.execute(
            "SELECT value,source_kind,evidence,status FROM suggestions ORDER BY status"
        ).fetchall()
        assert {row["source_kind"] for row in rows} == {"website", "council"}
        assert {row["status"] for row in rows} == {"approved", "superseded"}
        winner = next(row for row in rows if row["status"] == "approved")
        assert get_lead(conn, lead_id)["data"]["role"] == winner["value"]
        assert (
            conn.execute("SELECT count(*) AS n FROM events WHERE action='approved'").fetchone()["n"]
            == 1
        )


def test_suppression_blocks_capture_research_review_preview_and_sync(database, monkeypatch):
    lead_id = make_lead(database)
    suggestion_id = propose(database, lead_id)
    before = plan_for(database, lead_id)
    with database() as conn:
        enqueue(conn, [lead_id])
        suppress(conn, lead_id, "revisora-teste")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Um contato suprimido não pode iniciar coleta externa.")

    monkeypatch.setattr(sources, "registry_evidence", forbidden)
    monkeypatch.setattr(sources, "website_evidence", forbidden)
    assert worker.run_one() is False
    with database() as conn:
        assert enqueue(conn, [lead_id]) == {"queued": 0}
        assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "cancelled"
    for operation in (
        lambda conn: capture(conn, lead_payload(), "suppressed-recapture"),
        lambda conn: review(conn, suggestion_id, "approve", "reviewer", allow_replace=True),
        lambda conn: hubspot.preview(conn, lead_id),
        lambda conn: hubspot.sync(conn, lead_id, before["fingerprint"]),
    ):
        with pytest.raises(Conflict, match="exclusão"), database() as conn:
            operation(conn)


def test_suppression_during_collection_discards_result_before_storage(database, monkeypatch):
    lead_id = make_lead(database)
    with database() as conn:
        conn.execute(
            "UPDATE settings SET value=%s WHERE key='sources'", (Jsonb({"registry": True}),)
        )
        enqueue(conn, [lead_id])

    def collecting(_cnpj):
        with database() as conn:
            suppress(conn, lead_id, "opt-out-while-running")
        return [
            {
                "field": "capital_social",
                "value": 100000,
                "source_kind": "registry",
                "source_url": "https://cadastro-ficticio.invalid",
                "evidence": "Dado de teste obtido antes da conclusão.",
                "confidence": 95,
            }
        ]

    monkeypatch.setattr(standard_sources, "registry", lambda lead: (collecting(lead), []))
    assert worker.run_one() is True
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM suggestions").fetchone()["n"] == 0
        assert get_lead(conn, lead_id)["suppressed"] is True
        assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "cancelled"


def test_old_source_is_not_approved_or_exportable(database):
    lead_id = make_lead(database)
    suggestion_id = propose(database, lead_id)
    with database() as conn:
        conn.execute(
            "UPDATE suggestions SET observed_at=now()-interval '91 days' WHERE id=%s",
            (suggestion_id,),
        )
    with pytest.raises(Conflict, match="vencida"):
        approve(database, suggestion_id, allow_replace=True)
    assert plan_for(database, lead_id)["approved"] == {}


def test_fit_becomes_stale_before_review_and_again_after_context_changes(database):
    qualification = {"business_model": "builder", "absorbs_method": False}
    lead_id = make_lead(database, qualification=qualification)
    with database() as conn:
        original_fit = qualify(get_lead(conn, lead_id))
    assert original_fit["offers"] == ["BIM ONE"]
    stale_id = propose(database, lead_id, "product_fit", "BIM ONE", "playbook")
    with database() as conn:
        capture(
            conn,
            lead_payload(
                qualification={"absorbs_method": True, "building_work": True, "uses_bim": False}
            ),
            "new-discovery",
        )
    with pytest.raises(Conflict, match="qualificação mudou"):
        approve(database, stale_id)
    with database() as conn:
        changed_fit = qualify(get_lead(conn, lead_id))
    assert changed_fit["offers"] == ["Base Digital · Visus Start"]
    fresh_id = propose(database, lead_id, "product_fit", "Base Digital · Visus Start", "playbook")
    approve(database, fresh_id)
    assert plan_for(database, lead_id)["approved"] == {"product_fit": "Base Digital · Visus Start"}
    with database() as conn:
        capture(
            conn, lead_payload(qualification={"absorbs_method": False}), "discovery-changed-again"
        )
    assert plan_for(database, lead_id)["approved"] == {}


def test_partial_discovery_does_not_refresh_old_unanswered_qualification(database):
    lead_id = make_lead(
        database,
        qualification={"business_model": "builder", "absorbs_method": False},
    )
    with database() as conn:
        # A historical capture from before per-field timestamps existed.
        conn.execute(
            """UPDATE leads SET qualification=jsonb_build_object(
                'business_model','builder','absorbs_method',false,
                'observed_at',(now()-interval '181 days')::text) WHERE id=%s""",
            (lead_id,),
        )
    with database() as conn:
        capture(
            conn,
            lead_payload(qualification={"business_model": "builder", "pain": "quantities"}),
            "partial-discovery-today",
        )
        lead = get_lead(conn, lead_id)
        result = qualify(lead)
    assert lead["qualification"]["pain"] == "quantities"
    assert result["offers"] == []
    assert any("pessoa" in question for question in result["questions"])


def test_real_http_sync_reads_writes_confirms_and_replays_without_duplicate_patch(database, remote):
    lead_id = make_lead(database)
    approve(database, propose(database, lead_id), allow_replace=True)
    plan = plan_for(database, lead_id)
    with database() as conn:
        sent = hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert sent["status"] == "sent"
    assert [call[0] for call in remote["calls"]] == ["GET", "PATCH", "GET"]
    assert remote["properties"]["jobtitle"] == "Coordenador"
    with database() as conn:
        replay = hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
        assert conn.execute("SELECT count(*) AS n FROM syncs").fetchone()["n"] == 1
    assert replay["id"] == sent["id"] and replay["replayed"] is True
    assert sum(call[0] == "PATCH" for call in remote["calls"]) == 1
    assert remote["properties"]["jobtitle"] == "Coordenador"


def test_response_loss_after_remote_commit_is_reconciled_without_second_patch(database, remote):
    lead_id = make_lead(database)
    approve(database, propose(database, lead_id), allow_replace=True)
    plan = plan_for(database, lead_id)
    remote["drop_response_once"] = True
    with pytest.raises(httpx.HTTPError), database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert remote["properties"]["jobtitle"] == "Coordenador"
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM syncs").fetchone()["n"] == 0
        sent = hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert sent["status"] == "sent"
    assert sum(call[0] == "PATCH" for call in remote["calls"]) == 1
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM syncs WHERE status='sent'").fetchone()["n"] == 1
        )


def test_actual_remote_drift_prevents_patch_and_does_not_claim_sent(database, remote):
    lead_id = make_lead(database)
    approve(database, propose(database, lead_id), allow_replace=True)
    remote["properties"]["jobtitle"] = "Diretor atualizada por outro vendedor"
    plan = plan_for(database, lead_id)
    with pytest.raises(Conflict, match="mudou na HubSpot"), database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert [call[0] for call in remote["calls"]] == ["GET"]
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM syncs").fetchone()["n"] == 0


def test_success_response_without_matching_readback_is_not_recorded_as_sent(database, remote):
    lead_id = make_lead(database)
    approve(database, propose(database, lead_id), allow_replace=True)
    plan = plan_for(database, lead_id)
    remote["discard_patch_once"] = True
    with pytest.raises(Conflict, match="confirmou"), database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM syncs").fetchone()["n"] == 0
    assert [call[0] for call in remote["calls"]] == ["GET", "PATCH", "GET"]


def test_second_approved_change_after_successful_sync_is_not_false_remote_drift(database, remote):
    lead_id = make_lead(database)
    approve(database, propose(database, lead_id), allow_replace=True)
    plan = plan_for(database, lead_id)
    with database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    approve(database, propose(database, lead_id, value="Diretor"), allow_replace=True)
    next_plan = plan_for(database, lead_id)
    with database() as conn:
        hubspot.sync(conn, lead_id, next_plan["fingerprint"], simulate=False)
    assert remote["properties"]["jobtitle"] == "Diretor"
    assert [call[2] for call in remote["calls"] if call[0] == "PATCH"] == [
        {"jobtitle": "Coordenador"},
        {"jobtitle": "Diretor"},
    ]


def test_new_approval_can_return_crm_to_a_previously_synchronized_value(database, remote):
    lead_id = make_lead(database)
    results = []
    # The third observation is independently attributed, so each step has a new review.
    for value, source in (
        ("Coordenador", "website"),
        ("Diretor", "website"),
        ("Coordenador", "council"),
    ):
        approve(
            database, propose(database, lead_id, value=value, source=source), allow_replace=True
        )
        plan = plan_for(database, lead_id)
        with database() as conn:
            results.append(hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False))
        assert remote["properties"]["jobtitle"] == value
    assert [call[2] for call in remote["calls"] if call[0] == "PATCH"] == [
        {"jobtitle": "Coordenador"},
        {"jobtitle": "Diretor"},
        {"jobtitle": "Coordenador"},
    ]
    assert len({result["id"] for result in results}) == 3
    assert all(result["status"] == "sent" and not result["replayed"] for result in results)


def test_remote_reimport_invalidates_approval_made_before_external_edit(database, remote):
    lead_id = make_lead(database)
    approved_id = propose(database, lead_id, value="Coordenador")
    approve(database, approved_id, allow_replace=True)
    assert plan_for(database, lead_id)["approved"] == {"role": "Coordenador"}

    # A salesperson changes the CRM after our review but before its first synchronization.
    remote["properties"]["jobtitle"] = "Diretor"
    remote_value = hubspot.HubSpotClient().read_contact("contact-7", ["jobtitle"])["jobtitle"]
    row = {
        "name": "Marina Exemplo",
        "email": "marina@empresa-ficticia.invalid",
        "company": "Construtora Horizonte Fictícia",
        "hubspot_id": "contact-7",
        "role": remote_value,
    }
    with database() as conn:
        imported = import_rows(
            conn, [row], {key: key for key in row}, "fresh-crm-read", origin="hubspot"
        )
        assert imported["duplicates"] == 1 and imported["errors"] == []
        assert imported["ids"] == [lead_id]
        historical = conn.execute(
            "SELECT value FROM suggestions WHERE id=%s", (approved_id,)
        ).fetchone()
        assert historical["value"] == "Coordenador"
    plan = plan_for(database, lead_id)
    assert plan["approved"] == plan["properties"] == {}
    with pytest.raises(ValueError), database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert remote["properties"]["jobtitle"] == "Diretor"
    assert not any(call[0] == "PATCH" for call in remote["calls"])


def test_same_fit_value_can_be_reviewed_again_after_designer_count_changes(database, api_client):
    lead_id = make_lead(
        database,
        qualification={
            "business_model": "engineering_office",
            "building_design": True,
            "disciplines": ["structure"],
            "designers": 8,
            "pain": "management",
            "sponsor": True,
            "assisted_capacity": True,
        },
    )
    first_response = api_client.post(f"/api/leads/{lead_id}/fit")
    assert first_response.status_code == 200 and first_response.json()["created"] is True
    with database() as conn:
        first = conn.execute("SELECT * FROM suggestions WHERE field='product_fit'").fetchone()
    approve(database, first["id"])
    assert plan_for(database, lead_id)["approved"] == {"product_fit": first["value"]}

    with database() as conn:
        capture(conn, lead_payload(data={}, qualification={"designers": 9}), "nine-designers")
    assert plan_for(database, lead_id)["approved"] == {}
    second_response = api_client.post(f"/api/leads/{lead_id}/fit")
    assert second_response.status_code == 200 and second_response.json()["created"] is True
    with database() as conn:
        fresh = conn.execute(
            "SELECT * FROM suggestions WHERE field='product_fit' AND status='pending'"
        ).fetchone()
        assert fresh is not None and fresh["id"] != first["id"]
        assert fresh["value"] == first["value"]
        assert fresh["basis_fingerprint"] != first["basis_fingerprint"]
    assert plan_for(database, lead_id)["approved"] == {}
    approve(database, fresh["id"])
    assert plan_for(database, lead_id)["approved"] == {"product_fit": first["value"]}


def test_repeat_research_refreshes_expired_evidence_without_rewriting_its_history(
    database, monkeypatch
):
    lead_id = make_lead(
        database, data={"role": "Engenheiro", "website": "https://empresa-ficticia.invalid"}
    )
    collected = []

    def same_page(url):
        collected.append(url)
        return (
            [
                {
                    "field": "role",
                    "value": "Coordenador",
                    "source_kind": "website",
                    "source_url": url,
                    "evidence": "Marina Exemplo é Coordenador da empresa fictícia.",
                    "confidence": 85,
                    "standard_proof": data_standard.seal("role", "Coordenador", "website"),
                }
            ],
            url,
            "Marina Exemplo é Coordenador da empresa fictícia.",
        )

    monkeypatch.setattr(sources, "website_evidence", same_page)
    with database() as conn:
        conn.execute(
            "UPDATE settings SET value=%s WHERE key='sources'", (Jsonb({"website": True}),)
        )
        enqueue(conn, [lead_id])
    assert worker.run_one() is True
    with database() as conn:
        old_id = conn.execute("SELECT id FROM suggestions").fetchone()["id"]
        conn.execute(
            "UPDATE suggestions SET observed_at=now()-interval '200 days' WHERE id=%s", (old_id,)
        )
        historical = conn.execute("SELECT * FROM suggestions WHERE id=%s", (old_id,)).fetchone()
        enqueue(conn, [lead_id])
    assert worker.run_one() is True
    with database() as conn:
        fresh = conn.execute(
            "SELECT * FROM suggestions WHERE id<>%s AND status='pending'", (old_id,)
        ).fetchone()
        assert fresh is not None
        unchanged_history = conn.execute(
            "SELECT * FROM suggestions WHERE id=%s", (old_id,)
        ).fetchone()
        assert unchanged_history["observed_at"] == historical["observed_at"]
        assert unchanged_history["evidence"] == historical["evidence"]
        assert fresh["observed_at"] > historical["observed_at"]
        assert fresh["value"] == historical["value"]
    approve(database, fresh["id"], allow_replace=True)
    assert plan_for(database, lead_id)["approved"] == {"role": "Coordenador"}
    assert collected == ["https://empresa-ficticia.invalid"] * 2


def test_changed_identity_for_existing_hubspot_id_is_explicit_and_atomic(database):
    lead_id = make_lead(database)
    with database() as conn:
        original = get_lead(conn, lead_id)
    for index, identity_change in enumerate(
        (
            {"email": "novo-contato@empresa-ficticia.invalid"},
            {"name": "Rafael Exemplo"},
            {"company": "Outra Empresa Fictícia"},
        )
    ):
        payload = lead_payload(
            **identity_change, data={"role": "Projetista", "capital_social": 12345}
        )
        with pytest.raises(Conflict, match="(?i)identidade"), database() as conn:
            capture(conn, payload, f"changed-identity-{index}", origin="hubspot")
        with database() as conn:
            assert get_lead(conn, lead_id) == original
            assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 1
            assert conn.execute("SELECT count(*) AS n FROM suggestions").fetchone()["n"] == 0
            assert conn.execute("SELECT count(*) AS n FROM requests").fetchone()["n"] == 1
            assert conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 1


def test_fit_expires_when_its_context_ages_without_a_new_capture(database, monkeypatch):
    class QualificationDate(date):
        current = date(2026, 10, 3)

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(qualification_module, "date", QualificationDate)
    timestamp = (QualificationDate.current - timedelta(days=179)).isoformat() + "T09:00:00+00:00"
    context = {
        "business_model": "builder",
        "absorbs_method": False,
        "observed_at": timestamp,
        "_observed": {"business_model": timestamp, "absorbs_method": timestamp},
    }
    approved_lead = make_lead(database)
    with database() as conn:
        pending_lead = capture(
            conn,
            lead_payload(
                name="Rafael Exemplo",
                email="rafael@empresa-ficticia.invalid",
                hubspot_id="contact-8",
            ),
            "second-context-expiry",
        )["id"]
        conn.execute(
            "UPDATE leads SET qualification=%s WHERE id=ANY(%s::uuid[])",
            (Jsonb(context), [approved_lead, pending_lead]),
        )
    approved_id = propose(database, approved_lead, "product_fit", "BIM ONE", "playbook")
    pending_id = propose(database, pending_lead, "product_fit", "BIM ONE", "playbook")
    approve(database, approved_id)
    assert plan_for(database, approved_lead)["approved"] == {"product_fit": "BIM ONE"}
    with database() as conn:
        observations_before = conn.execute(
            "SELECT id,observed_at FROM suggestions ORDER BY id"
        ).fetchall()

    # Only the classifier's clock advances; no lead, source or review row is edited.
    QualificationDate.current += timedelta(days=2)
    assert plan_for(database, approved_lead)["approved"] == {}
    with pytest.raises(Conflict, match="qualificação mudou"):
        approve(database, pending_id)
    with database() as conn:
        assert get_lead(conn, approved_lead)["qualification"] == context
        assert get_lead(conn, pending_lead)["qualification"] == context
        assert (
            conn.execute("SELECT id,observed_at FROM suggestions ORDER BY id").fetchall()
            == observations_before
        )
