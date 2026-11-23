
"""TradeX Telegram boti (aiogram 3). Ishga tushirish: python -m bot.bot"""

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from backend.app import services
from backend.app.config import settings
from backend.app.models import User
from backend.app.database import Base, SessionLocal, engine


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tradex.bot")

router = Dispatcher()


def _with_db(fn, *args):
    """Sinxron servis funksiyasini alohida thread'da, o'z sessiyasi bilan ishlatadi."""
    with SessionLocal() as db:
        return fn(db, *args)


def _open_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📈 TradeX'ni ochish",
                    web_app=WebAppInfo(url=settings.WEBAPP_URL),
                )
            ]
        ]
    )


async def _ensure_user(message: Message, start_param: str | None = None):
    tg = message.from_user

    tg_user = {
        "id": tg.id,
        "username": tg.username,
        "first_name": tg.first_name,
    }

    return await asyncio.to_thread(
        _with_db,
        lambda db: services.get_or_create_user(db, tg_user, start_param),
    )


@router.message(CommandStart(deep_link=True))
async def start_with_ref(message: Message, command) -> None:
    await _start(message, command.args)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await _start(message, None)


async def _start(message: Message, start_param: str | None) -> None:
    user, created = await _ensure_user(message, start_param)
    balance = settings.START_BALANCE

    if created:
        text = (
            f"👋 Xush kelibsiz, <b>{message.from_user.first_name}</b>!\n\n"
            f"Sizga <b>${balance:,.0f}</b> virtual balans berildi.\n"
            "Bu o'yin faqat virtual — haqiqiy pul yoki kripto ishtirok etmaydi.\n\n"
            "Tugmani bosib savdoni boshlang 👇"
        )
    else:
        text = (
            "Qaytganingizdan xursandmiz! "
            "Savdoni davom ettirish uchun tugmani bosing 👇"
        )

    await message.answer(text, reply_markup=_open_button())


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(
        "<b>TradeX buyruqlari</b>\n\n"
        "/start — ro'yxatdan o'tish va Mini App\n"
        "/balance — balans va umumiy P/L\n"
        "/portfolio — ochiq pozitsiyalar\n"
        "/leaderboard — reyting jadvali\n"
        "/invite — do'st taklif qilish havolasi\n"
        "/daily — kunlik sovg'a\n"
        "/help — yordam\n"
        "/admin — administrator uchun kirish kodi (faqat adminlar)"
    )


@router.message(Command("balance"))
async def balance_cmd(message: Message) -> None:
    user, _ = await _ensure_user(message)

    summary = await asyncio.to_thread(
        _with_db,
        lambda db: services.portfolio_summary(db, user),
    )

    await message.answer(
        f"💰 <b>Naqd:</b> ${float(summary['cash']):,.2f}\n"
        f"📊 <b>Equity:</b> ${float(summary['equity']):,.2f}\n"
        f"📈 <b>Umumiy P/L:</b> ${float(summary['total_pnl']):,.2f}\n"
        f"✅ <b>Realized P/L:</b> ${float(summary['realized_pnl']):,.2f}",
        reply_markup=_open_button(),
    )


@router.message(Command("portfolio"))
async def portfolio_cmd(message: Message) -> None:
    user, _ = await _ensure_user(message)

    summary = await asyncio.to_thread(
        _with_db,
        lambda db: services.portfolio_summary(db, user),
    )

    if not summary["positions"]:
        await message.answer(
            "Hozircha ochiq pozitsiya yo'q. Birinchi savdoni boshlang!",
            reply_markup=_open_button(),
        )
        return

    lines = [
        f"• <b>{p['symbol']}</b>: {float(p['quantity']):g} dona, "
        f"o'rtacha ${float(p['avg_entry']):,.2f}"
        for p in summary["positions"]
    ]

    await message.answer(
        "💼 <b>Portfel</b>\n" + "\n".join(lines),
        reply_markup=_open_button(),
    )


@router.message(Command("leaderboard"))
async def leaderboard_cmd(message: Message) -> None:
    rows = await asyncio.to_thread(
        _with_db,
        lambda db: services.leaderboard(db, limit=10),
    )

    if not rows:
        await message.answer("Reyting hali bo'sh.")
        return

    lines = [
        f"{r['rank']}. {r['name']} — "
        f"${float(r['equity']):,.0f} (Lv.{r['level']})"
        for r in rows
    ]

    await message.answer(
        "🏆 <b>Top 10</b>\n" + "\n".join(lines),
        reply_markup=_open_button(),
    )


@router.message(Command("invite"))
async def invite_cmd(message: Message) -> None:
    user, _ = await _ensure_user(message)

    stats = await asyncio.to_thread(
        _with_db,
        lambda db: services.referral_stats(db, user),
    )

    await message.answer(
        "👥 <b>Do'stlaringizni taklif qiling</b>\n\n"
        f"Sizning havolangiz:\n<code>{stats['link']}</code>\n\n"
        f"Taklif qilganlar: <b>{stats['invited_count']}</b>\n"
        f"Har bir yangi do'st uchun: "
        f"${settings.REFERRAL_BONUS:,.0f} bonus va "
        f"{settings.REFERRAL_XP} XP."
    )


@router.message(Command("daily"))
async def daily_cmd(message: Message) -> None:
    user, _ = await _ensure_user(message)

    try:
        result = await asyncio.to_thread(
            _with_db,
            lambda db: services.claim_daily_reward(
                db,
                db.get(User, user.id),
            ),
        )

        await message.answer(
            f"🎁 Kunlik sovg'a: "
            f"+${result['balance_bonus']:,.0f} "
            f"va +{result['xp_awarded']} XP!"
        )

    except services.ServiceError as exc:
        await message.answer(f"ℹ️ {exc.message}")


@router.message(Command("admin"))
async def admin_cmd(message: Message) -> None:
    tg_id = message.from_user.id

    if tg_id not in settings.admin_ids:
        await message.answer(
            "Bu buyruq faqat administratorlar uchun."
        )
        return

    await _ensure_user(message)

    code = await asyncio.to_thread(
        _with_db,
        lambda db: services.create_admin_otp(db, tg_id),
    )

    await message.answer(
        f"🔐 Admin panel kodi: <code>{code}</code>\n"
        "Kod 5 daqiqa amal qiladi va faqat bir marta ishlatiladi.\n"
        f"Panel: {settings.WEBAPP_URL.rstrip('/')}/admin"
    )


async def main() -> None:
    if not settings.BOT_TOKEN:
        log.error(
            "BOT_TOKEN .env faylida yo'q. Bot ishga tushmadi."
        )
        return

    Base.metadata.create_all(bind=engine)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        await bot.set_my_commands(
            [
                {"command": "start", "description": "Boshlash"},
                {"command": "balance", "description": "Mening balansim"},
                {"command": "portfolio", "description": "Mening portfelim"},
                {"command": "leaderboard", "description": "Reyting jadvali"},
                {"command": "invite", "description": "Do'st taklif qilish"},
                {"command": "daily", "description": "Kunlik sovg'a"},
                {"command": "help", "description": "Yordam"},
            ]
        )

        log.info("TradeX boti ishga tushdi")
        await router.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
