"""Initial commercial hypotheses from public activity, separate from full ICP.

Does not require a consultant to have answered all discovery questions and does
not invent sponsor, buying budget, readiness, designer count or exact ICP tier.
"""

from datetime import UTC, datetime

from radar.qualification import PLAYBOOK_BUILD, PLAYBOOK_OFFICE


def infer_fit(lead, signals):
    from radar.research import candidate, evidence

    by_kind = {}

    def expired(signal):
        published = signal["doc"]["published_at"]
        return bool(
            published
            and not 0
            <= (datetime.now(UTC).date() - datetime.fromisoformat(published).date()).days
            <= 90
        )

    for signal in sorted(signals, key=expired):
        by_kind.setdefault(signal["activity"], signal)
    if not by_kind:
        return []
    if "software_vendor" in by_kind:
        source = by_kind["software_vendor"]
        value = "Fora do ICP direto · fornecedor de software para engenharia"
        segment = "Outro"
        reason = "A empresa fornece tecnologia ao setor. Isso não comprova que seja escritório projetista ou construtora compradora. Avaliar parceria/uso interno antes de abordagem comercial."
        playbook = PLAYBOOK_OFFICE
        supporting_kinds = {"software_vendor"}
    else:
        offers = []
        if "structural_design" in by_kind:
            offers.append("Eberick")
        if "installation_design" in by_kind:
            offers.append("Builder")
        if offers:
            source = by_kind.get("structural_design", by_kind.get("installation_design"))
            segment, playbook = "Escritório de projetos", PLAYBOOK_OFFICE
            reason = "Autoria técnica publicada sugere aderência aos produtos por disciplina. Quantidade de projetistas, dores, sponsor e prontidão para Impulso continuam a qualificar."
            supporting_kinds = {"structural_design", "installation_design"}
        elif "budgeting_service" in by_kind:
            source = by_kind["budgeting_service"]
            offers = ["Visus Cost por usuário"]
            segment, playbook = "Escritório de gestão", PLAYBOOK_BUILD
            reason = "Serviço de orçamento sugere a rota Nicho. Validar entregável, operação e escopo antes da oferta."
            supporting_kinds = {"budgeting_service"}
        elif "construction_management" in by_kind:
            source = by_kind["construction_management"]
            offers = ["Visus · diagnóstico de gestão da construção"]
            segment, playbook = "Construtora", PLAYBOOK_BUILD
            reason = "Atividade sugere aderência inicial a gestão. Não classifica ICP/PBA nem seleciona plano: absorção, BIM, dor e autonomia continuam desconhecidos."
            supporting_kinds = {"construction_management"}
        elif "architecture_only" in by_kind:
            source = by_kind["architecture_only"]
            offers = ["Aderência pontual à colaboração · avaliar Visus Collab"]
            segment, playbook = "Arquitetura", PLAYBOOK_OFFICE
            reason = "Arquitetura sem autoria estrutural/instalações não sustenta Eberick/Builder. Colaboração pode ser investigada."
            supporting_kinds = {"architecture_only"}
        else:
            return []
        value = "Hipótese inicial · " + " + ".join(offers)
    support = [
        evidence(signal["doc"], signal["quote"])
        for signal in by_kind.values()
        if signal is not source and signal["activity"] in supporting_kinds
    ]
    fit = candidate(
        lead,
        "product_fit",
        value,
        source["doc"],
        source["quote"],
        kind="inference",
        rule="public-activity-product-fit-v1",
        reason=reason,
        extra_evidence=support,
    )
    fit["assessment"]["playbook"] = playbook
    fit["assessment"]["missing_context"] = [
        "Dor prioritária",
        "Participação na compra",
        "Capacidade de adoção",
    ]
    classification = candidate(
        lead,
        "segment",
        segment,
        source["doc"],
        source["quote"],
        kind="inference",
        rule="public-activity-segment-v1",
        reason="Segmento sugerido pela atividade publicada; revisar empresas com múltiplas atuações.",
    )
    return [fit, classification]
