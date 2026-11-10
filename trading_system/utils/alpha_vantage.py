"""Titulares para el agente macro: Alpha Vantage NEWS_SENTIMENT + RSS de respaldo.

Todo pasa por la caché: la clave gratuita de Alpha Vantage da 25 peticiones al
día y como mucho 1 por segundo.
"""
import asyncio
import datetime as dt
import json
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

import config
from utils import cache_manager as cache
from utils import db

log = logging.getLogger("news")
_av_lock = asyncio.Lock()
_av_last = 0.0


def _get(url: str) -> bytes:
    with requests.get(url, headers={"User-Agent": "trading-system/1.0"}, timeout=15, stream=True) as r:
        r.raise_for_status()
        return r.raw.read(2_000_000, decode_content=True)  # tope de 2 MB: la respuesta viene de terceros


async def _av_news(symbol: str) -> list[dict]:
    """Noticias de un activo, o [] si no hay clave, presupuesto o permiso."""
    global _av_last
    if not config.ALPHAVANTAGE_API_KEY:
        return []
    ticker = "CRYPTO:" + symbol.split("/")[0]
    key = f"av_news:{ticker}"
    hit = cache.cache_get(key)
    if hit is not None:
        return hit
    # Repartir el presupuesto diario entre los activos a lo largo de 24 h.
    ttl = max(config.MACRO_INTERVAL, 86400 * len(config.SYMBOLS) / config.AV_DAILY_BUDGET)
    day = f"av_calls:{dt.datetime.now(dt.timezone.utc).date()}"
    used = db.get_state(day, 0)
    if used >= config.AV_DAILY_BUDGET:
        return []
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
        "function": "NEWS_SENTIMENT", "tickers": ticker, "sort": "LATEST", "limit": 20,
        "apikey": config.ALPHAVANTAGE_API_KEY})
    async with _av_lock:  # llamadas en ráfaga se rechazan aunque quede cuota diaria
        await asyncio.sleep(max(0.0, 1.5 - (time.monotonic() - _av_last)))
        raw = await asyncio.to_thread(_get, url)
        _av_last = time.monotonic()
    db.set_state(day, used + 1)
    data = json.loads(raw)
    items = []
    for a in data.get("feed", [])[:15]:
        score = next((float(t["ticker_sentiment_score"]) for t in a.get("ticker_sentiment", [])
                      if t.get("ticker") == ticker), None)
        if score is None:
            score = float(a.get("overall_sentiment_score") or 0)
        items.append({"title": str(a.get("title", ""))[:200], "source": a.get("source", "AV"),
                      "time": a.get("time_published", ""), "score": score, "symbol": symbol})
    if "feed" not in data:
        # Sin "feed" llega un aviso: límite alcanzado o endpoint premium (el
        # plan gratuito puede no incluir noticias). El vacío también se cachea
        # para no gastar el presupuesto reintentando algo que no va a cambiar.
        log.warning("Alpha Vantage sin noticias para %s: %s", ticker,
                    str(data.get("Information") or data.get("Note") or data.get("Error Message") or data)[:140])
    cache.cache_set(key, items, ttl)
    return items


async def _rss_news() -> list[dict]:
    hit = cache.cache_get("rss_news")
    if hit is not None:
        return hit
    items = []
    for url in config.NEWS_RSS_URLS:
        if not url.startswith("https://"):
            log.warning("RSS ignorado (solo https): %s", url)
            continue
        try:
            root = ET.fromstring(await asyncio.to_thread(_get, url))
        except Exception as e:
            log.warning("RSS %s falló: %s", urllib.parse.urlparse(url).netloc, type(e).__name__)
            continue
        host = urllib.parse.urlparse(url).netloc
        for it in list(root.iter("item"))[:12]:
            title = " ".join((it.findtext("title") or "").split())
            if title:
                items.append({"title": title[:200], "source": host,
                              "time": (it.findtext("pubDate") or "")[:31], "score": None, "symbol": None})
    cache.cache_set("rss_news", items, config.MACRO_INTERVAL)
    return items


async def headlines() -> list[dict]:
    """Titulares recientes de todos los activos configurados, sin duplicados."""
    out, seen = [], set()
    for symbol in config.SYMBOLS:
        try:
            out += await _av_news(symbol)
        except Exception as e:  # solo el tipo: el mensaje de requests lleva la URL con la clave
            log.warning("Alpha Vantage falló para %s: %s", symbol, type(e).__name__)
    out += await _rss_news()
    return [x for x in out if not (x["title"] in seen or seen.add(x["title"]))][:30]
