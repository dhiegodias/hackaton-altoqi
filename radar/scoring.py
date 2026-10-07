"""Evidence scores from published database configuration; no CRM decisions here."""

import json
from pathlib import Path

from psycopg.types.json import Jsonb

GROUPS = [
    {
        "label": "1. Onde o dado foi encontrado",
        "options": [
            {"key": "source_site", "label": "No domínio do site informado"},
            {"key": "source_other", "label": "Em outra fonte pública"},
        ],
    },
    {
        "label": "2. Identificação da empresa e da pessoa",
        "options": [
            {"key": "identity_match", "label": "Identificação compatível com o cadastro"},
            {
                "key": "identity_unresolved",
                "label": "Identificação ainda incerta (continua bloqueada)",
            },
        ],
    },
    {
        "label": "3. O que a fonte sustenta",
        "options": [
            {"key": "fact", "label": "Valor escrito na fonte"},
            {"key": "estimate", "label": "Faixa ou limite, como 300+"},
            {"key": "inference", "label": "Conclusão por regra, como provável decisor"},
        ],
    },
    {
        "label": "4. Data da informação",
        "options": [
            {"key": "dated", "label": "A página informa a data de publicação"},
            {"key": "undated", "label": "A página não informa a data"},
        ],
    },
    {
        "label": "5. Apoio de outra origem",
        "options": [
            {"key": "corroborated", "label": "Há outra origem apoiando a conclusão (uma vez)"},
        ],
    },
]
KEYS = {item["key"] for group in GROUPS for item in group["options"]}


def bootstrap_weights():
    """Only for first database initialization and isolated test inputs."""
    return validate(json.loads(Path(__file__).with_name("scoring_defaults.json").read_text()))


def validate(weights):
    if not isinstance(weights, dict) or set(weights) != KEYS:
        raise ValueError("Informe todos os pesos e somente os campos da tela.")
    if any(type(value) is not int or not 0 <= value <= 100 for value in weights.values()):
        raise ValueError("Cada peso deve ser um número inteiro entre 0 e 100.")
    for order in (
        ("source_site", "source_other"),
        ("identity_match", "identity_unresolved"),
        ("fact", "estimate", "inference"),
        ("dated", "undated"),
    ):
        if any(weights[a] < weights[b] for a, b in zip(order, order[1:], strict=False)):
            raise ValueError(
                "Evidência mais fraca não pode valer mais que a mais forte do mesmo grupo."
            )
    if not 1 <= maximum(weights) <= 100:
        raise ValueError("A soma dos maiores pesos dos cinco grupos deve ficar entre 1 e 100.")
    return dict(weights)


def maximum(weights):
    return sum(max(weights[item["key"]] for item in group["options"]) for group in GROUPS)


def initialize(conn):
    if conn.execute("SELECT 1 FROM settings WHERE key='scoring'").fetchone():
        return
    weights = bootstrap_weights()
    version = conn.execute(
        "INSERT INTO scoring_versions(weights,reviewer,note) VALUES (%s,%s,%s) RETURNING revision",
        (
            Jsonb(weights),
            "Inicialização",
            "Valores iniciais; ajustar com exemplos revisados pela equipe.",
        ),
    ).fetchone()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES ('scoring',%s)",
        (Jsonb({"revision": version["revision"], "weights": weights}),),
    )


def active(conn, lock=False):
    row = conn.execute(
        "SELECT value FROM settings WHERE key='scoring'" + (" FOR UPDATE" if lock else "")
    ).fetchone()
    if not row:
        raise ValueError("Configuração de pesos ausente. Inicialize o banco antes de pesquisar.")
    return row["value"]


def signals(assessment):
    if assessment.get("signals"):
        return assessment["signals"]
    # Older suggestions retain sufficient observations to recover their choices.
    proof = (assessment.get("evidence") or [{}])[0]
    return {
        "source": "source_site" if proof.get("official") else "source_other",
        "identity": "identity_match"
        if proof.get("company_matched") and not assessment.get("blockers")
        else "identity_unresolved",
        "kind": assessment.get("kind", "inference"),
        "date": "dated" if proof.get("published_at") else "undated",
        "corroborated": assessment.get("factors", {}).get("corroboração_independente", 0) > 0,
    }


def calculate(observed, weights):
    factors = {
        "autoridade_da_fonte": weights[observed["source"]],
        "identidade": weights[observed["identity"]],
        "extração_ou_inferência": weights[observed["kind"]],
        "atualidade": weights[observed["date"]],
        "corroboração_independente": weights["corroborated"] if observed["corroborated"] else 0,
    }
    return {"confidence": sum(factors.values()), "factors": factors}


def rescore(item, config):
    assessment = item.get("assessment")
    if not assessment:
        return item
    observed = signals(assessment)
    result = calculate(observed, config["weights"])
    return {
        **item,
        "confidence": result["confidence"],
        "assessment": {
            **assessment,
            "signals": observed,
            "factors": result["factors"],
            "scoring_revision": config["revision"],
            "scoring_weights": config["weights"],
        },
    }
