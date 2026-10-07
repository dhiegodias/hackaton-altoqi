import hashlib
import json
import uuid
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from radar import data_standard, privacy
from radar.qualification import qualify
from radar.validation import clean_data, normalize_email, validate_value


class Conflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def fit_basis(lead):
    # Includes current rule/version and expiry, even when no stored answers changed.
    return digest([lead["qualification"], qualify(lead)])


def audit(conn, lead_id, action, detail=None, actor="equipe"):
    conn.execute(
        "INSERT INTO events(lead_id,action,actor,detail) VALUES (%s,%s,%s,%s)",
        (lead_id, action, actor, Jsonb(detail or {})),
    )


def get_lead(conn, lead_id, lock=False):
    if lock:
        privacy.lock(conn)
    row = conn.execute(
        "SELECT * FROM leads WHERE id=%s" + (" FOR UPDATE" if lock else ""), (lead_id,)
    ).fetchone()
    if not row:
        raise LookupError("Contato não encontrado.")
    return row


def require_active(lead):
    if lead["suppressed"]:
        raise Conflict("Contato na lista de exclusão. Pesquisa, revisão e envio bloqueados.")


def assessment_identity(assessment):
    if not assessment:
        return {}
    # Access time belongs to evidence history, not candidate identity. A repeated
    # collection cannot resurrect a rejection or multiply pending suggestions.
    return {
        **{
            key: value
            for key, value in assessment.items()
            if key not in {"signals", "factors", "scoring_revision", "scoring_weights"}
        },
        "evidence": [
            {key: value for key, value in proof.items() if key != "accessed_at"}
            for proof in assessment.get("evidence", [])
        ],
    }


def add_suggestion(conn, lead, proposal):
    privacy.lock(conn)
    require_active(lead)
    privacy.require_allowed(conn, lead)
    if not lead.get("demo"):
        proposal = data_standard.prepare_proposal(conn, lead, proposal)
    if proposal.get("assessment"):
        from radar import scoring

        proposal = scoring.rescore(proposal, scoring.active(conn))
    field = proposal["field"]
    value = validate_value(field, proposal["value"])
    current = lead["data"].get(field)
    latest = conn.execute(
        "SELECT * FROM suggestions WHERE lead_id=%s AND field=%s ORDER BY observed_at DESC,id DESC LIMIT 1",
        (lead["id"], field),
    ).fetchone()
    max_days = (
        90
        if field in ("role", "employee_count", "employee_range", "decision_role", "product_fit")
        else 180
    )
    if current == value and field != "product_fit" and field not in data_standard.RESTRICTED:
        if not latest or (datetime.now(UTC) - latest["observed_at"]).days <= max_days:
            return None
    if not proposal.get("evidence", "").strip():
        raise ValueError("Toda sugestão precisa de evidência.")
    basis = fit_basis(lead) if field == "product_fit" else None
    identity = digest(
        [
            field,
            value,
            current,
            proposal["source_kind"],
            proposal.get("source_url", ""),
            proposal["evidence"],
            basis,
            assessment_identity(proposal.get("assessment", {})),
            proposal.get("standard_proof", {}),
        ]
    )
    previous = conn.execute(
        """SELECT * FROM suggestions WHERE lead_id=%s AND field=%s AND value=%s
        AND previous_value IS NOT DISTINCT FROM %s AND source_kind=%s AND source_url=%s
        AND evidence=%s AND basis_fingerprint IS NOT DISTINCT FROM %s
        ORDER BY observed_at DESC,id DESC LIMIT 1""",
        (
            lead["id"],
            field,
            Jsonb(value),
            Jsonb(current),
            proposal["source_kind"],
            proposal.get("source_url", ""),
            proposal["evidence"],
            basis,
        ),
    ).fetchone()
    if previous and (
        assessment_identity(previous["assessment"])
        != assessment_identity(proposal.get("assessment", {}))
        or previous.get("standard_proof", {}) != proposal.get("standard_proof", {})
    ):
        previous = None
    if previous:
        fresh = (datetime.now(UTC) - previous["observed_at"]).days <= max_days
        if fresh and (
            previous["status"] in ("pending", "rejected")
            or (previous["status"] == "approved" and current == value)
        ):
            return None
        # Preserve the old observation and its review. A new collection is a new fact in time.
        identity += ":" + str(uuid.uuid4())
        if previous["status"] == "pending":
            conn.execute(
                "UPDATE suggestions SET status='superseded' WHERE id=%s", (previous["id"],)
            )
    row = conn.execute(
        """INSERT INTO suggestions(id,lead_id,field,value,previous_value,source_kind,source_url,evidence,confidence,fingerprint,basis_fingerprint,assessment,proposed_value,standard_proof)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
        (
            uuid.uuid4(),
            lead["id"],
            field,
            Jsonb(value),
            Jsonb(current),
            proposal["source_kind"],
            proposal.get("source_url", ""),
            proposal["evidence"],
            proposal["confidence"],
            identity,
            basis,
            Jsonb(proposal.get("assessment", {})),
            Jsonb(value),
            Jsonb(proposal.get("standard_proof", {})),
        ),
    ).fetchone()
    if row:
        audit(
            conn,
            lead["id"],
            "suggested",
            {"field": field, "source": proposal["source_kind"], "suggestion_id": str(row["id"])},
            "radar",
        )
    return row


def capture(conn, payload, key, origin="campo", demo=False, prepared_email=None):
    privacy.lock(conn)
    privacy.require_allowed(conn, payload, key)
    if not key or len(key) > 150:
        raise ValueError("Chave de idempotência obrigatória (até 150 caracteres).")
    payload_digest = digest([payload, origin, demo])
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
    previous = conn.execute("SELECT * FROM requests WHERE key=%s", (key,)).fetchone()
    if previous:
        if previous["digest"] != payload_digest:
            raise Conflict("Esta chave já foi usada para outro conteúdo.")
        return previous["result"]
    name = data_standard.normalize_name(payload.get("name", ""))
    company = str(payload.get("company", "")).strip()[:250]
    email = normalize_email(payload.get("email", ""))
    if not any((name, company, email)):
        raise ValueError("Informe pelo menos nome, empresa ou e-mail.")
    raw_data = clean_data(payload.get("data", {}))
    data, intake_data = (raw_data, {}) if demo else data_standard.intake(conn, raw_data)
    if prepared_email is not None:
        if (
            not isinstance(prepared_email, data_standard.PreparedEmail)
            or prepared_email.email != email
        ):
            raise ValueError("Conferência de e-mail não corresponde ao contato.")
        if prepared_email.revision != data_standard.config(conn)["revision"]:
            raise data_standard.VerificationUnavailable(
                "Padrão alterado durante o lote; a validação será repetida."
            )
        identity_checks = {"email": prepared_email.proof}
    else:
        identity_checks = (
            {} if demo else {"email": data_standard.email_domain(email, data_standard.config(conn))}
        )
    qualification = payload.get("qualification", {})
    # Serializa criações com o mesmo e-mail sem unir pessoas pelo CNPJ da empresa.
    if email:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))", (email,))
    remote_id = payload.get("hubspot_id") or None
    if remote_id:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 2))", (str(remote_id),))
    by_email = (
        conn.execute("SELECT * FROM leads WHERE lower(email)=%s FOR UPDATE", (email,)).fetchone()
        if email
        else None
    )
    by_remote = (
        conn.execute("SELECT * FROM leads WHERE hubspot_id=%s FOR UPDATE", (remote_id,)).fetchone()
        if remote_id
        else None
    )
    if by_email and by_remote and by_email["id"] != by_remote["id"]:
        raise Conflict("E-mail e ID HubSpot apontam para contatos diferentes. Revise a identidade.")
    lead = by_remote or by_email
    if lead:
        require_active(lead)
        if remote_id and lead["hubspot_id"] not in (None, remote_id):
            raise Conflict("O contato já está associado a outro ID HubSpot.")
        if remote_id:
            identity_values = {"email": email, "name": name, "company": company}
            changed_identity = [
                field
                for field, incoming in identity_values.items()
                if incoming
                and lead[field]
                and " ".join(incoming.split()).casefold()
                != " ".join(lead[field].split()).casefold()
            ]
            if changed_identity:
                raise Conflict(
                    "Identidade do contato HubSpot mudou ("
                    + ", ".join(changed_identity)
                    + "). Concilie os cadastros antes de reimportar."
                )
            changed_fields = [
                field
                for field, value in raw_data.items()
                if value != lead["crm_baseline"].get(field)
            ]
            if changed_fields:
                conn.execute(
                    "UPDATE suggestions SET status='superseded' WHERE lead_id=%s AND field=ANY(%s) AND status IN ('approved','pending')",
                    (lead["id"], changed_fields),
                )
                audit(
                    conn, lead["id"], "crm_reimported", {"review_required": changed_fields}, origin
                )
            baseline = {**lead["crm_baseline"], **raw_data}
            conn.execute(
                "UPDATE leads SET hubspot_id=%s,crm_baseline=%s,name=%s,email=%s,company=%s,updated_at=now() WHERE id=%s",
                (
                    remote_id,
                    Jsonb(baseline),
                    lead["name"] or name,
                    lead["email"] or email,
                    lead["company"] or company,
                    lead["id"],
                ),
            )
        conn.execute(
            "UPDATE leads SET intake_data=intake_data || %s,identity_checks=%s WHERE id=%s",
            (Jsonb(intake_data), Jsonb(identity_checks), lead["id"]),
        )
        for field, value in raw_data.items():
            if (
                field in intake_data
                and lead.get("intake_data", {}).get(field) == intake_data[field]
                and field not in lead["data"]
            ):
                continue
            add_suggestion(
                conn,
                lead,
                {
                    "field": field,
                    "value": value,
                    "source_kind": "consultant" if origin == "campo" else "import",
                    "source_url": "",
                    "evidence": f"Atualização informada via {origin}; revisar identidade e atualidade.",
                    "confidence": 70,
                },
            )
        if qualification:
            # Contexto declarado não altera fatos CRM; sua origem fica na auditoria.
            now = datetime.now(UTC).isoformat()
            old = lead["qualification"]
            observed = {
                key: old.get("_observed", {}).get(key, old.get("observed_at", ""))
                for key in old
                if not key.startswith("_") and key != "observed_at"
            }
            observed.update({key: now for key in qualification})
            merged = {
                **lead["qualification"],
                **qualification,
                "observed_at": now,
                "_observed": observed,
            }
            conn.execute(
                "UPDATE leads SET qualification=%s,updated_at=now() WHERE id=%s",
                (Jsonb(merged), lead["id"]),
            )
        audit(
            conn,
            lead["id"],
            "capture_merged",
            {"origin": origin, "qualification": qualification},
            payload.get("consultant", "equipe"),
        )
        result = {
            "id": str(lead["id"]),
            "duplicate": True,
            "message": "Contato localizado. Novos dados enviados para revisão.",
        }
    else:
        lead_id = uuid.uuid4()
        if qualification:
            now = datetime.now(UTC).isoformat()
            qualification = {
                **qualification,
                "observed_at": now,
                "_observed": {key: now for key in qualification},
            }
        conn.execute(
            """INSERT INTO leads(id,name,email,company,data,baseline,crm_baseline,qualification,origin,demo,hubspot_id,intake_data,identity_checks)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                lead_id,
                name,
                email,
                company,
                Jsonb(data),
                Jsonb(data),
                Jsonb(raw_data),
                Jsonb(qualification),
                origin,
                demo,
                remote_id,
                Jsonb(intake_data),
                Jsonb(identity_checks),
            ),
        )
        audit(
            conn,
            lead_id,
            "created",
            {
                "origin": origin,
                "fields": list(raw_data),
                "awaiting_evidence": list(intake_data),
                "qualification": qualification,
                "demo": demo,
            },
            payload.get("consultant", "equipe"),
        )
        result = {"id": str(lead_id), "duplicate": False, "message": "Contato salvo no Radar."}
    conn.execute(
        "INSERT INTO requests(key,digest,result) VALUES (%s,%s,%s)",
        (key, payload_digest, Jsonb(result)),
    )
    return result


def review(conn, suggestion_id, action, reviewer, edited_value=None, allow_replace=False, note=""):
    privacy.lock(conn)
    ref = conn.execute("SELECT lead_id FROM suggestions WHERE id=%s", (suggestion_id,)).fetchone()
    if not ref:
        raise LookupError("Sugestão não encontrada.")
    lead = get_lead(conn, ref["lead_id"], lock=True)
    require_active(lead)
    item = conn.execute(
        "SELECT * FROM suggestions WHERE id=%s FOR UPDATE", (suggestion_id,)
    ).fetchone()
    target_status = "approved" if action == "approve" else "rejected"
    if item["status"] == target_status:
        if (
            action == "approve"
            and edited_value is not None
            and validate_value(item["field"], edited_value) != item["value"]
        ):
            raise Conflict("Sugestão já aprovada com outro valor.")
        return {"status": target_status, "id": str(item["id"])}
    if item["status"] != "pending":
        raise Conflict("Sugestão já revisada. Atualize a página.")
    value = validate_value(
        item["field"], edited_value if edited_value is not None else item["value"]
    )
    current = lead["data"].get(item["field"])
    if action == "approve":
        problems = data_standard.blockers(conn, lead, item, value)
        if problems:
            raise Conflict(" ".join(problems))
        value = (
            data_standard.controlled(item["field"], value, data_standard.config(conn))
            if not lead.get("demo")
            else value
        )
        if item.get("assessment"):
            from radar.backfill import evidence_blockers

            blockers = evidence_blockers(item, lead)
            if blockers:
                raise Conflict(" ".join(blockers))
        max_days = (
            90
            if item["field"]
            in ("role", "employee_count", "employee_range", "decision_role", "product_fit")
            else 180
        )
        if (datetime.now(UTC) - item["observed_at"]).days > max_days:
            raise Conflict("Evidência vencida. Execute uma nova pesquisa.")
        if item["basis_fingerprint"] and item["basis_fingerprint"] != fit_basis(lead):
            raise Conflict("A qualificação mudou. Gere uma nova hipótese de fit.")
        if edited_value is not None and value != item["value"] and not note.strip():
            raise ValueError("Justifique a edição para preservar a rastreabilidade.")
        if current != item["previous_value"]:
            raise Conflict("O dado mudou após a pesquisa. Gere uma nova sugestão antes de aprovar.")
        if current is not None and current != value and not allow_replace:
            raise Conflict("Existe um dado no CRM. Confirme a substituição deste campo.")
        updated = {**lead["data"], item["field"]: value}
        conn.execute(
            "UPDATE leads SET data=%s,intake_data=intake_data - %s,updated_at=now() WHERE id=%s",
            (Jsonb(updated), item["field"], lead["id"]),
        )
        conn.execute(
            "UPDATE suggestions SET status='superseded' WHERE lead_id=%s AND field=%s AND status='pending' AND id<>%s",
            (lead["id"], item["field"], item["id"]),
        )
    conn.execute(
        "UPDATE suggestions SET status=%s,value=%s,reviewer=%s,reviewed_at=now() WHERE id=%s",
        (target_status, Jsonb(value), reviewer, item["id"]),
    )
    audit(
        conn,
        lead["id"],
        target_status,
        {
            "field": item["field"],
            "before": current,
            "proposed": item["value"],
            "after": value if action == "approve" else current,
            "suggestion_id": str(item["id"]),
            "note": note,
        },
        reviewer,
    )
    return {"id": str(item["id"]), "status": target_status}


def enqueue(conn, lead_ids=None):
    privacy.lock(conn)
    leads = conn.execute(
        "SELECT id FROM leads WHERE NOT suppressed"
        + (" AND id=ANY(%s::uuid[])" if lead_ids is not None else "")
        + " ORDER BY last_scanned_at NULLS FIRST LIMIT 500",
        (lead_ids,) if lead_ids is not None else (),
    ).fetchall()
    count = 0
    for lead in leads:
        row = conn.execute(
            "INSERT INTO jobs(id,lead_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING id",
            (uuid.uuid4(), lead["id"]),
        ).fetchone()
        count += bool(row)
    return {"queued": count}


def suppress(conn, lead_id, actor):
    privacy.lock(conn)
    lead = get_lead(conn, lead_id, lock=True)
    if not lead["suppressed"]:
        conn.execute("UPDATE leads SET suppressed=true,updated_at=now() WHERE id=%s", (lead_id,))
        conn.execute(
            "UPDATE jobs SET status='cancelled',finished_at=now() WHERE lead_id=%s AND status IN ('queued','running')",
            (lead_id,),
        )
        conn.execute(
            "UPDATE suggestions SET status='superseded' WHERE lead_id=%s AND status='pending'",
            (lead_id,),
        )
        audit(
            conn,
            lead_id,
            "suppressed",
            {
                "scope": "Coleta, revisão, exportação e sincronização bloqueadas; não é eliminação dos dados."
            },
            actor,
        )
    return {"suppressed": True}
