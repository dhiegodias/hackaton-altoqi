"""Bounded discovery and evidence extraction; no CRM mutations.

Public pages are untrusted. Every fetch uses the existing pinned-IP transport.
Entity matching, quotes and score components remain visible for review.
"""

import json
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from radar import backfill, data_standard, research_ai, scoring, sources, standard_sources
from radar.validation import normalize_cnpj, valid_cnpj, validate_value

ROLE_WORDS = r"\b(?:CEO|CFO|CTO|COO|CRO|presidente|diretor\w*|sóci[oa]|fundador\w*|head|chief|gerente|manager|coordenador\w*|coordinator|líder|leader|especialista|specialist|analista|analyst|engenheir\w*|engineer|arquiteto|arquiteta|projetista|supervisor\w*|inside sales)\b"
ACTIVITIES = {
    "software_vendor": r"(?:desenvolv\w*|fornec\w*|licenci\w*)\s+(?:de\s+)?(?:softwares?|programas de computador)|soluções tecnológicas para projetos",
    "structural_design": r"(?:elabor\w*|desenvolv\w*|realiz\w*|especializ\w*|atua\w*)[^.!?\n]{0,65}projetos? estruturais|(?:nossos serviços|serviços de)[:\s]+projetos? estruturais",
    "installation_design": r"(?:elabor\w*|desenvolv\w*|realiz\w*|especializ\w*|atua\w*)[^.!?\n]{0,65}projetos? (?:de )?(?:instalações|elétric\w*|hidrossanitári\w*)|(?:nossos serviços|serviços de)[:\s]+projetos? (?:de )?instalações",
    "construction_management": r"(?:construímos|executamos obras|gerenciamos obras|gestão de obras|construtora de|incorporadora de)",
    "budgeting_service": r"(?:prestamos|elaboramos|serviços de)[^.!?\n]{0,50}(?:orçamento de obras|orçamentação|orçamentos BIM)",
    "architecture_only": r"(?:escritório de arquitetura|projetos arquitetônicos)",
}


def normal(value):
    return " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKD", str(value)).casefold()
            if not unicodedata.combining(char)
        ).split()
    )


def host(url):
    return (urlparse(url).hostname or "").lower().removeprefix("www.").rstrip(".")


def publisher(url):
    parts = host(url).split(".")
    # Conservative grouping; subdomains of a company are never independent proof.
    suffix = ".".join(parts[-2:])
    return ".".join(
        parts[-3:] if suffix in {"com.br", "org.br", "net.br", "gov.br", "co.uk"} else parts[-2:]
    )


def same_site(url, website):
    anchor = host(website)
    return bool(anchor and (host(url) == anchor or host(url).endswith("." + anchor)))


def document(url, html, lead):
    soup = BeautifulSoup(html, "html.parser")
    links = [
        (urljoin(url, a["href"]), a.get_text(" ", strip=True))
        for a in soup.find_all("a", href=True)
    ]
    published = None
    for meta in soup.select('meta[property="article:published_time"],meta[name="date"]'):
        try:
            published = (
                datetime.fromisoformat(meta.get("content", "").replace("Z", "+00:00"))
                .date()
                .isoformat()
            )
        except ValueError:
            continue
    structured = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            root = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        records = root if isinstance(root, list) else [root]
        for record in list(records):
            if isinstance(record, dict) and isinstance(record.get("@graph"), list):
                records.extend(record["@graph"])
        structured.extend(record for record in records if isinstance(record, dict))
    for record in structured:
        if not published and isinstance(record.get("datePublished"), str):
            try:
                published = (
                    datetime.fromisoformat(record["datePublished"].replace("Z", "+00:00"))
                    .date()
                    .isoformat()
                )
            except (ValueError, TypeError):
                pass
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for node in soup(["script", "style"]):
        node.decompose()
    legal_text = " ".join(soup.stripped_strings)[:60000]
    for node in soup(["nav", "footer", "header"]):
        node.decompose()
    text = "\n".join(soup.stripped_strings)[:45000]
    company = normal(lead["company"])
    matched = len(company) >= 3 and company in normal(title + " " + text)
    return {
        "url": url,
        "title": title,
        "text": text,
        "links": links,
        "structured": structured,
        "legal_text": legal_text,
        "published_at": published,
        "accessed_at": datetime.now(UTC).isoformat(),
        "official": data_standard.website_matches(url, lead),
        "company_matched": matched,
    }


def evidence(doc, quote):
    return {
        key: doc[key]
        for key in ("url", "title", "published_at", "accessed_at", "official", "company_matched")
    } | {"quote": quote[:400]}


def candidate(
    lead,
    field,
    value,
    doc,
    quote,
    *,
    kind="fact",
    rule="literal",
    reason="",
    extra_evidence=(),
    person_matched=True,
):
    value = validate_value(field, value)
    proof = [evidence(doc, quote), *extra_evidence]
    publishers = {publisher(item["url"]) for item in proof}
    observed = {
        "source": "source_site" if doc["official"] else "source_other",
        "identity": "identity_match"
        if doc["company_matched"] and person_matched
        else "identity_unresolved",
        "kind": kind,
        "date": "dated" if doc["published_at"] else "undated",
        "corroborated": len(publishers) > 1,
    }
    blockers = []
    if not doc["company_matched"] or not person_matched:
        blockers.append("Identidade da empresa/pessoa precisa ser conciliada.")
    explanation = reason or "Valor publicado na fonte vinculada ao cadastro."
    if not doc["published_at"]:
        explanation += " A página não informa a data de publicação; a data exibida é a da coleta."
    return {
        "field": field,
        "value": value,
        "source_kind": "backfill",
        "source_url": doc["url"],
        "evidence": quote[:700],
        "confidence": 0,  # Filled from the published configuration after collection.
        "standard_proof": data_standard.seal(field, value, "website")
        if field in {"role", "segment", "phone"}
        and doc["official"]
        and doc["company_matched"]
        and person_matched
        else {},
        "assessment": {
            "version": backfill.VERSION,
            "kind": kind,
            "rule": rule,
            "reason": explanation,
            "signals": observed,
            "evidence": proof,
            "blockers": blockers,
            "input_fields": [field for field in ("website", "cnpj") if lead["data"].get(field)],
            "basis": backfill.context_basis(
                lead, [field for field in ("website", "cnpj") if lead["data"].get(field)]
            ),
        },
    }


def role_candidates(lead, doc, role_words=None):
    role_words = role_words or ROLE_WORDS
    name = lead["name"].strip()
    if len(name.split()) < 2 or not doc["company_matched"]:
        return []
    results = []
    lines = doc["text"].splitlines()
    for index, line in enumerate(lines):
        if normal(line) != normal(name):
            continue
        nearby = lines[index + 1 : index + 3]
        if nearby and 2 <= len(nearby[0]) <= 100 and re.search(role_words, nearby[0], re.I):
            quote = line + "\n" + nearby[0]
            results.append(
                candidate(
                    lead,
                    "role",
                    nearby[0],
                    doc,
                    quote,
                    rule="named-team-profile",
                    reason="Nome completo e cargo aparecem juntos na página da empresa.",
                )
            )
    for match in re.finditer(
        re.escape(name) + r"\s*[,–—:-]\s*([^.!?\n]{2,100})", doc["text"], re.I
    ):
        role = match.group(1).strip()
        if re.search(role_words, role, re.I):
            results.append(
                candidate(lead, "role", role, doc, match.group(), rule="named-role-statement")
            )
    for item in results[:2]:
        title = normal(item["value"])
        senior = re.search(
            r"\b(ceo|cfo|cto|coo|cro|presidente|socio|socia|diretor|diretora|head|chief|fundador|fundadora)\b",
            title,
        )
        value = "Provável decisor" if senior else "Provável influenciador"
        inferred = candidate(
            lead,
            "decision_role",
            value,
            doc,
            item["evidence"],
            kind="inference",
            rule="buying-role-from-public-title",
            reason="Cargo sugere participação na decisão; não comprova alçada, orçamento ou intenção de compra. Confirmar no contato comercial.",
        )
        results.append(inferred)
    return results


def headcount_candidates(lead, doc):
    if not doc["company_matched"]:
        return []
    results = []
    expression = r"(?P<prefix>mais de|\+\s*de|cerca de|aproximadamente|entre)?\s*(?P<low>\d[\d.]*)\s*(?:(?:a|e|até|–|-)\s*(?P<high>\d[\d.]*))?\s*(?P<plus>\+)?\s*(?:colaboradores|funcionários|empregados|employees)\b"
    for match in re.finditer(expression, doc["text"], re.I):
        quote = match.group().strip()
        low = int(match.group("low").replace(".", ""))
        if low > 1000000:
            continue
        context = normal(doc["text"][max(0, match.start() - 90) : match.end() + 90])
        if any(
            word in context
            for word in (
                "vagas",
                "nosso cliente",
                "nossos clientes",
                "nosso parceiro",
                " e da ",
                " e do ",
                "somando",
                "grupo inteiro",
            )
        ):
            continue
        prefix = normal(match.group("prefix") or "")
        if match.group("high"):
            high = int(match.group("high").replace(".", ""))
            if high < low:
                continue
            value, kind, field = f"{low}–{high}", "estimate", "employee_range"
        elif prefix or match.group("plus"):
            if prefix in {"cerca de", "aproximadamente"}:
                # An approximation is not a one-sided lower bound.
                continue
            value, kind, field = f"{low}+", "estimate", "employee_range"
        else:
            value, kind, field = low, "fact", "employee_count"
        results.append(
            candidate(
                lead,
                field,
                value,
                doc,
                quote,
                kind=kind,
                rule="public-headcount-statement",
                reason="Escala publicada pela empresa; faixa/limite inferior fica separado da quantidade exata.",
            )
        )
    return results


def social_candidates(lead, doc):
    results = []
    if not doc["official"] or not doc["company_matched"]:
        return results
    for url, _label in doc["links"]:
        parsed = urlparse(url)
        domain = host(url)
        field = None
        if domain in {
            "linkedin.com",
            "br.linkedin.com",
            "pt.linkedin.com",
        } and parsed.path.startswith("/company/"):
            field = "linkedin"
            url = urlunparse(("https", "www.linkedin.com", parsed.path.rstrip("/"), "", "", ""))
        elif (
            domain == "instagram.com"
            and parsed.path.strip("/")
            and not parsed.path.startswith(("/p/", "/reel/", "/share"))
        ):
            field = "instagram"
            url = urlunparse(
                ("https", "www.instagram.com", parsed.path.rstrip("/").lower(), "", "", "")
            )
        if field:
            try:
                results.append(
                    candidate(
                        lead,
                        field,
                        url,
                        doc,
                        "Link corporativo publicado na página: " + url,
                        rule="official-social-link",
                        reason="Link corporativo no site informado; LinkedIn regional normalizado, sem raspar rede social.",
                    )
                )
            except ValueError:
                continue
    return results


def structured_candidates(lead, doc):
    """Retain facts supported by explicit Organization markup and legal text."""
    if not doc["company_matched"]:
        return []
    results = []
    company = normal(lead["company"])
    for item in doc["structured"]:
        names = normal(item.get("name", ""))
        types = item.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if (
            not isinstance(types, list)
            or "Organization" not in types
            or not names
            or company not in names
        ):
            continue
        links = item.get("sameAs", [])
        if isinstance(links, str):
            links = [links]
        if isinstance(links, list):
            results.extend(
                social_candidates(
                    lead,
                    {**doc, "links": [(link, "sameAs") for link in links if isinstance(link, str)]},
                )
            )
        employees = item.get("numberOfEmployees")
        if isinstance(employees, dict):
            employees = employees.get("value")
        if type(employees) is int and 0 <= employees <= 1000000:
            results.append(
                candidate(
                    lead,
                    "employee_count",
                    employees,
                    doc,
                    f"Organization {item['name']}: numberOfEmployees = {employees}",
                    rule="organization-structured-headcount",
                )
            )
    cnpjs = {
        normalize_cnpj(match)
        for match in re.findall(
            r"\b[A-Z0-9]{2}\.?[A-Z0-9]{3}\.?[A-Z0-9]{3}/?[A-Z0-9]{4}-?\d{2}\b", doc["legal_text"]
        )
    }
    cnpjs = {number for number in cnpjs if valid_cnpj(number)}
    if len(cnpjs) == 1:
        number = cnpjs.pop()
        results.append(
            candidate(
                lead,
                "cnpj",
                number,
                doc,
                f"CNPJ publicado na página: {number}",
                rule="public-cnpj-statement",
                reason="CNPJ único válido publicado no site da empresa; vínculo sujeito à revisão.",
            )
        )
    return results


def registry_candidates(lead, proposals):
    results = []
    for item in proposals:
        doc = {
            "url": item["source_url"],
            "title": "BrasilAPI · CNPJ "
            + str(item.get("standard_proof", {}).get("cnpj") or lead["data"].get("cnpj", "")),
            "official": False,
            "company_matched": True,
            "published_at": None,
            "accessed_at": datetime.now(UTC).isoformat(),
        }
        result = candidate(
            lead,
            item["field"],
            item["value"],
            doc,
            item["evidence"],
            rule="registry-cnpj-match",
            reason="O CNPJ retornado pelo agregador coincide com o informado. Atualidade cadastral depende da origem.",
        )
        result["source_kind"] = "registry"
        result["standard_proof"] = item.get("standard_proof", {})
        results.append(result)
    return results


def activity_signals(doc):
    signals = []
    for key, pattern in ACTIVITIES.items():
        match = re.search(pattern, doc["text"], re.I)
        if match:
            signals.append({"activity": key, "doc": doc, "quote": match.group()})
    return signals


def collect(
    lead, config, *, score_config, fetch=None, use_ai=False, cache=None, standard_config=None
):
    role_words = data_standard.role_pattern(standard_config) if standard_config else ROLE_WORDS
    lead = {**lead, "data": data_standard.inputs(lead)}
    fetch = fetch or sources.fetch_public
    cache = cache if cache is not None else {}
    queue = []
    if lead["data"].get("website"):
        queue.append(lead["data"]["website"])
    notes = []
    ai_completed = False
    if config["web_search"]:
        try:
            queue.extend(research_ai.discover(lead))
        except (ValueError, OSError, httpx.HTTPError) as exc:
            notes.append(
                str(exc)
                if isinstance(exc, ValueError)
                else "Busca indisponível; pesquisa no site preservada."
            )
    documents, seen = [], set()
    max_pages = config["max_pages"]
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        parsed = urlparse(url)
        url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        if url in seen:
            continue
        seen.add(url)
        try:
            if url not in cache:
                cache[url] = fetch(url)
            resolved, html = cache[url]
            doc = document(resolved, html, lead)
        except (ValueError, OSError, httpx.HTTPError) as exc:
            notes.append(f"Página indisponível ({type(exc).__name__}): {url[:180]}")
            continue
        documents.append(doc)
        links = []
        for link, label in doc["links"]:
            if not same_site(link, lead["data"].get("website", "")) or link in seen:
                continue
            if re.search(r"\.(pdf|zip|exe|png|jpg|svg)(?:$|\?)", link, re.I):
                continue
            rank = (
                30
                if re.search("institucional|quem.somos|sobre|about|equipe|team", link + label, re.I)
                else 25
                if re.search("carreir|career|imprensa", link + label, re.I)
                else 10
                if re.search("servi[cç]os|services|empresa|contato", link + label, re.I)
                else 0
            )
            if rank:
                links.append((rank, link))
        queue.extend(
            link for _, link in sorted(links, key=lambda pair: -pair[0]) if link not in queue
        )
    proposals, signals = [], []
    for doc in documents:
        proposals.extend(structured_candidates(lead, doc))
        proposals.extend(social_candidates(lead, doc))
        proposals.extend(role_candidates(lead, doc, role_words))
        proposals.extend(headcount_candidates(lead, doc))
        proposals.extend(standard_sources.phone_candidates(lead, doc))
        if doc["company_matched"]:
            signals.extend(activity_signals(doc))
    if use_ai and documents:
        try:
            interpreted = research_ai.interpret(lead, documents)
            ai_completed = True
            for item in interpreted:
                if not isinstance(item, dict) or not isinstance(item.get("value"), str):
                    continue
                index, quote = item.get("document_id"), item.get("quote", "")
                if (
                    type(index) is not int
                    or not 0 <= index < len(documents)
                    or not isinstance(quote, str)
                    or not 8 <= len(quote) <= 250
                ):
                    continue
                doc = documents[index]
                if quote not in doc["text"] or not doc["company_matched"]:
                    continue
                field, value = item.get("field"), item.get("value")
                if field == "activity" and value in ACTIVITIES:
                    signals.append({"activity": value, "doc": doc, "quote": quote})
                elif (
                    field == "role"
                    and len(lead["name"].split()) >= 2
                    and normal(lead["name"]) in normal(quote)
                    and normal(value) in normal(quote)
                    and re.search(role_words, value, re.I)
                ):
                    proposals.append(
                        candidate(
                            lead,
                            field,
                            value,
                            doc,
                            quote,
                            rule="ai-grounded-extraction",
                            reason=item.get("reason", ""),
                        )
                    )
                    # Reuse the same commercial inference regardless of extraction engine.
                    inferred_doc = {**doc, "text": lead["name"] + "\n" + value}
                    inferred = role_candidates(lead, inferred_doc, role_words)
                    for suggestion in inferred:
                        if suggestion["field"] == "decision_role":
                            suggestion["evidence"] = quote
                            suggestion["assessment"]["evidence"] = [evidence(doc, quote)]
                            proposals.append(suggestion)
                elif field in {"employee_count", "employee_range"}:
                    # Independent numeric/category parser must agree with the claimed extraction.
                    excerpt_doc = {**doc, "text": quote}
                    candidates = headcount_candidates(lead, excerpt_doc)
                    proposals.extend(
                        proposal
                        for proposal in candidates
                        if proposal["field"] == field and str(proposal["value"]) == str(value)
                    )
                # Segment is derived by the commercial activity rules below, so an
                # opaque AI classification cannot contradict a software-vendor signal.
        except (ValueError, OSError, httpx.HTTPError) as exc:
            notes.append(
                str(exc) if isinstance(exc, ValueError) else "Interpretação por IA indisponível."
            )
    from radar.prospecting import infer_fit

    proposals.extend(infer_fit(lead, signals))
    unique = {}
    for proposal in proposals:
        key = (proposal["field"], str(proposal["value"]))
        if key not in unique:
            unique[key] = proposal
        else:
            proof = unique[key]["assessment"]["evidence"]
            for item in proposal["assessment"]["evidence"]:
                age = (
                    (
                        datetime.now(UTC).date()
                        - datetime.fromisoformat(item["published_at"]).date()
                    ).days
                    if item["published_at"]
                    else 0
                )
                if 0 <= age <= 90 and item["url"] not in {existing["url"] for existing in proof}:
                    proof.append(item)
    if not documents:
        notes.append(
            "Nenhuma página utilizável. Informe o site ou configure a busca web para descobrir fontes pelo nome."
        )
    return [scoring.rescore(item, score_config) for item in unique.values()], {
        "pages": [
            {key: doc[key] for key in ("url", "title", "published_at", "accessed_at")}
            for doc in documents
        ],
        "notes": notes,
        "engine": backfill.VERSION,
        "ai_requested": use_ai,
        "ai_used": ai_completed,
        "web_search": config["web_search"],
    }
