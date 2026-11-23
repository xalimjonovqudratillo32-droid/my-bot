# TradeX — Telegram Crypto Trading Game

TradeX Telegram ichida ishlaydigan **virtual** kripto savdo o'yini. Foydalanuvchi $10,000 virtual balans bilan
boshlaydi. Real kripto sotib olinmaydi, pul kiritilmaydi va yechilmaydi.

## Imkoniyatlar
- Market va Limit buyurtmalar (limit buyurtmalar fon jarayonida bajariladi)
- Portfel: ochiq pozitsiyalar, o'rtacha kirish narxi, realized va unrealized P/L
- Buyurtmalar tarixi va tranzaksiyalar tarixi
- XP, 5 ta daraja (Beginner → Master), kunlik va haftalik missiyalar, yutuqlar, kunlik sovg'a
- Referral: individual havola, faqat yangi foydalanuvchi ro'yxatdan o'tganda bonus, o'zini o'zi taklif qilish bloklangan
- Global reyting
- Telegram bot (aiogram 3) va Mini App (HTML/CSS/JS, Lightweight Charts)
- Admin panel: statistika, foydalanuvchilar, ban/unban, balans va XP tuzatish, e'lonlar, audit log, API holati, sozlamalar
- Xavfsizlik: Telegram initData HMAC tekshiruvi, JWT, rate limit, Decimal hisob-kitob, idempotency kaliti, manfiy balansni bloklash

## Texnologiyalar
Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (production) yoki SQLite (lokal), aiogram 3, python-jose (JWT),
CoinGecko va Binance narx manbalari (zaxira bilan).

## Tuzilma
```
backend/app/         FastAPI ilova, servislar, modellar, xavfsizlik, narxlar
backend/app/routers/ API endpointlar (public, admin)
bot/                 Telegram bot
frontend/            Mini App (index.html, app.css, app.js)
admin/               Admin panel (index.html)
migrations/          Alembic migratsiyalari
tests/               pytest testlari
scripts/             Yordamchi skriptlar
docs/                Qo'shimcha qo'llanmalar
```

## Tez ishga tushirish (Windows)
1. Python 3.12 o'rnating.
2. `START.bat` faylini ikki marta bosing. Skript virtual muhit yaratadi, kutubxonalarni o'rnatadi, `.env` yaratadi
   va migratsiyalarni ishlatadi.
3. Brauzerda `http://127.0.0.1:8000/?dev=1001` oching (Mini App, lokal test rejimi).
4. Admin panel: `http://127.0.0.1:8000/admin`.

## Qo'lda ishga tushirish (Linux/macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m alembic upgrade head
python -m uvicorn backend.app.main:app --reload --port 8000
# alohida terminalda (BOT_TOKEN kerak):
python -m bot.bot
```

## Testlar
```bash
python -m pytest -q
```
Testlar SQLite'da ishlaydi, narxlar tarmoqqa chiqmasdan test qilinadi.

## Konfiguratsiya
Barcha sozlamalar `.env` orqali beriladi:
- `BOT_TOKEN`: BotFather tokeni. Bo'sh bo'lsa ham API lokal rejimda ishlaydi.
- `ADMIN_TELEGRAM_IDS`: admin Telegram ID lari, vergul bilan.
- `JWT_SECRET`: production'da albatta o'zgartiring (server buni tekshiradi).
- `DEV_AUTH_BYPASS`: faqat lokal test uchun. `APP_ENV=production` bo'lganda avtomatik o'chadi.
- `WEBAPP_URL`: Mini App uchun HTTPS manzil (Telegram talabi).

Batafsil qo'llanma: [docs/INSTALL_UZ.md](docs/INSTALL_UZ.md). Unda BotFather ulash, admin kirish va serverga joylashtirish bor.

## Ma'lum cheklovlar
- Narxlar bepul API'dan olinadi (CoinGecko, zaxira: Binance). Ikkalasi ham ishlamasa narx "mavjud emas" deb ko'rsatiladi va savdo to'xtatiladi. Soxta narx ishlatilmaydi.
- Reyting har so'rovda barcha foydalanuvchilar equity'sini hisoblaydi. Juda katta auditoriya uchun keshlash kerak.
- Rate limit xotirada saqlanadi. Bir nechta server uchun Redis kerak bo'ladi.
- Mini App grafigi 1 kunlik ma'lumot ko'rsatadi.
- "Bugungi foyda" bugun yopilgan savdolarning realized P/L yig'indisi.
- Premium/monetizatsiya uchun arxitektura tayyor (kengaytirish nuqtalari), lekin to'lov integratsiyasi yo'q.
- `portfolios` alohida jadval emas: portfel `positions` va `balances` jadvallaridan hisoblanadi.
