import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Settings, create_app  # noqa: E402
from models import Store  # noqa: E402

ADMIN_KEY = "test-admin-key"


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def client(tmp_path):
    settings = Settings(db_path=str(tmp_path / "app.db"), admin_key=ADMIN_KEY, window_seconds=15)
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def admin_headers():
    return {"X-Admin-Key": ADMIN_KEY}


GAME = {
    "home_name": "Green Bay",
    "home_primary": "#1F6B3A",
    "home_secondary": "#F2C230",
    "away_name": "Chicago",
    "away_primary": "#14213D",
    "away_secondary": "#F26A1B",
}
