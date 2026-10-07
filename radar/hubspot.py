import json
import os
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from psycopg.types.json import Jsonb

from radar import data_standard, privacy
from radar.service import Conflict, audit, digest, fit_basis, get_lead, require_active


def field_map():
    mapping = json.loads(os.getenv("HUBSPOT_FIELD_MAP", '{"website":"website","role":"jobtitle"}'))
    if not isinstance(mapping, dict) or any(
        not isinstance(value, str) or not value.replace("_", "").isalnum()
        for value in mapping.values()
    ):
        raise ValueError("HUBSPOT_FIELD_MAP inválido.")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError(
            "Duas colunas do Radar não podem apontar para a mesma propriedade HubSpot."
        )
    return mapping


def wire_value(value):
    return str(value) if value is not None else ""


def approved_fields(conn, lead, items=None, standard_config=None):
    if items is None:
        items = conn.execute(
            "SELECT DISTINCT ON (field) * FROM suggestions WHERE lead_id=%s AND status='approved' ORDER BY field,reviewed_at DESC",
            (lead["id"],),
        ).fetchall()
    result = {}
    for item in items:
        if data_standard.blockers(conn, lead, item, cfg=standard_config):
            continue
        if item.get("assessment"):
            from radar.backfill import evidence_blockers

            if evidence_blockers(item, lead):
                continue
        days = (
            90
            if item["field"]
            in ("role", "employee_count", "employee_range", "decision_role", "product_fit")
            else 180
        )
        if (datetime.now(UTC) - item["observed_at"]).days > days:
            continue
        if item["basis_fingerprint"] and item["basis_fingerprint"] != fit_basis(lead):
            continue
        if item["value"] == lead["data"].get(item["field"]):
            result[item["field"]] = item
    return result


def preview(conn, lead_id):
    lead = get_lead(conn, lead_id)
    require_active(lead)
    approved = approved_fields(conn, lead)
    mapping = field_map()
    payload = {
        mapping[field]: wire_value(item["value"])
        for field, item in approved.items()
        if field in mapping
    }
    result = {
        "lead_id": str(lead["id"]),
        "hubspot_id": lead["hubspot_id"],
        "properties": payload,
        "approved": {field: item["value"] for field, item in approved.items()},
        "approval_ids": {field: str(item["id"]) for field, item in approved.items()},
        "unmapped": [field for field in approved if field not in mapping],
        "demo": lead["demo"],
        "real_enabled": os.getenv("HUBSPOT_WRITE_ENABLED", "false").lower() == "true"
        and bool(os.getenv("HUBSPOT_ACCESS_TOKEN")),
    }
    result["fingerprint"] = digest(result)
    return result


class HubSpotTemporaryError(ValueError):
    """Retryable upstream rate limit or service failure, without response-body data."""


class HubSpotClient:
    def __init__(self):
        token = os.getenv("HUBSPOT_ACCESS_TOKEN")
        if not token:
            raise ValueError("Configure HUBSPOT_ACCESS_TOKEN para conectar seu portal.")
        self.headers = {"Authorization": f"Bearer {token}"}

    def request(self, method, path, **kwargs):
        response = httpx.request(
            method, "https://api.hubapi.com" + path, headers=self.headers, timeout=20, **kwargs
        )
        if response.status_code == 429:
            raise HubSpotTemporaryError(
                "HubSpot limitou as requisições. Aguarde e tente novamente."
            )
        if response.status_code >= 500:
            raise HubSpotTemporaryError("HubSpot temporariamente indisponível. Tente novamente.")
        if response.status_code >= 400:
            raise ValueError(
                f"HubSpot respondeu HTTP {response.status_code}. Verifique permissões e mapeamento das propriedades."
            )
        return response.json()

    def read_contact(self, contact_id, properties):
        return self.request(
            "GET",
            "/crm/v3/objects/contacts/" + quote(str(contact_id), safe=""),
            params={"properties": ",".join(properties)},
        )["properties"]

    def update_contact(self, contact_id, properties):
        return self.request(
            "PATCH",
            "/crm/v3/objects/contacts/" + quote(str(contact_id), safe=""),
            json={"properties": properties},
        )

    def list_contacts(self, after=None):
        properties = sorted({"firstname", "lastname", "email", "company", *field_map().values()})
        params = {"limit": 100, "properties": ",".join(properties)}
        if after:
            params["after"] = after
        return self.request("GET", "/crm/v3/objects/contacts", params=params)


def sync(conn, lead_id, fingerprint, simulate=True, client=None):
    privacy.lock(conn)
    lead = get_lead(conn, lead_id, lock=True)
    require_active(lead)
    plan = preview(conn, lead_id)
    if fingerprint != plan["fingerprint"]:
        raise Conflict("A prévia mudou. Revise o envio novamente.")
    if not plan["approved"]:
        raise ValueError("Aprove pelo menos um campo antes de preparar o envio.")
    mode = "simulation" if simulate else "hubspot"
    sync_fingerprint = digest([mode, plan["fingerprint"]])
    previous = conn.execute(
        "SELECT * FROM syncs WHERE fingerprint=%s", (sync_fingerprint,)
    ).fetchone()
    if simulate and previous and previous["status"] == "simulated":
        return {"id": str(previous["id"]), "status": previous["status"], "replayed": True}
    if not simulate:
        if lead["demo"]:
            raise ValueError("Contatos fictícios não podem ser enviados à HubSpot.")
        if not plan["real_enabled"]:
            raise ValueError("Envio real desativado. Configure token e HUBSPOT_WRITE_ENABLED.")
        if not lead["hubspot_id"]:
            raise ValueError("Contato sem ID HubSpot. Importe do CRM ou use CSV com hubspot_id.")
        if plan["unmapped"]:
            raise ValueError(
                "Mapeie todos os campos aprovados em HUBSPOT_FIELD_MAP antes de enviar."
            )
        client = client or HubSpotClient()
        remote = client.read_contact(lead["hubspot_id"], list(plan["properties"]))
        changed = {}
        mapping = field_map()
        for field, value in plan["approved"].items():
            prop = mapping[field]
            current = remote.get(prop) or ""
            intended = wire_value(value)
            if current == intended:
                continue  # reconcilia resposta perdida sem repetir o PATCH
            if current != wire_value(lead["crm_baseline"].get(field)):
                raise Conflict(
                    f"O campo {field} mudou na HubSpot desde a importação. O envio foi bloqueado."
                )
            changed[prop] = intended
        if changed:
            client.update_contact(lead["hubspot_id"], changed)
        confirmed = client.read_contact(lead["hubspot_id"], list(plan["properties"]))
        if any((confirmed.get(key) or "") != value for key, value in plan["properties"].items()):
            raise Conflict(
                "A HubSpot ainda não confirmou os valores enviados. Revise e tente novamente."
            )
        baseline = {**lead["crm_baseline"], **plan["approved"]}
        conn.execute("UPDATE leads SET crm_baseline=%s WHERE id=%s", (Jsonb(baseline), lead_id))
        if previous and previous["status"] == "sent":
            return {"id": str(previous["id"]), "status": "sent", "replayed": True}
    sync_id = previous["id"] if previous else uuid.uuid4()
    status = "simulated" if simulate else "sent"
    conn.execute(
        """INSERT INTO syncs(id,lead_id,fingerprint,mode,status,payload,completed_at)
        VALUES (%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status,completed_at=now()""",
        (sync_id, lead_id, sync_fingerprint, mode, status, Jsonb(plan["approved"])),
    )
    audit(
        conn, lead_id, status, {"fields": list(plan["approved"]), "sync_id": str(sync_id)}, "equipe"
    )
    return {"id": str(sync_id), "status": status, "replayed": False}
