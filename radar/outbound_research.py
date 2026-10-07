"""Public person discovery followed by the same evidence-based enrichment as Backfill."""

import re
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from radar import (
    data_standard,
    outbound_provider,
    research,
    scoring,
    service,
    sources,
    standard_sources,
)
from radar.validation import normalize_email, public_url, validate_value

# Shared inbox providers cannot identify the person's employer from their domain.
MAIL_PROVIDERS = {
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "yahoo.com",
    "yahoo.com.br",
    "icloud.com",
    "uol.com.br",
    "bol.com.br",
    "proton.me",
    "protonmail.com",
}


def normalize_query(payload):
    mode = payload.get("mode")
    query = str(payload.get("query", "")).strip()
    company = str(payload.get("company", "")).strip()
    website = str(payload.get("website", "")).strip()
    if mode not in {"email", "linkedin", "role_company"} or not query:
        raise ValueError("Informe um e-mail, LinkedIn ou cargo e empresa.")
    if mode == "email":
        query = normalize_email(query)
    elif mode == "linkedin":
        query = validate_value("linkedin_person", query)
    elif not company:
        parts = re.split(r"\s+(?:da|do|na|no|em)\s+", query, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            query, company = parts
    if mode == "role_company" and (len(query) < 3 or len(company) < 2):
        raise ValueError("Informe o cargo e a empresa, por exemplo: BIM Manager da Empresa X.")
    if max(len(query), len(company), len(website)) > 350:
        raise ValueError("Entrada muito longa. Use até 350 caracteres por campo.")
    return {
        "mode": mode,
        "query": query,
        "company": company,
        "website": public_url(website) if website else "",
    }


def role_matches(wanted, found):
    wanted, found = research.normal(wanted), research.normal(found)
    if "bim" in wanted:
        return "bim" in found and bool(
            re.search(r"manager|gerente|coordenad|coordinat|head|lider|lead|responsavel", found)
        )
    tokens = set(re.findall(r"\w+", wanted)) - {
        "de",
        "da",
        "do",
        "a",
        "o",
        "responsavel",
        "representante",
    }
    return bool(tokens) and tokens <= set(re.findall(r"\w+", found))


def company_matches(wanted, found):
    return (
        not wanted
        or research.normal(wanted) in research.normal(found)
        or research.normal(found) in research.normal(wanted)
    )


def prospect_lead(item):
    return {
        "id": str(uuid4()),
        "name": item.get("name", ""),
        "company": item["company"],
        "email": item.get("email", ""),
        "data": {"website": item["website"]} if item.get("website") else {},
        "qualification": {},
    }


def direct_candidates(query, policy, config, cache, standard_config=None):
    website = query["website"]
    if not website and query["mode"] == "email":
        domain = query["query"].split("@", 1)[1]
        if domain not in MAIL_PROVIDERS:
            website = "https://" + domain
    if not website:
        return []
    if website not in cache:
        cache[website] = sources.fetch_public(website)
    resolved, html = cache[website]
    raw_doc = research.document(
        resolved, html, {"name": "", "company": "", "data": {"website": website}}
    )
    company = query["company"]
    if not company:
        company = next(
            (
                record.get("name", "")
                for record in raw_doc["structured"]
                if record.get("@type") in ("Organization", "Corporation")
                and isinstance(record.get("name"), str)
            ),
            "",
        )
    if not company:
        return []
    seed = {
        "name": "",
        "company": company,
        "email": query["query"] if query["mode"] == "email" else "",
        "website": website,
    }
    lead = prospect_lead(seed)
    _, trace = research.collect(
        lead,
        {**policy, "web_search": False},
        score_config=config,
        cache=cache,
        standard_config=standard_config,
    )
    people = []
    for page in trace["pages"]:
        cached = next((value for value in cache.values() if value[0] == page["url"]), None)
        if not cached:
            continue
        doc = research.document(*cached, lead)
        if not doc["company_matched"]:
            continue
        lines = doc["text"].splitlines()
        for index, line in enumerate(lines[:-1]):
            name, role = line.strip(), lines[index + 1].strip()
            if not (
                2 <= len(name.split()) <= 5
                and len(name) <= 100
                and all(word[0].isalpha() for word in name.split())
                and name[0].isupper()
            ):
                continue
            role_words = (
                data_standard.role_pattern(standard_config)
                if standard_config
                else research.ROLE_WORDS
            )
            if len(role) > 100 or not re.search(role_words, role, re.I):
                continue
            nearby = "\n".join(lines[max(0, index - 1) : index + 4])
            if query["mode"] == "role_company" and not role_matches(query["query"], role):
                continue
            if query["mode"] == "email" and query["query"] not in nearby.lower():
                continue
            if query["mode"] == "linkedin" and not any(
                link.rstrip("/") == query["query"]
                and research.normal(name) in research.normal(label)
                for link, label in doc["links"]
            ):
                continue
            people.append(
                {
                    **seed,
                    "name": name,
                    "role": role,
                    "linkedin_person": query["query"] if query["mode"] == "linkedin" else "",
                    "source_url": doc["url"],
                    "quote": nearby,
                    "direct": True,
                }
            )
    if query["mode"] == "email" and not people:
        # The email was supplied, not discovered or guessed. No person is assigned to it.
        return [
            {
                **seed,
                "role": "",
                "linkedin_person": "",
                "source_url": resolved,
                "quote": "",
                "direct": True,
                "email_only": True,
            }
        ]
    return people[:3]


def normalize_candidate(raw, query):
    if not isinstance(raw, dict):
        return None
    try:
        item = {
            key: str(raw.get(key, "")).strip()[:350]
            for key in (
                "name",
                "company",
                "role",
                "email",
                "linkedin_person",
                "website",
                "source_url",
                "quote",
            )
        }
        if not item["company"] or (len(item["name"].split()) < 2 and not raw.get("email_only")):
            return None
        if not company_matches(query["company"], item["company"]):
            return None
        item["source_url"] = public_url(item["source_url"])
        item["website"] = public_url(item["website"]) if item["website"] else ""
        item["email"] = normalize_email(item["email"])
        item["linkedin_person"] = (
            validate_value("linkedin_person", item["linkedin_person"])
            if item["linkedin_person"]
            else ""
        )
        if query["mode"] == "linkedin" and item["linkedin_person"] != query["query"]:
            return None
        if query["mode"] == "email" and item["email"] != query["query"]:
            return None
        if query["mode"] == "role_company" and not role_matches(query["query"], item["role"]):
            return None
        item["email_only"] = bool(raw.get("email_only"))
        return item
    except (ValueError, TypeError):
        return None


def run(query, policy, config, source_config, allow=lambda item: True, standard_config=None):
    cache, notes = {}, []
    items = []
    if outbound_provider.configured():
        items = outbound_provider.search(query)
    if not items:
        try:
            items = direct_candidates(query, policy, config, cache, standard_config)
        except (ValueError, OSError, httpx.HTTPError):
            notes.append(
                "O site informado não pôde ser consultado. Confira o endereço ou use a busca web."
            )
    direct_lookup_possible = bool(query["website"]) or (
        query["mode"] == "email" and query["query"].split("@", 1)[1] not in MAIL_PROVIDERS
    )
    if not items and not outbound_provider.configured() and not direct_lookup_possible:
        raise ValueError(
            "Busca web não configurada. Configure OPENAI_API_KEY e OPENAI_MODEL no servidor, ou informe empresa e site para pesquisa direta."
        )
    candidates, seen = [], set()
    for raw in items[:3]:
        item = normalize_candidate(raw, query)
        if not item:
            continue
        candidate_id = service.digest(
            [item["name"], item["company"], item["email"], item["linkedin_person"]]
        )[:24]
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        if not allow(item):
            notes.append("Um resultado corresponde a contato na lista de exclusão e foi omitido.")
            continue
        lead = prospect_lead(item)
        verified, proof_doc = False, None
        try:
            if item["source_url"] not in cache:
                cache[item["source_url"]] = sources.fetch_public(item["source_url"])
            proof_doc = research.document(*cache[item["source_url"]], lead)
            verified = bool(
                item["quote"]
                and research.normal(item["quote"]) in research.normal(proof_doc["text"])
                and research.normal(item["name"]) in research.normal(item["quote"])
                and proof_doc["company_matched"]
            )
            if query["mode"] == "email":
                verified = verified and query["query"] in item["quote"].lower()
            if query["mode"] == "linkedin":
                verified = verified and any(
                    link.rstrip("/") == query["query"]
                    and research.normal(item["name"]) in research.normal(label)
                    for link, label in proof_doc["links"]
                )
        except (ValueError, OSError, httpx.HTTPError):
            pass
        if (
            query["mode"] != "email"
            and item["email"]
            and (not verified or item["email"] not in item["quote"].lower())
        ):
            item["email"] = ""
            lead["email"] = ""
        proposals, trace = [], {"notes": [], "pages": []}
        if item["website"]:
            proposals, trace = research.collect(
                lead,
                {**policy, "web_search": False},
                score_config=config,
                cache=cache,
                use_ai=source_config.get("ai", False),
                standard_config=standard_config,
            )
        if (
            verified
            and item["role"]
            and research.normal(item["role"]) in research.normal(item["quote"])
        ):
            proposal = research.candidate(
                lead, "role", item["role"], proof_doc, item["quote"], rule="outbound-public-role"
            )
            if not any(p["field"] == "role" and p["value"] == item["role"] for p in proposals):
                proposals.append(scoring.rescore(proposal, config))
        cnpjs = {p["value"] for p in proposals if p["field"] == "cnpj"}
        if source_config.get("registry") and len(cnpjs) == 1:
            cnpj = next(iter(cnpjs))
            try:
                registry_lead = {**lead, "data": {**lead["data"], "cnpj": cnpj}}
                registered, registry_notes = standard_sources.registry(registry_lead)
                trace["notes"].extend(registry_notes)
                found = research.registry_candidates(registry_lead, registered)
                # Bind suggestions to the reviewed candidate's current research inputs.
                for proposal in found:
                    proposal["assessment"]["input_fields"] = ["website"] if item["website"] else []
                    proposal["assessment"]["basis"] = research.backfill.context_basis(
                        lead, proposal["assessment"]["input_fields"]
                    )
                    proposals.append(scoring.rescore(proposal, config))
            except (ValueError, OSError, httpx.HTTPError):
                trace["notes"].append("Consulta cadastral indisponível; demais campos preservados.")
        candidates.append(
            {
                **item,
                "id": candidate_id,
                "identity_verified": verified,
                "identity_note": "Nome e vínculo encontrados no trecho da página consultada; confira se continuam atuais."
                if verified
                else "Identidade ainda precisa de conferência. O resultado de busca ou domínio do e-mail não comprova o vínculo atual.",
                "collected_at": datetime.now(UTC).isoformat(),
                "proposals": proposals,
                "notes": trace["notes"],
            }
        )
    return {
        "candidates": candidates,
        "notes": notes,
        "provider": "Busca web + fontes públicas"
        if outbound_provider.configured()
        else "Pesquisa direta no site",
        "remote_writes": 0,
    }
