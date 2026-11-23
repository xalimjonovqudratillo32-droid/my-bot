from decimal import Decimal

import pytest

from backend.app import services
from backend.app.models import Order, Position, Transaction
from backend.app.services import ServiceError
from tests.conftest import set_prices


def new_user(db, tg_id=1):
    user, _ = services.get_or_create_user(db, {"id": tg_id, "first_name": f"u{tg_id}"})
    return user


def test_buy_updates_cash_and_position(db):
    user = new_user(db)
    services.place_market_order(db, user, "BTC", "buy", Decimal("2"))
    bal = services.get_balance(db, user.id)
    pos = db.query(Position).filter_by(user_id=user.id, symbol="BTC").one()
    assert Decimal(str(bal.cash)) == Decimal("9800")
    assert Decimal(str(pos.quantity)) == Decimal("2")
    assert Decimal(str(pos.avg_entry)) == Decimal("100")


def test_weighted_average_entry(db):
    user = new_user(db)
    services.place_market_order(db, user, "BTC", "buy", Decimal("1"))
    set_prices(BTC=Decimal("200"))
    services.place_market_order(db, user, "BTC", "buy", Decimal("1"))
    pos = db.query(Position).filter_by(user_id=user.id, symbol="BTC").one()
    assert Decimal(str(pos.avg_entry)) == Decimal("150")


def test_sell_realizes_pnl(db):
    user = new_user(db)
    services.place_market_order(db, user, "BTC", "buy", Decimal("2"))
    set_prices(BTC=Decimal("150"))
    services.place_market_order(db, user, "BTC", "sell", Decimal("1"))
    bal = services.get_balance(db, user.id)
    pos = db.query(Position).filter_by(user_id=user.id, symbol="BTC").one()
    assert Decimal(str(bal.cash)) == Decimal("9950")
    assert Decimal(str(bal.realized_pnl)) == Decimal("50")
    assert Decimal(str(pos.quantity)) == Decimal("1")


def test_cannot_go_negative(db):
    user = new_user(db)
    with pytest.raises(ServiceError):
        services.place_market_order(db, user, "BTC", "buy", Decimal("101"))
    assert Decimal(str(services.get_balance(db, user.id).cash)) == Decimal("10000")


def test_cannot_sell_more_than_held(db):
    user = new_user(db)
    services.place_market_order(db, user, "BTC", "buy", Decimal("1"))
    with pytest.raises(ServiceError):
        services.place_market_order(db, user, "BTC", "sell", Decimal("2"))


def test_idempotency_key_prevents_double_execution(db):
    user = new_user(db)
    first = services.place_market_order(db, user, "ETH", "buy", Decimal("1"), idempotency_key="k-1")
    second = services.place_market_order(db, user, "ETH", "buy", Decimal("1"), idempotency_key="k-1")
    assert first.id == second.id
    assert db.query(Transaction).filter_by(user_id=user.id).count() == 1


def test_limit_buy_fills_when_price_drops(db):
    user = new_user(db)
    services.place_limit_order(db, user, "BTC", "buy", Decimal("1"), Decimal("90"))
    assert services.process_pending_orders(db) == 0
    set_prices(BTC=Decimal("85"))
    assert services.process_pending_orders(db) == 1
    order = db.query(Order).filter_by(user_id=user.id).one()
    assert order.status == "filled"


def test_limit_reserve_blocks_overcommit(db):
    user = new_user(db)
    services.place_limit_order(db, user, "BTC", "buy", Decimal("60"), Decimal("100"))  # 6000 band
    with pytest.raises(ServiceError):
        services.place_limit_order(db, user, "ETH", "buy", Decimal("1"), Decimal("5000"))


def test_cancel_open_limit_order(db):
    user = new_user(db)
    order = services.place_limit_order(db, user, "SOL", "buy", Decimal("1"), Decimal("10"))
    services.cancel_order(db, user, order.id)
    assert db.get(Order, order.id).status == "cancelled"
    with pytest.raises(ServiceError):
        services.cancel_order(db, user, order.id)


def test_stale_prices_block_trading(db):
    from backend.app import prices
    user = new_user(db)
    prices._state["ok_at"] = 0  # eskirgan narx
    with pytest.raises(ServiceError):
        services.place_market_order(db, user, "BTC", "buy", Decimal("1"))
    set_prices(BTC=Decimal("100"))


def test_trading_disabled_by_admin(db):
    admin = new_user(db, 999)
    user = new_user(db, 5)
    services.set_setting(db, admin, services.TRADING_KEY, "false")
    with pytest.raises(ServiceError):
        services.place_market_order(db, user, "BTC", "buy", Decimal("1"))
