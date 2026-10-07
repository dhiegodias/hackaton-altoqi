"""Configurable evidence policy, transactional batch review and local autofill.

Scores rank operational evidence; they are not calibrated probabilities.
Nothing here calls HubSpot. Existing remote preview/review boundaries still apply.
"""

from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from radar import data_standard, privacy
from radar.service import Conflict, audit, digest, fit_basis, review
from radar.validation import FIELDS

VERSION = "backfill-2026-10-03.1"
DEFAULT_POLICY = {
    "threshold": 80,
    "field_thresholds": {},
    "auto_fill_empty": False,
    "max_pages": 6,
    "web_search": False,
}


def policy(conn, lock=False):
    row = conn.execute(
        "SELECT value FROM settings WHERE key='backfill'" + (" FOR UPDATE" if lock else "")
    ).fetchone()
    return {**DEFAULT_POLICY, **(row["value"] if row else {})}


def save_policy(conn, value):
    if set(value) != set(DEFAULT_POLICY):
        raise ValueError("Configuração de backfill incompleta ou desconhecida.")
    if type(value["threshold"]) is not int or not 0 <= value["threshold"] <= 100:
        raise ValueError("O limiar deve estar entre 0 e 100.")
    overrides = value["field_thresholds"]
    if not isinstance(overrides, dict) or any(
        key not in FIELDS or type(score) is not int or not 0 <= score <= 100
        for key, score in overrides.items()
    ):
        raise ValueError("Limiares por campo inválidos.")
    if type(value["max_pages"]) is not int or not 1 <= value["max_pages"] <= 10:
        raise ValueError("A pesquisa aceita entre 1 e 10 páginas por contato.")
    if any(type(value[key]) is not bool for key in ("auto_fill_empty", "web_search")):
        raise ValueError("Automação e busca devem ser booleanos.")
    policy(conn, lock=True)
    conn.execute("UPDATE settings SET value=%s WHERE key='backfill'", (Jsonb(value),))
    audit(conn, None, "backfill_policy_updated", {"policy": value, "version": VERSION})
    return value


def evaluate(item, lead, config, competing=False):
    threshold = config["field_thresholds"].get(item["field"], config["threshold"])
    reasons = evidence_blockers(item, lead)
    if item["status"] != "pending":
        reasons.append("Sugestão já revisada.")
    if lead["suppressed"]:
        reasons.append("Contato excluído das próximas ações.")
    if lead["demo"]:
        reasons.append("Dados demo exigem revisão manual e não são preenchidos pela política.")
    current = lead["data"].get(item["field"])
    if current not in (None, ""):
        reasons.append("Substituição de valor existente exige revisão individual.")
    if current != item["previous_value"]:
        reasons.append("O cadastro mudou desde a pesquisa.")
    if competing:
        reasons.append("Fontes apresentam valores diferentes para o mesmo campo.")
    if item["confidence"] < threshold:
        reasons.append("Confiança abaixo do limiar configurado.")
    return {
        "eligible": not reasons,
        "threshold": threshold,
        "reasons": list(dict.fromkeys(reasons)),
    }


def evidence_blockers(item, lead):
    """Validity also applies to individual approval and subsequent CRM previews."""
    assessment = item.get("assessment") or {}
    reasons = list(assessment.get("blockers", []))
    if assessment.get("version") != VERSION:
        reasons.append("Sugestão sem avaliação do motor de backfill atual.")
    if not assessment.get("evidence"):
        reasons.append("Sugestão sem links e trechos de evidência.")
    max_days = (
        90
        if item["field"]
        in {"role", "decision_role", "employee_count", "employee_range", "product_fit"}
        else 180
    )
    age = (datetime.now(UTC) - item["observed_at"]).days
    if age < 0 or age > max_days:
        reasons.append("Coleta vencida ou futura; pesquise novamente.")
    for evidence in assessment.get("evidence", []):
        timestamp = evidence.get("published_at")
        if timestamp:
            try:
                age = (datetime.now(UTC).date() - datetime.fromisoformat(timestamp).date()).days
            except (ValueError, TypeError):
                reasons.append("Data da fonte inválida.")
            else:
                if age < 0 or age > max_days:
                    reasons.append("Publicação da fonte vencida ou futura.")
    if item["basis_fingerprint"] and item["basis_fingerprint"] != fit_basis(lead):
        reasons.append("Contexto de fit mudou desde a pesquisa.")
    if assessment.get("basis") and assessment["basis"] != context_basis(
        lead, assessment.get("input_fields", ("website", "cnpj"))
    ):
        reasons.append("Identidade ou dados usados na inferência mudaram.")
    return reasons


def context_basis(lead, fields=("website", "cnpj")):
    # Product qualification has its own stronger, time-aware fingerprint in service.py.
    # Include only identity and research inputs, so filling another field does not invalidate peers.
    return digest(
        [
            lead["name"],
            lead["company"],
            lead["email"],
            {field: data_standard.inputs(lead).get(field) for field in fields},
        ]
    )


def assessed_candidates(conn, lead_ids=None):
    condition = " AND l.id=ANY(%s::uuid[])" if lead_ids is not None else ""
    rows = conn.execute(
        """SELECT s.*,l.name,l.company,l.demo,l.suppressed,l.data,l.intake_data,l.email,l.qualification,
        l.id AS contact_id FROM suggestions s JOIN leads l ON l.id=s.lead_id
        WHERE s.status='pending' AND s.assessment<>'{}'::jsonb"""
        + condition
        + " ORDER BY s.lead_id,s.field,s.confidence DESC,s.id LIMIT 1000",
        (lead_ids,) if lead_ids is not None else (),
    ).fetchall()
    config = policy(conn)
    groups = {}
    contacts = {row["lead_id"]: row for row in rows}
    rivals = (
        conn.execute(
            "SELECT * FROM suggestions WHERE status='pending' AND lead_id=ANY(%s::uuid[])",
            (list({row["lead_id"] for row in rows}),),
        ).fetchall()
        if rows
        else []
    )
    for row in rivals:
        lead = contacts[row["lead_id"]]
        if row.get("assessment") and evidence_blockers(row, lead):
            continue
        if not row.get("assessment") and (datetime.now(UTC) - row["observed_at"]).days > 90:
            continue
        if row["previous_value"] != lead["data"].get(row["field"]):
            continue
        key = (str(row["lead_id"]), row["field"])
        groups.setdefault(key, set()).add(digest(row["value"]))
    candidates = []
    chosen = set()
    for row in rows:
        key = (str(row["lead_id"]), row["field"])
        lead = {**row, "id": row["contact_id"]}
        decision = evaluate(row, lead, config, len(groups.get(key, set())) > 1)
        source_problems = data_standard.blockers(conn, lead, row)
        if source_problems:
            decision = {
                **decision,
                "eligible": False,
                "reasons": decision["reasons"] + source_problems,
            }
        if decision["eligible"] and key in chosen:
            decision = {
                **decision,
                "eligible": False,
                "reasons": ["Outra evidência do mesmo valor já está selecionada."],
            }
        if decision["eligible"]:
            chosen.add(key)
        candidates.append(
            {
                "id": str(row["id"]),
                "lead_id": str(row["lead_id"]),
                "company": row["company"],
                "name": row["name"],
                "field": row["field"],
                "value": row["value"],
                "previous_value": row["previous_value"],
                "confidence": row["confidence"],
                "assessment": row["assessment"],
                **decision,
            }
        )
    return config, candidates


def preview(conn, lead_ids=None):
    config, candidates = assessed_candidates(conn, lead_ids)
    eligible = [item for item in candidates if item["eligible"]]
    plan = {
        "policy": config,
        "candidates": candidates,
        "eligible_count": len(eligible),
        "total": len(candidates),
        "version": VERSION,
    }
    plan["fingerprint"] = digest(plan)
    return plan


def apply(conn, fingerprint, lead_ids=None, automatic=False):
    privacy.lock(conn)
    # Lock contacts before the policy: worker/review also hold contact locks first.
    conn.execute(
        "SELECT id FROM leads"
        + (" WHERE id=ANY(%s::uuid[])" if lead_ids is not None else "")
        + " ORDER BY id FOR UPDATE",
        (lead_ids,) if lead_ids is not None else (),
    ).fetchall()
    config = policy(conn, lock=True)
    plan = preview(conn, lead_ids)
    if not automatic and fingerprint != plan["fingerprint"]:
        raise Conflict("A prévia do backfill mudou. Confira os limiares e gere uma nova prévia.")
    if automatic and not config["auto_fill_empty"]:
        return {"applied": 0, "ids": []}
    approved = []
    for item in plan["candidates"]:
        if not item["eligible"]:
            continue
        review(
            conn,
            item["id"],
            "approve",
            "Política automática" if automatic else "Revisão em lote",
            note=f"{VERSION}; confiança {item['confidence']}; limiar {item['threshold']}; apenas campo vazio.",
        )
        approved.append(item["id"])
    audit(
        conn,
        None,
        "backfill_applied",
        {"ids": approved, "automatic": automatic, "policy": config, "version": VERSION},
    )
    return {"applied": len(approved), "ids": approved, "remote_writes": 0}
