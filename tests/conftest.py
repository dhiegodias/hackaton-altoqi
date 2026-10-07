"""PostgreSQL isolation: application tables are never truncated or dropped."""

import json
import os
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from radar import data_standard, hubspot, sources
from radar.db import connection, initialize


@pytest.fixture(scope="session")
def postgres_schema():
    original_dsn = os.environ.get("DATABASE_URL")
    if not original_dsn:
        pytest.fail("DATABASE_URL é obrigatória. Execute os testes pelo Docker Compose.")
    schema = "radar_test_" + uuid.uuid4().hex
    scoped_dsn = make_conninfo(original_dsn, options=f"-csearch_path={schema}")
    env = pytest.MonkeyPatch()
    with psycopg.connect(original_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        env.setenv("DATABASE_URL", scoped_dsn)
        initialize()
        yield SimpleNamespace(name=schema, dsn=scoped_dsn)
    finally:
        env.undo()
        with psycopg.connect(original_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def database(postgres_schema):
    # Qualifying every table keeps cleanup safe even if a test changes search_path.
    tables = (
        "transfer_rows",
        "transfer_blobs",
        "transfer_jobs",
        "erasure_blocks",
        "erasure_receipts",
        "outbound_promotions",
        "outbound_searches",
        "calibration_reviews",
        "scoring_versions",
        "events",
        "suggestions",
        "jobs",
        "requests",
        "syncs",
        "leads",
        "settings",
    )
    with connection() as conn:
        assert (
            conn.execute("SELECT current_schema() AS name").fetchone()["name"]
            == postgres_schema.name
        )
        identifiers = [sql.Identifier(postgres_schema.name, table) for table in tables]
        conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(", ").join(identifiers))
        )
    initialize()
    yield connection


@pytest.fixture(autouse=True)
def example_dns(monkeypatch):
    """External DNS transport for reserved, fictional test domains only.

    Real normalization, DNS-response parsing and all source gates still execute.
    Domain-validation tests supply their own response streams, including failures.
    """
    data_standard._dns_cache.clear()
    original = sources.fetch_public

    def resolve(url, *args, **kwargs):
        parsed = urlparse(url)
        name = parse_qs(parsed.query).get("name", [""])[0]
        if parsed.hostname == "dns.google" and name.endswith((".invalid", ".example", ".test")):
            return url, json.dumps(
                {"Status": 0, "Answer": [{"type": 15, "data": "10 mail." + name}]}
            )
        return original(url, *args, **kwargs)

    monkeypatch.setattr(sources, "fetch_public", resolve)
    monkeypatch.setenv(
        "RADAR_ERASURE_KEY",
        "test-erasure-key-placeholder-32-characters",  # gitleaks:allow — fixture only
    )


@pytest.fixture
def personal_email_dns(monkeypatch):
    """DNS boundary for personal-mail journeys; never query fictional mailboxes."""
    original = sources.fetch_public
    calls = []

    def resolve(url, *args, **kwargs):
        parsed = urlparse(url)
        domain = parse_qs(parsed.query).get("name", [""])[0]
        if parsed.hostname == "dns.google" and domain in {"gmail.com", "outlook.com", "yahoo.com"}:
            calls.append(url)
            return url, json.dumps(
                {"Status": 0, "Answer": [{"type": 15, "data": "10 mx.mail-provider.invalid."}]}
            )
        return original(url, *args, **kwargs)

    monkeypatch.setattr(sources, "fetch_public", resolve)
    return calls


@pytest.fixture
def api_client(database, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("RADAR_ACCESS_TOKEN", raising=False)
    from radar.api import app

    # No lifespan: initialize() is owned by the isolated schema fixture, not demo seed.
    client = TestClient(app)
    yield client
    client.close()


@pytest.fixture
def remote(monkeypatch):
    """Real HTTP transport. A PATCH can mutate the CRM and lose its response."""
    state = {
        "properties": {"jobtitle": "Engenheiro", "website": ""},
        "calls": [],
        "http_requests": [],
        "drop_response_once": False,
        "discard_patch_once": False,
    }
    lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, body, status=200):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            parsed = urlparse(self.path)
            properties = parse_qs(parsed.query)["properties"][0].split(",")
            with lock:
                state["calls"].append(("GET", parsed.path, properties))
                state["http_requests"].append(
                    {"method": "GET", "path": parsed.path, "properties": properties}
                )
                body = {key: state["properties"].get(key, "") for key in properties}
            self.send_json({"id": "contact-7", "properties": body})

        def do_PATCH(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                state["http_requests"].append(
                    {"method": "PATCH", "path": self.path, "body": payload}
                )
                props = payload.get("properties")
                allowed = state.get("allowed_properties")
                if (
                    set(payload) != {"properties"}
                    or not isinstance(props, dict)
                    or any(not isinstance(value, str) for value in props.values())
                    or (allowed is not None and not set(props) <= set(allowed))
                ):
                    self.send_json({"message": "Invalid property contract"}, status=400)
                    return
                state["calls"].append(("PATCH", self.path, payload["properties"]))
                if state["discard_patch_once"]:
                    state["discard_patch_once"] = False
                else:
                    state["properties"].update(payload["properties"])
                if state["drop_response_once"]:
                    state["drop_response_once"] = False
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                body = dict(state["properties"])
            self.send_json({"id": "contact-7", "properties": body})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_request = httpx.request

    def routed_request(method, url, **kwargs):
        assert url.startswith("https://api.hubapi.com/")
        return real_request(
            method, f"http://127.0.0.1:{server.server_port}" + urlparse(url).path, **kwargs
        )

    monkeypatch.setattr(hubspot.httpx, "request", routed_request)
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-placeholder-not-a-secret")
    monkeypatch.setenv("HUBSPOT_WRITE_ENABLED", "true")
    monkeypatch.setenv(
        "HUBSPOT_FIELD_MAP", '{"role":"jobtitle","website":"website","product_fit":"fit"}'
    )
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
