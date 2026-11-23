from decimal import Decimal

from backend.app import services
from backend.app.config import settings
from backend.app.models import Referral


def test_referral_credited_once_for_new_user(db):
    referrer, _ = services.get_or_create_user(db, {"id": 10, "first_name": "Ref"})
    code = referrer.referral_code
    friend, created = services.get_or_create_user(db, {"id": 11, "first_name": "Friend"}, f"ref_{code}")
    assert created and friend.referred_by_id == referrer.id
    # Qayta kirish (login) bonusni takror bermasligi kerak
    services.get_or_create_user(db, {"id": 11, "first_name": "Friend"}, f"ref_{code}")
    assert db.query(Referral).filter_by(referrer_id=referrer.id).count() == 1
    cash = Decimal(str(services.get_balance(db, referrer.id).cash))
    assert cash == Decimal(str(settings.START_BALANCE)) + Decimal(str(settings.REFERRAL_BONUS))


def test_self_referral_blocked(db):
    user, _ = services.get_or_create_user(db, {"id": 20, "first_name": "Me"})
    services.get_or_create_user(db, {"id": 20, "first_name": "Me"}, f"ref_{user.referral_code}")
    assert db.query(Referral).count() == 0


def test_invalid_code_ignored(db):
    user, created = services.get_or_create_user(db, {"id": 30, "first_name": "X"}, "ref_NOPE1234")
    assert created and user.referred_by_id is None
    assert db.query(Referral).count() == 0


def test_referral_stats_and_link(db):
    referrer, _ = services.get_or_create_user(db, {"id": 40, "first_name": "Boss"})
    for tg in (41, 42):
        services.get_or_create_user(db, {"id": tg, "first_name": f"F{tg}"}, f"ref_{referrer.referral_code}")
    stats = services.referral_stats(db, referrer)
    assert stats["invited_count"] == 2
    assert stats["link"].endswith(f"?start=ref_{referrer.referral_code}")
    assert stats["leaderboard"][0]["invited"] == 2
