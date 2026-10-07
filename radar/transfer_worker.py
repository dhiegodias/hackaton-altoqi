"""Dedicated worker: streaming parsing, bounded batches, leases and atomic progress."""

import csv
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
from psycopg.types.json import Jsonb

from radar import data_standard, hubspot, importer, privacy, service, transfer_files, transfers
from radar.db import connection, initialize
from radar.validation import FIELDS, clean_data, csv_safe, normalize_email

log = logging.getLogger("radar.transfers")
BATCH = 100
PARSE_BATCH = 500
LEASE_SECONDS = 600
email_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="radar-domain")


class LostLease(Exception):
    pass


def claim():
    with connection() as conn:
        job = conn.execute(
            "SELECT * FROM transfer_jobs WHERE expires_at>now() AND ((status='queued' AND available_at<=now()) OR (status='running' AND lease_until<now())) ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if not job:
            return None
        if job["status"] == "running" and job["attempts"] >= 3:
            conn.execute(
                "UPDATE transfer_jobs SET status='failed',lease=NULL,error='Worker interrompido repetidamente. Retome após conferir o serviço.',updated_at=now() WHERE id=%s",
                (job["id"],),
            )
            return None
        return conn.execute(
            "UPDATE transfer_jobs SET status='running',lease=%s,lease_until=now()+make_interval(secs=>%s),attempts=attempts+%s,updated_at=now() WHERE id=%s RETURNING *",
            (uuid4(), LEASE_SECONDS, int(job["status"] == "running"), job["id"]),
        ).fetchone()


def guard(conn, job):
    privacy.lock(conn)
    current = transfers.get(conn, job["id"], True)
    if (
        current["status"] != "running"
        or current["lease"] != job["lease"]
        or current["lease_until"] <= datetime.now(UTC)
    ):
        raise LostLease()
    return current


def release(conn, job, status="queued", **changes):
    columns = {"status": status, "lease": None, "lease_until": None, **changes}
    clauses = ",".join(key + "=%s" for key in columns)
    conn.execute(
        "UPDATE transfer_jobs SET " + clauses + ",updated_at=now(),available_at=now() WHERE id=%s",
        (*columns.values(), job["id"]),
    )


def heartbeat(conn, job):
    conn.execute(
        "UPDATE transfer_jobs SET lease_until=now()+make_interval(secs=>%s),updated_at=now() WHERE id=%s",
        (LEASE_SECONDS, job["id"]),
    )


def stage_batch(job, headers, batch, staged):
    with connection() as conn:
        guard(conn, job)
        conn.execute(
            "UPDATE transfer_jobs SET headers=%s,staged=%s WHERE id=%s",
            (Jsonb(headers), staged, job["id"]),
        )
        # Each parser retry starts over, but only writes rows after the saved physical line.
        with conn.cursor().copy("COPY transfer_rows(job_id,line,row_data) FROM STDIN") as copy:
            for line, row in batch:
                copy.write_row((job["id"], line, Jsonb(row)))
        heartbeat(conn, job)


def parse(job):
    with connection() as conn:
        checkpoint = conn.execute(
            "SELECT coalesce(max(line),1) AS n FROM transfer_rows WHERE job_id=%s", (job["id"],)
        ).fetchone()["n"]
    with connection() as source:
        source.autocommit = True
        file = transfer_files.BlobReader(source, job)
        iterator = transfer_files.records(file, job["filename"], job["encoding"])
        _, headers = next(iterator)
        batch = []
        count = 0
        for line, row in iterator:
            count += 1
            if line > checkpoint:
                batch.append((line, row))
            if len(batch) >= PARSE_BATCH:
                stage_batch(job, headers, batch, count)
                batch = []
        if batch:
            stage_batch(job, headers, batch, count)
        with connection() as conn:
            guard(conn, job)
            if job["mapping"] is not None:
                transfers.validate_mapping(job["mapping"], headers)
            conn.execute(
                "DELETE FROM transfer_blobs WHERE job_id=%s AND kind='input'", (job["id"],)
            )
            release(
                conn,
                job,
                "queued" if job["mapping"] is not None else "awaiting_mapping",
                headers=Jsonb(headers),
                total=count,
                staged=count,
                phase="importing",
            )


def prepare_rows(rows, mapping, cfg):
    entries = []
    futures = {}
    for row in rows:
        try:
            payload = importer.row_payload(row["row_data"], mapping)
            if not payload:
                raise ValueError("Linha sem valores nas colunas mapeadas.")
            data_standard.normalize_name(payload.get("name", ""))
            clean_data(payload["data"])
            email = normalize_email(payload.get("email", ""))
            # Validate each domain once per batch, with bounded external concurrency.
            domain = email.rsplit("@", 1)[-1] if email else ""
            if domain not in futures:
                futures[domain] = email_pool.submit(data_standard.prepare_email, email, cfg)
            entries.append((row, payload, email, futures[domain], None))
        except (ValueError, TypeError) as exc:
            entries.append((row, None, "", None, str(exc)))
    result = []
    for row, payload, email, future, error in entries:
        checked = None
        if future:
            try:
                proof = future.result()
                checked = data_standard.PreparedEmail(email, proof.revision, proof.proof)
            except data_standard.VerificationUnavailable:
                raise
            except (ValueError, TypeError) as exc:
                error = str(exc)
        result.append((row, payload, checked, error))
    return result


def import_batch(job):
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transfer_rows WHERE job_id=%s AND status='pending' ORDER BY line LIMIT %s",
            (job["id"], BATCH),
        ).fetchall()
        cfg = data_standard.config(conn)
    prepared = prepare_rows(rows, job["mapping"], cfg)
    with connection() as conn:
        current = guard(conn, job)
        created, duplicates, errors = 0, 0, 0
        for row, payload, email, error in prepared:
            result = None
            if not error:
                try:
                    with conn.transaction():
                        # Stable across jobs and response loss, including contacts without email.
                        key = "import:" + service.digest(
                            [
                                job["digest"],
                                row["line"],
                                payload,
                                "hubspot" if job["kind"] == "hubspot_import" else "csv",
                            ]
                        )
                        replay = conn.execute(
                            "SELECT 1 FROM requests WHERE key=%s", (key,)
                        ).fetchone()
                        result = service.capture(
                            conn,
                            payload,
                            key,
                            origin="hubspot" if job["kind"] == "hubspot_import" else "csv",
                            prepared_email=email,
                        )
                        duplicate = bool(replay or result["duplicate"])
                except data_standard.VerificationUnavailable:
                    raise
                except (ValueError, TypeError, service.Conflict) as exc:
                    error = str(exc)
            if error:
                errors += 1
                conn.execute(
                    "UPDATE transfer_rows SET status='error',error=%s,row_data=NULL WHERE job_id=%s AND line=%s",
                    (error[:400], job["id"], row["line"]),
                )
            else:
                duplicates += int(duplicate)
                created += int(not duplicate)
                conn.execute(
                    "UPDATE transfer_rows SET status=%s,lead_id=%s,row_data=NULL WHERE job_id=%s AND line=%s",
                    ("duplicate" if duplicate else "created", result["id"], job["id"], row["line"]),
                )
        processed = current["processed"] + len(rows)
        is_done = processed >= current["staged"]
        next_phase = (
            "hubspot_fetch"
            if job["kind"] == "hubspot_import" and not job["source_done"] and is_done
            else "importing"
        )
        done = is_done and (job["kind"] != "hubspot_import" or job["source_done"])
        release(
            conn,
            job,
            "completed" if done else "queued",
            processed=processed,
            created_count=current["created_count"] + created,
            duplicate_count=current["duplicate_count"] + duplicates,
            error_count=current["error_count"] + errors,
            phase=next_phase,
            finished_at=datetime.now(UTC) if done else None,
            attempts=0,
        )


def hubspot_page(job):
    # Source HTTP happens without the privacy/write lock or an open DB transaction.
    response = hubspot.HubSpotClient().list_contacts(job["source_after"])
    mapping = hubspot.field_map()
    rows = []
    for item in response.get("results", []):
        props = item.get("properties", {})
        value = {
            "name": " ".join(filter(None, [props.get("firstname"), props.get("lastname")])),
            "company": props.get("company") or "",
            "email": props.get("email") or "",
            "hubspot_id": item["id"],
        }
        value.update(
            {
                field: props[prop]
                for field, prop in mapping.items()
                if props.get(prop) not in (None, "")
            }
        )
        rows.append(value)
    after = response.get("paging", {}).get("next", {}).get("after")
    if after is not None and str(after) == job["source_after"]:
        raise ValueError("HubSpot repetiu o cursor de paginação; operação interrompida.")
    if job["staged"] + len(rows) > transfer_files.MAX_ROWS:
        raise ValueError(
            "Limite de linhas atingido. Confira o resultado parcial antes de continuar outra importação."
        )
    with connection() as conn:
        current = guard(conn, job)
        for offset, row in enumerate(rows, start=2):
            conn.execute(
                "INSERT INTO transfer_rows(job_id,line,row_data) VALUES (%s,%s,%s)",
                (job["id"], current["staged"] + offset, Jsonb(row)),
            )
        mapping = {key: key for key in {*FIELDS, "name", "email", "company", "hubspot_id"}}
        release(
            conn,
            job,
            phase="importing",
            mapping=Jsonb(mapping),
            staged=current["staged"] + len(rows),
            total=current["staged"] + len(rows) if after is None else None,
            source_after=str(after) if after is not None else None,
            source_done=after is None,
            attempts=0,
        )


def export_batch(job):
    with connection() as conn:
        current = guard(conn, job)
        now = datetime.now(UTC)
        current_stamp = transfers.stamp(conn)
        if current["snapshot_stamp"] is not None and current_stamp != current["snapshot_stamp"]:
            transfers.purge(
                conn,
                job["id"],
                "invalidated",
                "A base mudou durante a exportação. Gere outra após concluir as alterações.",
            )
            return
        if current["snapshot_at"] is None:
            current["snapshot_at"] = now
            current["snapshot_stamp"] = current_stamp
            current["total"] = conn.execute(
                "SELECT count(*) AS n FROM leads WHERE NOT suppressed AND created_at<=%s", (now,)
            ).fetchone()["n"]
            current["expires_at"] = min(
                now + timedelta(minutes=30),
                (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
            )
        rows = conn.execute(
            "SELECT * FROM leads WHERE NOT suppressed AND created_at<=%s AND (%s='' OR id>%s::uuid) ORDER BY id LIMIT %s",
            (
                current["snapshot_at"],
                current["cursor_value"],
                current["cursor_value"] or "00000000-0000-0000-0000-000000000000",
                BATCH,
            ),
        ).fetchall()
        ids = [row["id"] for row in rows]
        suggestions = conn.execute(
            "SELECT DISTINCT ON (lead_id,field) * FROM suggestions WHERE lead_id=ANY(%s) AND status='approved' ORDER BY lead_id,field,reviewed_at DESC",
            (ids,),
        ).fetchall()
        by_lead = {ident: [] for ident in ids}
        for item in suggestions:
            by_lead[item["lead_id"]].append(item)
        cfg = data_standard.config(conn)
        output = io.StringIO()
        writer = csv.writer(output)
        if not current["cursor_value"]:
            output.write("\ufeff")
            writer.writerow(["hubspot_id", "nome", "empresa", "email", *FIELDS])
        exported = 0
        expires = current["expires_at"]
        for lead in rows:
            approved = hubspot.approved_fields(conn, lead, by_lead[lead["id"]], cfg)
            if not approved:
                continue
            values = [
                lead["hubspot_id"],
                lead["name"],
                lead["company"],
                lead["email"],
                *[approved[key]["value"] if key in approved else "" for key in FIELDS],
            ]
            writer.writerow([csv_safe(v) for v in values])
            exported += 1
            for item in approved.values():
                days = (
                    90
                    if item["field"]
                    in ("role", "employee_count", "employee_range", "decision_role", "product_fit")
                    else 180
                )
                expires = min(expires, item["observed_at"] + timedelta(days=days + 1))
        blob = output.getvalue().encode("utf-8")
        part = conn.execute(
            "SELECT coalesce(max(part),-1)+1 AS n FROM transfer_blobs WHERE job_id=%s AND kind='output'",
            (job["id"],),
        ).fetchone()["n"]
        for offset in range(0, len(blob), transfer_files.CHUNK_BYTES):
            conn.execute(
                "INSERT INTO transfer_blobs(job_id,kind,part,content) VALUES (%s,'output',%s,%s)",
                (job["id"], part, blob[offset : offset + transfer_files.CHUNK_BYTES]),
            )
            part += 1
        done = len(rows) < BATCH or current["processed"] + len(rows) >= current["total"]
        release(
            conn,
            job,
            "completed" if done else "queued",
            total=current["total"],
            snapshot_at=current["snapshot_at"],
            snapshot_stamp=Jsonb(current["snapshot_stamp"]),
            cursor_value=str(rows[-1]["id"]) if rows else current["cursor_value"],
            processed=current["processed"] + len(rows),
            exported_count=current["exported_count"] + exported,
            expires_at=expires,
            finished_at=now if done else None,
            attempts=0,
        )


def failure(job, message, retry):
    with connection() as conn:
        try:
            current = guard(conn, job)
        except LostLease:
            return
        attempts = current["attempts"] + 1
        if retry and attempts < 3:
            release(conn, job, "queued", error=message, attempts=attempts)
            conn.execute(
                "UPDATE transfer_jobs SET available_at=now()+make_interval(secs=>%s) WHERE id=%s",
                (min(60, 2**attempts), job["id"]),
            )
        else:
            release(conn, job, "failed", error=message, attempts=attempts)


def run_one():
    job = claim()
    if not job:
        return False
    try:
        {
            "parsing": parse,
            "importing": import_batch,
            "hubspot_fetch": hubspot_page,
            "exporting": export_batch,
        }[job["phase"]](job)
        with connection() as conn:
            result = transfers.get(conn, job["id"])
        if (
            result["status"] in ("completed", "awaiting_mapping", "invalidated")
            or result["phase"] != job["phase"]
        ):
            log.info(
                "job=%s phase=%s status=%s processed=%s total=%s",
                job["id"],
                result["phase"],
                result["status"],
                result["processed"],
                result["total"],
            )

    except LostLease:
        pass
    except (
        data_standard.VerificationUnavailable,
        hubspot.HubSpotTemporaryError,
        httpx.HTTPError,
        OSError,
        psycopg.OperationalError,
    ):
        failure(
            job,
            "Serviço temporariamente indisponível. O progresso foi preservado para nova tentativa.",
            True,
        )
    except ValueError as exc:
        failure(job, str(exc)[:400], False)
    except Exception as exc:
        log.error("job=%s failure=%s", job["id"], type(exc).__name__)
        failure(
            job,
            "Falha no processamento. Progresso preservado; confira o serviço antes de retomar.",
            False,
        )
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    initialize()
    last_cleanup = 0
    while True:
        try:
            if time.monotonic() - last_cleanup > 60:
                with connection() as conn:
                    transfers.cleanup(conn)
                last_cleanup = time.monotonic()
            if not run_one():
                time.sleep(1)
        except Exception as exc:
            log.error("worker_failure=%s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
