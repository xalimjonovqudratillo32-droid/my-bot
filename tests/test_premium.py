from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app import services
from backend.app.main import app

from tests.conftest import set_prices

client = TestClient(app)


def auth(tg_id: int) -> dict:
    token = client.post("/api/auth/telegram", headers={"X-Dev-User": str(tg_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def admin_headers() -> dict:
    return auth(999)


def uid_of(tg_id: int) -> int:
    auth(tg_id)  # foydalanuvchi mavjudligiga ishonch hosil qilamiz
    return client.get(f"/api/admin/users?q={tg_id}", headers=admin_headers()).json()[0]["id"]


def test_regular_user_is_not_premium():
    body = client.get("/api/premium", headers=auth(201)).json()
    assert body["is_premium"] is False and body["premium_until"] is None
    assert len(body["perks"]) >= 3


def test_analytics_locked_for_non_premium():
    r = client.get("/api/premium/analytics", headers=auth(202))
    assert r.status_code == 403


def test_admin_grant_activates_premium_and_badge():
    user_h = auth(203)
    uid = uid_of(203)
    r = client.post(f"/api/admin/users/{uid}/premium/grant", json={"days": 30}, headers=admin_headers())
    assert r.status_code == 200 and r.json()["is_premium"] is True
    me = client.get("/api/me", headers=user_h).json()
    assert me["user"]["is_premium"] is True
    assert client.get("/api/premium/analytics", headers=user_h).status_code == 200


def test_grant_stacks_on_existing_subscription(db):
    admin, _ = services.get_or_create_user(db, {"id": 999, "first_name": "Admin"})
    user, _ = services.get_or_create_user(db, {"id": 204, "first_name": "U"})
    services.grant_premium(db, admin, user, 10)
    first_end = user.premium_until
    services.grant_premium(db, admin, user, 5)
    assert (user.premium_until - first_end).days == 5


def test_invalid_days_rejected():
    uid = uid_of(203)
    r = client.post(f"/api/admin/users/{uid}/premium/grant", json={"days": 0}, headers=admin_headers())
    assert r.status_code == 422


def test_revoke_removes_premium():
    uid = uid_of(203)
    client.post(f"/api/admin/users/{uid}/premium/grant", json={"days": 5}, headers=admin_headers())
    r = client.post(f"/api/admin/users/{uid}/premium/revoke", headers=admin_headers())
    assert r.json()["is_premium"] is False
    assert client.get("/api/premium/analytics", headers=auth(203)).status_code == 403


def test_non_admin_cannot_grant_premium():
    uid = uid_of(203)
    r = client.post(f"/api/admin/users/{uid}/premium/grant", json={"days": 30}, headers=auth(205))
    assert r.status_code == 403


def test_premium_gets_1_5x_xp_per_trade(db):
    admin, _ = services.get_or_create_user(db, {"id": 999, "first_name": "Admin"})
    plain, _ = services.get_or_create_user(db, {"id": 301, "first_name": "P"})
    vip, _ = services.get_or_create_user(db, {"id": 302, "first_name": "V"})
    services.grant_premium(db, admin, vip, 30)
    services.place_market_order(db, plain, "BTC", "buy", Decimal("0.1"))
    services.place_market_order(db, vip, "BTC", "buy", Decimal("0.1"))
    db.refresh(plain); db.refresh(vip)
    # Ikkalasiga ham "first_trade" yutug'i (+50) tushadi; farq faqat savdo XP'idan
    expected_diff = round(services.XP_PER_TRADE * services.PREMIUM_XP_MULTIPLIER) - services.XP_PER_TRADE
    assert vip.xp - plain.xp == expected_diff


def test_premium_daily_reward_doubled(db):
    admin, _ = services.get_or_create_user(db, {"id": 999, "first_name": "Admin"})
    vip, _ = services.get_or_create_user(db, {"id": 303, "first_name": "V"})
    services.grant_premium(db, admin, vip, 30)
    res = services.claim_daily_reward(db, vip)
    assert Decimal(str(res["balance_bonus"])) == Decimal(str(services.settings.DAILY_REWARD)) * 2


def test_premium_analytics_content(db):
    admin, _ = services.get_or_create_user(db, {"id": 999, "first_name": "Admin"})
    vip, _ = services.get_or_create_user(db, {"id": 304, "first_name": "V"})
    services.grant_premium(db, admin, vip, 30)
    services.place_market_order(db, vip, "BTC", "buy", Decimal("2"))
    set_prices(BTC=Decimal("150"))
    services.place_market_order(db, vip, "BTC", "sell", Decimal("1"))
    stats = services.premium_analytics(db, vip)
    assert stats["sells"] == 1
    assert stats["realized_pnl"] == Decimal("50.00000000")
    assert stats["win_rate"] == 100.0
    assert stats["by_symbol"][0]["symbol"] == "BTC"
    assert stats["best_trade"]["side"] == "sell"
    set_prices(BTC=Decimal("100"))


def test_premium_expires(db):
    admin, _ = services.get_or_create_user(db, {"id": 999, "first_name": "Admin"})
    u, _ = services.get_or_create_user(db, {"id": 305, "first_name": "E"})
    services.grant_premium(db, admin, u, 1)
    from datetime import timedelta
    u.premium_until = services.utcnow() - timedelta(seconds=1)
    db.commit()
    assert services.is_premium(u) is False
