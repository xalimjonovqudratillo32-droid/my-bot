from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app import services
from backend.app.config import Settings, settings
from backend.app.main import app
from backend.app.models import Transaction
from backend.app.services import ServiceError
from tests.conftest import set_prices

client = TestClient(app)


def test_production_disables_dev_bypass():
    s = Settings(APP_ENV="production", DEV_AUTH_BYPASS=True)
    assert s.dev_bypass_enabled is False
    assert Settings(APP_ENV="development", DEV_AUTH_BYPASS=True).dev_bypass_enabled is True


def test_dev_header_rejected_when_bypass_off(monkeypatch):
    monkeypatch.setattr(settings, "DEV_AUTH_BYPASS", False)
    r = client.post("/api/auth/telegram", headers={"X-Dev-User": "777"})
    assert r.status_code == 401


def test_limit_order_cannot_fill_twice(db):
    user, _ = services.get_or_create_user(db, {"id": 501, "first_name": "L"})
    order = services.place_limit_order(db, user, "BTC", "buy", Decimal("1"), Decimal("90"))
    set_prices(BTC=Decimal("85"))
    assert services.process_pending_orders(db) == 1
    # Ikkinchi marta bajarishga urinish (parallel worker simulyatsiyasi)
    with pytest.raises(ServiceError):
        services._fill(db, user, order, Decimal("85"))
    db.rollback()
    assert db.query(Transaction).filter_by(user_id=user.id).count() == 1


def test_security_headers_present():
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]
    a = client.get("/admin")
    assert "frame-ancestors 'none'" in a.headers["Content-Security-Policy"]
    assert a.headers["Cache-Control"] == "no-store"


def test_sql_injection_string_is_harmless():
    h = {"X-Dev-User": "888"}
    token = client.post("/api/auth/telegram", headers=h).json()["token"]
    r = client.get("/api/admin/users?q=' OR 1=1 --", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403  # oddiy foydalanuvchi, SQL ishlamaydi
