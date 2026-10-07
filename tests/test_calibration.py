"""Configuration and review workflows, measured against independently known outcomes."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from radar import backfill, calibration, research, scoring, service, worker


def seed(database, *, official=True, kind="fact", demo=False):
    with database() as conn:
        suffix = uuid4().hex[:8]
        lead_id = service.capture(
            conn,
            {
                "name": "Marina Teste",
                "company": "Engenharia Fictícia " + suffix,
                "email": "",
                "data": {"website": "https://empresa.invalid"},
            },
            suffix,
            demo=demo,
        )["id"]
        lead = service.get_lead(conn, lead_id)
        url = "https://empresa.invalid/equipe" if official else "https://noticias.invalid/perfil"
        doc = research.document(
            url, f"<title>{lead['company']}</title><p>Marina Teste, Diretora técnica.</p>", lead
        )
        field = "role" if kind == "fact" else "product_fit"
        value = "Diretora técnica" if kind == "fact" else "Hipótese inicial · Eberick"
        proposal = research.candidate(
            lead, field, value, doc, "Marina Teste, Diretora técnica.", kind=kind
        )
        suggestion = service.add_suggestion(conn, lead, proposal)
    return lead_id, str(suggestion["id"])


def get_sample(client, suggestion_id):
    return next(
        item
        for item in client.get("/api/calibration/samples").json()["items"]
        if item["id"] == suggestion_id
    )


def annotate(client, suggestion_id, judgment):
    item = get_sample(client, suggestion_id)
    result = client.post(
        f"/api/calibration/samples/{suggestion_id}/review",
        json={
            "fingerprint": item["fingerprint"],
            "expected_review_id": item["last_review_id"],
            "judgment": judgment,
            "reviewer": "Revisão sintética de teste",
            "note": "Resultado conhecido no caso de teste.",
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def draft(client, **overrides):
    return {**client.get("/api/scoring").json()["weights"], **overrides}


def publish(client, weights):
    preview = client.post("/api/scoring/preview", json={"weights": weights})
    assert preview.status_code == 200, preview.text
    result = client.post(
        "/api/scoring/publish",
        json={
            "weights": weights,
            "fingerprint": preview.json()["fingerprint"],
            "reviewer": "QA de pesos",
            "note": "Ensaio local, não validação comercial.",
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_measured_errors_and_good_values_before_after_match_known_cases(database, api_client):
    policy = {**backfill.DEFAULT_POLICY, "threshold": 75}
    api_client.put("/api/backfill/policy", json=policy).raise_for_status()
    cases = [
        (True, "fact", "correct"),
        (False, "fact", "incorrect"),
        (True, "inference", "correct"),
        (False, "inference", "correct"),
        (True, "fact", "unknown"),
    ]
    for official, kind, answer in cases:
        _, item = seed(database, official=official, kind=kind)
        annotate(api_client, item, answer)
    before_settings = api_client.get("/api/scoring").json()
    before_leads = api_client.get("/api/leads").json()
    plan = api_client.post(
        "/api/scoring/preview", json={"weights": draft(api_client, source_other=10, inference=15)}
    ).json()
    assert plan["unconfirmed"] == 1
    assert plan["current"]["overall"] == {
        "reviewed": 4,
        "passed": 3,
        "correct_passed": 2,
        "incorrect_passed": 1,
        "correct_held": 1,
        "incorrect_held": 0,
        "accuracy": 66.7,
        "coverage": 75.0,
        "correct_recovery": 66.7,
    }
    assert plan["proposed"]["overall"] == {
        "reviewed": 4,
        "passed": 2,
        "correct_passed": 2,
        "incorrect_passed": 0,
        "correct_held": 1,
        "incorrect_held": 1,
        "accuracy": 100.0,
        "coverage": 50.0,
        "correct_recovery": 66.7,
    }
    assert plan["current"]["fields"]["role"]["accuracy"] == 50.0
    assert plan["proposed"]["fields"]["role"]["accuracy"] == 100.0
    assert api_client.get("/api/scoring").json() == before_settings
    assert api_client.get("/api/leads").json() == before_leads


def test_crm_approval_is_not_a_correctness_vote_and_original_edit_is_preserved(
    database, api_client
):
    lead_id, item = seed(database)
    edited = api_client.post(
        f"/api/suggestions/{item}/review",
        json={
            "action": "approve",
            "value": "Gerente de projetos",
            "note": "Cargo corrigido sem nova fonte",
        },
    )
    assert edited.status_code == 409
    api_client.post(
        f"/api/suggestions/{item}/review", json={"action": "approve"}
    ).raise_for_status()
    sample = get_sample(api_client, item)
    assert sample["value"] == sample["current_value"] == "Diretor"
    plan = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    assert plan["current"]["overall"]["reviewed"] == 0
    assert plan["current"]["overall"]["accuracy"] is None
    annotate(api_client, item, "incorrect")
    assert api_client.get(f"/api/leads/{lead_id}").json()["data"]["role"] == "Diretor"
    plan = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    assert plan["current"]["overall"]["incorrect_passed"] == 1


def test_publication_rescores_pending_keeps_past_approval_and_rejects_old_batch(
    database, api_client
):
    lead_a, pending = seed(database, kind="inference")
    lead_b, approved = seed(database)
    api_client.post(
        f"/api/suggestions/{approved}/review", json={"action": "approve"}
    ).raise_for_status()
    old_batch = api_client.get("/api/backfill/preview").json()
    old_record = api_client.get(f"/api/leads/{lead_b}").json()["suggestions"][0]
    weights = draft(api_client, inference=15)
    result = publish(api_client, weights)
    assert result["remote_writes"] == 0 and result["pending_rescored"] == 1
    detail = api_client.get(f"/api/leads/{lead_a}").json()
    record = next(row for row in detail["suggestions"] if row["id"] == pending)
    assert record["confidence"] == 81 and "product_fit" not in detail["data"]
    assert record["assessment"]["scoring_revision"] == result["revision"]
    approved_record = api_client.get(f"/api/leads/{lead_b}").json()["suggestions"][0]
    assert approved_record == old_record
    assert (
        api_client.post(
            "/api/backfill/apply", json={"fingerprint": old_batch["fingerprint"]}
        ).status_code
        == 409
    )
    with database() as conn:
        assert scoring.active(conn)["weights"]["inference"] == 15
        assert conn.execute("SELECT count(*) AS n FROM scoring_versions").fetchone()["n"] == 2
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM events WHERE action='scoring_published'"
            ).fetchone()["n"]
            == 1
        )


def test_new_collection_uses_published_weights_without_restart(database, api_client, monkeypatch):
    with database() as conn:
        lead_id = service.capture(
            conn,
            {
                "name": "Marina Teste",
                "company": "Empresa Fictícia",
                "data": {"website": "https://empresa.invalid"},
            },
            "worker-score",
        )["id"]
    original_revision = api_client.get("/api/scoring").json()["revision"]
    updated = False

    def fetch(url):
        nonlocal updated
        if not updated:
            publish(api_client, draft(api_client, inference=15))
            updated = True
        return url, "<title>Empresa Fictícia</title><p>Elaboramos projetos estruturais.</p>"

    monkeypatch.setattr(research.sources, "fetch_public", fetch)
    api_client.put("/api/sources", json={"website": True, "backfill": True}).raise_for_status()
    api_client.post("/api/scan", json={"ids": [lead_id]}).raise_for_status()
    assert worker.run_one()
    detail = api_client.get(f"/api/leads/{lead_id}").json()
    fit = next(row for row in detail["suggestions"] if row["field"] == "product_fit")
    assert fit["confidence"] == 81
    assert fit["assessment"]["scoring_revision"] > original_revision


@pytest.mark.parametrize("change", ["review", "policy", "weights"])
def test_publication_preview_expires_when_underlying_review_or_policy_changes(
    database, api_client, change
):
    _, item = seed(database)
    weights = draft(api_client, source_other=10)
    plan = api_client.post("/api/scoring/preview", json={"weights": weights}).json()
    if change == "review":
        annotate(api_client, item, "correct")
    elif change == "policy":
        api_client.put(
            "/api/backfill/policy", json={**backfill.DEFAULT_POLICY, "threshold": 85}
        ).raise_for_status()
    else:
        publish(api_client, draft(api_client, inference=15))
    result = api_client.post(
        "/api/scoring/publish",
        json={
            "weights": weights,
            "fingerprint": plan["fingerprint"],
            "reviewer": "QA",
            "note": "Prévia vencida",
        },
    )
    assert result.status_code == 409


def test_latest_human_vote_counts_once_and_stale_edits_conflict(database, api_client):
    _, item = seed(database)
    sample = get_sample(api_client, item)
    old_payload = {
        "fingerprint": sample["fingerprint"],
        "judgment": "correct",
        "reviewer": "Primeira pessoa",
        "note": "Confirmado",
        "expected_review_id": None,
    }
    api_client.post(f"/api/calibration/samples/{item}/review", json=old_payload).raise_for_status()
    assert (
        api_client.post(f"/api/calibration/samples/{item}/review", json=old_payload).status_code
        == 409
    )
    annotate(api_client, item, "incorrect")
    plan = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    assert plan["current"]["overall"]["reviewed"] == 1
    assert plan["current"]["overall"]["incorrect_passed"] == 1
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM calibration_reviews").fetchone()["n"] == 2


def test_two_publications_from_same_preview_cannot_silently_overwrite(database, api_client):
    seed(database)
    weights = draft(api_client, inference=15)
    plan = api_client.post("/api/scoring/preview", json={"weights": weights}).json()

    def save_once():
        try:
            with database() as conn:
                return calibration.publish(
                    conn, weights, plan["fingerprint"], "QA", "Publicação concorrente"
                )["revision"]
        except service.Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: save_once(), range(2)))
    assert len([value for value in values if isinstance(value, int)]) == 1
    assert values.count("conflict") == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_other": 31},
        {"identity_unresolved": 31},
        {"inference": 26},
        {"undated": 11},
        {"fact": 40},
        {"fact": -1},
        {"dated": True},
        {"fact": 2.5},
        {"invented": 9},
    ],
)
def test_invalid_weights_cannot_be_previewed_or_saved(api_client, overrides):
    weights = draft(api_client, **overrides)
    response = api_client.post("/api/scoring/preview", json={"weights": weights})
    assert response.status_code in {400, 422}
    assert len(api_client.get("/api/scoring").json()["history"]) == 1


def test_no_examples_or_no_passing_cases_never_show_fake_accuracy(database, api_client):
    empty = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    assert empty["proposed"]["overall"]["accuracy"] is None
    _, item = seed(database)
    annotate(api_client, item, "correct")
    api_client.put(
        "/api/backfill/policy", json={**backfill.DEFAULT_POLICY, "threshold": 100}
    ).raise_for_status()
    plan = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    assert plan["proposed"]["overall"]["accuracy"] is None
    assert plan["proposed"]["overall"]["correct_recovery"] == 0


def test_field_minimum_overrides_general_minimum_in_comparison(database, api_client):
    _, role = seed(database)
    _, fit = seed(database, kind="inference")
    annotate(api_client, role, "incorrect")
    annotate(api_client, fit, "correct")
    api_client.put(
        "/api/backfill/policy",
        json={**backfill.DEFAULT_POLICY, "threshold": 75, "field_thresholds": {"role": 95}},
    ).raise_for_status()
    plan = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    counts = plan["current"]["overall"]
    assert counts["passed"] == 1 and counts["correct_passed"] == 1
    assert counts["incorrect_held"] == 1 and counts["accuracy"] == 100
    thresholds = {row["field"]: row["threshold"] for row in plan["current"]["cases"]}
    assert thresholds == {"role": 95, "product_fit": 75}


def test_changed_evidence_excludes_old_vote_and_can_be_reviewed_again(database, api_client):
    _, item = seed(database)
    first = annotate(api_client, item, "correct")
    with database() as conn:
        row = conn.execute("SELECT assessment FROM suggestions WHERE id=%s", (item,)).fetchone()
        row["assessment"]["evidence"][0]["quote"] = "Correção da fonte: cargo não confirmado."
        conn.execute(
            "UPDATE suggestions SET assessment=%s WHERE id=%s", (Jsonb(row["assessment"]), item)
        )
    sample = get_sample(api_client, item)
    assert sample["review"] is None and sample["stale_review"]
    assert sample["last_review_id"] == first["id"]
    plan = api_client.post("/api/scoring/preview", json={"weights": draft(api_client)}).json()
    assert plan["current"]["overall"]["reviewed"] == 0
    second = annotate(api_client, item, "incorrect")
    assert second["id"] != first["id"]
    assert get_sample(api_client, item)["review"]["judgment"] == "incorrect"


def test_publishing_keeps_suppressed_and_demo_suggestions_unchanged(database, api_client):
    _, demo_item = seed(database, kind="inference", demo=True)
    real_id, real_item = seed(database, kind="inference")
    api_client.post(f"/api/leads/{real_id}/suppress").raise_for_status()
    result = publish(api_client, draft(api_client, inference=15))
    assert result["pending_rescored"] == 0
    with database() as conn:
        for suggestion in (demo_item, real_item):
            row = conn.execute(
                "SELECT confidence,assessment FROM suggestions WHERE id=%s", (suggestion,)
            ).fetchone()
            assert row["confidence"] == 76
            assert row["assessment"]["scoring_revision"] < result["revision"]


def test_demo_and_suppressed_contacts_do_not_pollute_calibration(database, api_client):
    demo_lead, demo_item = seed(database, demo=True)
    real_lead, real_item = seed(database)
    annotate(api_client, real_item, "correct")
    api_client.post(f"/api/leads/{real_lead}/suppress").raise_for_status()
    assert api_client.get("/api/calibration/samples").json()["items"] == []
    blocked = api_client.post(
        f"/api/calibration/samples/{demo_item}/review",
        json={"fingerprint": "none", "judgment": "correct", "reviewer": "QA"},
    )
    assert blocked.status_code == 400


def test_weight_changes_do_not_resurrect_rejected_suggestions(database, api_client):
    lead_id, item = seed(database, kind="inference")
    sample = get_sample(api_client, item)
    api_client.post(f"/api/suggestions/{item}/review", json={"action": "reject"}).raise_for_status()
    publish(api_client, draft(api_client, inference=15))
    with database() as conn:
        lead = service.get_lead(conn, lead_id)
        row = conn.execute("SELECT * FROM suggestions WHERE id=%s", (item,)).fetchone()
        assert service.add_suggestion(conn, lead, row) is None
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM suggestions WHERE lead_id=%s", (lead_id,)
            ).fetchone()["n"]
            == 1
        )
    assert get_sample(api_client, item)["fingerprint"] == sample["fingerprint"]
