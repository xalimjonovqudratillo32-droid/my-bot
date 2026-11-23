"""Mini App uchun ochiq va foydalanuvchi API'lari (/api prefiksi bilan ulanadi)."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import prices, services
from ..database import get_db
from ..models import Notification, User
from ..security import create_token, get_current_user, tg_identity

router = APIRouter()
AMOUNT = Field(gt=0, le=10**9)


class OrderIn(BaseModel):
    symbol: str = Field(min_length=2, max_length=10)
    side: Literal["buy", "sell"]
    quantity: Decimal = AMOUNT


class LimitOrderIn(OrderIn):
    limit_price: Decimal = AMOUNT


class FavoritesOut(BaseModel):
    favorites: list[str]


@router.post("/auth/telegram")
def login(identity: dict = Depends(tg_identity), db: Session = Depends(get_db)):
    user, created = services.get_or_create_user(db, identity["user"], identity.get("start_param"))
    if user.is_banned:
        raise HTTPException(403, "Hisob bloklangan")
    services.touch_activity(db, user)
    return {"token": create_token(user), "created": created, "user": services.user_public(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.dashboard(db, user)


@router.get("/market/prices")
def market_prices(user: User = Depends(get_current_user)):
    snap = prices.get_snapshot()
    coins = []
    for sym, meta in prices.COINS.items():
        item = snap["items"].get(sym)
        coins.append({
            "symbol": sym, "name": meta["name"],
            "price": item["price"] if item else None,
            "change_24h": item["change_24h"] if item else None,
        })
    return {"coins": coins, "stale": snap["stale"], "source": snap["source"],
            "updated_at": snap["updated_at"], "error": snap["error"] if snap["stale"] else None}


@router.get("/market/{symbol}/chart")
def market_chart(symbol: str, user: User = Depends(get_current_user)):
    sym = services._normalize_symbol(symbol)
    return {"symbol": sym, "points": prices.get_chart(sym)}


@router.post("/trade/market")
def trade_market(body: OrderIn, idempotency_key: str | None = Header(default=None),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = services.place_market_order(db, user, body.symbol, body.side, body.quantity, idempotency_key)
    return services.serialize_order(order)


@router.post("/trade/limit")
def trade_limit(body: LimitOrderIn, idempotency_key: str | None = Header(default=None),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = services.place_limit_order(db, user, body.symbol, body.side, body.quantity,
                                       body.limit_price, idempotency_key)
    services.process_pending_orders(db)  # narx allaqachon limitga yetgan bo'lsa darhol bajariladi
    db.refresh(order)
    return services.serialize_order(order)


@router.get("/trade/orders")
def list_orders(status: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [services.serialize_order(o) for o in services.list_orders(db, user, status)]


@router.post("/trade/orders/{order_id}/cancel")
def cancel(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.serialize_order(services.cancel_order(db, user, order_id))


@router.get("/trade/positions")
def positions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.portfolio_summary(db, user)["positions"]


@router.get("/trade/transactions")
def transactions(limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [services.serialize_transaction(t) for t in services.list_transactions(db, user, limit)]


@router.get("/portfolio")
def portfolio(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.portfolio_summary(db, user)


@router.get("/missions")
def missions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.missions_overview(db, user)


@router.post("/missions/{user_mission_id}/claim")
def claim_mission(user_mission_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.claim_mission(db, user, user_mission_id)


@router.post("/daily-reward")
def daily_reward(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.claim_daily_reward(db, user)


@router.get("/leaderboard")
def leaderboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.leaderboard(db, me_id=user.id)


@router.get("/referrals")
def referrals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.referral_stats(db, user)


@router.post("/favorites/{symbol}", response_model=FavoritesOut)
def toggle_favorite(symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"favorites": services.toggle_favorite(db, user, symbol)}


@router.get("/notifications")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": n.id, "message": n.message, "is_read": n.is_read, "created_at": n.created_at.isoformat()}
            for n in services.list_notifications(db, user)]


@router.post("/notifications/read")
def mark_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter_by(user_id=user.id, is_read=False).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.get("/premium")
def premium(user: User = Depends(get_current_user)):
    return services.premium_status(user)


@router.get("/premium/analytics")
def premium_analytics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return services.premium_analytics(db, user)


@router.get("/settings/public")
def public_settings(db: Session = Depends(get_db)):
    return {"trading_enabled": services.get_setting(db, services.TRADING_KEY) != "false",
            "announcement_banner": services.get_setting(db, "announcement_banner") or ""}
