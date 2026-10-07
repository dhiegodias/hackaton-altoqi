"""Bootstrap persisted local secrets and launch commands with the runtime configuration."""

import fcntl
import hmac
import os
import secrets
import sys
import tempfile
from pathlib import Path

from psycopg.conninfo import make_conninfo

SECRETS_DIR = Path("/run/radar-secrets")


def bootstrap(directory=SECRETS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
    # Only the services mounting this volume can access these files.
    directory.chmod(0o755)
    with (directory / ".bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name, variable, length, mode in (
            ("postgres_password", "POSTGRES_PASSWORD", 32, 0o644),
            ("erasure_key", "RADAR_ERASURE_KEY", 48, 0o400),
        ):
            target = directory / name
            supplied = os.getenv(variable, "")
            if supplied.startswith("generate-"):
                raise ValueError(f"Remova o placeholder de {variable} ou deixe o valor vazio.")
            if variable == "RADAR_ERASURE_KEY" and supplied and len(supplied) < 32:
                raise ValueError("RADAR_ERASURE_KEY precisa ter pelo menos 32 caracteres.")
            if target.exists():
                saved = target.read_text()
                if not saved or (variable == "RADAR_ERASURE_KEY" and len(saved) < 32):
                    raise ValueError(f"Arquivo local de {variable} inválido; restaure o volume.")
                if supplied and not hmac.compare_digest(saved.encode(), supplied.encode()):
                    raise ValueError(
                        f"{variable} difere do valor persistido. Restaure a configuração anterior; "
                        "alterar .env não troca a credencial de uma base existente."
                    )
                continue
            value = supplied or secrets.token_urlsafe(length)
            fd, temporary = tempfile.mkstemp(dir=directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                if name == "erasure_key" and os.geteuid() == 0:
                    os.chown(temporary, 1000, 1000)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)


def configure(directory=SECRETS_DIR):
    if not os.getenv("DATABASE_URL"):
        os.environ["DATABASE_URL"] = make_conninfo(
            host="db",
            port=5432,
            dbname="radar",
            user="radar",
            password=(directory / "postgres_password").read_text(),
        )
    if not os.getenv("RADAR_ERASURE_KEY"):
        os.environ["RADAR_ERASURE_KEY"] = (directory / "erasure_key").read_text()


def main():
    if sys.argv[1:] == ["bootstrap"]:
        bootstrap()
        print("Configuração local pronta; valores existentes preservados.")
        return
    if len(sys.argv) < 2:
        raise ValueError("Informe o comando a executar.")
    configure()
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        # Never print connection strings or secret values, including in tracebacks.
        message = (
            str(exc) if isinstance(exc, ValueError) else "Confira o volume de configuração local."
        )
        print("Falha na inicialização: " + message, file=sys.stderr)
        sys.exit(1)
