
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="tradex_test_")

os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite:///{Path(_tmp).as_posix()}/test.db",
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DEV_AUTH_BYPASS": "true",
    "ADMIN_TELEGRAM_IDS": "999",
    "RATE_LIMIT_PER_MINUTE": "100000",
    "JWT_SECRET": "test-secret-for-tradex-tests-32-bytes-minimum",
    "BOT_USERNAME": "TradeXTestBot",
})

import pytest  # noqa: E402

from backend.app import prices, services  # noqa: E402
from backend.app.database import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        services.seed_defaults(db)

    prices.inject_for_tests({
        "BTC": {"price": Decimal("100"), "change_24h": 1.5},
        "ETH": {"price": Decimal("2000"), "change_24h": -0.4},
        "SOL": {"price": Decimal("150"), "change_24h": 3.2},
    })

    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def set_prices(**kwargs):
    prices.inject_for_tests({
        key: {"price": value, "change_24h": 0.0}
        for key, value in kwargs.items()
    })
