"""Validate incoming data against independent public-response fixtures and policy.

No real HubSpot calls. DNS/registry fixtures represent provider responses; the
actual parsers, restrictions, transactions and HTTP contracts execute normally.
"""

import json

import pytest

from radar import data_standard as standard
from radar import hubspot, service, standard_sources
from radar.validation import normalize_email, validate_value

SITE = "https://empresa-padrao.invalid"
PERSON = {
    "name": "MARIA DA SILVA",
    "company": "Empresa Padrão",
    "email": "maria@empresa-padrao.invalid",
    "data": {
        "website": SITE,
        "cnpj": "12345678000195",
        "role": "Eng. civil",
        "phone": "48999991234",
        "city": "Florianopolis",
        "state": "SC",
    },
}
RECORD = {
    "cnpj": "12345678000195",
    "nome_fantasia": "Empresa Padrão",
    "razao_social": "EMPRESA PADRÃO LTDA",
    "porte": "EPP",
    "cnae_fiscal": 4321500,
    "cnae_fiscal_descricao": "Instalações elétricas",
    "municipio": "FLORIANOPOLIS",
    "codigo_municipio_ibge": 4205407,
    "uf": "SC",
    "capital_social": 100000,
}
CITY = {
    "id": 4205407,
    "nome": "Florianópolis",
    "microrregiao": {"mesorregiao": {"UF": {"sigla": "SC"}}},
}


def provider(record=None, city=None):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith("https://brasilapi.com.br/api/cnpj/"):
            return url, json.dumps(RECORD if record is None else record)
        if url.startswith("https://servicodados.ibge.gov.br/"):
            return url, json.dumps(CITY if city is None else city)
        raise AssertionError("Unexpected source: " + url)

    return fetch, calls


def test_name_normalization_does_not_expand_unknown_initials():
    assert standard.normalize_name("  MARIA   DA SILVA ") == "Maria da Silva"
    assert standard.normalize_name("ana e silva") == "Ana e Silva"
    for value in ("M. Silva", "Maria S", "Maria", "Maria 123", "J Silva"):
        with pytest.raises(ValueError):
            standard.normalize_name(value)


def test_email_format_rejects_malformed_mailboxes_before_dns():
    assert normalize_email(" Maria+evento@ALTOQI.COM.BR ") == "maria+evento@altoqi.com.br"
    for email in (
        "a..b@altoqi.com.br",
        ".a@altoqi.com.br",
        "a@-altoqi.com.br",
        "a<b@altoqi.com.br",
    ):
        with pytest.raises(ValueError, match="E-mail inválido"):
            normalize_email(email)


def test_new_team_role_is_used_by_the_public_page_extractor(database):
    with database() as conn:
        cfg = standard.config(conn)
        cfg["roles"].append({"label": "Orçamentista", "aliases": ["Estimadora"]})
        cfg = standard.save(conn, {k: v for k, v in cfg.items() if k != "revision"}, "Equipe")
        ident = service.capture(conn, PERSON, "dynamic-role")["id"]
        lead = service.get_lead(conn, ident)
        proposals = standard_sources.from_url(
            lead,
            SITE + "/equipe",
            cfg,
            fetch=lambda url: (
                url,
                "<title>Empresa Padrão</title><p>Maria da Silva, Estimadora.</p>",
            ),
        )
        role = next(p for p in proposals if p["field"] == "role")
        saved = service.add_suggestion(conn, lead, role)
        service.review(conn, saved["id"], "approve", "Equipe")
        assert service.get_lead(conn, ident)["data"]["role"] == "Orçamentista"


@pytest.mark.parametrize(
    ("phone", "expected"),
    [("48999991234", "+55 (48) 99999-1234"), ("+55 (11) 3333-2222", "+55 (11) 3333-2222")],
)
def test_phone_format(phone, expected):
    assert validate_value("phone", phone) == expected


@pytest.mark.parametrize(
    "phone", ["00999991234", "+1 4155551234", "4811111111", "+55 11 811112222"]
)
def test_invalid_phone_is_not_silently_repaired(phone):
    with pytest.raises(ValueError):
        validate_value("phone", phone)


def test_email_dns_null_mx_nxdomain_and_temporary_failure(database):
    with database() as conn:
        cfg = standard.config(conn)
    for response, error in [
        ({"Status": 3}, ValueError),
        ({"Status": 0, "Answer": [{"type": 15, "data": "0 ."}]}, ValueError),
        ({"Status": 2}, standard.VerificationUnavailable),
    ]:
        with pytest.raises(error):
            standard.email_domain(
                "pessoa@empresa-qa.invalid",
                cfg,
                fetch=lambda url, response=response: (url, json.dumps(response)),
            )
    calls = []

    def fetch(url):
        calls.append(url)
        return url, json.dumps(
            {
                "Status": 0,
                "Answer": [{"type": 1, "data": "203.0.113.1"}] if url.endswith("type=A") else [],
            }
        )

    result = standard.email_domain("PESSOA@EMPRESA-QA.INVALID", cfg, fetch=fetch)
    assert result["status"] == "active_domain" and not result["mailbox_verified"]
    assert len(calls) == 3 and all("pessoa" not in url for url in calls)
    personal = standard.email_domain("pessoa@gmail.com", cfg, fetch=fetch)
    assert personal["status"] == "active_domain" and not personal["mailbox_verified"]
    assert len(calls) == 6


def test_intake_does_not_certify_source_and_registry_checks_identity_and_ibge(database):
    fetch, calls = provider()
    with database() as conn:
        ident = service.capture(conn, PERSON, "intake")["id"]
        lead = service.get_lead(conn, ident)
        assert lead["name"] == "Maria da Silva"
        assert lead["data"] == {"website": SITE}
        assert lead["intake_data"]["role"] == "Engenheiro civil"
        proposals, notes = standard_sources.registry(lead, fetch=fetch)
        assert not notes
        values = {p["field"]: p["value"] for p in proposals}
        assert values["city"] == "Florianópolis" and values["segment"] == "Instaladora"
        for p in proposals:
            suggestion = service.add_suggestion(conn, lead, p)
            service.review(conn, suggestion["id"], "approve", "Equipe teste")
        updated = service.get_lead(conn, ident)
        assert updated["data"]["legal_name"] == "EMPRESA PADRÃO LTDA"
        assert updated["data"]["city"] == "Florianópolis"
        assert "phone" not in updated["data"]
        assert len(hubspot.preview(conn, ident)["approved"]) == 8
    assert len(calls) == 2
    bad, calls = provider(
        {**RECORD, "nome_fantasia": "Outra Empresa", "razao_social": "Outra Empresa LTDA"}
    )
    with pytest.raises(ValueError, match="não coincide"):
        standard_sources.registry(lead, fetch=bad)
    assert len(calls) == 1
    wrong, _ = provider(city={**CITY, "nome": "Joinville"})
    proposals, notes = standard_sources.registry(lead, fetch=wrong)
    assert notes and not {"city", "state"} & {p["field"] for p in proposals}


def test_source_limits_apply_to_review_edit_and_hubspot(database):
    with database() as conn:
        ident = service.capture(conn, PERSON, "limits")["id"]
        lead = service.get_lead(conn, ident)
        imported = service.add_suggestion(
            conn,
            lead,
            {
                "field": "phone",
                "value": "48999991234",
                "source_kind": "import",
                "source_url": "",
                "evidence": "Planilha recebida",
                "confidence": 100,
            },
        )
    with database() as conn, pytest.raises(service.Conflict, match="comprovação"):
        service.review(conn, imported["id"], "approve", "Equipe")
    html = '<title>Empresa Padrão</title><p>Maria da Silva, Engenheira civil.</p><a href="tel:+5548999991234">Telefone</a>'
    with database() as conn:
        lead = service.get_lead(conn, ident)
        proposals = standard_sources.from_url(
            lead, SITE + "/equipe", standard.config(conn), fetch=lambda url: (url, html)
        )
        by_field = {p["field"]: p for p in proposals}
        role = service.add_suggestion(conn, lead, by_field["role"])
        phone = service.add_suggestion(conn, lead, by_field["phone"])
    with database() as conn, pytest.raises(service.Conflict, match="nova evidência"):
        service.review(
            conn,
            role["id"],
            "approve",
            "Equipe",
            edited_value="Diretor",
            note="Sem fonte para este cargo",
        )
    with database() as conn:
        service.review(conn, role["id"], "approve", "Equipe")
        service.review(conn, phone["id"], "approve", "Equipe")
        assert hubspot.preview(conn, ident)["approved"]["phone"] == "+55 (48) 99999-1234"
        assert hubspot.preview(conn, ident)["approved"]["role"] == "Engenheiro civil"
        cfg = standard.config(conn)
        cfg["roles"] = [item for item in cfg["roles"] if item["label"] != "Engenheiro civil"]
        standard.save(conn, {k: v for k, v in cfg.items() if k != "revision"}, "Equipe")
        assert "role" not in hubspot.preview(conn, ident)["approved"]
    with pytest.raises(ValueError, match="domínio|página"):
        standard_sources.from_url(
            lead,
            "https://noticia.invalid/perfil",
            cfg,
            fetch=lambda url: (_ for _ in ()).throw(AssertionError("must not fetch")),
        )


def test_council_page_can_supply_title_without_claiming_employment(database):
    with database() as conn:
        ident = service.capture(
            conn, {"name": "Maria da Silva", "company": "Empresa Padrão"}, "council"
        )["id"]
        lead = service.get_lead(conn, ident)
        items = standard_sources.from_url(
            lead,
            "https://caubr.gov.br/perfil-publico",
            standard.config(conn),
            fetch=lambda url: (url, "<p>Maria da Silva — Arquiteta e urbanista</p>"),
        )
        assert {p["field"] for p in items} == {"role"}
        assert items[0]["value"] == "Arquiteto"
        suggestion = service.add_suggestion(conn, lead, items[0])
        service.review(conn, suggestion["id"], "approve", "Equipe")
        assert service.get_lead(conn, ident)["data"] == {"role": "Arquiteto"}
