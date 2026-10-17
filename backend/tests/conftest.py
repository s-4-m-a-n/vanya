import os
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

# Configure before any app import: settings are cached on first use.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://digitalhunter:digitalhunter@localhost:5433/digitalhunter_test",
)
os.environ["STORAGE_PROVIDER"] = "local"
os.environ["STORAGE_LOCAL_DIR"] = tempfile.mkdtemp(prefix="dh-media-")
os.environ["EVENTS_PROVIDER"] = "inprocess"
os.environ["EVENTS_INPROCESS_ASYNC"] = "false"  # deterministic: handlers finish first
os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-at-least-32-bytes"
# Never call real AI or GBIF services from tests, whatever backend/.env says.
os.environ["LLM_PROVIDER"] = "none"
os.environ["SPECIES_ID_PROVIDER"] = "stub"
os.environ["TAXONOMY_RESOLVER"] = "local"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.adapters.events import base as events_base  # noqa: E402
from app.cli import DEFAULT_TAXA_FILE, seed_taxa  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Role, User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402

# Argon2 is deliberately slow and memory-hard (64 MB per hash); hash the shared test
# password once instead of for every test user.
TEST_PASSWORD_HASH = hash_password("correct-horse")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    # Round-trip proves every migration is reversible.
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def _isolated_event_handlers() -> Iterator[None]:
    saved = {topic: list(handlers) for topic, handlers in events_base._handlers.items()}
    yield
    events_base._handlers.clear()
    events_base._handlers.update(saved)


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded() -> None:
    seed_taxa(DEFAULT_TAXA_FILE)


@pytest.fixture
def db() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def make_user(db: Session):
    def factory(
        role: Role = Role.OBSERVER, email: str | None = None, consent: bool = True
    ) -> tuple[User, dict]:
        user = User(
            email=email or f"{uuid.uuid4().hex[:8]}@example.org",
            display_name="Test User",
            password_hash=TEST_PASSWORD_HASH,
            role=role.value,
            location_consent_at=datetime.now(UTC) if consent else None,
        )
        db.add(user)
        db.commit()
        headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}
        return user, headers

    return factory
