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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
        except Exception:  # pragma: no cover
            log.exception("Fon vazifasi xatosi")
        await asyncio.sleep(settings.PRICE_CACHE_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.APP_ENV == "production" and (settings.JWT_SECRET == DEFAULT_JWT_SECRET or len(settings.JWT_SECRET) < 32):
        raise RuntimeError("Production'da JWT_SECRET kamida 32 belgili va standartdan farqli bo'lishi shart")
    # Lokal ishga tushirish uchun jadvallarni yaratamiz; production'da alembic ishlatiladi.
    if settings.dev_bypass_enabled:
        log.warning("DEV_AUTH_BYPASS yoqilgan: X-Dev-User orqali kirish mumkin. Faqat lokal test uchun!")
    if settings.JWT_SECRET == DEFAULT_JWT_SECRET:
        log.warning("JWT_SECRET standart qiymatda. Serverda uzun tasodifiy qiymat qo'ying.")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        services.seed_defaults(db)
    task = asyncio.create_task(_background_loop())
    yield
    task.cancel()


app = FastAPI(title="TradeX API", version="1.0.0", lifespan=lifespan)
app.include_router(public_router.router, prefix="/api")
app.include_router(admin_router.router, prefix="/api/admin")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_hits: dict[str, list[float]] = collections.defaultdict(list)


CSP_MINIAPP = ("default-src 'self'; script-src 'self' https://telegram.org https://unpkg.com; "
               "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
               "object-src 'none'; base-uri 'none'; frame-ancestors https://web.telegram.org https://*.telegram.org")
CSP_ADMIN = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
             "frame-ancestors 'none'; form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = CSP_MINIAPP
    elif request.url.path == "/admin":
        response.headers["Content-Security-Policy"] = CSP_ADMIN
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api"):
        ip = request.client.host if request.client else "unknown"
        if len(_hits) > 50000:  # xotira cheklovi: eski IP yozuvlarini tozalaymiz
            _hits.clear()
        now = time.time()
        window = [t for t in _hits[ip] if now - t < 60]
        if len(window) >= settings.RATE_LIMIT_PER_MINUTE:
            _hits[ip] = window
            return JSONResponse({"detail": "Juda ko'p so'rov. Biroz kutib qayta urinib ko'ring"}, status_code=429)
        window.append(now)
        _hits[ip] = window
    return await call_next(request)


@app.exception_handler(ServiceError)
async def service_error_handler(_request: Request, exc: ServiceError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.get("/", include_in_schema=False)
def miniapp():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_panel():
    return FileResponse(ADMIN_HTML)


@app.get("/health")
def health():
    return {"status": "ok", "time": time.time()}
