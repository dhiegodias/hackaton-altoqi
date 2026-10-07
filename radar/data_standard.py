"""Team-controlled vocabulary and evidence requirements for the PDF data standard.

Intake is retained separately from verified fields. Only server-generated evidence
can authorize a field: declaring a source type in a spreadsheet does not do so.
"""

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from psycopg.types.json import Jsonb

from radar.validation import normalize_email, validate_value

VERSION = "data-standard-2026-10-07.1"
REGISTRY_FIELDS = {"cnpj", "legal_name", "company_size", "cnae", "city", "state", "capital_social"}
RESTRICTED = REGISTRY_FIELDS | {"phone", "role", "segment"}
SOURCE_RULES = {
    "phone": ["Site da empresa"],
    "legal_name": ["Cadastro CNPJ"],
    "cnpj": ["Cadastro CNPJ"],
    "company_size": ["Cadastro CNPJ"],
    "cnae": ["Cadastro CNPJ"],
    "capital_social": ["Cadastro CNPJ"],
    "role": ["Site da empresa", "CAU/CREA"],
    "segment": ["Site da empresa", "CNAE cadastral"],
    "city": ["Cadastro CNPJ conferido no IBGE"],
    "state": ["Cadastro CNPJ conferido no IBGE"],
}
_dns_cache = {}


class VerificationUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class PreparedEmail:
    email: str
    revision: int
    proof: dict


def prepare_email(email, cfg):
    email = normalize_email(email)
    return PreparedEmail(email, cfg["revision"], email_domain(email, cfg))


def normal(value):
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def normalize_name(value):
    value = " ".join(str(value or "").split())
    if not value:
        return ""
    tokens = re.split(r"[\s\-’']+", value)
    if (
        len([t for t in tokens if normal(t) not in {"da", "de", "do", "das", "dos", "e", "d"}]) < 2
        or any((len(t.rstrip(".")) < 2 and t.lower() not in {"e", "d"}) or "." in t for t in tokens)
        or any(normal(t) in {"jr", "sr", "dr", "dra", "prof", "eng", "arq"} for t in tokens)
    ):
        raise ValueError("Informe nome e sobrenome por extenso, sem iniciais ou abreviações.")
    if any(not (c.isalpha() or c in " -’'") for c in value):
        raise ValueError("Nome completo deve conter apenas letras, espaços, apóstrofos e hífens.")
    particles = {"da", "das", "de", "do", "dos", "e", "van", "von"}
    return " ".join(
        t.lower() if i and t.lower() in particles else t.title()
        for i, t in enumerate(value.split())
    )


def initialize(conn):
    initial = json.loads(Path(__file__).with_name("standard_defaults.json").read_text())
    conn.execute(
        "INSERT INTO settings(key,value) VALUES ('data_standard',%s) ON CONFLICT DO NOTHING",
        (Jsonb(initial),),
    )


def config(conn):
    return conn.execute("SELECT value FROM settings WHERE key='data_standard'").fetchone()["value"]


def save(conn, payload, reviewer):
    from radar.service import audit

    current = config(conn)
    if set(payload) != {"roles", "segments", "council_domains", "personal_email_domains"}:
        raise ValueError("Configuração de padrão incompleta.")
    if not reviewer.strip():
        raise ValueError("Informe o responsável pela alteração.")
    for key in ("segments", "council_domains", "personal_email_domains"):
        values = payload[key]
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 150
            or any(not isinstance(v, str) or not 2 <= len(v.strip()) <= 100 for v in values)
        ):
            raise ValueError("Listas precisam de 1 a 150 valores não vazios.")
        if len({normal(v) for v in values}) != len(values):
            raise ValueError("A lista contém valores duplicados.")
    for domain in payload["council_domains"] + payload["personal_email_domains"]:
        if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", domain) or "." not in domain:
            raise ValueError("Informe somente domínios, sem caminhos ou curingas.")
    roles = payload["roles"]
    if not isinstance(roles, list) or not 1 <= len(roles) <= 150:
        raise ValueError("Informe a lista de cargos.")
    vocabulary = set()
    for item in roles:
        if (
            not isinstance(item, dict)
            or set(item) != {"label", "aliases"}
            or not isinstance(item["aliases"], list)
            or len(item["aliases"]) > 30
        ):
            raise ValueError("Cada cargo precisa de nome e lista de variações.")
        for v in [item["label"], *item["aliases"]]:
            if not isinstance(v, str) or not 2 <= len(v.strip()) <= 100 or normal(v) in vocabulary:
                raise ValueError("Cargo/variação vazio, repetido ou muito longo.")
            vocabulary.add(normal(v))
    # Serializes publication so concurrent changes do not reuse a revision.
    row = conn.execute("SELECT value FROM settings WHERE key='data_standard' FOR UPDATE").fetchone()
    updated = {**payload, "revision": row["value"]["revision"] + 1}
    conn.execute("UPDATE settings SET value=%s WHERE key='data_standard'", (Jsonb(updated),))
    audit(
        conn, None, "data_standard_updated", {"before": current, "after": updated}, reviewer.strip()
    )
    return updated


def controlled(field, value, cfg):
    if field == "role":
        term = normal(value)
        for role in cfg["roles"]:
            if term in {normal(v) for v in [role["label"], *role["aliases"]]}:
                return role["label"]
        # Explicit team-authored aliases only, never an opaque model classification.
        matches = []
        for item in cfg["roles"]:
            for alias in [item["label"], *item["aliases"]]:
                if re.search(r"(?<!\w)" + re.escape(normal(alias)) + r"(?!\w)", term):
                    matches.append((len(normal(alias)), item["label"]))
        if matches:
            return sorted(matches, reverse=True)[0][1]
        raise ValueError(
            "Cargo fora da lista controlada. Cadastre o cargo ou sua variação em Padrão de dados."
        )
    if field == "segment":
        for item in cfg["segments"]:
            if normal(value) == normal(item):
                return item
        raise ValueError("Segmento fora da lista controlada da equipe.")
    return validate_value(field, value)


def email_domain(email, cfg, fetch=None):
    from radar import sources

    email = normalize_email(email)
    if not email:
        return {}
    domain = email.rsplit("@", 1)[1].encode("idna").decode("ascii")
    # Personal addresses are valid identifiers too. Provider domains only limit
    # employer inference in outbound research, never intake or domain validation.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", domain):
        raise ValueError("Domínio de e-mail inválido.")
    cached = _dns_cache.get(domain)
    if fetch is None and cached and cached[0] > time.monotonic():
        return cached[1]
    fetch = fetch or sources.fetch_public

    def query(kind):
        try:
            url, body = fetch(f"https://dns.google/resolve?name={domain}&type={kind}")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("DNS inválido")
            return url, result
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise VerificationUnavailable(
                "Consulta do domínio indisponível; tente novamente."
            ) from exc

    try:
        url, result = query("MX")
        if result.get("Status") not in (0, 3):
            raise VerificationUnavailable(
                "Consulta DNS indisponível. Tente novamente; o e-mail não foi confirmado."
            )
        answers = [r for r in result.get("Answer", []) if r.get("type") == 15]
        if result.get("Status") == 3 or any(r.get("data", "").strip() == "0 ." for r in answers):
            raise ValueError("O domínio do e-mail não existe ou declara não receber mensagens.")
        active = bool(answers)
        if not active:
            for typ, number in [("A", 1), ("AAAA", 28)]:
                _, response = query(typ)
                if response.get("Status") not in (0, 3):
                    raise VerificationUnavailable("Não foi possível confirmar o domínio do e-mail.")
                active |= any(r.get("type") == number for r in response.get("Answer", []))
        if not active:
            raise ValueError("Domínio de e-mail sem registros DNS ativos.")
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationUnavailable("Consulta do domínio indisponível; tente novamente.") from exc
    proof = {
        "status": "active_domain",
        "domain": domain,
        "source_url": url,
        "checked_at": datetime.now(UTC).isoformat(),
        "mailbox_verified": False,
    }
    _dns_cache[domain] = (time.monotonic() + 600, proof)
    if len(_dns_cache) > 4096:
        _dns_cache.pop(next(iter(_dns_cache), None), None)
    return proof


def role_pattern(cfg):
    terms = {value for item in cfg["roles"] for value in [item["label"], *item["aliases"]]}
    return (
        r"(?<!\w)(?:"
        + "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
        + r")(?!\w)"
    )


def inputs(lead):
    return {**lead.get("intake_data", {}), **lead["data"]}


def intake(conn, values):
    cfg = config(conn)
    verified, pending = {}, {}
    for field, value in values.items():
        value = validate_value(field, value)
        if field in {"role", "segment"}:
            value = controlled(field, value, cfg)
        (pending if field in RESTRICTED else verified)[field] = value
    return verified, pending


def host(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.").rstrip(".")
    except ValueError:
        return ""


def is_council(url, cfg):
    domain = host(url)
    return bool(domain) and any(
        domain == d or domain.endswith("." + d) for d in cfg["council_domains"]
    )


def website_matches(url, lead):
    expected = host(inputs(lead).get("website", ""))
    return bool(expected) and host(url) == expected


def blockers(conn, lead, item, value=None, cfg=None):
    field = item["field"]
    if field not in RESTRICTED or lead.get("demo"):
        return []
    cfg = cfg or config(conn)
    try:
        normalized = controlled(field, item["value"] if value is None else value, cfg)
    except ValueError as exc:
        return [str(exc)]
    proof = item.get("standard_proof") or {}
    if proof.get("version") != VERSION or proof.get("value") != normalized:
        return [
            "Dado sem comprovação do padrão atual. Pesquise uma fonte permitida; a edição precisa de nova evidência."
        ]
    category = proof.get("category")
    allowed = (
        category == "registry"
        if field in REGISTRY_FIELDS
        else category == "website"
        if field == "phone"
        else category in {"website", "council"}
        if field == "role"
        else category in {"website", "registry"}
    )
    if not allowed:
        return ["Fonte não permitida para este campo."]
    if category == "website" and not website_matches(item.get("source_url", ""), lead):
        return ["A evidência não pertence ao site informado da empresa."]
    if category == "council" and not is_council(item.get("source_url", ""), cfg):
        return ["Conselho fora da lista de fontes permitidas."]
    if category == "registry" and (
        proof.get("company") != lead["company"]
        or (inputs(lead).get("cnpj") and proof.get("cnpj") != inputs(lead).get("cnpj"))
    ):
        return ["CNPJ da evidência não corresponde ao cadastro atual."]
    return []


def seal(field, value, category, **extra):
    return {"version": VERSION, "field": field, "value": value, "category": category, **extra}


def prepare_proposal(conn, lead, proposal):
    result = dict(proposal)
    field = result["field"]
    cfg = config(conn)
    if field in {"role", "segment"}:
        result["value"] = controlled(field, result["value"], cfg)
    proof = result.get("standard_proof") or {}
    if proof:
        # Connectors create the proof before persistence. Keep its value aligned to
        # the published vocabulary, preserving the literal quote separately.
        proof = {**proof, "value": controlled(field, proof["value"], cfg)}
        result["standard_proof"] = proof
    return result
