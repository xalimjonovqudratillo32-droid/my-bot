
"""TradeX FastAPI ilovasi: API, Mini App va admin panel."""

import asyncio
import collections
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import prices, services
from .config import DEFAULT_JWT_SECRET, settings
from .database import Base, SessionLocal, engine
from .routers import admin as admin_router
from .routers import public as public_router
from .services import ServiceError


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
ADMIN_HTML = ROOT / "admin" / "index.html"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tradex")


def _tick() -> None:
    prices.refresh(force=True)

    with SessionLocal() as db:
        filled = services.process_pending_orders(db)

    if filled:
        log.info("%s ta limit buyurtma bajarildi", filled)


async def _background_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            log.exception("Fon vazifasi xatosi")

        await asyncio.sleep(settings.PRICE_CACHE_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Production xavfsizligi
    if settings.APP_ENV == "production" and (
        settings.JWT_SECRET == DEFAULT_JWT_SECRET
        or len(settings.JWT_SECRET) < 32
    ):
        raise RuntimeError(
            "Production'da JWT_SECRET kamida 32 belgili "
            "va standartdan farqli bo'lishi shart"
        )

    if settings.dev_bypass_enabled:
        log.warning(
            "DEV_AUTH_BYPASS yoqilgan: faqat lokal test uchun!"
        )

    if settings.JWT_SECRET == DEFAULT_JWT_SECRET:
        log.warning("JWT_SECRET standart qiymatda.")

    # Ma'lumotlar bazasini tayyorlash
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        services.seed_defaults(db)

    # Narxlar va limit buyurtmalar uchun fon vazifasi
    background_task = asyncio.create_task(
        _background_loop(),
        name="tradex-background",
    )

    # Telegram botni shu servis ichida ishga tushirish
    bot_task = None

    if settings.BOT_TOKEN:

        async def run_bot():
            from bot.bot import main as bot_main

            while True:
                try:
                    log.info("Telegram bot ishga tushirilmoqda")
                    await bot_main()

                    log.warning(
                        "Telegram bot to'xtadi. 5 soniyadan keyin qayta urinish."
                    )

                except asyncio.CancelledError:
                    log.info("Telegram bot vazifasi to'xtatilmoqda")
                    raise

                except Exception:
                    log.exception("Telegram bot xatosi")

                await asyncio.sleep(5)

        bot_task = asyncio.create_task(
            run_bot(),
            name="tradex-telegram-bot",
        )

        log.info("Telegram bot fon rejimida ishga tushirildi")

    else:
        log.warning(
            "BOT_TOKEN yo'q. Telegram bot ishga tushmadi."
        )

    try:
        yield

    finally:
        log.info("TradeX servis yopilmoqda")

        tasks = [background_task]

        if bot_task is not None:
            tasks.append(bot_task)

        for running_task in tasks:
            running_task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        log.info("TradeX servis muvaffaqiyatli yopildi")


app = FastAPI(
    title="TradeX API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(
    public_router.router,
    prefix="/api",
)

app.include_router(
    admin_router.router,
    prefix="/api/admin",
)

app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="static",
)


_hits: dict[str, list[float]] = collections.defaultdict(list)


CSP_MINIAPP = (
    "default-src 'self'; "
    "script-src 'self' https://telegram.org https://unpkg.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org"
)

CSP_ADMIN = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)

    response.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff",
    )

    response.headers.setdefault(
        "Referrer-Policy",
        "strict-origin-when-cross-origin",
    )

    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )

    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = CSP_MINIAPP

    elif request.url.path == "/admin":
        response.headers["Content-Security-Policy"] = CSP_ADMIN
        response.headers["Cache-Control"] = "no-store"

    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api"):
        ip = (
            request.client.host
            if request.client
            else "unknown"
        )

        if len(_hits) > 50000:
            _hits.clear()

        now = time.time()

        window = [
            t for t in _hits[ip]
            if now - t < 60
        ]

        if len(window) >= settings.RATE_LIMIT_PER_MINUTE:
            _hits[ip] = window

            return JSONResponse(
                {
                    "detail": (
                        "Juda ko'p so'rov. "
                        "Biroz kutib qayta urinib ko'ring"
                    )
                },
                status_code=429,
            )

        window.append(now)
        _hits[ip] = window

    return await call_next(request)


@app.exception_handler(ServiceError)
async def service_error_handler(
    _request: Request,
    exc: ServiceError,
):
    return JSONResponse(
        {"detail": exc.message},
        status_code=exc.status_code,
    )


@app.get("/", include_in_schema=False)
def miniapp():
    return FileResponse(
        FRONTEND_DIR / "index.html"
    )


@app.get("/admin", include_in_schema=False)
def admin_panel():
    return FileResponse(
        ADMIN_HTML
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": time.time(),
    }
