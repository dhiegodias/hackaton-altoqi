"""Radar B: decisions from attributable evidence; no storage or network dependencies."""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

PLAYBOOK_OFFICE = "https://solution-playbook.vercel.app/escritorios/icp.html"
PLAYBOOK_IMPULSE = "https://solution-playbook.vercel.app/escritorios/ofertas.html"
PLAYBOOK_BUILD = "https://solutionplaybookgestaodaconstrucao.netlify.app/"


@dataclass(frozen=True)
class Observation:
    value: Any
    source: str
    reference: str
    captured: date
    excerpt: str
    kind: str = "fact"  # fact | declaration | inference | absent
    identity: str = "matched"

    def problem(self, today: date, max_days: int = 180) -> str | None:
        if self.kind not in {"fact", "declaration"}:
            return "Inference or absence is not an observed fact"
        if self.source not in {"registry", "company", "consultant", "crm"}:
            return "Source is not permitted"
        if self.kind == "declaration" and self.source != "consultant":
            return "A declaration requires an attributed consultant"
        if self.source == "consultant" and self.kind != "declaration":
            return "Consultant reports must remain labelled declarations"
        if self.identity != "matched":
            return "Entity identity must be resolved"
        if self.value is None or self.value == "" or not self.reference or not self.excerpt:
            return "Missing value, source reference or supporting excerpt"
        if self.source in {"registry", "company"}:
            parsed = urlparse(self.reference)
            if parsed.scheme != "https" or not parsed.hostname:
                return "Public evidence requires an HTTPS source URL"
        age = (today - self.captured).days
        if age < 0 or age > max_days:
            return "Evidence is stale or future dated"
        return None


@dataclass
class Recommendation:
    group: str
    route: str
    offers: list[str]
    reasons: list[str]
    questions: list[str]
    evidence: dict[str, str]
    playbook: str
    kind: str = "inference"


def recommend(observations: dict[str, Observation], today: date) -> Recommendation:
    """Missing, stale and inferred inputs remain unknown, never false/zero."""
    evidence, questions, reasons = {}, [], []

    def get(key: str, question: str = "") -> Any:
        item = observations.get(key)
        if item is None or item.problem(today):
            if question and question not in questions:
                questions.append(question)
            return None
        evidence[key] = item.reference
        return item.value

    def result(group, route, offers, reason, playbook=PLAYBOOK_BUILD):
        return Recommendation(
            group, route, offers, reasons + [reason], questions.copy(), evidence.copy(), playbook
        )

    model = get("business_model", "A empresa projeta, constrói ou presta gestão a terceiros?")
    if model == "public_owner":
        return result(
            "Outra esteira", "Setor público", [], "Órgão dono da obra tem playbook próprio."
        )
    if model == "manufacturer":
        return result(
            "Iniciativa específica",
            "Fabricantes",
            [],
            "Oferta para o próprio produto; não é BIM ONE.",
        )
    if model in {"investor", "land_developer"}:
        return result(
            "Fora de alvo",
            "Relacionamento",
            [],
            "Capital ou lote sem edificação não define consumidor da plataforma.",
        )
    if model == "engineering_office":
        building = get("building_design", "A autoria é de engenharia predial?")
        disciplines = get("disciplines", "Produz estruturas, instalações ou só arquitetura?")
        designers = get(
            "designers", "Quantos projetistas atuam no escritório, sem contar outras funções?"
        )
        if building is False or (
            disciplines is not None and not set(disciplines) & {"structure", "installations"}
        ):
            return result(
                "PBA Escritórios",
                "Avaliação pontual",
                [],
                "Baixa autoria de engenharia predial.",
                PLAYBOOK_OFFICE,
            )
        if building is not True or not disciplines or type(designers) is not int or designers < 1:
            return result(
                "A qualificar",
                "Discovery Escritórios",
                [],
                "Faltam critérios de autoria ou quantidade de projetistas.",
                PLAYBOOK_OFFICE,
            )
        products = (["Eberick"] if "structure" in disciplines else []) + (
            ["Builder"] if "installations" in disciplines else []
        )
        if designers <= 2:
            return result(
                "PA2 Escritório P",
                "FSB",
                products,
                "Até 2 projetistas: entrada técnica gradual.",
                PLAYBOOK_OFFICE,
            )
        group = "ICP 1 Escritório M" if designers <= 15 else "ICP 2 Escritório G"
        pain = get("pain", "A dor prioritária está no projeto técnico ou na gestão da operação?")
        sponsor = get("sponsor", "Existe sponsor interno para conduzir a mudança?")
        capacity = get("assisted_capacity", "Há disponibilidade para operação assistida?")
        if (
            sponsor is not True
            or capacity is not True
            or pain not in {"engineering", "management", "both"}
        ):
            return result(
                group,
                "Diagnóstico Impulso",
                products,
                "Perfil aderente; a jornada exige dor, sponsor e disponibilidade.",
                PLAYBOOK_IMPULSE,
            )
        maturity = get(
            "maturity", "Avaliar as 7 dimensões de maturidade antes de propor trilhas simultâneas."
        )
        complete = (
            isinstance(maturity, list)
            and len(maturity) == 7
            and all(type(v) is int and 1 <= v <= 3 for v in maturity)
        )
        if complete:
            reasons.append(f"Maturidade declarada no diagnóstico: {sum(maturity)}/21.")
        if pain == "both" and complete and sum(maturity) >= 16:
            return result(
                group,
                "Impulso Completo",
                products + ["Visus Collab", "Visus Workflow"],
                "Dor nas duas frentes e prontidão confirmada para diagnóstico da oferta.",
                PLAYBOOK_IMPULSE,
            )
        if pain == "both":
            questions.append(
                "Qual dor deve vir primeiro? Não iniciar as duas trilhas sem prontidão."
            )
            return result(
                group,
                "Escolher uma trilha",
                products,
                "Maturidade não demonstrada para Impulso Completo.",
                PLAYBOOK_IMPULSE,
            )
        offers = products if pain == "engineering" else ["Visus Collab", "Visus Workflow"]
        return result(
            group,
            "Impulso Engenharia" if pain == "engineering" else "Impulso Gestão",
            offers,
            "Trilha alinhada à dor declarada.",
            PLAYBOOK_IMPULSE,
        )
    if model == "management_service":
        service = get(
            "service", "Entrega orçamento, coordenação, compatibilização ou planejamento?"
        )
        offers = {
            "budget": ["Visus Cost por usuário"],
            "coordination": ["Visus Collab", "Visus Workflow"],
            "compatibility": ["Projetos com qualidade · validar escopo"],
            "planning": [],
        }.get(service, [])
        if service == "planning":
            questions.append(
                "Validar escopo com Sales Engineering; não prometer Planning como solução pronta."
            )
        return result(
            "Nicho",
            "Comercial + Sales Engineering",
            offers,
            "Presta gestão a terceiros; parceria é evolução condicionada ao programa.",
        )
    if model not in {"builder", "developer", "industrial_owner"}:
        return result(
            "A qualificar", "Discovery", [], "O modelo de negócio ainda não está confirmado."
        )
    absorbs = get(
        "absorbs_method", "Há ao menos uma pessoa com tempo para aprender e sustentar o método?"
    )
    if absorbs is False:
        return result(
            "PBA Gestão",
            "Qualificar encaminhamento a Alliancer",
            ["BIM ONE"],
            "Declarou não absorver internamente; validar parceiro disponível antes de prometer entrega.",
        )
    if absorbs is None:
        return result(
            "A qualificar",
            "Discovery Gestão",
            [],
            "Ausência de evidência de absorção não equivale a ausência de time.",
        )
    custom = get("needs_customization")
    scale = get("large_scale")
    funds = get("adaptation_budget")
    if custom is True and scale is True and funds is True:
        return result(
            "PAP",
            "BizDev",
            [],
            "Escala, verba e adaptação confirmadas; sem promessa de prateleira.",
        )
    building = get("building_work", "A obra é edificação ou infraestrutura/planta industrial?")
    if building is not True:
        return result(
            "A qualificar" if building is None else "Fit parcial",
            "Avaliação técnica",
            [],
            "Não presumir aderência de edificação para infraestrutura.",
        )
    if model == "developer":
        projects = get("internal_project_management", "Gerencia projetos internamente?")
        budget = get("internal_budgeting", "O orçamento é operado internamente?")
        if projects is None or budget is None:
            return result(
                "A qualificar",
                "Discovery Gestão",
                [],
                "Incorporadora pura exige as duas capacidades internas.",
            )
        if projects is False or budget is False:
            return result(
                "PA2 Gestão",
                "Comercial + Services",
                ["Base Digital · Visus Start"],
                "Uma capacidade ainda é terceirizada; internalizar antes da oferta de quantitativos.",
            )
    bim = get("uses_bim", "Já recebe e trabalha com projeto BIM?")
    if bim is False:
        return result(
            "PA2 Gestão",
            "Comercial + Services",
            ["Base Digital · Visus Start"],
            "Quer absorver o método e ainda precisa formar a base BIM.",
        )
    pain = get(
        "pain", "Qual dor concreta motivou a conversa: quantidade, coordenação ou orçamento?"
    )
    autonomy = get("sector_autonomy", "O setor tem autonomia para testar e comprar?")
    if (
        bim is not True
        or autonomy is not True
        or pain not in {"quantities", "coordination", "budget"}
    ):
        return result(
            "A qualificar",
            "Discovery Gestão",
            [],
            "Porte e capital não comprovam o comportamento de compra ICP.",
        )
    offer = (
        "Projetos com qualidade · Visus Growth"
        if pain == "coordination"
        else "Quantitativos precisos · Visus Advance"
    )
    if pain == "budget":
        ready = all(
            get(key, question) is True
            for key, question in (
                ("model_ready", "Há modelo utilizável?"),
                ("eap_ready", "A EAP está pronta?"),
                ("quantities_ready", "Os quantitativos estão prontos no Visus Cost?"),
            )
        )
        if ready:
            offer = "Orçamento vivo · Visus Enterprise"
    return result(
        "ICP Gestão",
        "Comercial + Sales Engineering + Services",
        [offer],
        "Edificação, absorção, BIM, dor de setor e autonomia demonstrados.",
    )


RULE_VERSION = "altoqi-2026-10-03.1"


def qualify(lead):
    q = lead.get("qualification", {})
    observations = {}
    for key, value in q.items():
        if key.startswith("_") or key == "observed_at" or value is None:
            continue
        timestamp = q.get("_observed", {}).get(key, q.get("observed_at", ""))
        try:
            captured = date.fromisoformat(timestamp[:10])
        except (ValueError, TypeError):
            continue
        observations[key] = Observation(
            value,
            "consultant",
            "capture:" + str(lead["id"]),
            captured,
            "Contexto declarado pelo consultor; confirmar no diagnóstico.",
            "declaration",
        )
    recommendation = asdict(recommend(observations, date.today()))
    recommendation["version"] = RULE_VERSION
    recommendation["nature"] = "Hipótese comercial baseada em declarações; validar no diagnóstico."
    return recommendation
