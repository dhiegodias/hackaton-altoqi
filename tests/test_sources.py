"""Evidence extraction and network boundaries with independent input fixtures."""

import gzip
import json
import socket
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from radar import sources
from radar.qualification import Observation, recommend
from radar.validation import valid_cnpj, validate_value


def test_compressed_public_response_is_bounded_and_not_silently_truncated():
    raw = '{"nome":"Florianópolis"}'.encode()
    packed = gzip.compress(raw)
    assert json.loads(sources.decode_body(packed, "gzip"))["nome"] == "Florianópolis"
    with pytest.raises(ValueError, match="1 MB"):
        sources.decode_body(gzip.compress(b"x" * 1_000_001), "gzip")
    for damaged in (packed[:-8], packed + b"ignored", b"not-gzip"):
        with pytest.raises(ValueError):
            sources.decode_body(damaged, "gzip")


@pytest.mark.parametrize(
    "url",
    [
        "https://br.linkedin.com/company/exemplo",
        "https://m.instagram.com/exemplo/",
        "https://linkedin.com./company/exemplo",
    ],
)
def test_social_platform_subdomains_are_not_collected(url, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Rede social deve ser recusada antes de abrir conexão.")

    monkeypatch.setattr(sources, "PinnedHTTPSConnection", forbidden)
    with pytest.raises(ValueError, match="não raspa redes sociais"):
        sources.fetch_public(url)


@pytest.mark.parametrize(
    "number", ["12.ABC.345/01DE-35", "12.345.678/0001-95", "11.222.333/0001-81"]
)
def test_cnpj_accepts_independent_checksum_vectors(number):
    # The alpha vector is the Receita Federal worked example; no vector is generated here.
    assert valid_cnpj(number)


@pytest.mark.parametrize(
    "number",
    [
        "12.ABC.345/01DE-36",
        "12.ABC.345/01DE-3A",
        "12.ÁBC.345/01DE-35",
        "00000000000000",
        "11111111111111",
        "12345678000195<script>",
    ],
)
def test_cnpj_rejects_wrong_checksum_unicode_and_embedded_markup(number):
    assert not valid_cnpj(number)


def test_alphanumeric_registry_lookup_fails_explicitly_without_network():
    called = []

    def fetch(url):
        called.append(url)
        raise AssertionError("Não deve enviar letras a um conector não habilitado.")

    with pytest.raises(ValueError, match="alfanumérico válido"):
        sources.registry_evidence("12.ABC.345/01DE-35", fetch=fetch)
    assert called == []


def test_registry_identity_mismatch_rejects_all_values():
    def wrong_company(url):
        return url, json.dumps(
            {"cnpj": "11222333000181", "capital_social": 9000000, "municipio": "Cidade Fictícia"}
        )

    with pytest.raises(ValueError, match="não corresponde"):
        sources.registry_evidence("12345678000195", fetch=wrong_company)


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.12.0.1"],
        ["169.254.169.254"],
        ["192.168.1.1"],
        ["::1"],
        ["fc00::1"],
        ["8.8.8.8", "127.0.0.1"],
    ],
)
def test_dns_private_loopback_metadata_and_mixed_answers_are_blocked(monkeypatch, addresses):
    def resolve(_host, port, **_kwargs):
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in addresses
        ]

    monkeypatch.setattr(sources.socket, "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="internos"):
        sources.safe_addresses("empresa-ficticia.invalid", 443)


def test_redirect_to_private_address_is_blocked_before_opening_second_socket(monkeypatch):
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.9/metadata")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_dns = socket.getaddrinfo
    original_connect = socket.create_connection
    opened = []

    def dns(host, port, *args, **kwargs):
        if host == "empresa-ficticia.invalid":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        return original_dns(host, port, *args, **kwargs)

    def local_transport(address, *args, **kwargs):
        opened.append(address)
        return original_connect(("127.0.0.1", server.server_port), *args, **kwargs)

    monkeypatch.setattr(sources.socket, "getaddrinfo", dns)
    monkeypatch.setattr(sources.socket, "create_connection", local_transport)
    try:
        with pytest.raises(ValueError, match="internos"):
            sources.fetch_public("http://empresa-ficticia.invalid")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert opened == [("8.8.8.8", 80)]


def test_website_organization_only_does_not_use_person_profile_or_headcount_range():
    html = """<html><script type="application/ld+json">{"@graph":[
      {"@type":"Person","sameAs":"https://linkedin.com/in/contato-pessoal","numberOfEmployees":900},
      {"@type":"Organization","sameAs":["https://linkedin.com/company/empresa-ficticia","https://instagram.com/empresa-ficticia"],"numberOfEmployees":{"minValue":11,"maxValue":50}}
    ]}</script><p>Empresa fictícia de testes.</p></html>"""
    proposals, _url, _content = sources.website_evidence(
        "https://empresa-ficticia.invalid", fetch=lambda url: (url, html)
    )
    assert {proposal["field"] for proposal in proposals} == {"linkedin", "instagram"}
    assert all("contato-pessoal" not in proposal["value"] for proposal in proposals)


def test_old_declared_absorption_is_unknown_instead_of_low_fit():
    today = date(2026, 10, 3)
    observations = {
        "business_model": Observation(
            "builder", "consultant", "capture:1", today, "É construtora.", "declaration"
        ),
        "absorbs_method": Observation(
            False,
            "consultant",
            "capture:1",
            today - timedelta(days=181),
            "Não havia ninguém para absorver.",
            "declaration",
        ),
    }
    answer = recommend(observations, today)
    assert answer.offers == []
    assert "BIM ONE" not in answer.offers
    assert any("pessoa" in question for question in answer.questions)


def test_ai_discards_missing_invented_and_nonliteral_quotes(monkeypatch):
    content = "A empresa fictícia tem 24 pessoas na equipe e atua em projetos prediais."
    answers = [
        [{"field": "employee_count", "value": "900"}],
        [
            {
                "field": "employee_count",
                "value": "900",
                "quote": "A empresa tem novecentos funcionários próprios.",
            }
        ],
        [{"field": "employee_count", "value": "900", "quote": content}],
    ]
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    def complete(*_args, **_kwargs):
        facts = answers.pop(0)
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.invalid"),
            json={
                "status": "completed",
                "output": [
                    {"content": [{"type": "output_text", "text": json.dumps({"facts": facts})}]}
                ],
            },
        )

    monkeypatch.setattr(sources.httpx, "post", complete)
    for _ in range(3):
        assert sources.ai_evidence("https://empresa-ficticia.invalid", content) == []


@pytest.mark.parametrize(
    "value", [None, "", {}, [], True, -1, "11–50", 1.5, float("nan"), float("inf")]
)
def test_invalid_headcounts_are_not_made_into_exact_employee_counts(value):
    with pytest.raises((ValueError, TypeError)):
        validate_value("employee_count", value)
