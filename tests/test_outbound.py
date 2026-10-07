"""Outbound journeys with real parsing, PostgreSQL and observable source boundaries."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from radar import data_standard, outbound, outbound_provider, outbound_research, scoring, service

HTML = """<html><title>Obras Fictícias</title><script type="application/ld+json">{"@type":"Organization","name":"Obras Fictícias"}</script><h1>Obras Fictícias</h1><p>Ana Oliveira</p><p>BIM Manager</p><p>Gerenciamos obras de edificação. Temos 45 funcionários.</p><a href="https://www.linkedin.com/company/obras-ficticias">LinkedIn</a><a href="https://www.linkedin.com/in/ana-oliveira">Ana Oliveira</a></html>"""
URL = "https://obras.invalid"


@pytest.fixture
def public_site(monkeypatch):
    calls = []
    dns_transport = outbound_research.sources.fetch_public

    def fetch(url):
        if url.startswith("https://dns.google/"):
            return dns_transport(url)
        calls.append(url)
        if url == URL:
            return url, HTML
        raise ValueError("Fonte de teste indisponível")

    monkeypatch.setattr(outbound_research.sources, "fetch_public", fetch)
    monkeypatch.setattr(outbound_provider, "configured", lambda: False)
    return calls


def start(client, **changes):
    query = {
        "mode": "role_company",
        "query": "BIM Manager",
        "company": "Obras Fictícias",
        "website": URL,
        **changes,
    }
    result = client.post("/api/outbound", headers={"Idempotency-Key": str(uuid4())}, json=query)
    assert result.status_code == 202, result.text
    return result.json()["id"]


def finish(client, search_id):
    assert outbound.run_one()
    result = client.get("/api/outbound/" + search_id).json()
    assert result["status"] == "done", result
    return result


def add(client, search_id, candidate):
    return client.post(
        f"/api/outbound/{search_id}/candidates/{candidate['id']}/add",
        json={
            "fingerprint": candidate["fingerprint"],
            "identity_confirmed": True,
            "reviewer": "Conferência de teste",
        },
    )


def test_direct_search_finds_person_enriches_and_preserves_review_boundary(
    database, api_client, public_site
):
    search_id = start(api_client)
    result = finish(api_client, search_id)
    assert api_client.get("/api/leads").json() == []
    assert len(result["result"]["candidates"]) == 1
    person = result["result"]["candidates"][0]
    assert person["name"] == "Ana Oliveira" and person["identity_verified"]
    values = {p["field"]: p["value"] for p in person["proposals"]}
    assert values["role"] == "BIM Manager"
    assert values["employee_count"] == 45
    assert values["decision_role"] == "Provável influenciador"
    assert "Visus" in values["product_fit"]
    response = add(api_client, search_id, person)
    assert response.status_code == 200, response.text
    lead = api_client.get("/api/leads/" + response.json()["id"]).json()
    assert lead["data"] == {"website": URL}
    assert lead["email"] == "" and lead["origin"] == "Prospecção outbound"
    assert all(s["status"] == "pending" for s in lead["suggestions"])
    assert all(s["confidence"] > 0 for s in lead["suggestions"])
    assert response.json()["remote_writes"] == 0
    role = next(s for s in lead["suggestions"] if s["field"] == "role")
    approved = api_client.post(
        "/api/suggestions/" + role["id"] + "/review", json={"action": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert api_client.get("/api/leads/" + lead["id"]).json()["data"]["role"] == "BIM Manager"
    repeated = add(api_client, search_id, person)
    assert repeated.status_code == 200 and repeated.json()["id"] == lead["id"]
    assert len(api_client.get("/api/leads").json()) == 1


def test_email_seed_enriches_company_without_inventing_a_person(database, api_client, public_site):
    result = finish(api_client, start(api_client, mode="email", query="contato@obras.invalid"))
    person = result["result"]["candidates"][0]
    assert person["name"] == "" and person["email"] == "contato@obras.invalid"
    assert person["email_only"] and not person["identity_verified"]
    assert "role" not in {p["field"] for p in person["proposals"]}
    assert next(p["value"] for p in person["proposals"] if p["field"] == "employee_count") == 45


def test_personal_email_search_can_be_promoted_after_source_confirmation(
    database, api_client, personal_email_dns, monkeypatch
):
    email = "ana.ficticia@gmail.com"
    quote = "Ana Oliveira\nBIM Manager\n" + email
    html = HTML.replace("<p>BIM Manager</p>", "<p>BIM Manager</p><p>" + email + "</p>")
    dns_transport = outbound_research.sources.fetch_public
    calls, queries = [], []

    def fetch(url):
        if url.startswith("https://dns.google/"):
            return dns_transport(url)
        calls.append(url)
        assert url == URL
        return url, html

    def search(query):
        queries.append(query)
        return [
            {
                "name": "Ana Oliveira",
                "company": "Obras Fictícias",
                "role": "BIM Manager",
                "email": email,
                "website": URL,
                "source_url": URL,
                "quote": quote,
            }
        ]

    monkeypatch.setattr(outbound_research.sources, "fetch_public", fetch)
    monkeypatch.setattr(outbound_provider, "configured", lambda: True)
    monkeypatch.setattr(outbound_provider, "search", search)
    ident = start(api_client, mode="email", query=email, website="", company="")
    person = finish(api_client, ident)["result"]["candidates"][0]
    assert queries[0]["query"] == email and calls == [URL]
    assert person["identity_verified"] and person["quote"] == quote
    response = add(api_client, ident, person)
    assert response.status_code == 200, response.text
    lead = api_client.get("/api/leads/" + response.json()["id"]).json()
    assert lead["email"] == email and lead["company"] == "Obras Fictícias"
    assert lead["data"]["website"] == URL
    assert lead["suggestions"] and all(s["status"] == "pending" for s in lead["suggestions"])
    assert response.json()["remote_writes"] == 0 and len(personal_email_dns) == 1


def test_team_personal_domain_is_not_used_as_company_site(database, api_client, public_site):
    with database() as conn:
        cfg = data_standard.config(conn)
        cfg["personal_email_domains"].append("caixapessoal.invalid")
        data_standard.save(conn, {k: v for k, v in cfg.items() if k != "revision"}, "Equipe")
    ident = start(api_client, mode="email", query="ana@caixapessoal.invalid", website="")
    assert outbound.run_one()
    result = api_client.get("/api/outbound/" + ident).json()
    assert result["status"] == "failed" and "não configurada" in result["error"]
    assert public_site == []
    # An explicit company website still allows the same address through the full journey.
    ident = start(api_client, mode="email", query="ana@caixapessoal.invalid")
    person = finish(api_client, ident)["result"]["candidates"][0]
    assert person["email_only"] and not person["identity_verified"]
    response = add(api_client, ident, person)
    assert response.status_code == 200, response.text
    assert (
        api_client.get("/api/leads/" + response.json()["id"]).json()["email"]
        == "ana@caixapessoal.invalid"
    )


def test_team_role_is_found_in_outbound_and_can_be_reviewed(database, api_client, monkeypatch):
    with database() as conn:
        cfg = data_standard.config(conn)
        cfg["roles"].append({"label": "Orçamentista", "aliases": ["Estimadora"]})
        data_standard.save(conn, {k: v for k, v in cfg.items() if k != "revision"}, "Equipe")
    monkeypatch.setattr(outbound_provider, "configured", lambda: False)
    monkeypatch.setattr(
        outbound_research.sources,
        "fetch_public",
        lambda url: (url, HTML.replace("BIM Manager", "Estimadora")),
    )
    search_id = start(api_client, query="Estimadora")
    person = finish(api_client, search_id)["result"]["candidates"][0]
    response = add(api_client, search_id, person)
    assert response.status_code == 200, response.text
    lead = api_client.get("/api/leads/" + response.json()["id"]).json()
    role = next(s for s in lead["suggestions"] if s["field"] == "role")
    assert role["value"] == "Orçamentista" and not role["standard_blockers"]
    approved = api_client.post(
        "/api/suggestions/" + role["id"] + "/review", json={"action": "approve"}
    )
    assert approved.status_code == 200, approved.text


def test_provider_identity_is_exact_and_guessed_email_is_discarded(
    database, api_client, public_site, monkeypatch
):
    monkeypatch.setattr(outbound_provider, "configured", lambda: True)
    profile = "https://www.linkedin.com/in/ana-oliveira"
    valid = {
        "name": "Ana Oliveira",
        "company": "Obras Fictícias",
        "role": "BIM Manager",
        "email": "inventado@obras.invalid",
        "linkedin_person": profile,
        "website": URL,
        "source_url": URL,
        "quote": "Ana Oliveira\nBIM Manager",
    }
    monkeypatch.setattr(
        outbound_provider,
        "search",
        lambda query: [
            {**valid, "linkedin_person": "https://www.linkedin.com/in/outra-pessoa"},
            valid,
        ],
    )
    result = finish(api_client, start(api_client, mode="linkedin", query=profile))
    people = result["result"]["candidates"]
    assert len(people) == 1 and people[0]["linkedin_person"] == profile
    assert people[0]["email"] == ""
    assert people[0]["identity_verified"]


def test_unread_search_result_is_identified_as_unconfirmed(
    database, api_client, public_site, monkeypatch
):
    monkeypatch.setattr(outbound_provider, "configured", lambda: True)
    profile = "https://www.linkedin.com/in/ana-oliveira"
    monkeypatch.setattr(
        outbound_provider,
        "search",
        lambda query: [
            {
                "name": "Ana Oliveira",
                "company": "Obras Fictícias",
                "role": "BIM Manager",
                "linkedin_person": profile,
                "website": "",
                "source_url": profile,
                "quote": "Ana Oliveira, BIM Manager na Obras Fictícias.",
            }
        ],
    )
    result = finish(api_client, start(api_client, mode="linkedin", query=profile, website=""))
    person = result["result"]["candidates"][0]
    assert not person["identity_verified"] and not person["proposals"]
    refused = api_client.post(
        f"/api/outbound/{result['id']}/candidates/{person['id']}/add",
        json={"fingerprint": person["fingerprint"], "reviewer": "QA", "identity_confirmed": False},
    )
    assert refused.status_code == 400


@pytest.mark.parametrize(
    "mode,query",
    [
        ("role_company", "BIM Manager"),
        ("linkedin", "https://www.linkedin.com/in/pessoa-ficticia"),
        ("email", "pessoa-ficticia@gmail.com"),
    ],
)
def test_missing_provider_is_failure_not_fake_empty_success(
    database, api_client, public_site, mode, query
):
    search_id = start(api_client, mode=mode, query=query, website="")
    assert outbound.run_one()
    result = api_client.get("/api/outbound/" + search_id).json()
    assert result["status"] == "failed" and "não configurada" in result["error"]
    assert api_client.get("/api/leads").json() == []
    assert public_site == []


def test_request_retry_is_idempotent_and_changed_query_conflicts(database, api_client):
    payload = {"mode": "email", "query": "ana@obras.invalid"}
    headers = {"Idempotency-Key": "retry-search"}
    first = api_client.post("/api/outbound", json=payload, headers=headers)
    again = api_client.post("/api/outbound", json=payload, headers=headers)
    assert first.json()["id"] == again.json()["id"]
    assert (
        api_client.post(
            "/api/outbound", json={**payload, "query": "outra@obras.invalid"}, headers=headers
        ).status_code
        == 409
    )
    assert len(api_client.get("/api/outbound").json()["items"]) == 1


def test_concurrent_promotions_create_only_one_contact(database, api_client, public_site):
    search_id = start(api_client)
    person = finish(api_client, search_id)["result"]["candidates"][0]

    def promote_once(_):
        with database() as conn:
            return outbound.promote(
                conn,
                search_id,
                person["id"],
                {
                    "fingerprint": person["fingerprint"],
                    "identity_confirmed": True,
                    "reviewer": "QA",
                },
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(promote_once, range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert len(api_client.get("/api/leads").json()) == 1


def test_suppression_after_search_blocks_promotion(database, api_client, public_site):
    search_id = start(api_client, mode="email", query="contato@obras.invalid")
    person = finish(api_client, search_id)["result"]["candidates"][0]
    with database() as conn:
        lead = service.capture(
            conn, {"email": "contato@obras.invalid", "name": "Contato excluído"}, "excluded"
        )
    api_client.post("/api/leads/" + lead["id"] + "/suppress").raise_for_status()
    assert add(api_client, search_id, person).status_code == 409
    blocked = api_client.post(
        "/api/outbound",
        headers={"Idempotency-Key": "blocked-search"},
        json={"mode": "email", "query": "contato@obras.invalid"},
    )
    assert blocked.status_code == 409


def test_existing_person_email_is_linked_without_overwriting(database, api_client, public_site):
    with database() as conn:
        original = service.capture(
            conn,
            {
                "name": "Nome conferido",
                "company": "Obras Fictícias",
                "email": "contato@obras.invalid",
                "data": {"role": "Gerente confirmado"},
            },
            "existing",
        )
    search_id = start(api_client, mode="email", query="contato@obras.invalid")
    person = finish(api_client, search_id)["result"]["candidates"][0]
    assert person["existing"][0]["id"] == original["id"]
    result = add(api_client, search_id, person)
    assert result.json()["duplicate"] and result.json()["id"] == original["id"]
    lead = api_client.get("/api/leads/" + original["id"]).json()
    assert lead["name"] == "Nome Conferido" and lead["intake_data"]["role"] == "Gerente"
    assert "role" not in lead["data"]
    assert not lead["suggestions"]


def test_search_proof_and_age_cannot_be_replaced_at_promotion(database, api_client, public_site):
    search_id = start(api_client)
    person = finish(api_client, search_id)["result"]["candidates"][0]
    assert add(api_client, search_id, {**person, "fingerprint": "wrong"}).status_code == 409
    with database() as conn:
        conn.execute(
            "UPDATE outbound_searches SET finished_at=now()-interval '7 days 1 minute' WHERE id=%s",
            (search_id,),
        )
    assert add(api_client, search_id, person).status_code == 409
    assert not api_client.get("/api/leads").json()


def test_weights_from_database_apply_to_outbound(database, api_client, public_site):
    with database() as conn:
        config = scoring.active(conn)
        config["weights"]["inference"] = 15
        conn.execute("UPDATE settings SET value=%s WHERE key='scoring'", (Jsonb(config),))
    result = finish(api_client, start(api_client))
    fit = next(
        p for p in result["result"]["candidates"][0]["proposals"] if p["field"] == "product_fit"
    )
    assert fit["confidence"] == 81


def test_provider_adapter_keeps_only_cited_results_and_forces_web_search(monkeypatch):
    from radar import research_ai

    calls = []
    import json

    responses = [
        {
            "output": [
                {"type": "web_search_call", "action": {"sources": [{"url": URL}]}},
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Ana Oliveira — BIM Manager — Obras Fictícias",
                            "annotations": [],
                        }
                    ]
                },
            ]
        },
        {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "candidates": [
                                        {"source_url": URL, "name": "Ana Oliveira"},
                                        {
                                            "source_url": "https://inventado.invalid",
                                            "name": "Inventado",
                                        },
                                    ]
                                }
                            ),
                        }
                    ]
                }
            ]
        },
    ]

    def request(payload):
        calls.append(payload)
        return responses.pop(0)

    monkeypatch.setattr(research_ai, "request", request)
    result = outbound_provider.search(
        {"mode": "role_company", "query": "BIM Manager", "company": "Obras Fictícias"}
    )
    assert result == [{"source_url": URL, "name": "Ana Oliveira"}]
    assert calls[0]["tool_choice"] == "required"
    assert calls[0]["max_tool_calls"] == 3
    assert URL in calls[1]["input"]


def test_email_domain_cannot_bypass_private_address_protection(database, api_client, monkeypatch):
    monkeypatch.setattr(outbound_provider, "configured", lambda: False)
    calls = []
    resolve = outbound_research.sources.socket.getaddrinfo
    monkeypatch.setattr(
        outbound_research.sources.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: (
            [(2, 1, 6, "", ("127.0.0.1", 443))]
            if host == "internal.invalid"
            else resolve(host, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        outbound_research.sources.socket,
        "create_connection",
        lambda *args, **kwargs: calls.append(args),
    )
    result = finish(
        api_client,
        start(api_client, mode="email", query="ana@internal.invalid", website="", company=""),
    )
    assert not result["result"]["candidates"] and not calls


def test_structured_query_and_person_profile_validation():
    from radar.validation import validate_value

    query = outbound_research.normalize_query(
        {"mode": "role_company", "query": "BIM Manager da Empresa X"}
    )
    assert query["company"] == "Empresa X" and query["query"] == "BIM Manager"
    assert (
        validate_value("linkedin_person", "https://br.linkedin.com/in/ana-oliveira/?tracking=123")
        == "https://www.linkedin.com/in/ana-oliveira"
    )
    with pytest.raises(ValueError):
        validate_value("linkedin", "https://www.linkedin.com/in/ana-oliveira")
    with pytest.raises(ValueError):
        outbound_research.normalize_query(
            {"mode": "linkedin", "query": "https://linkedin.com.evil.invalid/in/ana"}
        )


def test_late_worker_response_does_not_replace_a_new_attempt(
    database, api_client, public_site, monkeypatch
):
    search_id = start(api_client)
    fetch = outbound_research.sources.fetch_public
    changed = False

    def replace_lease(url):
        nonlocal changed
        if not changed:
            with database() as conn:
                conn.execute(
                    "UPDATE outbound_searches SET attempts=attempts+1,result=%s WHERE id=%s",
                    (Jsonb({"newer_attempt": True}), search_id),
                )
            changed = True
        return fetch(url)

    monkeypatch.setattr(outbound_research.sources, "fetch_public", replace_lease)
    assert outbound.run_one()
    record = api_client.get("/api/outbound/" + search_id).json()
    assert record["attempts"] == 2 and record["status"] == "running"
    assert record["result"] == {"newer_attempt": True}


def test_suppression_before_worker_blocks_external_lookup(database, api_client, public_site):
    with database() as conn:
        lead = service.capture(conn, {"email": "ana@obras.invalid"}, "before-worker")
    search_id = start(api_client, mode="email", query="ana@obras.invalid")
    api_client.post("/api/leads/" + lead["id"] + "/suppress").raise_for_status()
    assert outbound.run_one()
    record = api_client.get("/api/outbound/" + search_id).json()
    assert record["status"] == "failed" and "exclusão" in record["error"]
    assert not public_site


def test_same_name_company_is_shown_for_review_not_silently_merged(
    database, api_client, public_site
):
    with database() as conn:
        lead = service.capture(
            conn, {"name": "Ana Oliveira", "company": "Obras Fictícias"}, "same-name"
        )
    search_id = start(api_client)
    person = finish(api_client, search_id)["result"]["candidates"][0]
    assert person["possible_duplicates"][0]["id"] == lead["id"]
    assert add(api_client, search_id, person).status_code == 409
    assert len(api_client.get("/api/leads").json()) == 1
