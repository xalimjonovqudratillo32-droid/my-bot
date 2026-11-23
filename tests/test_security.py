import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from backend.app.security import AuthError, verify_init_data

TOKEN = "123456:TEST-TOKEN"


def make_init_data(user: dict, token: str = TOKEN, auth_date: int | None = None, start_param: str | None = None) -> str:
    params = {"auth_date": str(auth_date or int(time.time())),
              "user": json.dumps(user, separators=(",", ":")), "query_id": "AAH"}
    if start_param:
        params["start_param"] = start_param
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


def test_valid_init_data_accepted():
    data = verify_init_data(make_init_data({"id": 42, "first_name": "Ali"}), TOKEN)
    assert data["user"]["id"] == 42


def test_tampered_init_data_rejected():
    raw = make_init_data({"id": 42, "first_name": "Ali"})
    tampered = raw.replace("Ali", "Evil")
    with pytest.raises(AuthError):
        verify_init_data(tampered, TOKEN)


def test_wrong_token_rejected():
    with pytest.raises(AuthError):
        verify_init_data(make_init_data({"id": 1}), "999:OTHER")


def test_expired_init_data_rejected():
    old = make_init_data({"id": 1}, auth_date=int(time.time()) - 90000)
    with pytest.raises(AuthError):
        verify_init_data(old, TOKEN)


def test_missing_hash_rejected():
    with pytest.raises(AuthError):
        verify_init_data("auth_date=1&user=%7B%7D", TOKEN)
