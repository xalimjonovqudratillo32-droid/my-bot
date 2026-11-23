"""Administrator paneli API. Barcha endpointlar (OTP login'dan tashqari) admin talab qiladi."""
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..models import User
from ..notifications import send_telegram_message
from ..security import create_token, require_admin

router = APIRouter()


class OtpLoginIn(BaseModel):
    telegram_id: int
    code: str = Field(min_length=6, max_length=6)


class BanIn(BaseModel):
    banned: bool


class AmountIn(BaseModel):
    amount: Decimal
    reason: str = Field(default="", max_length=200)


class XpIn(BaseModel):
    amount: int
    reason: str = Field(default="", max_length=200)


class AnnouncementIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class PremiumIn(BaseModel):
    days: int = Field(ge=1, le=3650)


class SettingIn(BaseModel):
    key: str
    value: str = Field(max_length=500)


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    return user


@router.post("/otp-login")
def otp_login(body: OtpLoginIn, db: Session = Depends(get_db)):
    admin = services.verify_admin_otp(db, body.telegram_id, body.code)
    if admin is None:
        raise HTTPException(401, "Kod noto'g'ri yoki muddati tugagan")
    return {"token": create_token(admin), "user": services.user_public(admin)}


@router.get("/stats")
def stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return services.admin_stats(db)


@router.get("/users")
def users(q: str = "", admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": u.id, "telegram_id": u.telegram_id, "name": u.first_name, "username": u.username,
             "level": u.level, "xp": u.xp, "is_banned": u.is_banned, "is_admin": u.is_admin}
            for u in services.search_users(db, q)]


@router.get("/users/{user_id}")
def user_detail(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return services.admin_user_detail(db, _get_user(db, user_id))


@router.post("/users/{user_id}/ban")
def ban(user_id: int, body: BanIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    services.set_ban(db, admin, _get_user(db, user_id), body.banned)
    return {"ok": True, "is_banned": body.banned}


@router.post("/users/{user_id}/balance")
def balance(user_id: int, body: AmountIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    cash = services.adjust_balance(db, admin, _get_user(db, user_id), body.amount, body.reason)
    return {"cash": cash}


@router.post("/users/{user_id}/xp")
def xp(user_id: int, body: XpIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {"xp": services.adjust_xp(db, admin, _get_user(db, user_id), body.amount, body.reason)}


@router.post("/announcements")
def announce(body: AnnouncementIn, background: BackgroundTasks,
             admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    targets = services.create_announcement_targets(db)
    services.audit(db, admin, "announcement", f"recipients:{len(targets)}", body.text[:200])
    db.commit()

    def _broadcast():
        for chat_id in targets:
            send_telegram_message(chat_id, body.text)

    background.add_task(_broadcast)
    return {"queued": len(targets), "bot_token_configured": bool(services.settings.BOT_TOKEN)}


@router.get("/referrals")
def referral_overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..models import Referral
    return {"total": db.query(Referral).count(),
            "top": services.referral_stats(db, admin)["leaderboard"]}


@router.post("/users/{user_id}/premium/grant")
def premium_grant(user_id: int, body: PremiumIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = services.grant_premium(db, admin, _get_user(db, user_id), body.days)
    return services.user_public(target)


@router.post("/users/{user_id}/premium/revoke")
def premium_revoke(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = services.revoke_premium(db, admin, _get_user(db, user_id))
    return services.user_public(target)


@router.get("/logs")
def logs(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": l.id, "admin_user_id": l.admin_user_id, "action": l.action, "target": l.target,
             "details": l.details, "created_at": l.created_at.isoformat()}
            for l in services.list_admin_logs(db)]


@router.get("/api-status")
def api_status(admin: User = Depends(require_admin)):
    return services.api_status()


@router.get("/settings")
def get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return services.list_settings(db)


@router.put("/settings")
def put_settings(body: SettingIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    services.set_setting(db, admin, body.key, body.value)
    return services.list_settings(db)
