"""Public providers for registry identity, IBGE names and professional contacts."""

import json
import re

from radar import data_standard as standard
from radar import sources
from radar.validation import normalize_cnpj, valid_cnpj, validate_value


def company_key(value):
    value = standard.normal(value)
    return " ".join(re.sub(r"[^\w ]", " ", value).split())


def matches_company(company, record):
    needle = company_key(company)
    if not needle or needle in {"empresa", "engenharia", "construtora", "projetos", "arquitetura"}:
        return False
    for key in ("razao_social", "nome_fantasia"):
        full = company_key(record.get(key, ""))
        if full and (full == needle or (" " + needle + " ") in (" " + full + " ")):
            return True
    return False


def municipality(record, fetch):
    code = str(record.get("codigo_municipio_ibge") or "")
    state = str(record.get("uf", "")).upper()
    if not re.fullmatch("[A-Z]{2}", state):
        raise ValueError("Cadastro sem UF válida para conferência no IBGE.")
    if re.fullmatch(r"\d{7}", code):
        url, body = fetch("https://servicodados.ibge.gov.br/api/v1/localidades/municipios/" + code)
        candidates = [json.loads(body)]
    else:
        url, body = fetch(
            "https://servicodados.ibge.gov.br/api/v1/localidades/estados/" + state + "/municipios"
        )
        candidates = json.loads(body)
    if standard.host(url) != "servicodados.ibge.gov.br":
        raise ValueError("Resposta do IBGE redirecionada para fonte não permitida.")
    for item in candidates:
        uf = (item.get("microrregiao") or {}).get("mesorregiao", {}).get("UF", {})
        if not uf:
            uf = (item.get("regiao-imediata") or {}).get("regiao-intermediaria", {}).get("UF", {})
        if (
            (not re.fullmatch(r"\d{7}", code) or str(item.get("id")) == code)
            and uf.get("sigla") == state
            and standard.normal(item.get("nome", ""))
            == standard.normal(record.get("municipio", ""))
        ):
            return {
                "city": item["nome"],
                "state": uf["sigla"],
                "ibge_code": str(item["id"]),
                "ibge_url": url,
            }
    raise ValueError("Cidade/UF do cadastro não conferem com o registro oficial do IBGE.")


def registry(lead, fetch=None, cnpj=None):
    fetch = fetch or sources.fetch_public
    number = normalize_cnpj(cnpj or standard.inputs(lead).get("cnpj", ""))
    if not valid_cnpj(number):
        raise ValueError("Informe um CNPJ válido para consultar os dados cadastrais.")
    if not number.isdigit():
        raise ValueError(
            "CNPJ alfanumérico válido. O provedor cadastral disponível ainda não consulta esse formato; os campos permanecem pendentes, sem confirmação inventada."
        )
    url, body = fetch("https://brasilapi.com.br/api/cnpj/v1/" + number)
    if standard.host(url) != "brasilapi.com.br":
        raise ValueError("Resposta cadastral redirecionada para fonte não permitida.")
    record = json.loads(body)
    if normalize_cnpj(record.get("cnpj", "")) != number or not matches_company(
        lead["company"], record
    ):
        raise ValueError(
            "Empresa informada não coincide com a razão social/nome fantasia deste CNPJ. Confira a identidade antes de preencher."
        )
    values = {
        "cnpj": number,
        "legal_name": record.get("razao_social"),
        "company_size": record.get("porte"),
        "cnae": record.get("cnae_fiscal"),
        "capital_social": record.get("capital_social"),
    }
    # Do not discard valid registry facts if the independent city check is unavailable.
    notes = []
    extra = {}
    try:
        extra = municipality(record, fetch)
        values.update({k: extra[k] for k in ("city", "state")})
    except (ValueError, OSError) as exc:
        notes.append(
            str(exc) if isinstance(exc, ValueError) else "IBGE indisponível; cidade e UF pendentes."
        )
    results = []
    for field, value in values.items():
        if value in (None, ""):
            continue
        value = validate_value(field, value)
        proof = standard.seal(
            field, value, "registry", cnpj=number, company=lead["company"], **extra
        )
        item = sources.suggestion(
            field,
            value,
            "registry",
            extra["ibge_url"] if field in {"city", "state"} else url,
            f"Cadastro CNPJ {number}: {field} = {value}. Empresa: {record['razao_social']}."
            + (
                f" Localidade conferida no IBGE; cadastro de origem: {url}."
                if field in {"city", "state"}
                else ""
            ),
            95,
        )
        item["standard_proof"] = proof
        results.append(item)
    # Only explicit CNAE classifications; no company-size or profession inference.
    code = str(record.get("cnae_fiscal", ""))
    segment = (
        "Instaladora"
        if code.startswith("432")
        else "Construtora"
        if code.startswith("412")
        else "Incorporadora"
        if code.startswith("411")
        else None
    )
    if segment:
        item = sources.suggestion(
            "segment",
            segment,
            "registry",
            url,
            f"CNAE {code}: {record.get('cnae_fiscal_descricao', '')}",
            90,
        )
        item["standard_proof"] = standard.seal(
            "segment", segment, "registry", cnpj=number, company=lead["company"]
        )
        results.append(item)
    return results, notes


def phone_candidates(lead, doc):
    from radar import research

    if not doc["official"] or not doc["company_matched"]:
        return []
    raw = [
        (url.removeprefix("tel:"), label) for url, label in doc["links"] if url.startswith("tel:")
    ]
    for item in doc["structured"]:
        if (
            item.get("@type") == "Organization"
            and standard.normal(lead["company"]) in standard.normal(item.get("name", ""))
            and isinstance(item.get("telephone"), str)
        ):
            raw.append(
                (item["telephone"], "Telefone da organização publicado: " + item["telephone"])
            )
    raw.extend(
        (m.group(), m.group())
        for m in re.finditer(r"(?:\+55\s*)?\(?\d{2}\)?[\s.-]+\d{4,5}[\s.-]?\d{4}\b", doc["text"])
    )
    results = []
    for phone, quote in raw:
        try:
            value = validate_value("phone", phone)
        except ValueError:
            continue
        item = research.candidate(
            lead, "phone", value, doc, quote or phone, rule="official-business-phone"
        )
        item["standard_proof"] = standard.seal("phone", value, "website")
        results.append(item)
    return results


def from_url(lead, url, cfg, fetch=None):
    from radar import research

    fetch = fetch or sources.fetch_public
    council = standard.is_council(url, cfg)
    if not council and not standard.website_matches(url, lead):
        raise ValueError(
            "Use uma página do site informado ou um domínio CAU/CREA aprovado em Padrão de dados."
        )
    resolved, html = fetch(url)
    if not (
        standard.is_council(resolved, cfg) if council else standard.website_matches(resolved, lead)
    ):
        raise ValueError("Redirecionamento para fonte fora do padrão.")
    doc = research.document(resolved, html, lead)
    if council:
        # Registration establishes a professional title, never employment or buying power.
        name = standard.normal(lead["name"])
        if len(name.split()) < 2 or name not in standard.normal(doc["text"]):
            raise ValueError("A página do conselho não identifica o nome completo deste contato.")
        results = []
        for line in doc["text"].splitlines():
            if name not in standard.normal(line):
                continue
            for role in cfg["roles"]:
                for alias in [role["label"], *role["aliases"]]:
                    if standard.normal(alias) in standard.normal(line):
                        item = research.candidate(
                            lead,
                            "role",
                            role["label"],
                            {**doc, "company_matched": True},
                            line,
                            rule="council-professional-title",
                            reason="Título profissional publicado pelo conselho; não comprova vínculo empregatício ou alçada.",
                        )
                        item["standard_proof"] = standard.seal("role", role["label"], "council")
                        results.append(item)
                        break
        if not results:
            raise ValueError(
                "Título profissional não legível junto do nome. Consultas com login, formulário ou CAPTCHA precisam de conferência no conselho; o Radar não contorna esses controles."
            )
        return results
    results = research.role_candidates(lead, doc, standard.role_pattern(cfg)) + phone_candidates(
        lead, doc
    )
    if not results:
        raise ValueError(
            "Nenhum cargo ou telefone profissional atribuível encontrado nesta página."
        )
    return results
