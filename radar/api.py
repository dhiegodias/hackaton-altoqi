import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from radar import (
    audit_log,
    backfill,
    base_queries,
    calibration,
    data_standard,
    demo,
    hubspot,
    outbound,
    outbound_provider,
    privacy,
    scoring,
    service,
    standard_sources,
    transfer_files,
    transfers,
)
from radar.db import connection, initialize
from radar.qualification import RULE_VERSION, qualify
from radar.validation import DECISION_ROLES, FIELDS, completeness

ROOT = Path(__file__).resolve().parents[1]


def demo_mode():
    return os.getenv("DEMO_MODE", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app):
    if not demo_mode() and not os.getenv("RADAR_ACCESS_TOKEN"):
        raise RuntimeError("RADAR_ACCESS_TOKEN é obrigatório fora do modo demo.")
    initialize()
    if demo_mode():
        with connection() as conn:
            if (
                conn.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 0
                and not conn.execute("SELECT 1 FROM erasure_receipts LIMIT 1").fetchone()
            ):
                demo.seed(conn)
    yield


app = FastAPI(
    title="Radar CRM · AltoQi",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs" if demo_mode() else None,
)


def session_value():
    return hashlib.sha256(
        ("radar-session:" + os.getenv("RADAR_ACCESS_TOKEN", "")).encode()
    ).hexdigest()


@app.middleware("http")
async def boundaries(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if origin and origin.rstrip("/") != expected:
            return JSONResponse({"detail": "Origem da requisição não permitida."}, status_code=403)
    if request.url.path.startswith("/api/") and request.url.path not in (
        "/api/health",
        "/api/config",
        "/api/session",
    ):
        token = os.getenv("RADAR_ACCESS_TOKEN", "")
        cookie = request.cookies.get("radar_session", "")
        supplied = request.headers.get("x-radar-key", "")
        if token and not (
            hmac.compare_digest(cookie, session_value()) or hmac.compare_digest(supplied, token)
        ):
            return JSONResponse(
                {"detail": "Entre com o token da equipe para continuar."}, status_code=401
            )
    if request.url.path in ("/api/import", "/api/import/preview"):
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > transfer_files.MAX_BYTES + 65536:
            return JSONResponse({"detail": "Arquivo acima do limite permitido."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/static/sw.js":
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(service.Conflict)
async def conflict_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(ValueError)
async def value_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(privacy.ErasedContact)
async def erased_handler(request, exc):
    return JSONResponse({"detail": str(exc), "erased": True}, status_code=410)


@app.exception_handler(data_standard.VerificationUnavailable)
async def verification_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(LookupError)
async def missing_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


class Qualification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_model: (
        Literal[
            "engineering_office",
            "builder",
            "developer",
            "management_service",
            "industrial_owner",
            "manufacturer",
            "public_owner",
            "investor",
            "land_developer",
        ]
        | None
    ) = None
    building_design: StrictBool | None = None
    building_work: StrictBool | None = None
    disciplines: list[Literal["structure", "installations", "architecture"]] | None = None
    designers: Annotated[StrictInt, Field(ge=0, le=100000)] | None = None
    pain: (
        Literal["engineering", "management", "both", "quantities", "coordination", "budget"] | None
    ) = None
    sponsor: StrictBool | None = None
    assisted_capacity: StrictBool | None = None
    absorbs_method: StrictBool | None = None
    uses_bim: StrictBool | None = None
    sector_autonomy: StrictBool | None = None
    service: Literal["budget", "coordination", "planning", "compatibility"] | None = None
    needs_customization: StrictBool | None = None
    large_scale: StrictBool | None = None
    adaptation_budget: StrictBool | None = None
    internal_project_management: StrictBool | None = None
    internal_budgeting: StrictBool | None = None
    model_ready: StrictBool | None = None
    eap_ready: StrictBool | None = None
    quantities_ready: StrictBool | None = None
    maturity: list[Annotated[StrictInt, Field(ge=1, le=3)]] | None = Field(
        default=None, min_length=7, max_length=7
    )


class Capture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=250)
    email: str = Field(default="", max_length=250)
    data: dict[str, str | int | float] = Field(default_factory=dict)
    qualification: Qualification = Field(default_factory=Qualification)
    consultant: str = Field(default="Consultor", min_length=1, max_length=100)


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    reviewer: str = Field(default="Equipe do hackathon", min_length=1, max_length=100)
    value: str | int | float | None = None
    allow_replace: bool = False
    note: str = Field(default="", max_length=1000)


class Scan(BaseModel):
    ids: list[UUID] | None = Field(default=None, max_length=500)


class SourceSettings(BaseModel):
    registry: bool = False
    website: bool = False
    ai: bool = False
    backfill: bool = False


class BackfillPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: Annotated[StrictInt, Field(ge=0, le=100)] = 80
    field_thresholds: dict[str, Annotated[StrictInt, Field(ge=0, le=100)]] = Field(
        default_factory=dict
    )
    auto_fill_empty: StrictBool = False
    max_pages: Annotated[StrictInt, Field(ge=1, le=10)] = 6
    web_search: StrictBool = False


class BackfillApply(BaseModel):
    fingerprint: str


class ScoreDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, Annotated[StrictInt, Field(ge=0, le=100)]]


class ScorePublish(ScoreDraft):
    fingerprint: str
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=1000)


class CalibrationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    expected_review_id: StrictInt | None = None
    judgment: Literal["correct", "incorrect", "unknown"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1000)


class SyncRequest(BaseModel):
    fingerprint: str
    simulate: bool = True


class OutboundQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["linkedin", "email", "role_company"]
    query: str = Field(min_length=1, max_length=350)
    company: str = Field(default="", max_length=250)
    website: str = Field(default="", max_length=350)


class OutboundPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    identity_confirmed: StrictBool = False
    reviewer: str = Field(min_length=1, max_length=100)


@app.get("/api/outbound")
def outbound_list():
    with connection() as conn:
        rows = conn.execute(
            "SELECT id,query,status,error,created_at,finished_at FROM outbound_searches ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
    return {"items": rows, "search_configured": outbound_provider.configured()}


@app.post("/api/outbound", status_code=202)
def outbound_start(payload: OutboundQuery, idempotency_key: Annotated[str, Header(max_length=150)]):
    with connection() as conn:
        return outbound.enqueue(conn, payload.model_dump(), idempotency_key)


@app.get("/api/outbound/{search_id}")
def outbound_detail(search_id: UUID):
    with connection() as conn:
        return outbound.detail(conn, search_id)


@app.post("/api/outbound/{search_id}/candidates/{candidate_id}/add")
def outbound_add(search_id: UUID, candidate_id: str, payload: OutboundPromotion):
    with connection() as conn:
        return outbound.promote(conn, search_id, candidate_id, payload.model_dump())


@app.get("/api/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/config")
def config():
    with connection() as conn:
        standard = data_standard.config(conn)
    return {
        "demo": demo_mode(),
        "auth_required": bool(os.getenv("RADAR_ACCESS_TOKEN")),
        "fields": FIELDS,
        "segments": standard["segments"],
        "roles": [item["label"] for item in standard["roles"]],
        "decision_roles": DECISION_ROLES,
        "rule_version": RULE_VERSION,
        "hubspot_connected": bool(os.getenv("HUBSPOT_ACCESS_TOKEN")),
        "hubspot_write": os.getenv("HUBSPOT_WRITE_ENABLED", "false").lower() == "true",
        "ai_configured": bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL")),
        "backfill_version": backfill.VERSION,
        "import_limits": {"rows": transfer_files.MAX_ROWS, "bytes": transfer_files.MAX_BYTES},
    }


@app.post("/api/session")
def login(payload: dict):
    token = str(payload.get("token", ""))
    if not os.getenv("RADAR_ACCESS_TOKEN") or not hmac.compare_digest(
        token, os.environ["RADAR_ACCESS_TOKEN"]
    ):
        return JSONResponse({"detail": "Token inválido."}, status_code=401)
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        "radar_session",
        session_value(),
        httponly=True,
        samesite="strict",
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        max_age=28800,
    )
    return response


def present(lead):
    return {
        **lead,
        "completeness": completeness(lead["data"]),
        "baseline_completeness": completeness(lead["baseline"]),
        "fit": qualify(lead),
    }


@app.get("/api/leads")
def leads():
    with connection() as conn:
        rows = conn.execute("""SELECT l.*, count(s.id) FILTER(WHERE s.status='pending') AS pending,
            count(s.id) FILTER(WHERE s.status='pending' AND s.previous_value<>'null'::jsonb) AS conflicts
            FROM leads l LEFT JOIN suggestions s ON s.lead_id=l.id WHERE NOT l.suppressed GROUP BY l.id ORDER BY l.created_at,l.company LIMIT 1000""").fetchall()
    return [present(row) for row in rows]


@app.get("/api/leads/page")
def leads_page(
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str, Query(max_length=150)] = "",
    filter_by: Literal["all", "pending", "priority"] = "all",
):
    with connection() as conn:
        result = base_queries.page(conn, page, page_size, q, filter_by)
    result["items"] = [present(lead) for lead in result["items"]]
    return result


@app.get("/api/leads/{lead_id}")
def lead_detail(lead_id: UUID):
    with connection() as conn:
        lead = present(service.get_lead(conn, lead_id))
        lead["suggestions"] = conn.execute(
            "SELECT * FROM suggestions WHERE lead_id=%s ORDER BY observed_at DESC,field", (lead_id,)
        ).fetchall()
        lead["events"] = conn.execute(
            "SELECT * FROM events WHERE lead_id=%s ORDER BY id DESC LIMIT 60", (lead_id,)
        ).fetchall()
        lead["jobs"] = conn.execute(
            "SELECT * FROM jobs WHERE lead_id=%s ORDER BY created_at DESC LIMIT 5", (lead_id,)
        ).fetchall()
        for item in lead["suggestions"]:
            item["standard_blockers"] = data_standard.blockers(conn, lead, item)
        lead["unverified_fields"] = (
            [
                field
                for field in data_standard.RESTRICTED
                if field in lead["data"]
                and not any(
                    item["field"] == field
                    and item["status"] == "approved"
                    and not item["standard_blockers"]
                    for item in lead["suggestions"]
                )
            ]
            if not lead["demo"]
            else []
        )
    return lead


@app.get("/api/stats")
def stats():
    with connection() as conn:
        coverage = base_queries.coverage(conn)
        suggestions = conn.execute(
            "SELECT status,count(*) AS n FROM suggestions s JOIN leads l ON l.id=s.lead_id WHERE NOT l.suppressed GROUP BY status"
        ).fetchall()
        syncs = conn.execute("SELECT status,count(*) AS n FROM syncs GROUP BY status").fetchall()
        jobs = conn.execute("SELECT status,count(*) AS n FROM jobs GROUP BY status").fetchall()
        recent = conn.execute(
            "SELECT j.*,l.company FROM jobs j JOIN leads l ON l.id=j.lead_id ORDER BY j.created_at DESC LIMIT 6"
        ).fetchall()
        suppressed = conn.execute("SELECT count(*) AS n FROM leads WHERE suppressed").fetchone()[
            "n"
        ]
    return {
        **coverage,
        "suggestions": {row["status"]: row["n"] for row in suggestions},
        "syncs": {row["status"]: row["n"] for row in syncs},
        "jobs": {row["status"]: row["n"] for row in jobs},
        "recent_jobs": recent,
        "suppressed": suppressed,
    }


@app.post("/api/captures")
def capture(payload: Capture, idempotency_key: Annotated[str, Header()]):
    with connection() as conn:
        body = payload.model_dump(exclude_none=True)
        result = service.capture(conn, body, idempotency_key)
        lead = service.get_lead(conn, result["id"])
        return {**result, "fit": qualify(lead)}


@app.post("/api/scan")
def scan(payload: Scan):
    with connection() as conn:
        return service.enqueue(
            conn, [str(value) for value in payload.ids] if payload.ids is not None else None
        )


@app.post("/api/suggestions/{suggestion_id}/review")
def decide(suggestion_id: UUID, payload: Decision):
    with connection() as conn:
        return service.review(
            conn,
            suggestion_id,
            payload.action,
            payload.reviewer,
            payload.value,
            payload.allow_replace,
            payload.note,
        )


@app.post("/api/leads/{lead_id}/fit")
def propose_fit(lead_id: UUID):
    with connection() as conn:
        lead = service.get_lead(conn, lead_id, lock=True)
        fit = qualify(lead)
        if not fit["offers"]:
            raise ValueError("Complete as perguntas de qualificação antes de sugerir fit.")
        value = " · ".join([fit["group"], fit["route"], ", ".join(fit["offers"])])[:500]
        result = service.add_suggestion(
            conn,
            lead,
            {
                "field": "product_fit",
                "value": value,
                "source_kind": "playbook",
                "source_url": fit["playbook"],
                "evidence": f"Hipótese {RULE_VERSION}. " + " ".join(fit["reasons"]),
                "confidence": 70,
            },
        )
        return {"created": bool(result)}


@app.post("/api/leads/{lead_id}/suppress")
def suppress(lead_id: UUID):
    with connection() as conn:
        return service.suppress(conn, lead_id, "equipe")


@app.get("/api/leads/{lead_id}/sync-preview")
def sync_preview(lead_id: UUID):
    with connection() as conn:
        return hubspot.preview(conn, lead_id)


@app.post("/api/leads/{lead_id}/sync")
def sync_lead(lead_id: UUID, payload: SyncRequest):
    with connection() as conn:
        return hubspot.sync(conn, lead_id, payload.fingerprint, payload.simulate)


@app.get("/api/sources")
def source_settings():
    with connection() as conn:
        return conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()["value"]


@app.get("/api/backfill/policy")
def backfill_policy():
    with connection() as conn:
        return backfill.policy(conn)


@app.get("/api/scoring")
def score_settings():
    with connection() as conn:
        config = scoring.active(conn)
        history = conn.execute(
            "SELECT revision,weights,reviewer,note,created_at FROM scoring_versions ORDER BY revision DESC LIMIT 20"
        ).fetchall()
    return {
        **config,
        "groups": scoring.GROUPS,
        "maximum": scoring.maximum(config["weights"]),
        "history": history,
    }


@app.get("/api/calibration/samples")
def calibration_samples():
    with connection() as conn:
        return calibration.samples(conn)


@app.post("/api/calibration/samples/{suggestion_id}/review")
def calibrate_sample(suggestion_id: UUID, payload: CalibrationReview):
    with connection() as conn:
        return calibration.review_sample(conn, suggestion_id, payload.model_dump())


@app.post("/api/scoring/preview")
def score_preview(payload: ScoreDraft):
    with connection() as conn:
        return calibration.simulate(conn, payload.weights)


@app.post("/api/scoring/publish")
def publish_score(payload: ScorePublish):
    with connection() as conn:
        return calibration.publish(
            conn, payload.weights, payload.fingerprint, payload.reviewer, payload.note
        )


@app.put("/api/backfill/policy")
def update_backfill_policy(payload: BackfillPolicy):
    if payload.web_search and not config()["ai_configured"]:
        raise ValueError(
            "Configure OPENAI_API_KEY e OPENAI_MODEL no servidor antes de ativar a busca web."
        )
    with connection() as conn:
        return backfill.save_policy(conn, payload.model_dump())


@app.get("/api/backfill/preview")
def backfill_preview():
    with connection() as conn:
        return backfill.preview(conn)


@app.post("/api/backfill/apply")
def apply_backfill(payload: BackfillApply):
    with connection() as conn:
        return backfill.apply(conn, payload.fingerprint)


@app.put("/api/sources")
def update_sources(payload: SourceSettings):
    if payload.ai and not config()["ai_configured"]:
        raise ValueError("Configure OPENAI_API_KEY e OPENAI_MODEL no servidor antes de ativar IA.")
    if payload.ai and not payload.website:
        raise ValueError("A IA extrai apenas de páginas coletadas; ative Site informado.")
    if payload.backfill and not payload.website:
        raise ValueError("Ative Site informado para permitir a leitura das páginas pelo backfill.")
    with connection() as conn:
        conn.execute(
            "UPDATE settings SET value=%s WHERE key='sources'", (Jsonb(payload.model_dump()),)
        )
        service.audit(conn, None, "sources_updated", payload.model_dump())
    return payload


@app.post("/api/import/preview", status_code=202)
def import_preview(file: UploadFile, idempotency_key: Annotated[str | None, Header()] = None):
    return transfers.upload(file.file, file.filename, idempotency_key or uuid4())


@app.post("/api/import", status_code=202)
def import_file(
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str, Form()],
    idempotency_key: Annotated[str | None, Header()] = None,
):
    return transfers.upload(
        file.file, file.filename, idempotency_key or uuid4(), json.loads(mapping)
    )


@app.post("/api/hubspot/import", status_code=202)
def import_hubspot(payload: dict, idempotency_key: Annotated[str | None, Header()] = None):
    if not os.getenv("HUBSPOT_ACCESS_TOKEN"):
        raise ValueError("Configure HUBSPOT_ACCESS_TOKEN para conectar seu portal.")
    with connection() as conn:
        return transfers.enqueue(
            conn, "hubspot_import", idempotency_key or uuid4(), payload.get("after")
        )


@app.post("/api/exports", status_code=202)
def export_start(idempotency_key: Annotated[str | None, Header()] = None):
    with connection() as conn:
        return transfers.enqueue(conn, "export", idempotency_key or uuid4())


@app.get("/api/export.csv", status_code=202)
def export_approved():
    # Compatibility entry point now returns a queued operation, never builds a file in HTTP.
    with connection() as conn:
        return transfers.enqueue(conn, "export", uuid4())


@app.get("/api/transfers")
def transfer_list(page: Annotated[int, Query(ge=1, le=100000)] = 1):
    with connection() as conn:
        return transfers.listing(conn, page)


@app.get("/api/transfers/{job_id}")
def transfer_detail(job_id: UUID):
    with connection() as conn:
        return transfers.detail(conn, job_id)


@app.post("/api/transfers/{job_id}/start", status_code=202)
def transfer_start(job_id: UUID, payload: dict):
    with connection() as conn:
        return transfers.start(conn, job_id, payload.get("mapping"))


@app.post("/api/transfers/{job_id}/{action}")
def transfer_action(job_id: UUID, action: Literal["cancel", "retry"]):
    with connection() as conn:
        return transfers.action(conn, job_id, action)


@app.get("/api/transfers/{job_id}/errors")
def transfer_errors(job_id: UUID, page: Annotated[int, Query(ge=1, le=100000)] = 1):
    with connection() as conn:
        return transfers.errors_page(conn, job_id, page)


@app.get("/api/transfers/{job_id}/download-ready")
def transfer_download_ready(job_id: UUID):
    with connection() as conn:
        valid = transfers.download_check(conn, job_id)
    if not valid:
        raise service.Conflict("A base ou a validade mudou. Gere outra exportação.")
    return {"url": f"/api/transfers/{job_id}/download"}


@app.get("/api/transfers/{job_id}/download")
def transfer_download(job_id: UUID):
    with connection() as conn:
        valid = transfers.download_check(conn, job_id)
    if not valid:
        raise service.Conflict("A base ou a validade mudou; gere outra exportação.")
    return StreamingResponse(
        transfers.stream_download(job_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="radar-aprovados.csv"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/audit/page")
def audit_page(
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    through_id: Annotated[int | None, Query(ge=0, le=9223372036854775807)] = None,
):
    with connection() as conn:
        return audit_log.page(conn, page, page_size, through_id)


@app.get("/api/audit")
def events():
    with connection() as conn:
        return conn.execute(
            "SELECT e.*,l.company FROM events e LEFT JOIN leads l ON l.id=e.lead_id ORDER BY e.id DESC LIMIT 200"
        ).fetchall()


@app.post("/api/demo")
def seed_demo():
    if not demo_mode():
        raise ValueError("Demonstração desativada neste ambiente.")
    with connection() as conn:
        return demo.seed(conn)


@app.get("/")
def index():
    return FileResponse(ROOT / "static/index.html")


@app.get("/campo")
def field_capture():
    return FileResponse(ROOT / "static/campo.html")


@app.get("/campo.webmanifest")
def field_manifest():
    return FileResponse(
        ROOT / "static/campo.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        ROOT / "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class StandardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: dict
    reviewer: str = Field(min_length=1, max_length=100)


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=1000)


class ErasureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    confirmation: str


class PrivacyCheck(BaseModel):
    items: list[dict] = Field(max_length=200)


@app.get("/api/standard")
def standard_settings():
    with connection() as conn:
        return {
            "configuration": data_standard.config(conn),
            "sources": data_standard.SOURCE_RULES,
            "version": data_standard.VERSION,
        }


@app.put("/api/standard")
def standard_save(payload: StandardUpdate):
    with connection() as conn:
        return data_standard.save(conn, payload.configuration, payload.reviewer)


@app.post("/api/leads/{lead_id}/source")
def verify_source(lead_id: UUID, payload: SourceEvidence):
    with connection() as conn:
        lead = service.get_lead(conn, lead_id)
        service.require_active(lead)
        cfg = data_standard.config(conn)
    proposals = standard_sources.from_url(lead, payload.url, cfg)
    with connection() as conn:
        current = service.get_lead(conn, lead_id, lock=True)
        if service.digest(
            [lead["name"], lead["company"], data_standard.inputs(lead)]
        ) != service.digest([current["name"], current["company"], data_standard.inputs(current)]):
            raise service.Conflict("O cadastro mudou durante a coleta. Confira a fonte novamente.")
        created = sum(bool(service.add_suggestion(conn, current, item)) for item in proposals)
    return {"suggestions": created, "remote_writes": 0}


@app.get("/api/leads/{lead_id}/erasure-preview")
def erasure_preview(lead_id: UUID):
    with connection() as conn:
        return privacy.preview(conn, lead_id)


@app.post("/api/leads/{lead_id}/erase")
def erase_contact(lead_id: UUID, payload: ErasureRequest):
    with connection() as conn:
        return privacy.erase(conn, lead_id, payload.fingerprint, payload.confirmation)


@app.post("/api/privacy/check")
def check_local_privacy(payload: PrivacyCheck):
    with connection() as conn:
        return {
            "blocked": [
                i
                for i, item in enumerate(payload.items)
                if privacy.blocked(conn, item, item.get("request_key", ""))
            ]
        }


@app.get("/api/privacy/overview")
def privacy_overview():
    with connection() as conn:
        return {
            "suppressed": conn.execute(
                "SELECT id,name,company FROM leads WHERE suppressed ORDER BY company LIMIT 1000"
            ).fetchall(),
            "erasure_count": conn.execute("SELECT count(*) AS n FROM erasure_receipts").fetchone()[
                "n"
            ],
        }
