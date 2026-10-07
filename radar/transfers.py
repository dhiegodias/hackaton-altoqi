"""Durable bulk-operation lifecycle. No worker side effects in HTTP handlers."""

import codecs
import hashlib
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from radar import importer, privacy, service, transfer_files
from radar.db import connection

PUBLIC_COLUMNS = (
    "id",
    "kind",
    "status",
    "phase",
    "filename",
    "byte_count",
    "headers",
    "mapping",
    "total",
    "staged",
    "processed",
    "created_count",
    "duplicate_count",
    "error_count",
    "exported_count",
    "error",
    "attempts",
    "created_at",
    "updated_at",
    "finished_at",
    "expires_at",
)
ACTIVE = ("queued", "running", "awaiting_mapping")


def get(conn, job_id, lock=False):
    row = conn.execute(
        "SELECT * FROM transfer_jobs WHERE id=%s" + (" FOR UPDATE" if lock else ""), (job_id,)
    ).fetchone()
    if not row:
        raise LookupError("Operação não encontrada.")
    return row


def present(conn, job):
    result = {key: job[key] for key in PUBLIC_COLUMNS}
    result["download_url"] = (
        f"/api/transfers/{job['id']}/download"
        if job["kind"] == "export" and job["status"] == "completed"
        else None
    )
    result["progress"] = round(100 * job["processed"] / job["total"], 1) if job["total"] else 0
    result["can_retry"] = (
        job["status"] == "failed"
        and job["expires_at"] > datetime.now(UTC)
        and (
            job["phase"] in ("importing", "hubspot_fetch")
            or bool(
                conn.execute(
                    "SELECT 1 FROM transfer_blobs WHERE job_id=%s AND kind='input' LIMIT 1",
                    (job["id"],),
                ).fetchone()
            )
        )
    )
    return result


def detail(conn, job_id):
    job = get(conn, job_id)
    result = present(conn, job)
    result["sample"] = (
        [
            row["row_data"]
            for row in conn.execute(
                "SELECT row_data FROM transfer_rows WHERE job_id=%s AND row_data IS NOT NULL ORDER BY line LIMIT 5",
                (job_id,),
            ).fetchall()
        ]
        if job["status"] == "awaiting_mapping"
        else []
    )
    result["suggested_mapping"] = importer.automatic_mapping(job["headers"])
    result["errors"] = conn.execute(
        "SELECT line,error FROM transfer_rows WHERE job_id=%s AND status='error' ORDER BY line LIMIT 20",
        (job_id,),
    ).fetchall()
    return result


def listing(conn, page=1):
    rows = conn.execute(
        "SELECT * FROM transfer_jobs ORDER BY created_at DESC,id DESC LIMIT 25 OFFSET %s",
        ((page - 1) * 25,),
    ).fetchall()
    total = conn.execute("SELECT count(*) AS n FROM transfer_jobs").fetchone()["n"]
    return {
        "items": [present(conn, row) for row in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + 24) // 25),
    }


def validate_mapping(mapping, headers=None):
    if (
        not isinstance(mapping, dict)
        or len(mapping) > 64
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())
    ):
        raise ValueError("Mapeamento inválido.")
    importer.validate_mapping(mapping)
    if headers is not None and set(mapping) - set(headers):
        raise ValueError("O mapeamento menciona colunas ausentes do arquivo.")


def upload(file, filename, request_key, mapping=None):
    key = UUID(str(request_key))
    filename = PurePath(filename or "lista.csv").name[:150]
    if not filename.lower().endswith((".csv", ".xlsx")):
        raise ValueError("Envie um arquivo CSV ou XLSX.")
    if mapping is not None:
        validate_mapping(mapping)
    checksum = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    encoding = "utf-8-sig"
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,33))", (str(key),))
        previous = conn.execute(
            "SELECT * FROM transfer_jobs WHERE request_key=%s", (key,)
        ).fetchone()
        epoch = conn.execute("SELECT count(*) AS n FROM erasure_receipts").fetchone()["n"]
        job_id = previous["id"] if previous else uuid4()
        if not previous:
            conn.execute(
                "INSERT INTO transfer_jobs(id,request_key,kind,phase,status,filename,mapping) VALUES (%s,%s,'import','parsing','uploading',%s,%s)",
                (job_id, key, filename, Jsonb(mapping) if mapping is not None else None),
            )
        size, part = 0, 0
        while chunk := file.read(transfer_files.CHUNK_BYTES):
            size += len(chunk)
            if size > transfer_files.MAX_BYTES:
                raise ValueError(f"Arquivo excede {transfer_files.MAX_BYTES // 1024 // 1024} MB.")
            checksum.update(chunk)
            if encoding == "utf-8-sig":
                try:
                    decoder.decode(chunk)
                except UnicodeDecodeError:
                    encoding = "cp1252"
            if not previous:
                conn.execute(
                    "INSERT INTO transfer_blobs(job_id,kind,part,content) VALUES (%s,'input',%s,%s)",
                    (job_id, part, chunk),
                )
            part += 1
        if not size:
            raise ValueError("Arquivo vazio.")
        if encoding == "utf-8-sig":
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                encoding = "cp1252"
        fingerprint = checksum.hexdigest()
        if previous:
            if (
                previous["kind"] != "import"
                or previous["digest"] != fingerprint
                or (mapping is not None and mapping != previous["mapping"])
            ):
                raise service.Conflict("Esta chave já foi usada para outra operação.")
            return present(conn, previous)
        privacy.lock(conn)
        if epoch != conn.execute("SELECT count(*) AS n FROM erasure_receipts").fetchone()["n"]:
            raise service.Conflict(
                "Houve uma eliminação durante o upload. Confira a lista antes de reenviar."
            )
        conn.execute(
            "UPDATE transfer_jobs SET status='queued',byte_count=%s,digest=%s,encoding=%s WHERE id=%s",
            (size, fingerprint, encoding, job_id),
        )
        return present(conn, get(conn, job_id))


def enqueue(conn, kind, key, after=None):
    privacy.lock(conn)
    key = UUID(str(key))
    previous = conn.execute("SELECT * FROM transfer_jobs WHERE request_key=%s", (key,)).fetchone()
    if previous:
        if previous["kind"] != kind or previous["digest"] != service.digest([kind, after]):
            raise service.Conflict("Esta chave já identifica outra operação.")
        return present(conn, previous)
    ident = uuid4()
    conn.execute(
        "INSERT INTO transfer_jobs(id,request_key,kind,phase,source_after,digest) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            ident,
            key,
            kind,
            "exporting" if kind == "export" else "hubspot_fetch",
            str(after) if after else None,
            service.digest([kind, after]),
        ),
    )
    return present(conn, get(conn, ident))


def start(conn, job_id, mapping):
    privacy.lock(conn)
    job = get(conn, job_id, True)
    validate_mapping(mapping, job["headers"])
    if job["status"] != "awaiting_mapping":
        if job["mapping"] == mapping and job["phase"] == "importing":
            return present(conn, job)
        raise service.Conflict(
            "A lista ainda não está pronta para mapear, ou esta operação já terminou."
        )
    if job["expires_at"] <= datetime.now(UTC):
        raise service.Conflict("Arquivo expirado; envie novamente.")
    conn.execute(
        "UPDATE transfer_jobs SET mapping=%s,status='queued',phase='importing',available_at=now(),updated_at=now() WHERE id=%s",
        (Jsonb(mapping), job_id),
    )
    return present(conn, get(conn, job_id))


def purge(conn, job_id, status, message):
    conn.execute("DELETE FROM transfer_blobs WHERE job_id=%s", (job_id,))
    conn.execute("DELETE FROM transfer_rows WHERE job_id=%s AND status='pending'", (job_id,))
    conn.execute("UPDATE transfer_rows SET row_data=NULL WHERE job_id=%s", (job_id,))
    conn.execute(
        "UPDATE transfer_jobs SET status=%s,error=%s,lease=NULL,lease_until=NULL,finished_at=now(),updated_at=now() WHERE id=%s",
        (status, message, job_id),
    )


def action(conn, job_id, action):
    privacy.lock(conn)
    job = get(conn, job_id, True)
    if action == "cancel":
        if job["status"] not in ACTIVE:
            raise service.Conflict("A operação não está em andamento.")
        purge(
            conn,
            job_id,
            "cancelled",
            "Cancelada pela equipe. Os contatos já processados foram preservados.",
        )
    elif action == "retry":
        if not present(conn, job)["can_retry"]:
            raise service.Conflict("Esta operação não pode ser retomada. Envie uma nova lista.")
        conn.execute(
            "UPDATE transfer_jobs SET status='queued',error='',attempts=0,lease=NULL,available_at=now(),updated_at=now() WHERE id=%s",
            (job_id,),
        )
    else:
        raise ValueError("Ação inválida.")
    return present(conn, get(conn, job_id))


def stamp(conn):
    return {
        "event": conn.execute("SELECT coalesce(max(id),0) AS n FROM events").fetchone()["n"],
        "erasure": conn.execute("SELECT count(*) AS n FROM erasure_receipts").fetchone()["n"],
        "standard": conn.execute(
            "SELECT value->'revision' AS n FROM settings WHERE key='data_standard'"
        ).fetchone()["n"],
    }


def download_check(conn, job_id):
    privacy.lock(conn)
    job = get(conn, job_id, True)
    if job["kind"] != "export" or job["status"] != "completed":
        raise service.Conflict("Arquivo ainda não disponível ou invalidado. Gere outra exportação.")
    if job["expires_at"] <= datetime.now(UTC) or job["snapshot_stamp"] != stamp(conn):
        purge(conn, job_id, "invalidated", "A base ou a validade mudou; gere outra exportação.")
        return False
    return True


def stream_download(job_id):
    part = 0
    while True:
        with connection() as conn:
            valid = download_check(conn, job_id)
            row = conn.execute(
                "SELECT content FROM transfer_blobs WHERE job_id=%s AND kind='output' AND part=%s",
                (job_id, part),
            ).fetchone()
            chunk = bytes(row["content"]) if row else None
        if not valid:
            raise service.Conflict("Arquivo invalidado durante o download.")
        if chunk is None:
            break
        yield chunk
        part += 1


def privacy_impact(conn, lead_id):
    # Unmapped files cannot reliably be attributed. Invalidate them conservatively.
    ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM transfer_jobs WHERE filename<>'' OR headers<>'[]'::jsonb OR status IN ('queued','running','awaiting_mapping') OR EXISTS (SELECT 1 FROM transfer_blobs b WHERE b.job_id=transfer_jobs.id) OR EXISTS (SELECT 1 FROM transfer_rows r WHERE r.job_id=transfer_jobs.id AND (r.row_data IS NOT NULL OR r.lead_id=%s)) ORDER BY id",
            (lead_id,),
        ).fetchall()
    ]
    return ids


def erase_copies(conn, lead_id):
    affected = privacy_impact(conn, lead_id)
    for ident in affected:
        purge(
            conn,
            ident,
            "invalidated",
            "Arquivos temporários removidos por eliminação de dados. Reenvie uma lista revisada, se necessário.",
        )
        conn.execute(
            "UPDATE transfer_jobs SET filename='',headers='[]',mapping=NULL,digest='' WHERE id=%s",
            (ident,),
        )
    conn.execute("DELETE FROM transfer_rows WHERE lead_id=%s", (lead_id,))
    return len(affected)


def cleanup(conn):
    privacy.lock(conn)
    ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM transfer_jobs WHERE expires_at<=now() AND status NOT IN ('expired','invalidated','cancelled') LIMIT 25"
        ).fetchall()
    ]
    for ident in ids:
        purge(
            conn,
            ident,
            "expired",
            "Arquivos temporários expiraram. Os contatos já importados foram preservados.",
        )


def errors_page(conn, job_id, page):
    get(conn, job_id)
    rows = conn.execute(
        "SELECT line,error FROM transfer_rows WHERE job_id=%s AND status='error' ORDER BY line LIMIT 100 OFFSET %s",
        (job_id, (page - 1) * 100),
    ).fetchall()
    total = conn.execute(
        "SELECT count(*) AS n FROM transfer_rows WHERE job_id=%s AND status='error'", (job_id,)
    ).fetchone()["n"]
    return {"items": rows, "total": total, "page": page, "pages": max(1, (total + 99) // 100)}
