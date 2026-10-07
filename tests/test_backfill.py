"""Backfill acceptance: HTML inputs, real PostgreSQL and observable HTTP writes.

Synthetic company pages exercise extraction without depending on model answers.
Public AltoQi excerpts are preserved in tests/fixtures/altoqi_backfill_public.json.
"""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from psycopg.types.json import Jsonb

from radar import backfill, research, research_ai, scoring, service, worker
from radar.validation import validate_value

SITE = "https://engenharia-exemplo.invalid"
LEAD = {
    "name": "Marina Souza",
    "company": "Engenharia Exemplo",
    "email": "",
    "data": {"website": SITE},
    "qualification": {},
    "demo": False,
    "suppressed": False,
}
PAGES = {
    SITE: '<title>Engenharia Exemplo</title><a href="/sobre">Quem somos</a><a href="/equipe">Equipe</a><a href="https://127.0.0.1/secret">Sobre</a>',
    SITE + "/sobre": """<title>Engenharia Exemplo</title><p>Elaboramos projetos estruturais.</p>
        <p>Desenvolvemos projetos de instalações.</p><h2>+ de 300</h2><p>colaboradores</p>
        <p>60 mil clientes e 25 projetistas em nossos projetos de demonstração.</p>
        <footer><a href="https://br.linkedin.com/company/engenharia-exemplo">LinkedIn</a></footer>""",
    SITE
    + "/equipe": "<title>Equipe · Engenharia Exemplo</title><h2>Marina Souza</h2><p>Diretora técnica</p><h2>Marina Silva</h2><p>Analista de projetos</p>",
}


def collect(lead=None, pages=None, config=None, **kwargs):
    documents = pages or PAGES
    calls = []

    def fetch(url):
        calls.append(url)
        return url, documents[url]

    proposals, report = research.collect(
        lead or copy.deepcopy(LEAD),
        config or backfill.DEFAULT_POLICY,
        score_config={"revision": 1, "weights": scoring.bootstrap_weights()},
        fetch=fetch,
        **kwargs,
    )
    return proposals, report, calls


def prepare(database, *, data=None):
    with database() as conn:
        contact = service.capture(
            conn,
            {**LEAD, "data": {**LEAD["data"], **(data or {})}, "hubspot_id": "backfill-test-1"},
            "backfill-test",
        )
        if data:
            # Existing CRM values are retained for the replacement/conflict scenario.
            conn.execute(
                "UPDATE leads SET data=data || %s WHERE id=%s", (Jsonb(data), contact["id"])
            )
        lead = service.get_lead(conn, contact["id"])
        for item in collect(lead)[0]:
            service.add_suggestion(conn, lead, item)
    return contact["id"]


def save_policy(client, **kwargs):
    response = client.put("/api/backfill/policy", json={**backfill.DEFAULT_POLICY, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def test_discovery_produces_grounded_role_range_and_initial_fit_without_consultant():
    proposals, report, calls = collect()
    actual = {item["field"]: item["value"] for item in proposals}
    assert actual == {
        "role": "Diretora técnica",
        "decision_role": "Provável decisor",
        "employee_range": "300+",
        "product_fit": "Hipótese inicial · Eberick + Builder",
        "segment": "Escritório de projetos",
        "linkedin": "https://www.linkedin.com/company/engenharia-exemplo",
    }
    assert calls == [SITE, SITE + "/sobre", SITE + "/equipe"]
    assert len(report["pages"]) == 3 and report["ai_used"] is False
    fit = next(item for item in proposals if item["field"] == "product_fit")
    assert fit["assessment"]["kind"] == "inference"
    assert "Impulso" not in fit["value"]
    assert fit["assessment"]["playbook"].startswith("https://solution-playbook")
    role = next(item for item in proposals if item["field"] == "role")
    assert role["confidence"] > fit["confidence"]
    assert role["assessment"]["evidence"][0]["quote"] == "Marina Souza\nDiretora técnica"
    assert role["assessment"]["evidence"][0]["url"] == SITE + "/equipe"


@pytest.mark.parametrize(
    "text",
    [
        "Nossos clientes têm 900 funcionários.",
        "Temos 12 vagas para 40 funcionários.",
        "Temos 45 projetistas e 200 clientes.",
        "Capital social de 500000 reais.",
        "350 colaboradores da Exemplo e da Parceira.",
        "Entre 500 e 200 empregados.",
    ],
)
def test_unrelated_or_ambiguous_quantities_do_not_become_headcount(text):
    pages = {SITE: "<title>Engenharia Exemplo</title><p>" + text + "</p>"}
    proposals, _, _ = collect(pages=pages)
    assert not any(item["field"] in {"employee_count", "employee_range"} for item in proposals)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Temos 45 funcionários.", ("employee_count", 45)),
        ("Nossa equipe tem entre 201 a 500 funcionários.", ("employee_range", "201–500")),
        ("Contamos com mais de 300 colaboradores.", ("employee_range", "300+")),
    ],
)
def test_exact_counts_and_intervals_keep_their_units(text, expected):
    proposals, _, _ = collect(pages={SITE: "<title>Engenharia Exemplo</title><p>" + text + "</p>"})
    assert [(item["field"], item["value"]) for item in proposals] == [expected]


def test_software_supplier_is_not_misclassified_as_its_customers():
    pages = {
        SITE: """<title>Engenharia Exemplo</title><p>Desenvolvemos software para engenharia.</p>
        <p>Nossos clientes elaboram projetos estruturais e gestão de obras.</p>"""
    }
    proposals, _, _ = collect(pages=pages)
    values = {item["field"]: item["value"] for item in proposals}
    assert values["segment"] == "Outro"
    assert values["product_fit"] == "Fora do ICP direto · fornecedor de software para engenharia"


def test_namesakes_and_wrong_company_cannot_produce_roles():
    wrong_person = {**LEAD, "name": "Marina Oliveira"}
    assert not any(item["field"] in {"role", "decision_role"} for item in collect(wrong_person)[0])
    wrong_company = {**LEAD, "company": "Outra Engenharia"}
    assert collect(wrong_company)[0] == []


def test_page_budget_is_enforced_on_actual_fetches():
    _, report, calls = collect(config={**backfill.DEFAULT_POLICY, "max_pages": 1})
    assert calls == [SITE] and len(report["pages"]) == 1


def test_thresholds_recompute_then_apply_a_real_transaction(api_client, database):
    lead_id = prepare(database)
    initial = api_client.get("/api/backfill/preview").json()
    assert (
        initial["eligible_count"] == 3
    )  # cargo, faixa e LinkedIn; nenhum fit/cargo decisor confirmado.
    assert {row["field"] for row in initial["candidates"] if row["eligible"]} == {
        "role",
        "employee_range",
        "linkedin",
    }
    save_policy(api_client, threshold=75, field_thresholds={"decision_role": 90})
    stale = api_client.post("/api/backfill/apply", json={"fingerprint": initial["fingerprint"]})
    assert stale.status_code == 409
    plan = api_client.get("/api/backfill/preview").json()
    assert plan["eligible_count"] == 5
    response = api_client.post("/api/backfill/apply", json={"fingerprint": plan["fingerprint"]})
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 5 and response.json()["remote_writes"] == 0
    stored = api_client.get(f"/api/leads/{lead_id}").json()
    assert stored["data"]["role"] == "Diretor"
    assert stored["data"]["employee_range"] == "300+"
    assert "employee_count" not in stored["data"] and "decision_role" not in stored["data"]
    assert len([event for event in stored["events"] if event["action"] == "approved"]) == 5
    replay = api_client.post("/api/backfill/apply", json={"fingerprint": plan["fingerprint"]})
    assert replay.status_code == 409
    assert api_client.get("/api/backfill/preview").json()["eligible_count"] == 0


@pytest.mark.parametrize("threshold,eligible", [(75, True), (76, True), (77, False)])
def test_inclusive_threshold_boundary(api_client, database, threshold, eligible):
    prepare(database)
    save_policy(api_client, threshold=threshold)
    plan = api_client.get("/api/backfill/preview").json()
    row = next(row for row in plan["candidates"] if row["field"] == "decision_role")
    assert row["confidence"] == 76 and row["eligible"] is eligible


def test_existing_values_and_disagreeing_sources_require_individual_review(api_client, database):
    lead_id = prepare(database, data={"role": "Engenheira"})
    with database() as conn:
        service.add_suggestion(
            conn,
            service.get_lead(conn, lead_id),
            {
                "field": "employee_range",
                "value": "40–60",
                "confidence": 99,
                "source_kind": "consultant",
                "source_url": "",
                "evidence": "Equipe relatou faixa diferente na conversa.",
            },
        )
    save_policy(api_client, threshold=0)
    plan = api_client.get("/api/backfill/preview").json()
    rows = {row["field"]: row for row in plan["candidates"]}
    assert rows["role"]["eligible"] is False
    assert rows["employee_range"]["eligible"] is False
    assert "diferentes" in " ".join(rows["employee_range"]["reasons"])
    applied = api_client.post("/api/backfill/apply", json={"fingerprint": plan["fingerprint"]})
    assert applied.status_code == 200
    stored = api_client.get(f"/api/leads/{lead_id}").json()
    assert stored["data"]["role"] == "Engenheira" and "employee_range" not in stored["data"]


@pytest.mark.parametrize("days", [100, -2])
def test_publication_date_blocks_recent_collection_even_individual_approval(
    api_client, database, days
):
    lead_id = prepare(database)
    published = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    with database() as conn:
        row = conn.execute(
            "SELECT * FROM suggestions WHERE lead_id=%s AND field='role'", (lead_id,)
        ).fetchone()
        row["assessment"]["evidence"][0]["published_at"] = published
        conn.execute(
            "UPDATE suggestions SET assessment=%s WHERE id=%s",
            (Jsonb(row["assessment"]), row["id"]),
        )
    plan = api_client.get("/api/backfill/preview").json()
    assert not next(item for item in plan["candidates"] if item["field"] == "role")["eligible"]
    response = api_client.post(f"/api/suggestions/{row['id']}/review", json={"action": "approve"})
    assert response.status_code == 409 and "Publicação" in response.text


def test_changed_identity_invalidates_approval_and_remote_preview(api_client, database):
    lead_id = prepare(database)
    plan = api_client.get("/api/backfill/preview").json()
    api_client.post(
        "/api/backfill/apply", json={"fingerprint": plan["fingerprint"]}
    ).raise_for_status()
    with database() as conn:
        conn.execute("UPDATE leads SET name='Outra Pessoa' WHERE id=%s", (lead_id,))
    remote_plan = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    assert remote_plan["approved"] == {}


def test_fresh_research_does_not_resurrect_rejection_or_multiply_candidates(api_client, database):
    lead_id = prepare(database)
    detail = api_client.get(f"/api/leads/{lead_id}").json()
    item = next(row for row in detail["suggestions"] if row["field"] == "role")
    api_client.post(
        f"/api/suggestions/{item['id']}/review", json={"action": "reject"}
    ).raise_for_status()
    with database() as conn:
        lead = service.get_lead(conn, lead_id)
        for proposal in collect(lead)[0]:
            service.add_suggestion(conn, lead, proposal)
    refreshed = api_client.get(f"/api/leads/{lead_id}").json()
    assert len(refreshed["suggestions"]) == len(detail["suggestions"])
    assert (
        next(row for row in refreshed["suggestions"] if row["field"] == "role")["status"]
        == "rejected"
    )


@pytest.mark.parametrize("domain", ["gmail.com", "outlook.com", "yahoo.com"])
def test_personal_email_can_be_captured_and_researched(
    api_client, database, monkeypatch, personal_email_dns, domain
):
    response = api_client.post(
        "/api/captures",
        headers={"Idempotency-Key": "personal-email-research"},
        json={
            "name": LEAD["name"],
            "company": LEAD["company"],
            "data": LEAD["data"],
            "email": "marina.ficticia@" + domain,
        },
    )
    assert response.status_code == 200, response.text
    ident = response.json()["id"]
    detail = api_client.get(f"/api/leads/{ident}").json()
    assert detail["identity_checks"]["email"]["status"] == "active_domain"
    assert not detail["identity_checks"]["email"]["mailbox_verified"]
    assert len(personal_email_dns) == 1
    pages = []

    def fetch(url):
        pages.append(url)
        return url, PAGES[url]

    monkeypatch.setattr(research.sources, "fetch_public", fetch)
    api_client.put("/api/sources", json={"website": True, "backfill": True}).raise_for_status()
    assert api_client.post("/api/scan", json={"ids": [ident]}).json()["queued"] == 1
    assert worker.run_one()
    detail = api_client.get(f"/api/leads/{ident}").json()
    assert detail["jobs"][0]["status"] == "done"
    role = next(s for s in detail["suggestions"] if s["field"] == "role")
    assert role["source_url"] == SITE + "/equipe" and role["status"] == "pending"
    assert "Marina Souza" in role["evidence"] and role["confidence"] > 0
    assert SITE + "/equipe" in pages
    assert "role" not in detail["data"]


def test_worker_autofills_only_when_enabled_and_honors_suppression(
    api_client, database, monkeypatch
):
    lead_id = prepare(database)
    monkeypatch.setattr(research.sources, "fetch_public", lambda url: (url, PAGES[url]))
    api_client.put("/api/sources", json={"website": True, "backfill": True}).raise_for_status()
    api_client.post("/api/scan", json={"ids": [lead_id]}).raise_for_status()
    assert worker.run_one()
    assert "role" not in api_client.get(f"/api/leads/{lead_id}").json()["data"]
    save_policy(api_client, threshold=75, auto_fill_empty=True)
    api_client.post("/api/scan", json={"ids": [lead_id]}).raise_for_status()
    assert worker.run_one()
    detail = api_client.get(f"/api/leads/{lead_id}").json()
    assert detail["jobs"][0]["result"]["auto_filled"] == 6
    assert detail["data"]["decision_role"] == "Provável decisor"
    api_client.post("/api/scan", json={"ids": [lead_id]}).raise_for_status()
    assert worker.run_one()
    detail = api_client.get(f"/api/leads/{lead_id}").json()
    assert detail["jobs"][0]["result"]["auto_filled"] == 0
    assert len([row for row in detail["events"] if row["action"] == "approved"]) == 6
    api_client.post(f"/api/leads/{lead_id}/suppress").raise_for_status()
    assert api_client.post("/api/scan", json={"ids": [lead_id]}).json()["queued"] == 0


def test_two_competing_batch_requests_cannot_approve_twice(api_client, database):
    prepare(database)
    plan = api_client.get("/api/backfill/preview").json()

    def apply_once():
        try:
            with database() as conn:
                return backfill.apply(conn, plan["fingerprint"])["applied"]
        except service.Conflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply_once(), range(2)))
    assert results.count(3) == 1 and results.count("stale") == 1
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM events WHERE action='approved'").fetchone()["n"]
            == 3
        )


def test_backfilled_range_and_inferences_reach_the_captured_http_payload(
    api_client, database, remote, monkeypatch
):
    lead_id = prepare(database)
    save_policy(api_client, threshold=75)
    plan = api_client.get("/api/backfill/preview").json()
    api_client.post(
        "/api/backfill/apply", json={"fingerprint": plan["fingerprint"]}
    ).raise_for_status()
    mapping = {
        "role": "jobtitle",
        "employee_range": "radar_employee_range",
        "decision_role": "radar_decision_role",
        "product_fit": "radar_product_fit",
        "segment": "radar_segment",
        "linkedin": "radar_linkedin",
    }
    monkeypatch.setenv("HUBSPOT_FIELD_MAP", json.dumps(mapping))
    remote["properties"] = {}
    remote["allowed_properties"] = list(mapping.values())
    outgoing = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    response = api_client.post(
        f"/api/leads/{lead_id}/sync",
        json={"fingerprint": outgoing["fingerprint"], "simulate": False},
    )
    assert response.status_code == 200, response.text
    patch = next(call for call in remote["http_requests"] if call["method"] == "PATCH")
    assert patch["body"]["properties"] == {
        "jobtitle": "Diretor",
        "radar_employee_range": "300+",
        "radar_decision_role": "Provável decisor",
        "radar_product_fit": "Hipótese inicial · Eberick + Builder",
        "radar_segment": "Escritório de projetos",
        "radar_linkedin": "https://www.linkedin.com/company/engenharia-exemplo",
    }
    assert "numberofemployees" not in patch["body"]["properties"]
    assert remote["properties"] == patch["body"]["properties"]


def test_ai_invented_quotes_and_wrong_person_roles_are_discarded(monkeypatch):
    monkeypatch.setattr(
        research_ai,
        "interpret",
        lambda *_: [
            {
                "field": "role",
                "value": "CEO",
                "document_id": 2,
                "quote": "Marina Souza é CEO da empresa.",
                "reason": "Inventado",
            },
            {
                "field": "role",
                "value": "Analista de projetos",
                "document_id": 2,
                "quote": "Marina Silva\nAnalista de projetos",
                "reason": "Pessoa errada",
            },
            {
                "field": "employee_count",
                "value": "300",
                "document_id": 1,
                "quote": "+ de 300\ncolaboradores",
                "reason": "Precisão falsa",
            },
            {
                "field": "employee_count",
                "value": "25",
                "document_id": 1,
                "quote": "25 projetistas em nossos projetos de demonstração.",
                "reason": "Unidade errada",
            },
        ],
    )
    proposals, report, _ = collect(use_ai=True)
    assert report["ai_used"] is True
    assert [item["value"] for item in proposals if item["field"] == "role"] == ["Diretora técnica"]
    assert not any(item["field"] == "employee_count" for item in proposals)


def test_web_search_uses_cited_sources_and_keeps_credential_server_side(monkeypatch):
    captured = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-secret")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")

    def endpoint(url, **kwargs):
        captured.append((url, kwargs))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "https://invented.invalid/",
                                "annotations": [{"type": "url_citation", "url": SITE}],
                            }
                        ],
                    },
                    {
                        "type": "web_search_call",
                        "action": {"sources": [{"url": SITE}, {"url": SITE + "/equipe"}]},
                    },
                ],
            },
        )

    monkeypatch.setattr(research_ai.httpx, "post", endpoint)
    assert research_ai.discover({**LEAD, "email": "not-shared@example.invalid"}) == [
        SITE,
        SITE + "/equipe",
    ]
    url, request = captured[0]
    assert url == "https://api.openai.com/v1/responses"
    assert request["json"]["tools"][0]["type"] == "web_search"
    assert request["json"]["max_tool_calls"] == 3 and request["json"]["store"] is False
    assert "not-shared" not in json.dumps(request["json"])
    assert "test-placeholder" not in json.dumps(request["json"])


def test_provider_errors_preserve_useful_non_ai_research(monkeypatch):
    def failed(*_):
        raise httpx.ReadTimeout("Sensitive body should never reach evidence")

    monkeypatch.setattr(research_ai, "interpret", failed)
    proposals, report, _ = collect(use_ai=True)
    assert report["ai_used"] is False and report["ai_requested"] is True
    assert "Sensitive" not in json.dumps(report)
    assert any(item["field"] == "product_fit" for item in proposals)


@pytest.mark.parametrize("value", ["500–201", "1000001+", "aproximadamente 50", "nan", "-1–40"])
def test_invalid_ranges_are_rejected(value):
    with pytest.raises(ValueError):
        validate_value("employee_range", value)


def test_equivalent_social_urls_do_not_create_artificial_conflicts():
    html = """<title>Engenharia Exemplo</title>
    <a href="https://instagram.com/engenharia-exemplo/">Instagram</a>
    <a href="https://www.instagram.com/engenharia-exemplo?ref=site">Instagram</a>"""
    proposals, _, _ = collect(pages={SITE: html})
    assert [(item["field"], item["value"]) for item in proposals] == [
        ("instagram", "https://www.instagram.com/engenharia-exemplo")
    ]


def test_site_cnpj_requires_registry_confirmation_while_role_can_be_approved(api_client, database):
    pages = {
        SITE: """<title>Engenharia Exemplo</title><h2>Marina Souza</h2><p>Diretora técnica</p>
        <footer>CNPJ: 12.345.678/0001-95</footer>"""
    }
    with database() as conn:
        lead_id = service.capture(conn, LEAD, "cnpj-discovery")["id"]
        lead = service.get_lead(conn, lead_id)
        for proposal in collect(lead, pages=pages)[0]:
            service.add_suggestion(conn, lead, proposal)
    plan = api_client.get("/api/backfill/preview").json()
    assert plan["eligible_count"] == 1
    assert not next(item for item in plan["candidates"] if item["field"] == "cnpj")["eligible"]
    result = api_client.post("/api/backfill/apply", json={"fingerprint": plan["fingerprint"]})
    assert result.status_code == 200, result.text
    approved = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()["approved"]
    assert approved == {"role": "Diretor"}


def test_expired_conflicting_candidate_does_not_block_current_evidence(api_client, database):
    lead_id = prepare(database)
    with database() as conn:
        lead = service.get_lead(conn, lead_id)
        proposal = next(item for item in collect(lead)[0] if item["field"] == "role")
        proposal["value"] = "Analista"
        proposal["assessment"]["evidence"][0]["published_at"] = "2020-01-01"
        service.add_suggestion(conn, lead, proposal)
    rows = api_client.get("/api/backfill/preview").json()["candidates"]
    assert next(row for row in rows if row["field"] == "role" and row["value"] == "Diretor")[
        "eligible"
    ]
    assert not next(row for row in rows if row["field"] == "role" and row["value"] == "Analista")[
        "eligible"
    ]


def test_suppression_during_network_collection_prevents_local_autofill(
    api_client, database, monkeypatch
):
    lead_id = prepare(database)
    save_policy(api_client, threshold=75, auto_fill_empty=True)
    api_client.put("/api/sources", json={"website": True, "backfill": True}).raise_for_status()

    def fetch(url):
        api_client.post(f"/api/leads/{lead_id}/suppress").raise_for_status()
        return url, PAGES[url]

    monkeypatch.setattr(research.sources, "fetch_public", fetch)
    api_client.post("/api/scan", json={"ids": [lead_id]}).raise_for_status()
    assert worker.run_one()
    detail = api_client.get(f"/api/leads/{lead_id}").json()
    assert "role" not in detail["data"]
    assert not any(item["status"] == "approved" for item in detail["suggestions"])
