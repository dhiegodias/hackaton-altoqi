import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connection():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        yield conn


def initialize():
    from radar import data_standard, scoring

    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(907202610)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
        scoring.initialize(conn)
        data_standard.initialize(conn)
