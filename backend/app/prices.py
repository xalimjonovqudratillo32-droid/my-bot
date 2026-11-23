"""Kripto narxlari: CoinGecko (asosiy) va Binance (zaxira). Soxta narx hech qachon ishlatilmaydi."""
from __future__ import annotations

import json
import logging
import threading
import time
from decimal import Decimal

import httpx

from .config import settings

log = logging.getLogger("tradex.prices")

COINS = {
    "BTC": {"id": "bitcoin", "pair": "BTCUSDT", "name": "Bitcoin"},
    "ETH": {"id": "ethereum", "pair": "ETHUSDT", "name": "Ethereum"},
    "BNB": {"id": "binancecoin", "pair": "BNBUSDT", "name": "BNB"},
    "SOL": {"id": "solana", "pair": "SOLUSDT", "name": "Solana"},
    "XRP": {"id": "ripple", "pair": "XRPUSDT", "name": "XRP"},
    "DOGE": {"id": "dogecoin", "pair": "DOGEUSDT", "name": "Dogecoin"},
    "ADA": {"id": "cardano", "pair": "ADAUSDT", "name": "Cardano"},
}
STALE_AFTER_SECONDS = 300
CHART_CACHE_SECONDS = 300
_lock = threading.Lock()
_state: dict = {"items": {}, "ok_at": 0.0, "fetched_at": 0.0, "source": None, "error": None}
_chart_cache: dict = {}


def _fetch_coingecko() -> dict:
    params = {
        "ids": ",".join(c["id"] for c in COINS.values()),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    resp = httpx.get("https://api.coingecko.com/api/v3/simple/price", params=params, timeout=8)
    resp.raise_for_status()
    body = resp.json()
    items = {}
    for sym, meta in COINS.items():
        row = body.get(meta["id"]) or {}
        if row.get("usd") is not None:
            items[sym] = {"price": Decimal(str(row["usd"])),
                          "change_24h": float(row.get("usd_24h_change") or 0.0)}
    if not items:
        raise RuntimeError("bo'sh javob")
    return items


def _fetch_binance() -> dict:
    pair_to_sym = {meta["pair"]: sym for sym, meta in COINS.items()}
    resp = httpx.get("https://api.binance.com/api/v3/ticker/24hr",
                     params={"symbols": json.dumps(list(pair_to_sym))}, timeout=8)
    resp.raise_for_status()
    items = {}
    for row in resp.json():
        sym = pair_to_sym.get(row.get("symbol"))
        if sym:
            items[sym] = {"price": Decimal(row["lastPrice"]), "change_24h": float(row["priceChangePercent"])}
    if not items:
        raise RuntimeError("bo'sh javob")
    return items


def refresh(force: bool = False) -> None:
    now = time.time()
    with _lock:
        if not force and now - _state["fetched_at"] < settings.PRICE_CACHE_SECONDS:
            return
        _state["fetched_at"] = now
    errors = []
    for name, fetcher in (("coingecko", _fetch_coingecko), ("binance", _fetch_binance)):
        try:
            items = fetcher()
        except Exception as exc:  # tarmoq yoki API xatosi: keyingi manbaga o'tamiz
            errors.append(f"{name}: {exc}")
            continue
        with _lock:
            _state.update(items=items, ok_at=time.time(), source=name, error=None)
        return
    with _lock:
        _state["error"] = "; ".join(errors)
    log.warning("Narx manbalari ishlamadi: %s", errors)


def get_snapshot() -> dict:
    refresh()
    with _lock:
        items = dict(_state["items"])
        ok_at = _state["ok_at"]
        source = _state["source"]
        error = _state["error"]
    stale = not items or (time.time() - ok_at) > STALE_AFTER_SECONDS
    return {"items": items, "stale": stale, "source": source, "error": error, "updated_at": ok_at or None}


def get_chart(symbol: str) -> list[dict]:
    meta = COINS[symbol]
    cached = _chart_cache.get(symbol)
    if cached and time.time() - cached[0] < CHART_CACHE_SECONDS:
        return cached[1]
    try:
        resp = httpx.get(f"https://api.coingecko.com/api/v3/coins/{meta['id']}/market_chart",
                         params={"vs_currency": "usd", "days": "1"}, timeout=8)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Grafik olinmadi (%s): %s", symbol, exc)
        return cached[1] if cached else []
    points, seen = [], set()
    for ms, value in resp.json().get("prices", []):
        ts = int(ms // 1000)
        if ts not in seen:
            seen.add(ts)
            points.append({"time": ts, "value": float(value)})
    _chart_cache[symbol] = (time.time(), points)
    return points


def inject_for_tests(items: dict) -> None:
    """Faqat testlar uchun: tarmoqqa chiqmasdan narxlarni o'rnatadi."""
    now = time.time()
    with _lock:
        _state.update(
            items={k: {"price": Decimal(str(v["price"])), "change_24h": v.get("change_24h", 0.0)}
                   for k, v in items.items()},
            ok_at=now, fetched_at=now + 10**9, source="test", error=None,
        )
