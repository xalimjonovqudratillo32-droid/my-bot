from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def login(tg_id: int, start_param: str | None = None):
    headers = {"X-Dev-User": str(tg_id)}
    return client.post("/api/auth/telegram", headers=headers).json()


def auth(tg_id: int) -> dict:
    return {"Authorization": f"Bearer {login(tg_id)['token']}"}


def test_health_and_pages():
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200


def test_login_and_dashboard():
    r = client.get("/api/me", headers=auth(101))
    assert r.status_code == 200
    body = r.json()
    assert body["portfolio"]["cash"] == 10000.0 or float(body["portfolio"]["cash"]) == 10000.0
    assert body["user"]["name"] == "Beginner"


def test_protected_routes_need_token():
    assert client.get("/api/me").status_code == 401


def test_market_buy_via_api_with_idempotency():
    h = auth(102)
    body = {"symbol": "BTC", "side": "buy", "quantity": 1}
    r1 = client.post("/api/trade/market", json=body, headers={**h, "Idempotency-Key": "abc"})
    r2 = client.post("/api/trade/market", json=body, headers={**h, "Idempotency-Key": "abc"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    txs = client.get("/api/trade/transactions", headers=h).json()
    assert len(txs) == 1


def test_overspend_returns_400():
    r = client.post("/api/trade/market", json={"symbol": "BTC", "side": "buy", "quantity": 1000}, headers=auth(103))
    assert r.status_code == 400


def test_invalid_input_rejected():
    r = client.post("/api/trade/market", json={"symbol": "BTC", "side": "hold", "quantity": 1}, headers=auth(104))
    assert r.status_code == 422
    r = client.post("/api/trade/market", json={"symbol": "NOPE", "side": "buy", "quantity": 1}, headers=auth(104))
    assert r.status_code == 400


def test_daily_reward_once_per_day():
    h = auth(105)
    assert client.post("/api/daily-reward", headers=h).status_code == 200
    assert client.post("/api/daily-reward", headers=h).status_code == 409


def test_mission_progress_and_claim():
    h = auth(106)
    for _ in range(3):
        client.post("/api/trade/market", json={"symbol": "ETH", "side": "buy", "quantity": 0.01}, headers=h)
    missions = client.get("/api/missions", headers=h).json()["missions"]
    daily = next(m for m in missions if m["code"] == "daily_3_trades")
    assert daily["completed"] is True
    r = client.post(f"/api/missions/{daily['id']}/claim", headers=h)
    assert r.status_code == 200
    assert client.post(f"/api/missions/{daily['id']}/claim", headers=h).status_code == 409


def test_admin_forbidden_for_regular_user():
    assert client.get("/api/admin/stats", headers=auth(107)).status_code == 403


def test_admin_endpoints_work_for_admin():
    h = auth(999)
    stats = client.get("/api/admin/stats", headers=h)
    assert stats.status_code == 200 and stats.json()["total_users"] >= 1
    users = client.get("/api/admin/users?q=dev", headers=h).json()
    target = users[0]["id"]
    assert client.post(f"/api/admin/users/{target}/balance", json={"amount": 500, "reason": "test"},
                       headers=h).json()["cash"] == 10500.0
    assert client.get("/api/admin/logs", headers=h).json()[0]["action"] == "balance_adjust"


def test_banned_user_cannot_access():
    h_admin = auth(999)
    victim = auth(108)
    uid = client.get("/api/admin/users?q=dev108", headers=h_admin).json()[0]["id"]
    client.post(f"/api/admin/users/{uid}/ban", json={"banned": True}, headers=h_admin)
    assert client.get("/api/me", headers=victim).status_code == 403


def test_otp_login_flow():
    from backend.app.database import SessionLocal
    from backend.app import services
    login(555)
    with SessionLocal() as s:
        from backend.app.models import User
        u = s.query(User).filter_by(telegram_id=555).one()
        u.is_admin = True
        s.commit()
        code = services.create_admin_otp(s, 555)
    r = client.post("/api/admin/otp-login", json={"telegram_id": 555, "code": code})
    assert r.status_code == 200 and r.json()["token"]
    # Kod bir martalik
    assert client.post("/api/admin/otp-login", json={"telegram_id": 555, "code": code}).status_code == 401
