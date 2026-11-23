@echo off
chcp 65001 >nul
title TradeX launcher
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [XATO] Python topilmadi. https://www.python.org/downloads/ dan Python 3.12 o'rnating.
  pause
  exit /b 1
)

if not exist .venv (
  echo Virtual muhit yaratilmoqda...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo Kutubxonalar o'rnatilmoqda...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [XATO] Kutubxonalarni o'rnatib bo'lmadi.
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  REM Faqat LOKAL test uchun: Mini App ?dev=ID rejimi. Serverda bu qiymatni false qiling!
  powershell -NoProfile -Command "(Get-Content .env) -replace '^DEV_AUTH_BYPASS=.*','DEV_AUTH_BYPASS=true' | Set-Content .env"
  echo .env fayli yaratildi (lokal dev rejim yoqildi). BOT_TOKEN va ADMIN_TELEGRAM_IDS ni to'ldiring.
)

echo Ma'lumotlar bazasi migratsiyalari...
python -m alembic upgrade head
if errorlevel 1 (
  echo [XATO] Migratsiya muvaffaqiyatsiz.
  pause
  exit /b 1
)

start "TradeX API" cmd /k "call .venv\Scripts\activate.bat && python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
start "TradeX Bot" cmd /k "call .venv\Scripts\activate.bat && python -m bot.bot"

echo.
echo TradeX ishga tushdi:
echo   Mini App : http://127.0.0.1:8000/?dev=1001
echo   Admin    : http://127.0.0.1:8000/admin
echo.
pause
