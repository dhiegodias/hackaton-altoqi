"""Acceptance evidence: an actual XLSX, PostgreSQL and stateful HTTP destination.

The AltoQi fixture contains public company facts, not a real person's record.
Custom HubSpot property names and destination contact ID belong only to the test.
"""

import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from time import perf_counter
from xml.etree import ElementTree as ET

import httpx
import pytest

from radar import data_standard, hubspot, research, standard_sources
from radar.service import add_suggestion, capture, get_lead, review

FIXTURES = Path(__file__).parent / "fixtures"
ALTOQI = json.loads((FIXTURES / "altoqi_public.json").read_text())
MAPPING = {
    "website": "website",
    "linkedin": "radar_linkedin_empresa",
    "instagram": "radar_instagram_empresa",
    "cnpj": "radar_cnpj",
    "capital_social": "radar_capital_social",
    "cnae": "radar_cnae",
    "company_size": "radar_porte_cadastral",
    "city": "radar_cidade_empresa",
    "state": "radar_uf_empresa",
    "segment": "radar_segmento",
    "role": "jobtitle",
}
EXPECTED_PROPERTIES = {
    "website": "https://www.altoqi.com.br/home",
    "radar_linkedin_empresa": "https://www.linkedin.com/company/altoqi-tecnologia",
    "radar_instagram_empresa": "https://instagram.com/altoqitecnologia/",
    "radar_cnpj": "04305879000130",
    "radar_capital_social": "26161616.0",
    "radar_cnae": "6203100",
    "radar_porte_cadastral": "DEMAIS",
    "radar_cidade_empresa": "Florianópolis",
    "radar_uf_empresa": "SC",
}
EXCEL_MAPPING = {
    "Nome completo": "name",
    "E-mail": "email",
    "Empresa": "company",
    "Cargo": "role",
    "Funcionários": "employee_count",
    "Site": "website",
}


def save_evidence(filename, value):
    directory = os.getenv("RADAR_TEST_EVIDENCE_DIR")
    if directory:
        dest = Path(directory)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
        )


def prepare_altoqi(database):
    with database() as conn:
        lead_id = capture(
            conn,
            {
                "name": "Contato Fictício de Validação",
                "email": "teste-altoqi@radar-validacao.invalid",
                "company": "AltoQi",
                "hubspot_id": "900000001",
                "data": {"cnpj": ALTOQI["cnpj"]},
            },
            "altoqi-public-acceptance",
        )["id"]
        # CNPJ here is a research hint, not a value read from the fake remote CRM.
        conn.execute("UPDATE leads SET crm_baseline='{}' WHERE id=%s", (lead_id,))

        def fetch_registry(url):
            if "brasilapi.com.br" in url:
                return url, json.dumps(ALTOQI["corporate_registry_snapshot"])
            return url, json.dumps(
                [
                    {
                        "id": 4205407,
                        "nome": "Florianópolis",
                        "microrregiao": {"mesorregiao": {"UF": {"sigla": "SC"}}},
                    }
                ]
            )

        registered, _ = standard_sources.registry(get_lead(conn, lead_id), fetch=fetch_registry)
        proposals = [p for p in registered if p["field"] != "legal_name"] + [
            p for p in ALTOQI["proposals"] if p["field"] not in data_standard.REGISTRY_FIELDS
        ]
        for proposal in proposals:
            suggestion = add_suggestion(conn, get_lead(conn, lead_id), proposal)
            if suggestion:
                review(conn, suggestion["id"], "approve", "Validação técnica")
        pending = add_suggestion(
            conn,
            get_lead(conn, lead_id),
            {
                "field": "segment",
                "value": "Outro",
                "source_kind": "manual",
                "source_url": "https://www.altoqi.com.br/politica-de-privacidade",
                "evidence": "AltoQi desenvolve software para engenharia. Segmento Outro proposto, mas ainda não revisado.",
                "confidence": 70,
                "standard_proof": data_standard.seal("segment", "Outro", "website"),
            },
        )
        # Rejected candidate intentionally duplicates no real employee/job claim.
        rejected = add_suggestion(
            conn,
            get_lead(conn, lead_id),
            {
                "field": "role",
                "value": "Projetista",
                "source_kind": "manual",
                "source_url": "",
                "evidence": "Candidato artificial criado apenas para testar exclusão de uma sugestão rejeitada.",
                "confidence": 10,
            },
        )
        review(conn, rejected["id"], "reject", "Validação técnica")
    return lead_id, pending["id"]


def configure_remote(remote, monkeypatch):
    monkeypatch.setenv("HUBSPOT_FIELD_MAP", json.dumps(MAPPING))
    remote["allowed_properties"] = list(MAPPING.values())
    remote["properties"] = {
        "email": "destinatario-ficticio@radar-validacao.invalid",
        "jobtitle": "Preservar cargo remoto",
    }


def test_altoqi_public_data_reaches_real_http_body_and_replay_does_not_patch_twice(
    database, api_client, remote, monkeypatch
):
    configure_remote(remote, monkeypatch)
    lead_id, _ = prepare_altoqi(database)
    plan = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    assert plan["properties"] == EXPECTED_PROPERTIES
    assert plan["unmapped"] == []
    before = dict(remote["properties"])
    simulated = api_client.post(
        f"/api/leads/{lead_id}/sync", json={"fingerprint": plan["fingerprint"], "simulate": True}
    )
    assert simulated.status_code == 200 and simulated.json()["status"] == "simulated"
    assert remote["http_requests"] == []
    sent = api_client.post(
        f"/api/leads/{lead_id}/sync", json={"fingerprint": plan["fingerprint"], "simulate": False}
    )
    assert sent.status_code == 200 and sent.json()["status"] == "sent"
    assert [request["method"] for request in remote["http_requests"]] == ["GET", "PATCH", "GET"]
    patch = remote["http_requests"][1]
    assert patch == {
        "method": "PATCH",
        "path": "/crm/v3/objects/contacts/900000001",
        "body": {"properties": EXPECTED_PROPERTIES},
    }
    assert remote["properties"] == {**before, **EXPECTED_PROPERTIES}
    replay = api_client.post(
        f"/api/leads/{lead_id}/sync", json={"fingerprint": plan["fingerprint"], "simulate": False}
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["id"] == sent.json()["id"]
    assert sum(request["method"] == "PATCH" for request in remote["http_requests"]) == 1
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM syncs WHERE status='sent'").fetchone()["n"] == 1
        )
        stored = get_lead(conn, lead_id)
        assert stored["crm_baseline"] == plan["approved"]
        assert "role" not in stored["data"] and "segment" not in stored["data"]
    save_evidence("payload-hubspot.json", patch["body"])
    save_evidence(
        "hubspot-http.json",
        {
            "mode": "stateful HTTP test server inside Docker; no real HubSpot writes",
            "request": patch,
            "field_mapping": MAPPING,
            "before": before,
            "after": remote["properties"],
            "http_requests": remote["http_requests"],
            "simulated": simulated.json(),
            "sent": sent.json(),
            "replay": replay.json(),
            "pending_omitted": ["segment"],
            "rejected_omitted": ["role"],
            "unverified_omitted": ["decision_role", "employee_count", "product_fit"],
            "source_snapshot": ALTOQI["observed_at"],
        },
    )


def test_altoqi_default_mapping_blocks_unmapped_fields_before_http(
    database, api_client, remote, monkeypatch
):
    lead_id, _ = prepare_altoqi(database)
    monkeypatch.setenv("HUBSPOT_FIELD_MAP", '{"website":"website","role":"jobtitle"}')
    plan = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    assert plan["properties"] == {"website": "https://www.altoqi.com.br/home"}
    assert set(plan["unmapped"]) == {
        "linkedin",
        "instagram",
        "cnpj",
        "capital_social",
        "cnae",
        "company_size",
        "city",
        "state",
    }
    response = api_client.post(
        f"/api/leads/{lead_id}/sync", json={"fingerprint": plan["fingerprint"], "simulate": False}
    )
    assert response.status_code == 400 and remote["http_requests"] == []
    save_evidence(
        "hubspot-mapeamento-padrao.json",
        {
            "preview": plan,
            "status": response.status_code,
            "error": response.json(),
            "outbound_requests": 0,
        },
    )


def test_altoqi_remote_change_blocks_update_with_409(database, api_client, remote, monkeypatch):
    configure_remote(remote, monkeypatch)
    lead_id, _ = prepare_altoqi(database)
    remote["properties"]["website"] = "https://site-alterado-pelo-vendedor.invalid"
    plan = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    response = api_client.post(
        f"/api/leads/{lead_id}/sync", json={"fingerprint": plan["fingerprint"], "simulate": False}
    )
    assert response.status_code == 409
    assert [request["method"] for request in remote["http_requests"]] == ["GET"]
    assert remote["properties"]["website"] == "https://site-alterado-pelo-vendedor.invalid"
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM syncs").fetchone()["n"] == 0
    save_evidence(
        "hubspot-conflito.json",
        {
            "status": response.status_code,
            "error": response.json(),
            "http_requests": remote["http_requests"],
        },
    )


def test_altoqi_lost_patch_response_recovers_by_reading_all_nine_properties(
    database, remote, monkeypatch
):
    configure_remote(remote, monkeypatch)
    lead_id, _ = prepare_altoqi(database)
    with database() as conn:
        plan = hubspot.preview(conn, lead_id)
    remote["drop_response_once"] = True
    with pytest.raises(httpx.HTTPError), database() as conn:
        hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert {key: remote["properties"][key] for key in EXPECTED_PROPERTIES} == EXPECTED_PROPERTIES
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM syncs").fetchone()["n"] == 0
        result = hubspot.sync(conn, lead_id, plan["fingerprint"], simulate=False)
    assert result["status"] == "sent"
    assert sum(request["method"] == "PATCH" for request in remote["http_requests"]) == 1
    save_evidence(
        "hubspot-resposta-perdida.json",
        {"result": result, "http_requests": remote["http_requests"], "patch_count": 1},
    )


def test_altoqi_new_review_invalidates_old_preview(database, api_client, remote, monkeypatch):
    configure_remote(remote, monkeypatch)
    lead_id, pending_id = prepare_altoqi(database)
    plan = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    with database() as conn:
        review(conn, pending_id, "approve", "Validação técnica")
    response = api_client.post(
        f"/api/leads/{lead_id}/sync", json={"fingerprint": plan["fingerprint"], "simulate": False}
    )
    assert response.status_code == 409 and remote["http_requests"] == []
    save_evidence(
        "hubspot-previa-vencida.json",
        {"status": response.status_code, "error": response.json(), "outbound_requests": 0},
    )


def uploaded(content):
    return {
        "file": (
            "importacao-100-contatos.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def import_excel(api_client, content, mapping=EXCEL_MAPPING):
    from test_transfers import drain

    from radar.db import connection

    response = api_client.post(
        "/api/import", files=uploaded(content), data={"mapping": json.dumps(mapping)}
    )
    if response.status_code != 202:
        return response
    job = drain(api_client, response.json()["id"])
    with connection() as conn:
        rows = conn.execute(
            "SELECT lead_id,line,error,status FROM transfer_rows WHERE job_id=%s ORDER BY line",
            (job["id"],),
        ).fetchall()
    # Adapter for this existing acceptance report; the API itself returns a job.
    return httpx.Response(
        200,
        json={
            "created": job["created_count"],
            "duplicates": job["duplicate_count"],
            "errors": [
                {"row": r["line"], "message": r["error"]} for r in rows if r["status"] == "error"
            ],
            "ids": [str(r["lead_id"]) for r in rows if r["lead_id"]],
        },
    )


def changed_excel_cells(content, replacements):
    """Mutate XML cells only to exercise bad inputs; never author delivery workbooks."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    dest = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(dest, "w") as output:
        for entry in source.infolist():
            raw = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                tree = ET.fromstring(raw)
                for address, value in replacements.items():
                    cell = tree.find(f".//s:c[@r='{address}']", ns)
                    assert cell is not None
                    cell.clear()
                    cell.set("r", address)
                    cell.set("t", "inlineStr")
                    inline = ET.SubElement(cell, "{" + ns["s"] + "}is")
                    ET.SubElement(inline, "{" + ns["s"] + "}t").text = value
                raw = ET.tostring(tree)
            output.writestr(entry, raw)
    return dest.getvalue()


def conforming_excel():
    source = json.loads((FIXTURES / "importacao_100.json").read_text())
    return changed_excel_cells(
        (FIXTURES / "importacao_100.xlsx").read_bytes(),
        {
            f"A{i + 2}": re.sub(r" \(Teste \d+\)$", " Fictício", row[0])
            for i, row in enumerate(source["rows"])
        },
    )


def test_excel_100_contacts_upload_persistence_and_reimport(database, api_client):
    content = conforming_excel()
    source = json.loads((FIXTURES / "importacao_100.json").read_text())
    for row in source["rows"]:
        row[0] = re.sub(r" \(Teste \d+\)$", " Fictício", row[0])
    preview = api_client.post("/api/import/preview", files=uploaded(content))
    from test_transfers import drain

    assert preview.status_code == 202
    preview = httpx.Response(200, json=drain(api_client, preview.json()["id"], "awaiting_mapping"))
    assert preview.json()["total"] == 100
    assert preview.json()["headers"] == source["headers"]
    assert preview.json()["suggested_mapping"] == EXCEL_MAPPING
    start = perf_counter()
    first = import_excel(api_client, content)
    elapsed = perf_counter() - start
    assert first.status_code == 200
    result = first.json()
    assert result["created"] == 100 and result["duplicates"] == 0 and result["errors"] == []
    assert len(set(result["ids"])) == 100
    with database() as conn:
        stored = conn.execute("SELECT * FROM leads ORDER BY email").fetchall()
        assert len(stored) == 100
        # Compare all six fields of every row, including names with accents and numeric zero.
        for actual, expected in zip(stored, source["rows"], strict=True):
            name, email, company, role, people, website = expected
            assert (actual["name"], actual["email"], actual["company"]) == (name, email, company)
            assert actual["data"] == {"employee_count": people, "website": website}
            expected_roles = {
                "Engenheira": "Engenheiro",
                "Projetista": "Projetista",
                "Coordenador de projetos": "Coordenador",
                "Analista BIM": "Analista",
                "Arquiteta": "Arquiteto",
            }
            assert actual["intake_data"]["role"] == expected_roles[role]
            assert actual["origin"] == "csv" and actual["hubspot_id"] is None
        assert conn.execute("SELECT count(*) AS n FROM suggestions").fetchone()["n"] == 0
    repeat = import_excel(api_client, content).json()
    assert repeat["created"] == 0 and repeat["duplicates"] == 100 and repeat["errors"] == []
    assert repeat["ids"] == result["ids"]
    # Different bytes and one changed role exercise email matching, not only file replay.
    changed = changed_excel_cells(content, {"D2": "Diretora de projetos (teste)"})
    modified = import_excel(api_client, changed).json()
    assert modified["created"] == 0 and modified["duplicates"] == 100 and modified["errors"] == []
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 100
        current = conn.execute(
            "SELECT data,intake_data FROM leads WHERE email='contato001@radar-validacao.invalid'"
        ).fetchone()
        assert "role" not in current["data"]
        assert current["intake_data"]["role"] == "Diretor"
        suggestions = conn.execute("SELECT field,value,status FROM suggestions").fetchall()
        assert suggestions == [{"field": "role", "value": "Diretor", "status": "pending"}]
    save_evidence(
        "excel-100-resultados.json",
        {
            "sha256": hashlib.sha256(content).hexdigest(),
            "preview": preview.json(),
            "first": result,
            "reimport": repeat,
            "modified_reimport": modified,
            "database_count": 100,
            "all_600_values_match": True,
            "changed_role_requires_review": suggestions,
            "first_import_seconds": round(elapsed, 3),
        },
    )


def test_excel_invalid_row_reports_line_and_keeps_99_valid_contacts(database, api_client):
    content = changed_excel_cells(conforming_excel(), {"B51": "email-invalido"})
    response = import_excel(api_client, content)
    assert response.status_code == 200
    result = response.json()
    assert result["created"] == 99 and result["duplicates"] == 0
    assert result["errors"] == [{"row": 51, "message": "E-mail inválido."}]
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 99
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM leads WHERE email='contato100@radar-validacao.invalid'"
            ).fetchone()["n"]
            == 1
        )
    save_evidence("excel-linha-invalida.json", result)


def test_excel_ambiguous_mapping_rejects_without_creating_contacts(database, api_client):
    mapping = {**EXCEL_MAPPING, "Empresa": "name"}
    response = import_excel(api_client, (FIXTURES / "importacao_100.xlsx").read_bytes(), mapping)
    assert response.status_code == 400
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 0


def test_live_altoqi_backfill_fixture_reaches_observable_hubspot_destination(
    database, api_client, remote, monkeypatch
):
    from radar import backfill

    fixture = json.loads((FIXTURES / "altoqi_backfill_public.json").read_text())
    with database() as conn:
        lead_id = capture(
            conn,
            {**fixture["input"], "hubspot_id": "backfill-altoqi-test-only"},
            "altoqi-backfill-http",
        )["id"]
        lead = get_lead(conn, lead_id)
        for proposal in fixture["proposals"]:
            if proposal["field"] in {"role", "segment"}:
                quote = proposal["evidence"]
                doc = research.document(
                    proposal["source_url"], f"<title>AltoQi</title><p>{quote}</p>", lead
                )
                proposal = {
                    **proposal,
                    "standard_proof": research.candidate(
                        lead, proposal["field"], proposal["value"], doc, quote
                    )["standard_proof"],
                }
            add_suggestion(conn, lead, proposal)
        backfill.save_policy(conn, {**backfill.DEFAULT_POLICY, "threshold": 75})
        plan = backfill.preview(conn)
        assert plan["eligible_count"] == 7
        assert backfill.apply(conn, plan["fingerprint"])["applied"] == 7
    mapping = {
        "role": "jobtitle",
        "decision_role": "radar_decision_role",
        "employee_range": "radar_employee_range",
        "product_fit": "radar_product_fit",
        "segment": "radar_segment",
        "linkedin": "radar_linkedin_empresa",
        "instagram": "radar_instagram_empresa",
    }
    expected = {
        "jobtitle": "Especialista",
        "radar_decision_role": "Provável influenciador",
        "radar_employee_range": "300+",
        "radar_product_fit": "Fora do ICP direto · fornecedor de software para engenharia",
        "radar_segment": "Outro",
        "radar_linkedin_empresa": "https://www.linkedin.com/company/altoqi-tecnologia",
        "radar_instagram_empresa": "https://www.instagram.com/altoqitecnologia",
    }
    monkeypatch.setenv("HUBSPOT_FIELD_MAP", json.dumps(mapping))
    remote["properties"] = {"city": "Campo remoto preservado"}
    remote["allowed_properties"] = list(mapping.values())
    outgoing = api_client.get(f"/api/leads/{lead_id}/sync-preview").json()
    sent = api_client.post(
        f"/api/leads/{lead_id}/sync",
        json={"fingerprint": outgoing["fingerprint"], "simulate": False},
    )
    assert sent.status_code == 200 and sent.json()["status"] == "sent", sent.text
    patch = next(call for call in remote["http_requests"] if call["method"] == "PATCH")
    assert patch["body"] == {"properties": expected}
    assert remote["properties"] == {"city": "Campo remoto preservado", **expected}
    assert [call["method"] for call in remote["http_requests"]] == ["GET", "PATCH", "GET"]
    save_evidence("payload-hubspot-backfill.json", patch["body"])
    save_evidence(
        "http-hubspot-backfill.json",
        {
            "mode": "Stateful HTTP test destination in Docker; no real HubSpot writes",
            "fixture_collected_at": fixture["consulted_at"],
            "field_mapping": mapping,
            "requests": remote["http_requests"],
            "stored_properties": remote["properties"],
        },
    )
