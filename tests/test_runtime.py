import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from radar.runtime import bootstrap, configure


@pytest.fixture
def runtime_env(monkeypatch):
    for name in ("DATABASE_URL", "POSTGRES_PASSWORD", "RADAR_ERASURE_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_empty_install_generates_secrets_and_reuses_them_after_restart(
    tmp_path, runtime_env, monkeypatch
):
    bootstrap(tmp_path)
    before = {name: (tmp_path / name).read_bytes() for name in ("postgres_password", "erasure_key")}
    assert len(before["postgres_password"]) >= 32
    assert len(before["erasure_key"]) >= 48
    assert before["postgres_password"] != before["erasure_key"]
    bootstrap(tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
    # Track changes made by configure so they are restored after this test.
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RADAR_ERASURE_KEY", "")
    configure(tmp_path)
    connection = conninfo_to_dict(os.environ["DATABASE_URL"])
    assert connection["password"].encode() == before["postgres_password"]
    assert connection["host"] == "db"
    assert os.environ["RADAR_ERASURE_KEY"].encode() == before["erasure_key"]


def test_existing_env_is_adopted_without_losing_special_password_characters(
    tmp_path, runtime_env, monkeypatch
):
    # Deliberate non-secret fixture, including URI-sensitive characters.
    password = "fixture only:@/ ?#%'\\"
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    monkeypatch.setenv(
        "RADAR_ERASURE_KEY",
        "fixture-erasure-key-with-at-least-32-characters",  # gitleaks:allow — fixture only
    )
    monkeypatch.setenv("DATABASE_URL", "")
    bootstrap(tmp_path)
    configure(tmp_path)
    assert conninfo_to_dict(os.environ["DATABASE_URL"])["password"] == password
    original = (tmp_path / "erasure_key").read_bytes()
    monkeypatch.setenv("RADAR_ERASURE_KEY", "another-fixture-key-with-at-least-32-characters")
    with pytest.raises(ValueError, match="difere do valor persistido") as error:
        bootstrap(tmp_path)
    assert "another-fixture" not in str(error.value)
    assert (tmp_path / "erasure_key").read_bytes() == original


def test_password_changes_fail_without_overwriting_persisted_credentials(
    tmp_path, runtime_env, monkeypatch
):
    bootstrap(tmp_path)
    original = (tmp_path / "postgres_password").read_bytes()
    monkeypatch.setenv("POSTGRES_PASSWORD", "changed-fixture-password")
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD difere") as error:
        bootstrap(tmp_path)
    assert "changed-fixture-password" not in str(error.value)
    assert (tmp_path / "postgres_password").read_bytes() == original


def test_short_key_is_rejected_and_external_dsn_is_preserved(tmp_path, runtime_env, monkeypatch):
    monkeypatch.setenv("RADAR_ERASURE_KEY", "short-fixture")
    with pytest.raises(ValueError, match="pelo menos 32"):
        bootstrap(tmp_path)
    monkeypatch.delenv("RADAR_ERASURE_KEY")
    bootstrap(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "host=other-db dbname=isolated_fixture")
    monkeypatch.setenv("RADAR_ERASURE_KEY", "")
    configure(tmp_path)
    assert os.environ["DATABASE_URL"] == "host=other-db dbname=isolated_fixture"
