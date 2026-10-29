"""Run the API and worker locally without Docker, using an embedded PostgreSQL (pgserver).

For development only. Data lives under data/dev/ (gitignored).

    uv run python scripts/dev_stack.py --env-file path/to/dev.env
    # then, in apps/web: LK_API_INTERNAL_URL=http://127.0.0.1:8000 pnpm dev

The env file needs LK_SECRET_KEY and LK_ADMIN_PASSWORD; everything else has dev defaults.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    import pgserver  # dev dependency

    data = ROOT / "data" / "dev"
    data.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(str(data / "pg"), cleanup_mode=None)
    url = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)

    env = {**os.environ, **load_env(args.env_file)}
    env.setdefault("LK_ENVIRONMENT", "development")
    env.setdefault("LK_COOKIE_SECURE", "false")
    env.setdefault("LK_STORAGE_DIR", str(data / "storage"))
    env["LK_DATABASE_URL"] = url
    api_dir = ROOT / "services" / "api"

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=api_dir, env=env, check=True)
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "lk_api.main:create_app", "--factory",
                          "--host", "127.0.0.1", "--port", str(args.port)], cwd=api_dir, env=env),
        subprocess.Popen([sys.executable, "-m", "lk_api.worker"], cwd=api_dir, env=env),
    ]
    print(f"API on http://127.0.0.1:{args.port} (worker running). Ctrl+C to stop.", flush=True)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM if os.name != "nt" else signal.CTRL_BREAK_EVENT)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    main()
