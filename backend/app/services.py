"""Biznes mantiqi: savdo, portfel, gamification, referral, admin.

Barcha pul hisob-kitoblari Decimal bilan bajariladi. Har bir o'zgarish bitta
tranzaksiyada amalga oshiriladi, xatoda to'liq rollback bo'ladi.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import string
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import desc, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import prices
from .config import settings
from .database import utcnow
from .models import (
    Achievement, AdminLog, Balance, Mission, Notification, Order, Position, Referral, Setting,
    Subscription, Transaction, User, UserAchievement, UserMission,
)

EIGHT = Decimal("0.00000001")
LEVELS = [(1, 0, "Beginner"), (2, 500, "Trader"), (3, 1500, "Pro Trader"), (4, 4000, "Expert"), (5, 10000, "Master")]
XP_PER_TRADE = 10
PREMIUM_XP_MULTIPLIER = 1.5
PREMIUM_DAILY_MULTIPLIER = 2
PREMIUM_PERKS = [
    "XP ko'paytiruvchi: har bir savdo uchun 1.5× XP",
    "Kunlik sovg'a ikki baravar ko'p",
    "Kengaytirilgan statistika: P/L, win rate, eng yaxshi va eng yomon savdo",
    "Premium belgisi (⭐ PRO)",
]
VALID_SIDES = {"buy", "sell"}
TRADING_KEY = "trading_enabled"
SETTINGS_WHITELIST = {TRADING_KEY, "announcement_banner"}

DEFAULT_MISSIONS = [
    {"code": "daily_3_trades", "title": "3 ta savdo qiling", "period": "daily", "target": 3, "xp": 50},
    {"code": "weekly_15_trades", "title": "Hafta davomida 15 ta savdo", "period": "weekly", "target": 15, "xp": 300},
]
DEFAULT_ACHIEVEMENTS = [
    {"code": "first_trade", "title": "Birinchi savdo", "xp": 50},
    {"code": "first_referral", "title": "Birinchi taklif", "xp": 100},
    {"code": "level_3", "title": "Pro Trader darajasi", "xp": 200},
]


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(EIGHT, rounding=ROUND_HALF_UP)


def today_utc():
    return utcnow().date()


def _midnight_utc() -> datetime:
    return datetime.combine(today_utc(), datetime.min.time())


# ---------- Seed va yordamchilar ----------

def seed_defaults(db: Session) -> None:
    for data in DEFAULT_MISSIONS:
        if not db.query(Mission).filter_by(code=data["code"]).first():
            db.add(Mission(active=True, **data))
    for data in DEFAULT_ACHIEVEMENTS:
        if not db.query(Achievement).filter_by(code=data["code"]).first():
            db.add(Achievement(**data))
    if not db.get(Setting, TRADING_KEY):
        db.add(Setting(key=TRADING_KEY, value="true", updated_at=utcnow()))
    db.commit()


def level_info(xp: int) -> dict:
    current_idx = 0
    for idx, (_, floor, _) in enumerate(LEVELS):
        if xp >= floor:
            current_idx = idx
    level, floor, name = LEVELS[current_idx]
    next_xp = LEVELS[current_idx + 1][1] if current_idx + 1 < len(LEVELS) else None
    return {"level": level, "name": name, "xp": xp, "level_floor_xp": floor, "next_level_xp": next_xp}


def get_balance(db: Session, user_id: int, lock: bool = False) -> Balance:
    """lock=True: PostgreSQL'da qator qulfi (parallel savdo balansni manfiyga tushirmasligi uchun)."""
    q = db.query(Balance).filter(Balance.user_id == user_id)
    if lock:
        q = q.with_for_update()
    bal = q.first()
    if bal is None:
        bal = Balance(user_id=user_id, cash=money(settings.START_BALANCE), realized_pnl=Decimal("0"),
                      updated_at=utcnow())
        db.add(bal)
        db.flush()
    return bal


def _check_trading_enabled(db: Session) -> None:
    setting = db.get(Setting, TRADING_KEY)
    if setting and setting.value != "true":
        raise ServiceError("Savdo vaqtincha to'xtatilgan (admin)", 503)


# ---------- Foydalanuvchi va referral ----------

def _unique_referral_code(db: Session) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if not db.query(User.id).filter_by(referral_code=code).first():
            return code


def parse_start_param(param: str | None) -> str | None:
    if param and param.startswith("ref_"):
        code = param[4:].strip().upper()[:16]
        return code or None
    return None


def _register_referral(db: Session, new_user: User, code: str) -> bool:
    referrer = db.query(User).filter_by(referral_code=code).first()
    if referrer is None or referrer.is_banned:
        return False
    if referrer.id == new_user.id or referrer.telegram_id == new_user.telegram_id:
        return False  # o'zini o'zi taklif qilish bloklanadi
    new_user.referred_by_id = referrer.id
    db.add(Referral(referrer_id=referrer.id, referred_id=new_user.id, bonus_paid=True, created_at=utcnow()))
    get_balance(db, referrer.id).cash += money(settings.REFERRAL_BONUS)
    referrer.xp += settings.REFERRAL_XP
    award_achievement(db, referrer, "first_referral")
    update_level(db, referrer)
    return True


def get_or_create_user(db: Session, tg_user: dict, start_param: str | None = None) -> tuple[User, bool]:
    tg_id = int(tg_user["id"])
    username = tg_user.get("username")
    first_name = (tg_user.get("first_name") or "")[:128]
    is_admin = tg_id in settings.admin_ids

    user = db.query(User).filter_by(telegram_id=tg_id).first()
    if user:
        user.username, user.first_name, user.is_admin = username, first_name, is_admin
        db.commit()
        return user, False

    user = User(telegram_id=tg_id, username=username, first_name=first_name,
                referral_code=_unique_referral_code(db), is_admin=is_admin, created_at=utcnow())
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # bir vaqtda ikki so'rov
        db.rollback()
        return db.query(User).filter_by(telegram_id=tg_id).one(), False
    get_balance(db, user.id)
    code = parse_start_param(start_param)
    if code:
        _register_referral(db, user, code)
    db.commit()
    return user, True


def touch_activity(db: Session, user: User) -> None:
    today = today_utc()
    if user.last_active_date == today:
        return
    user.streak = user.streak + 1 if user.last_active_date == today - timedelta(days=1) else 1
    user.last_active_date = today
    db.commit()


def is_premium(user: User) -> bool:
    return bool(user.premium_until and user.premium_until > utcnow())


def user_public(user: User) -> dict:
    return {
        "id": user.id, "telegram_id": user.telegram_id, "first_name": user.first_name,
        "username": user.username, "referral_code": user.referral_code, "is_admin": user.is_admin,
        "streak": user.streak, "favorites": json.loads(user.favorites or "[]"),
        "is_premium": is_premium(user),
        "premium_until": user.premium_until.isoformat() if is_premium(user) else None,
        **level_info(user.xp),
    }


def _xp_for(user: User, base: int) -> int:
    return int(round(base * PREMIUM_XP_MULTIPLIER)) if is_premium(user) else base


def grant_premium(db: Session, admin: User, target: User, days: int) -> User:
    if not 1 <= days <= 3650:
        raise ServiceError("Kunlar soni 1 dan 3650 gacha bo'lishi kerak", 400)
    now = utcnow()
    base = target.premium_until if is_premium(target) else now  # mavjud obuna ustiga qo'shiladi
    target.premium_until = base + timedelta(days=days)
    db.add(Subscription(user_id=target.id, plan="premium", starts_at=now, ends_at=target.premium_until,
                        source="admin", granted_by_id=admin.id, created_at=now))
    db.add(Notification(user_id=target.id, message=f"⭐ Premium faollashtirildi: +{days} kun", created_at=now))
    audit(db, admin, "premium_grant", f"user:{target.id}", f"days={days}")
    db.commit()
    return target


def revoke_premium(db: Session, admin: User, target: User) -> User:
    target.premium_until = utcnow()
    audit(db, admin, "premium_revoke", f"user:{target.id}")
    db.commit()
    return target


def premium_status(user: User) -> dict:
    return {
        "is_premium": is_premium(user),
        "premium_until": user.premium_until.isoformat() if is_premium(user) else None,
        "perks": PREMIUM_PERKS,
        "xp_multiplier": PREMIUM_XP_MULTIPLIER,
        "daily_multiplier": PREMIUM_DAILY_MULTIPLIER,
    }


def premium_analytics(db: Session, user: User) -> dict:
    if not is_premium(user):
        raise ServiceError("Bu bo'lim faqat Premium foydalanuvchilar uchun", 403)
    txs = db.query(Transaction).filter_by(user_id=user.id).order_by(Transaction.id).all()
    sells = [t for t in txs if t.side == "sell"]
    realized = sum((Decimal(str(t.realized_pnl)) for t in sells), Decimal("0"))
    wins = sum(1 for t in sells if Decimal(str(t.realized_pnl)) > 0)
    per_symbol: dict[str, dict] = {}
    for t in txs:
        row = per_symbol.setdefault(t.symbol, {"symbol": t.symbol, "trades": 0, "volume": Decimal("0"),
                                               "realized_pnl": Decimal("0")})
        row["trades"] += 1
        row["volume"] += Decimal(str(t.total))
        row["realized_pnl"] += Decimal(str(t.realized_pnl))
    best = max(sells, key=lambda t: Decimal(str(t.realized_pnl)), default=None)
    worst = min(sells, key=lambda t: Decimal(str(t.realized_pnl)), default=None)
    volume = sum((Decimal(str(t.total)) for t in txs), Decimal("0"))
    return {
        "trades": len(txs),
        "sells": len(sells),
        "realized_pnl": money(realized),
        "win_rate": round(wins / len(sells) * 100, 1) if sells else None,
        "avg_trade_size": money(volume / len(txs)) if txs else Decimal("0"),
        "total_volume": money(volume),
        "best_trade": serialize_transaction(best) if best else None,
        "worst_trade": serialize_transaction(worst) if worst else None,
        "by_symbol": [{**r, "volume": money(r["volume"]), "realized_pnl": money(r["realized_pnl"])}
                      for r in sorted(per_symbol.values(), key=lambda r: r["volume"], reverse=True)],
    }


# ---------- Gamification ----------

def _period_key(period: str, day) -> str:
    if period == "daily":
        return day.isoformat()
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def update_level(db: Session, user: User) -> None:
    info = level_info(user.xp)
    if info["level"] > user.level:
        db.add(Notification(user_id=user.id, message=f"🎉 Yangi daraja: {info['name']} (Level {info['level']})",
                            created_at=utcnow()))
    user.level = info["level"]
    if user.level >= 3:
        award_achievement(db, user, "level_3")


def award_achievement(db: Session, user: User, code: str) -> bool:
    ach = db.query(Achievement).filter_by(code=code).first()
    if ach is None or db.query(UserAchievement).filter_by(user_id=user.id, achievement_id=ach.id).first():
        return False
    db.add(UserAchievement(user_id=user.id, achievement_id=ach.id, created_at=utcnow()))
    user.xp += ach.xp
    db.add(Notification(user_id=user.id, message=f"🏆 Yutuq: {ach.title} (+{ach.xp} XP)", created_at=utcnow()))
    return True


def _bump_missions(db: Session, user: User) -> None:
    today = today_utc()
    for mission in db.query(Mission).filter_by(active=True).all():
        key = _period_key(mission.period, today)
        um = db.query(UserMission).filter_by(user_id=user.id, mission_id=mission.id, period_key=key).first()
        if um is None:
            um = UserMission(user_id=user.id, mission_id=mission.id, period_key=key, progress=0,
                             completed=False, claimed=False)
            db.add(um)
        if not um.completed:
            um.progress += 1
            if um.progress >= mission.target:
                um.completed = True


def missions_overview(db: Session, user: User) -> dict:
    today = today_utc()
    missions = []
    for m in db.query(Mission).filter_by(active=True).order_by(Mission.id).all():
        um = db.query(UserMission).filter_by(
            user_id=user.id, mission_id=m.id, period_key=_period_key(m.period, today)).first()
        missions.append({
            "id": um.id if um else None, "code": m.code, "title": m.title, "period": m.period,
            "target": m.target, "xp": m.xp, "progress": um.progress if um else 0,
            "completed": bool(um and um.completed), "claimed": bool(um and um.claimed),
        })
    achievements = []
    unlocked = {ua.achievement_id for ua in db.query(UserAchievement).filter_by(user_id=user.id)}
    for a in db.query(Achievement).order_by(Achievement.id).all():
        achievements.append({"code": a.code, "title": a.title, "xp": a.xp, "unlocked": a.id in unlocked})
    return {"missions": missions, "achievements": achievements}


def claim_mission(db: Session, user: User, user_mission_id: int) -> dict:
    um = db.get(UserMission, user_mission_id)
    if um is None or um.user_id != user.id:
        raise ServiceError("Missiya topilmadi", 404)
    if not um.completed or um.claimed:
        raise ServiceError("Bu missiyani hozir olib bo'lmaydi", 409)
    mission = db.get(Mission, um.mission_id)
    um.claimed = True
    user.xp += mission.xp
    update_level(db, user)
    db.commit()
    return {"xp_awarded": mission.xp, "xp": user.xp}


def claim_daily_reward(db: Session, user: User) -> dict:
    today = today_utc()
    if user.daily_reward_date == today:
        raise ServiceError("Bugungi sovg'a allaqachon olingan. Ertaga qayting", 409)
    mult = PREMIUM_DAILY_MULTIPLIER if is_premium(user) else 1
    bonus = money(settings.DAILY_REWARD * mult)
    xp = settings.DAILY_REWARD_XP * mult
    get_balance(db, user.id).cash += bonus
    user.daily_reward_date = today
    user.xp += xp
    update_level(db, user)
    db.commit()
    return {"balance_bonus": bonus, "xp_awarded": xp}


def toggle_favorite(db: Session, user: User, symbol: str) -> list[str]:
    symbol = _normalize_symbol(symbol)
    favs = json.loads(user.favorites or "[]")
    favs = [f for f in favs if f != symbol] if symbol in favs else favs + [symbol]
    user.favorites = json.dumps(favs)
    db.commit()
    return favs


# ---------- Savdo ----------

def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if sym not in prices.COINS:
        raise ServiceError("Noma'lum coin", 400)
    return sym


def _validate(side: str, quantity: Decimal) -> None:
    if side not in VALID_SIDES:
        raise ServiceError("side faqat buy yoki sell bo'lishi mumkin", 400)
    if quantity <= 0:
        raise ServiceError("Miqdor musbat bo'lishi kerak", 400)


def _live_price(symbol: str) -> Decimal:
    snap = prices.get_snapshot()
    item = snap["items"].get(symbol)
    if item is None or snap["stale"]:
        raise ServiceError("Narx hozir yangilanmagan. Keyinroq urinib ko'ring", 503)
    return item["price"]


def _reserved_sell_qty(db: Session, user_id: int, symbol: str) -> Decimal:
    value = db.query(func.coalesce(func.sum(Order.quantity), 0)).filter(
        Order.user_id == user_id, Order.symbol == symbol, Order.side == "sell",
        Order.order_type == "limit", Order.status == "open").scalar()
    return Decimal(str(value))


def _reserved_buy_cost(db: Session, user_id: int) -> Decimal:
    rows = db.query(Order).filter(Order.user_id == user_id, Order.side == "buy",
                                  Order.order_type == "limit", Order.status == "open").all()
    return sum((o.quantity * o.limit_price for o in rows), Decimal("0"))


def _held_qty(db: Session, user_id: int, symbol: str) -> Decimal:
    pos = db.query(Position).filter_by(user_id=user_id, symbol=symbol).first()
    return Decimal(str(pos.quantity)) if pos else Decimal("0")


def _find_idempotent(db: Session, user: User, key: str | None) -> Order | None:
    if not key:
        return None
    return db.query(Order).filter_by(user_id=user.id, idempotency_key=key).first()


def _fill(db: Session, user: User, order: Order, price: Decimal) -> None:
    """Buyurtmani bajaradi: balans, pozitsiya va tranzaksiyani yangilaydi."""
    if order.status != "open":  # ikki marta bajarilishdan himoya
        raise ServiceError("Buyurtma allaqachon qayta ishlangan", 409)
    bal = get_balance(db, user.id, lock=True)  # tartib: avval balans, keyin pozitsiya
    pos = db.query(Position).filter_by(user_id=user.id, symbol=order.symbol).with_for_update().first()
    total = money(order.quantity * price)
    realized = Decimal("0")

    if order.side == "buy":
        if bal.cash < total:
            raise ServiceError("Balans yetarli emas", 400)
        bal.cash -= total
        if pos is None:
            pos = Position(user_id=user.id, symbol=order.symbol, quantity=Decimal("0"),
                           avg_entry=Decimal("0"), realized_pnl=Decimal("0"))
            db.add(pos)
        new_qty = Decimal(str(pos.quantity)) + order.quantity
        pos.avg_entry = money((Decimal(str(pos.quantity)) * Decimal(str(pos.avg_entry))
                               + order.quantity * price) / new_qty)
        pos.quantity = money(new_qty)
    else:
        if pos is None or Decimal(str(pos.quantity)) < order.quantity:
            raise ServiceError("Yetarli miqdor yo'q", 400)
        realized = money((price - Decimal(str(pos.avg_entry))) * order.quantity)
        pos.quantity = money(Decimal(str(pos.quantity)) - order.quantity)
        pos.realized_pnl = money(Decimal(str(pos.realized_pnl)) + realized)
        bal.realized_pnl = money(Decimal(str(bal.realized_pnl)) + realized)
        bal.cash += total
        if Decimal(str(pos.quantity)) == 0:
            pos.avg_entry = Decimal("0")

    bal.updated_at = utcnow()
    order.status = "filled"
    order.filled_price = price
    order.filled_at = utcnow()
    db.flush()
    db.add(Transaction(user_id=user.id, order_id=order.id, symbol=order.symbol, side=order.side,
                       quantity=order.quantity, price=price, total=total, realized_pnl=realized,
                       balance_after=bal.cash, created_at=utcnow()))
    user.xp += _xp_for(user, XP_PER_TRADE)
    _bump_missions(db, user)
    award_achievement(db, user, "first_trade")
    update_level(db, user)


def place_market_order(db: Session, user: User, symbol: str, side: str, quantity: Decimal,
                       idempotency_key: str | None = None) -> Order:
    symbol = _normalize_symbol(symbol)
    quantity = money(quantity)
    _validate(side, quantity)
    _check_trading_enabled(db)
    existing = _find_idempotent(db, user, idempotency_key)
    if existing:
        return existing
    price = _live_price(symbol)
    if side == "sell" and _held_qty(db, user.id, symbol) - _reserved_sell_qty(db, user.id, symbol) < quantity:
        raise ServiceError("Yetarli miqdor yo'q", 400)

    order = Order(user_id=user.id, symbol=symbol, side=side, order_type="market", quantity=quantity,
                  status="open", idempotency_key=idempotency_key, created_at=utcnow())
    db.add(order)
    try:
        db.flush()
        _fill(db, user, order, price)
        db.commit()
    except IntegrityError:
        db.rollback()
        return _find_idempotent(db, user, idempotency_key)
    except ServiceError:
        db.rollback()
        raise
    return order


def place_limit_order(db: Session, user: User, symbol: str, side: str, quantity: Decimal,
                      limit_price: Decimal, idempotency_key: str | None = None) -> Order:
    symbol = _normalize_symbol(symbol)
    quantity, limit_price = money(quantity), money(limit_price)
    _validate(side, quantity)
    if limit_price <= 0:
        raise ServiceError("Limit narx musbat bo'lishi kerak", 400)
    _check_trading_enabled(db)
    existing = _find_idempotent(db, user, idempotency_key)
    if existing:
        return existing

    if side == "buy":
        cost = money(quantity * limit_price)
        free_cash = get_balance(db, user.id, lock=True).cash - _reserved_buy_cost(db, user.id)
        if free_cash < cost:
            raise ServiceError("Balans yetarli emas (band qilingan mablag'ni hisobga olgan holda)", 400)
    else:
        available = _held_qty(db, user.id, symbol) - _reserved_sell_qty(db, user.id, symbol)
        if available < quantity:
            raise ServiceError("Yetarli miqdor yo'q", 400)

    order = Order(user_id=user.id, symbol=symbol, side=side, order_type="limit", quantity=quantity,
                  limit_price=limit_price, status="open", idempotency_key=idempotency_key, created_at=utcnow())
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _find_idempotent(db, user, idempotency_key)
    return order


def process_pending_orders(db: Session) -> int:
    """Ochiq limit buyurtmalarni joriy narx bo'yicha bajaradi. Kamida bir marta chaqirilsa kifoya."""
    snap = prices.get_snapshot()
    if snap["stale"]:
        return 0
    filled = 0
    candidates = (db.query(Order).filter(Order.status == "open", Order.order_type == "limit")
                  .order_by(Order.id).with_for_update(skip_locked=True).all())
    for order in candidates:
        item = snap["items"].get(order.symbol)
        if item is None:
            continue
        price = item["price"]
        hit = (order.side == "buy" and price <= order.limit_price) or \
              (order.side == "sell" and price >= order.limit_price)
        if not hit:
            continue
        order_id, user = order.id, db.get(User, order.user_id)
        try:
            if user is None or user.is_banned:
                raise ServiceError("Foydalanuvchi faol emas", 403)
            _fill(db, user, order, price)
            db.commit()
            filled += 1
        except ServiceError as exc:
            db.rollback()
            failed = db.get(Order, order_id)
            failed.status, failed.cancel_reason = "cancelled", exc.message[:255]
            db.commit()
    return filled


def cancel_order(db: Session, user: User, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise ServiceError("Buyurtma topilmadi", 404)
    if order.status != "open":
        raise ServiceError("Faqat ochiq buyurtmani bekor qilish mumkin", 409)
    order.status, order.cancel_reason = "cancelled", "foydalanuvchi bekor qildi"
    db.commit()
    return order


def list_orders(db: Session, user: User, status: str | None = None, limit: int = 100) -> list[Order]:
    q = db.query(Order).filter(Order.user_id == user.id)
    if status:
        q = q.filter(Order.status == status)
    return q.order_by(desc(Order.id)).limit(min(limit, 500)).all()


def list_transactions(db: Session, user: User, limit: int = 50) -> list[Transaction]:
    return (db.query(Transaction).filter(Transaction.user_id == user.id)
            .order_by(desc(Transaction.id)).limit(min(limit, 500)).all())


# ---------- Portfel va dashboard ----------

def _position_value(pos: Position, snap: dict) -> tuple[Decimal | None, Decimal | None]:
    item = snap["items"].get(pos.symbol)
    if item is None or snap["stale"]:
        return None, None
    price = item["price"]
    qty = Decimal(str(pos.quantity))
    avg = Decimal(str(pos.avg_entry))
    return money(qty * price), money((price - avg) * qty)


def portfolio_summary(db: Session, user: User) -> dict:
    bal = get_balance(db, user.id)
    snap = prices.get_snapshot()
    rows, market_value, unrealized_total = [], Decimal("0"), Decimal("0")
    positions = (db.query(Position).filter(Position.user_id == user.id, Position.quantity > 0)
                 .order_by(Position.symbol).all())
    for pos in positions:
        mv, unreal = _position_value(pos, snap)
        qty, avg = Decimal(str(pos.quantity)), Decimal(str(pos.avg_entry))
        if mv is None:  # narx yo'q: xarajat bo'yicha ko'rsatamiz, P/L hisoblanmaydi
            mv = money(qty * avg)
            unreal, pct = None, None
        else:
            pct = float(unreal / (qty * avg) * 100) if avg else 0.0
            unrealized_total += unreal
        market_value += mv
        rows.append({
            "symbol": pos.symbol, "quantity": qty, "avg_entry": avg,
            "price": snap["items"][pos.symbol]["price"] if (mv is not None and pos.symbol in snap["items"]) else None,
            "market_value": mv, "unrealized_pnl": unreal, "unrealized_pct": pct,
            "realized_pnl": Decimal(str(pos.realized_pnl)),
        })
    cash = Decimal(str(bal.cash))
    equity = money(cash + market_value)
    today_realized = db.query(func.coalesce(func.sum(Transaction.realized_pnl), 0)).filter(
        Transaction.user_id == user.id, Transaction.created_at >= _midnight_utc()).scalar()
    return {
        "cash": cash,
        "market_value": money(market_value),
        "equity": equity,
        "total_pnl": money(equity - Decimal(str(settings.START_BALANCE))),
        "realized_pnl": Decimal(str(bal.realized_pnl)),
        "unrealized_pnl": money(unrealized_total),
        "today_realized_pnl": Decimal(str(today_realized)),
        "prices_stale": snap["stale"],
        "positions": rows,
    }


def dashboard(db: Session, user: User) -> dict:
    recent = list_transactions(db, user, limit=5)
    return {
        "user": user_public(user),
        "portfolio": portfolio_summary(db, user),
        "daily_reward_available": user.daily_reward_date != today_utc(),
        "recent_transactions": [serialize_transaction(t) for t in recent],
    }


def serialize_order(o: Order) -> dict:
    return {
        "id": o.id, "symbol": o.symbol, "side": o.side, "type": o.order_type,
        "quantity": Decimal(str(o.quantity)),
        "limit_price": Decimal(str(o.limit_price)) if o.limit_price is not None else None,
        "filled_price": Decimal(str(o.filled_price)) if o.filled_price is not None else None,
        "status": o.status, "cancel_reason": o.cancel_reason,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


def serialize_transaction(t: Transaction) -> dict:
    return {
        "id": t.id, "symbol": t.symbol, "side": t.side, "quantity": Decimal(str(t.quantity)),
        "price": Decimal(str(t.price)), "total": Decimal(str(t.total)),
        "realized_pnl": Decimal(str(t.realized_pnl)), "balance_after": Decimal(str(t.balance_after)),
        "created_at": t.created_at.isoformat(),
    }


def equity_for_user(db: Session, user_id: int, snap: dict) -> Decimal:
    bal = db.get(Balance, user_id)
    cash = Decimal(str(bal.cash)) if bal else Decimal("0")
    total = cash
    for pos in db.query(Position).filter(Position.user_id == user_id, Position.quantity > 0).all():
        mv, _ = _position_value(pos, snap)
        if mv is not None:
            total += mv
    return money(total)


def leaderboard(db: Session, limit: int = 50, me_id: int | None = None) -> list[dict]:
    snap = prices.get_snapshot()
    users = db.query(User).filter(User.is_banned.is_(False)).all()
    scored = [(equity_for_user(db, u.id, snap), u) for u in users]
    scored.sort(key=lambda row: row[0], reverse=True)
    start = Decimal(str(settings.START_BALANCE))
    return [{"rank": i + 1, "name": u.first_name or u.username or f"user{u.id}", "level": u.level,
             "equity": eq, "total_pnl": money(eq - start), "xp": u.xp, "is_me": u.id == me_id}
            for i, (eq, u) in enumerate(scored[:limit])]


def _mask_name(name: str) -> str:
    name = name or "Foydalanuvchi"
    return name[0] + "***" if len(name) > 1 else name


def referral_stats(db: Session, user: User) -> dict:
    refs = db.query(Referral).filter_by(referrer_id=user.id).order_by(desc(Referral.created_at)).all()
    referred = {u.id: u for u in db.query(User).filter(User.id.in_([r.referred_id for r in refs])).all()} \
        if refs else {}
    history = [{"name": _mask_name(referred[r.referred_id].first_name) if r.referred_id in referred else "",
                "joined_at": r.created_at.isoformat(), "bonus": settings.REFERRAL_BONUS} for r in refs[:50]]
    top_rows = (db.query(Referral.referrer_id, func.count(Referral.id).label("cnt"))
                .group_by(Referral.referrer_id).order_by(desc("cnt")).limit(10).all())
    top = []
    for rank, (ref_id, cnt) in enumerate(top_rows, start=1):
        u = db.get(User, ref_id)
        top.append({"rank": rank, "name": _mask_name(u.first_name) if u else "", "invited": cnt})
    return {
        "link": f"https://t.me/{settings.BOT_USERNAME}?start=ref_{user.referral_code}",
        "code": user.referral_code,
        "invited_count": len(refs),
        "total_bonus": settings.REFERRAL_BONUS * len(refs),
        "bonus_per_referral": settings.REFERRAL_BONUS,
        "history": history,
        "leaderboard": top,
    }


def list_notifications(db: Session, user: User, limit: int = 30) -> list[Notification]:
    return (db.query(Notification).filter_by(user_id=user.id)
            .order_by(desc(Notification.id)).limit(limit).all())


# ---------- Admin ----------

def audit(db: Session, admin: User, action: str, target: str = "", details: str = "") -> None:
    db.add(AdminLog(admin_user_id=admin.id, action=action, target=target, details=details, created_at=utcnow()))


def admin_stats(db: Session) -> dict:
    day_ago = _midnight_utc() - timedelta(days=1)
    total_users = db.query(func.count(User.id)).scalar()
    return {
        "total_users": total_users,
        "active_users_24h": db.query(func.count(User.id)).filter(User.last_active_date >= day_ago.date()).scalar(),
        "new_registrations_today": db.query(func.count(User.id)).filter(User.created_at >= _midnight_utc()).scalar(),
        "banned_users": db.query(func.count(User.id)).filter(User.is_banned.is_(True)).scalar(),
        "trading_volume_total": Decimal(str(db.query(func.coalesce(func.sum(Transaction.total), 0)).scalar())),
        "trading_volume_today": Decimal(str(db.query(func.coalesce(func.sum(Transaction.total), 0)).filter(
            Transaction.created_at >= _midnight_utc()).scalar())),
        "trades_total": db.query(func.count(Transaction.id)).scalar(),
        "open_orders": db.query(func.count(Order.id)).filter(Order.status == "open").scalar(),
        "referrals_total": db.query(func.count(Referral.id)).scalar(),
    }


def search_users(db: Session, query: str, limit: int = 50) -> list[User]:
    q = db.query(User)
    query = (query or "").strip().lower()
    if query:
        term = f"%{query}%"
        conds = [func.lower(User.first_name).like(term), func.lower(User.username).like(term)]
        if query.isdigit():
            conds += [User.telegram_id == int(query), User.id == int(query)]
        q = q.filter(or_(*conds))
    return q.order_by(desc(User.id)).limit(limit).all()


def admin_user_detail(db: Session, user: User) -> dict:
    summary = portfolio_summary(db, user)
    return {
        "user": user_public(user), "telegram_id": user.telegram_id, "username": user.username,
        "is_banned": user.is_banned, "created_at": user.created_at.isoformat(),
        "referred_by_id": user.referred_by_id, "portfolio": summary,
        "recent_transactions": [serialize_transaction(t) for t in list_transactions(db, user, 10)],
    }


def set_ban(db: Session, admin: User, target: User, banned: bool) -> None:
    if target.id == admin.id:
        raise ServiceError("O'zingizni bloklay olmaysiz", 400)
    target.is_banned = banned
    audit(db, admin, "ban" if banned else "unban", f"user:{target.id}")
    db.commit()


def adjust_balance(db: Session, admin: User, target: User, amount: Decimal, reason: str) -> Decimal:
    bal = get_balance(db, target.id, lock=True)
    new_cash = money(Decimal(str(bal.cash)) + money(amount))
    if new_cash < 0:
        raise ServiceError("Balans manfiy bo'lishi mumkin emas", 400)
    bal.cash = new_cash
    audit(db, admin, "balance_adjust", f"user:{target.id}", f"amount={amount}; reason={reason}")
    db.commit()
    return new_cash


def adjust_xp(db: Session, admin: User, target: User, amount: int, reason: str) -> int:
    if target.xp + amount < 0:
        raise ServiceError("XP manfiy bo'lishi mumkin emas", 400)
    target.xp += amount
    update_level(db, target)
    audit(db, admin, "xp_adjust", f"user:{target.id}", f"amount={amount}; reason={reason}")
    db.commit()
    return target.xp


def list_admin_logs(db: Session, limit: int = 100) -> list[AdminLog]:
    return db.query(AdminLog).order_by(desc(AdminLog.id)).limit(limit).all()


def api_status() -> dict:
    snap = prices.get_snapshot()
    return {
        "price_source": snap["source"], "prices_stale": snap["stale"], "last_error": snap["error"],
        "last_success_at": datetime.fromtimestamp(snap["updated_at"]).isoformat() if snap["updated_at"] else None,
        "coins_available": sorted(snap["items"].keys()),
        "bot_token_configured": bool(settings.BOT_TOKEN),
    }


def get_setting(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


def set_setting(db: Session, admin: User, key: str, value: str) -> None:
    if key not in SETTINGS_WHITELIST:
        raise ServiceError("Bu sozlamani o'zgartirish mumkin emas", 400)
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        db.add(row)
    row.value, row.updated_at = value, utcnow()
    audit(db, admin, "setting_update", key, value)
    db.commit()


def list_settings(db: Session) -> dict:
    return {k: get_setting(db, k) for k in sorted(SETTINGS_WHITELIST)}


def create_announcement_targets(db: Session) -> list[int]:
    return [tg for (tg,) in db.query(User.telegram_id).filter(User.is_banned.is_(False)).all()]


def create_admin_otp(db: Session, telegram_id: int) -> str:
    """Bot orqali admin uchun bir martalik 6 xonali kod (5 daqiqa)."""
    code = str(secrets.randbelow(900000) + 100000)
    expires = utcnow() + timedelta(minutes=5)
    digest = hashlib.sha256(code.encode()).hexdigest()
    row = db.get(Setting, f"otp:{telegram_id}")
    value = f"{digest}|{expires.isoformat()}"
    if row is None:
        db.add(Setting(key=f"otp:{telegram_id}", value=value, updated_at=utcnow()))
    else:
        row.value, row.updated_at = value, utcnow()
    db.commit()
    return code


def verify_admin_otp(db: Session, telegram_id: int, code: str) -> User | None:
    row = db.get(Setting, f"otp:{telegram_id}")
    if row is None:
        return None
    digest, expires = row.value.split("|", 1)
    db.delete(row)  # har bir kod faqat bir marta sinab ko'riladi
    db.commit()
    if utcnow() > datetime.fromisoformat(expires):
        return None
    if not hmac.compare_digest(digest, hashlib.sha256(code.strip().encode()).hexdigest()):
        return None
    user = db.query(User).filter_by(telegram_id=telegram_id, is_admin=True, is_banned=False).first()
    return user
