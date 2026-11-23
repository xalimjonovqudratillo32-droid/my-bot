# TradeX: O'rnatish va ishga tushirish qo'llanmasi

## 1. Talablar
- Python 3.12
- (ixtiyoriy) PostgreSQL 16 yoki Docker Desktop
- Telegram akkaunt

## 2. Lokal ishga tushirish (Windows)
1. Loyiha papkasida `START.bat` ni ishga tushiring.
2. Ikkita oyna ochiladi: **TradeX API** va **TradeX Bot**.
3. Brauzerda `http://127.0.0.1:8000/?dev=1001` oching. `1001` ixtiyoriy test ID.

`BOT_TOKEN` bo'lmasa bot ishga tushmaydi, lekin API va Mini App (dev rejimda) ishlaydi.

## 3. BotFather orqali botni ulash
1. Telegram'da **@BotFather** ga yozing va `/newbot` yuboring.
2. Bot nomi va username (oxiri `bot` bilan tugashi kerak) ni kiriting.
3. BotFather bergan tokenni `.env` dagi `BOT_TOKEN=` ga yozing.
4. `BOT_USERNAME=` ga username'ni yozing. Referral havolalari shu orqali yaratiladi.
5. Mini App'ni ulash: BotFather → `/mybots` → botingiz → **Bot Settings** → **Menu Button** (yoki **Web App**). URL sifatida `WEBAPP_URL` dagi HTTPS manzilni kiriting.
6. `START.bat` ni qayta ishga tushiring yoki `python -m bot.bot` buyrug'ini bering.
7. Botga `/start` yuboring. $10,000 virtual balans va "TradeX'ni ochish" tugmasi chiqadi.

## 4. Admin sifatida kirish
1. Telegram ID ingizni @userinfobot orqali bilib oling.
2. `.env` da `ADMIN_TELEGRAM_IDS=SIZNING_ID` qiling va serverni qayta ishga tushiring.
3. Botga `/admin` yuboring. 6 xonali kod keladi (5 daqiqa, bir martalik).
4. `http://127.0.0.1:8000/admin` (serverda: `https://DOMEN/admin`) ni oching, Telegram ID va kodni kiriting.

## 5. Ma'lumotlar bazasi
- **Lokal:** `.env` da `DATABASE_URL=sqlite:///./tradex.db` (standart).
- **PostgreSQL:** `DATABASE_URL=postgresql+psycopg2://tradex:PAROL@localhost:5432/tradex`, so'ng:
  ```bash
  python -m alembic upgrade head
  python scripts/seed.py
  ```
- Yangi migratsiya: `python -m alembic revision --autogenerate -m "tavsif"`.

## 6. Serverga joylashtirish (Docker)
1. Serverga Docker va Docker Compose o'rnating, loyihani ko'chiring.
2. `cp .env.example .env` va quyidagilarni o'zgartiring:
   - `APP_ENV=production`
   - `DEV_AUTH_BYPASS=false`
   - `JWT_SECRET=` (uzun tasodifiy qiymat: `python -c "import secrets;print(secrets.token_urlsafe(48))"`)
   - `BOT_TOKEN`, `BOT_USERNAME`, `ADMIN_TELEGRAM_IDS`, `WEBAPP_URL=https://DOMEN/`
3. Parolni belgilang: `export POSTGRES_PASSWORD=kuchli_parol`
4. Ishga tushiring: `docker compose up -d --build`
5. Tekshiring: `curl https://DOMEN/health`

## 7. HTTPS (Telegram uchun majburiy)
Caddy eng oson yo'l. `Caddyfile`:
```
DOMEN {
    reverse_proxy localhost:8000
}
```
DNS A-yozuvi serverga yo'naltirilgach, Caddy sertifikatni avtomatik oladi.

## 8. Muammolarni hal qilish
- **"Narx hozir yangilanmagan"**: server CoinGecko/Binance'ga chiqa olmayapti. Tarmoq sozlamalarini tekshiring.
- **"initData imzosi noto'g'ri"**: `BOT_TOKEN` noto'g'ri, yoki Mini App boshqa bot tokeni bilan ochilgan.
- **Bot javob bermayapti**: `python -m bot.bot` ishlayotganini va `BOT_TOKEN` to'g'riligini tekshiring.
- **429 Too Many Requests**: `RATE_LIMIT_PER_MINUTE` ni oshiring.
