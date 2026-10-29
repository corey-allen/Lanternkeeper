"""Integration test fixtures.

Tests run against real PostgreSQL: either LK_TEST_DATABASE_URL, or an embedded
server started with the ``pgserver`` package. Each test gets a fresh database
created by running the Alembic migrations, so migrations are tested too.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from lk_dm.providers import FakeProvider
from lk_rules.dice import SeededRoller

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "services" / "api"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(API_DIR / "tests"))

ADMIN_PASSWORD = "correct-horse-battery-staple"
SECRET = "test-secret-key-that-is-long-enough-0123456789"


@pytest.fixture(scope="session")
def pg_base_url() -> str:
    url = os.environ.get("LK_TEST_DATABASE_URL")
    if url:
        return url
    pgserver = pytest.importorskip("pgserver", reason="install pgserver or set LK_TEST_DATABASE_URL")
    data_dir = tempfile.mkdtemp(prefix="lk-pg-")
    server = pgserver.get_server(data_dir, cleanup_mode="stop")
    return server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)


def migrate(url: str) -> None:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


@pytest.fixture
def database_url(pg_base_url: str) -> str:
    name = f"lk_test_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(pg_base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = pg_base_url.rsplit("/", 1)[0] + f"/{name}"
    if "?" in pg_base_url:
        url += "?" + pg_base_url.split("?", 1)[1]
    migrate(url)
    return url


@pytest.fixture
def storage_dir(tmp_path: Path) -> str:
    return str(tmp_path / "storage")


@pytest.fixture
def settings_factory(database_url: str, storage_dir: str):
    from lk_api.config import Settings

    def make(**overrides):
        values = dict(environment="test", database_url=database_url, storage_dir=storage_dir, secret_key=SECRET,
                      admin_username="keeper", admin_password=ADMIN_PASSWORD, cookie_secure=False,
                      dice_mode="seeded", dice_seed=7, log_level="WARNING")
        values.update(overrides)
        return Settings(**values)

    return make


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def app_factory(settings_factory, fake_provider):
    from lk_api.main import create_app

    def make(provider=None, roller=None, **overrides):
        return create_app(settings_factory(**overrides), provider=provider or fake_provider,
                          roller=roller or SeededRoller(7))

    return make


def login(client: TestClient, username: str = "keeper", password: str = ADMIN_PASSWORD) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def client(app_factory):
    with TestClient(app_factory()) as c:
        c.headers.update(login(c))
        yield c


@pytest.fixture(scope="session")
def fixture_pdf() -> bytes:
    from generate_fixture import build_pdf

    return build_pdf()


@pytest.fixture(scope="session")
def manifest() -> dict:
    from generate_fixture import MANIFEST

    return json.loads(json.dumps(MANIFEST))
