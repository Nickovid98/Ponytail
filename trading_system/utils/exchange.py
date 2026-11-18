"""Conexión de mercado (CCXT para cripto, Yahoo para acciones) y los dos brokers: simulado y real.

Ambos brokers exponen lo mismo: equity(), open(), check_exit(), close().
"""
import asyncio
import logging
import math
import time

import ccxt.async_support as ccxt
import pandas as pd
import requests

import config
from utils import cache_manager as cache
from utils import db
from utils import risk_manager as rm
from utils import trade_logger

log = logging.getLogger("exchange")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Lo que se pide de cada intervalo; de ahí se recorta lo que pida el agente. Las de 4 h salen de las de 1 h.
YAHOO_RANGE = {"1m": "1d", "5m": "60d", "15m": "1mo", "1h": "1y", "1d": "2y"}


def _yahoo_get(symbol: str, interval: str, span: str) -> tuple[int, dict]:
    r = requests.get(YAHOO + symbol, params={"interval": interval, "range": span},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


class YahooUnknown(ConnectionError):
    """Yahoo no conoce el símbolo (404): solo al arrancar es motivo para no seguir."""


_yahoo_pause = 0.0  # tras un 429 (demasiadas peticiones) no se le pide nada a Yahoo durante un rato


async def yahoo_chart(symbol: str, interval: str, span: str) -> dict:
    """Velas y último precio de una acción en Yahoo Finance (gratis, sin clave), ya comprobados.
    Devuelve {"price", "rows": [[ms, o, h, l, c, v], ...]}. Cualquier fallo, también una respuesta con otro
    formato, sale como ConnectionError: es de la red o de Yahoo, no del bot, y no lo mata."""
    # ponytail: Yahoo no tiene API oficial. Techo: si cambia el formato o bloquea, las acciones dejan de
    # tener señales y de operar (cripto sigue); no hay segunda fuente.
    global _yahoo_pause
    if time.monotonic() < _yahoo_pause:
        raise ConnectionError("Yahoo pidió esperar (demasiadas peticiones)")
    try:
        status, body = await asyncio.to_thread(_yahoo_get, symbol, interval, span)
    except requests.RequestException as e:
        raise ConnectionError(f"Yahoo no responde ({type(e).__name__})") from e
    if status == 429:
        _yahoo_pause = time.monotonic() + 120
    if status == 404:
        raise YahooUnknown(f"Yahoo no conoce {symbol}")
    try:
        r = body["chart"]["result"][0]
        price = float(r["meta"]["regularMarketPrice"])
        q = r["indicators"]["quote"][0] if r.get("timestamp") else {}
        # Las velas que llegan vacías (sin negocio) se descartan.
        rows = [[t * 1000, float(o), float(h), float(lo), float(c), float(v or 0)] for t, o, h, lo, c, v in zip(
            r.get("timestamp") or [], q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", []),
            q.get("volume", [])) if None not in (o, h, lo, c)]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ConnectionError(f"Yahoo respondió {status} con un formato inesperado para {symbol}") from e
    if status != 200 or not math.isfinite(price) or price <= 0:
        raise ConnectionError(f"Yahoo respondió {status} para {symbol}")
    return {"price": price, "rows": rows}


def make_exchange():
    """Cliente CCXT configurado. En modo paper no usa claves, salvo en Alpaca: allí
    solo sirven para leer datos sin el límite de peticiones de los accesos anónimos."""
    # include_OS_certificates: además de los certificados de certifi, acepta el que indique SSL_CERT_FILE
    # (redes que inspeccionan el TLS: proxy de empresa, algunos antivirus).
    opts = {"enableRateLimit": True, "options": {"include_OS_certificates": True}}
    alpaca_keys = config.EXCHANGE_ID == "alpaca" and bool(config.EXCHANGE_API_KEY)
    if config.EXECUTION_MODE != "paper" or alpaca_keys:
        opts |= {"apiKey": config.EXCHANGE_API_KEY, "secret": config.EXCHANGE_SECRET}
        if config.EXCHANGE_PASSWORD:
            opts["password"] = config.EXCHANGE_PASSWORD
    ex = getattr(ccxt, config.EXCHANGE_ID)(opts)
    if config.EXCHANGE_ID == "alpaca":
        if alpaca_keys and config.EXCHANGE_SANDBOX:
            ex.set_sandbox_mode(True)  # las claves de una cuenta paper solo valen en paper-api
        elif not alpaca_keys:
            # La lista de mercados de Alpaca exige clave; las velas de cripto no. Sin clave se
            # declaran a mano los pares configurados.
            ex.set_markets([{"id": s, "symbol": s, "base": s.split("/")[0], "quote": s.split("/")[1],
                             "baseId": s.split("/")[0], "quoteId": s.split("/")[1], "type": "spot", "spot": True,
                             "active": True, "precision": {"price": 0.01, "amount": 1e-9},
                             "limits": {"amount": {}, "price": {}, "cost": {}}} for s in config.SYMBOLS if "/" in s])
    return ex


class Market:
    """Datos de mercado cacheados. Cripto por CCXT; las acciones (símbolos sin "/") por Yahoo."""

    def __init__(self):
        crypto = [s for s in config.SYMBOLS if not rm.is_stock(s)]
        self.ex = make_exchange() if crypto or not config.SYMBOLS else None  # solo acciones: no hace falta exchange
        if self.ex and config.EXECUTION_MODE == "sandbox" and config.EXCHANGE_ID != "alpaca":
            kind = config.SANDBOX_KIND
            if kind == "auto":
                # Binance retiró el testnet clásico de futuros: ccxt acepta
                # set_sandbox_mode(True) sin quejarse y falla en la primera
                # orden. Su reemplazo es "demo trading".
                kind = "demo" if config.EXCHANGE_ID.startswith("binance") else "testnet"
            if kind == "demo":
                self.ex.enable_demo_trading(True)
            else:
                self.ex.set_sandbox_mode(True)

    async def start(self) -> None:
        if self.ex:
            await self.ex.load_markets()
            missing = [s for s in config.SYMBOLS if not rm.is_stock(s) and s not in self.ex.markets]
            if missing:
                raise SystemExit(f"Símbolos desconocidos en {config.EXCHANGE_ID}: {missing}")
        for s in filter(rm.is_stock, config.SYMBOLS):
            try:
                await yahoo_chart(s, "1d", "5d")
            except YahooUnknown as e:
                raise SystemExit(f"Sin datos de la acción {s} en Yahoo: {e}") from e
            except ConnectionError as e:  # pasajero: arranca igual (cripto no espera) y los bots lo reintentan
                log.warning("Yahoo no respondió al arrancar con %s: %s", s, e)

    async def _stock_bars(self, symbol: str, tf: str, limit: int) -> pd.DataFrame:
        interval = "1h" if tf == "4h" else tf
        if interval not in YAHOO_RANGE:
            raise ValueError(f"Yahoo no da velas de {tf}")
        # Con la bolsa cerrada las velas no cambian y se guardan 30 min; la clave cambia al abrir, así que en la
        # apertura se piden nuevas al momento en vez de seguir con las de ayer.
        open_now = rm.nyse_open_since() is not None
        key = f"ohlcv:yahoo:{symbol}:{interval}:{'abierta' if open_now else 'cerrada'}"
        rows = cache.cache_get(key)
        if rows is None:
            rows = (await yahoo_chart(symbol, interval, YAHOO_RANGE[interval]))["rows"]
            cache.cache_set(key, rows, cache.window(self._seconds(interval) / 4) if open_now else 1800)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df.ts, unit="ms", utc=True)
        if tf == "4h":
            df = df.set_index("ts").resample("4h", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        # Solo velas cerradas, como en el backtest: una mecha de la vela en curso no dispara nada. El precio de
        # ahora lo da price(), y PaperBroker.check_exit lo mira además de las velas.
        df = df[df.ts + pd.Timedelta(seconds=self._seconds(tf)) <= pd.Timestamp.now(tz="UTC")]
        return df.tail(limit).reset_index(drop=True)

    @staticmethod
    def _seconds(tf: str) -> int:
        return int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}[tf[-1]]

    async def ohlcv(self, symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
        if rm.is_stock(symbol):
            return await self._stock_bars(symbol, tf, limit)
        key = f"ohlcv:{config.EXCHANGE_ID}:{config.EXECUTION_MODE}:{symbol}:{tf}:{limit}"
        rows = cache.cache_get(key)
        if rows is None:
            if config.EXCHANGE_ID == "alpaca":
                # Sin fecha de inicio, Alpaca solo devuelve las velas de hoy, y con ella entrega
                # páginas de unos 7 días empezando por lo más antiguo. Se recorre hasta el presente
                # una ventana holgada (un intervalo sin operaciones no tiene vela) y se recorta.
                wide, span, now = int(limit * 1.5), self.ex.parse_timeframe(tf) * 1000, self.ex.milliseconds()
                since, rows = now - wide * span, []
                while since < now:
                    batch = await self.ex.fetch_ohlcv(symbol, tf, since=since, limit=wide)
                    if not batch or batch[-1][0] < since:
                        break
                    rows += batch
                    since = batch[-1][0] + span
                rows = rows[-limit:]
            else:
                rows = await self.ex.fetch_ohlcv(symbol, tf, limit=limit)
            # ponytail: al caducar se vuelve a pedir la ventana entera. Techo:
            # 1 petición por (símbolo, timeframe) cada 1-3 minutos. Si el rate
            # limit aprieta, pasar a descarga incremental con `since`.
            cache.cache_set(key, rows, cache.window(self.ex.parse_timeframe(tf) / 4))
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df.ts, unit="ms", utc=True)
        return df

    async def price(self, symbol: str) -> float:
        stock = rm.is_stock(symbol)
        key = f"price:{'yahoo' if stock else config.EXCHANGE_ID}:{config.EXECUTION_MODE}:{symbol}"
        p = cache.cache_get(key)
        if p is None:
            if stock:  # la petición más pequeña que trae el precio; fuera de la sesión, Yahoo da el último cierre
                p = (await yahoo_chart(symbol, "1d", "1d"))["price"]
            else:
                p = float((await self.ex.fetch_ticker(symbol))["last"])
            cache.cache_set(key, p, 5)
        return p

    async def book_imbalance(self, symbol: str, depth: int = 20) -> float:
        """(bids - asks) / (bids + asks) en los primeros niveles del libro: [-1, 1]."""
        if rm.is_stock(symbol):
            raise NotImplementedError("Yahoo no da el libro de órdenes de las acciones")
        key = f"book:{config.EXCHANGE_ID}:{symbol}"
        v = cache.cache_get(key)
        if v is None:
            ob = await self.ex.fetch_order_book(symbol, depth)
            b = sum(x[1] for x in ob["bids"][:depth])
            a = sum(x[1] for x in ob["asks"][:depth])
            v = (b - a) / (b + a) if b + a else 0.0
            cache.cache_set(key, v, 20)
        return v

    async def close(self) -> None:
        if self.ex:
            await self.ex.close()


class PaperBroker:
    """Simula las órdenes contra precios reales, con comisión y deslizamiento.
    No envía nada al exchange."""

    def __init__(self, market):
        self.market = market

    async def equity(self) -> float:
        # En la cuenta de fondeo simulada cada cuenta nueva empieza en CAPITAL: se descuenta lo de las anteriores.
        base = db.get_state("fund", {}).get("base", 0.0) if config.FUNDED else 0.0
        return config.CAPITAL + trade_logger.realized_pnl() - base

    # Tres ganchos que redefine la simulación de futuros (contratos enteros, costes por contrato).
    def _size(self, symbol, qty):
        if rm.is_stock(symbol):  # acciones enteras, siempre hacia abajo: el riesgo nunca pasa del previsto
            if int(qty) < 1:
                raise ValueError(f"el tamaño no llega a 1 acción de {symbol}")
            return float(int(qty))
        return qty

    def _slip(self, symbol, ref, buying):
        return rm.slip(ref, buying, symbol)

    def _fee(self, symbol, price, qty):
        return rm.fee(price, qty, symbol)

    def _fill(self, symbol, ref: float, qty: float, buying: bool, market_order: bool = True, **extra) -> dict:
        price = self._slip(symbol, ref, buying) if market_order else ref
        return {"price": price, "qty": qty, "fee": self._fee(symbol, price, qty),
                "slippage": abs(price - ref) * qty, **extra}

    async def open(self, symbol, side, qty, sl, tp, ref_price) -> dict:
        return self._fill(symbol, ref_price, self._size(symbol, qty), buying=side == "LONG", meta={})

    async def check_exit(self, t: dict) -> dict | None:
        # ponytail: SL/TP se evalúan con velas de 1m (cacheadas 60 s) más el
        # último precio en cada ciclo del jefe. Techo: resolución de 1 minuto, y en
        # cripto no modela huecos de precio más allá del deslizamiento fijo.
        # Mejora: stream de ticks por websocket.
        # Se mira desde la apertura de la operación, vela a vela y en orden: un stop tocado mientras el motor
        # estaba apagado también cuenta. Con velas de 1 min hasta donde llegan (16 h en cripto, la sesión de hoy
        # en acciones); si la operación es más vieja, con velas más gruesas para no perder lo de antes.
        long, stock = t["side"] == "LONG", rm.is_stock(t["symbol"])
        age = time.time() - t["opened_ts"]
        if stock:
            since_open = rm.nyse_open_since()
            tf = "1m" if since_open is not None and t["opened_ts"] >= since_open else "5m"
        else:
            tf = "1m" if age < 16 * 3600 else "5m" if age < 3.4 * 86400 else "1h"
        step = {"1m": 60, "5m": 300, "1h": 3600}[tf]
        bars = min(1000 if not stock else 5000, 60 * (int(age // (60 * step)) + 1) + 10)  # tramos de 60: caché
        df = await self.market.ohlcv(t["symbol"], tf, bars)
        since = df[df.ts >= pd.Timestamp(t["opened_ts"], unit="s", tz="UTC")]
        last = await self.market.price(t["symbol"])
        hit_sl = (lambda lo, hi: lo <= t["sl"]) if long else (lambda lo, hi: hi >= t["sl"])
        hit_tp = (lambda lo, hi: hi >= t["tp"]) if long else (lambda lo, hi: lo <= t["tp"])
        for b in [*since.itertuples(index=False), None]:
            lo, hi, first = (b.low, b.high, b.open) if b is not None else (last, last, last)
            # Si una misma vela toca ambos niveles se asume el stop: lo conservador.
            if hit_sl(lo, hi):
                # Acciones: si la vela abrió ya pasado el stop, o el precio de ahora ya lo está (un hueco en la
                # apertura o tras una parada), la orden a mercado se ejecuta ahí, no en el stop.
                ref = first if stock and hit_sl(first, first) else t["sl"]
                return self._fill(t["symbol"], ref, t["qty"], buying=not long, reason="SL")  # el stop es orden a mercado
            if hit_tp(lo, hi):
                return self._fill(t["symbol"], t["tp"], t["qty"], buying=not long, market_order=False, reason="TP")  # límite
        return None

    async def close(self, t: dict) -> dict:
        return self._fill(t["symbol"], await self.market.price(t["symbol"]), t["qty"], buying=t["side"] != "LONG")


class CcxtBroker:
    """Órdenes reales (sandbox o live). Asume futuros lineales en modo one-way.

    El stop y el take profit quedan como órdenes en el exchange: protegen la
    posición aunque este programa se caiga. El deslizamiento aquí es el real de
    la entrada (precio ejecutado frente al de referencia); la comisión se estima
    con FEE_PCT, y el saldo del exchange es siempre la cifra que manda.
    """

    def __init__(self, market):
        self.market = market
        self.ex = market.ex

    async def equity(self) -> float:
        return float((await self.ex.fetch_balance())["total"].get(config.QUOTE) or 0)

    async def open(self, symbol, side, qty, sl, tp, ref_price) -> dict:
        ex, m = self.ex, self.ex.market(symbol)
        cs = m.get("contractSize") or 1  # en algunos exchanges la cantidad va en contratos
        amount = float(ex.amount_to_precision(symbol, qty / cs))
        lim = m.get("limits") or {}
        if amount <= 0 or amount < ((lim.get("amount") or {}).get("min") or 0) \
                or amount * cs * ref_price < ((lim.get("cost") or {}).get("min") or 0):
            raise ValueError(f"tamaño {amount} por debajo del mínimo del exchange para {symbol}")
        try:
            await ex.set_leverage(max(1, math.ceil(config.MAX_LEVERAGE)), symbol)
        except Exception as e:  # no todos los exchanges lo permiten; el nocional ya está acotado
            log.warning("set_leverage falló en %s: %s", symbol, e)
        entry_side, exit_side = ("buy", "sell") if side == "LONG" else ("sell", "buy")
        order = await ex.create_order(symbol, "market", entry_side, amount)
        ids = {}
        try:
            for key, param, level in (("sl_id", "stopLossPrice", sl), ("tp_id", "takeProfitPrice", tp)):
                o = await ex.create_order(symbol, "market", exit_side, amount, None, {
                    param: float(ex.price_to_precision(symbol, level)), "reduceOnly": True})
                ids[key] = o["id"]
        except Exception:
            # Una posición sin stop no se deja viva: se deshace y se propaga el error.
            log.error("No se pudieron colocar SL/TP en %s: se cierra la posición", symbol)
            await self._cancel(symbol, ids)
            await ex.create_order(symbol, "market", exit_side, amount, None, {"reduceOnly": True})
            raise
        price = order.get("average") or order.get("price")
        if not price:  # hay exchanges que no devuelven el precio medio al crear la orden
            try:
                price = (await ex.fetch_order(order["id"], symbol)).get("average")
            except Exception:
                price = None
        price = float(price or ref_price)
        return {"price": price, "qty": amount * cs, "fee": rm.fee(price, amount * cs),
                "slippage": abs(price - ref_price) * amount * cs, "meta": ids}

    async def _cancel(self, symbol: str, ids: dict) -> None:
        for oid in ids.values():
            try:
                await self.ex.cancel_order(oid, symbol)
            except Exception:
                pass  # ya ejecutada o cancelada

    async def check_exit(self, t: dict) -> dict | None:
        ex, symbol = self.ex, t["symbol"]
        positions = await ex.fetch_positions([symbol])
        if any(abs(float(p.get("contracts") or 0)) > 0 for p in positions if p.get("symbol") == symbol):
            return None
        # La posición ya no existe: saltó el SL o el TP (o se cerró a mano).
        hit = None
        for key, reason in (("sl_id", "SL"), ("tp_id", "TP")):
            try:
                o = await ex.fetch_order(t["meta"][key], symbol)
                if o.get("status") == "closed":
                    hit = (float(o.get("average") or t[reason.lower()]), reason)
            except Exception:
                pass
        await self._cancel(symbol, t["meta"])
        if hit is None:
            # No se pudo leer qué orden se ejecutó: se estima por cercanía al
            # último precio y se marca como estimado.
            last = await self.market.price(symbol)
            near_sl = abs(last - t["sl"]) < abs(last - t["tp"])
            hit = (t["sl"] if near_sl else t["tp"], "SL?" if near_sl else "TP?")
        return {"price": hit[0], "reason": hit[1], "fee": rm.fee(hit[0], t["qty"]), "slippage": 0.0}

    async def close(self, t: dict) -> dict:
        ex, symbol = self.ex, t["symbol"]
        await self._cancel(symbol, t["meta"])
        cs = ex.market(symbol).get("contractSize") or 1
        side = "sell" if t["side"] == "LONG" else "buy"
        o = await ex.create_order(symbol, "market", side, float(ex.amount_to_precision(symbol, t["qty"] / cs)),
                                  None, {"reduceOnly": True})
        p = float(o.get("average") or o.get("price") or await self.market.price(symbol))
        return {"price": p, "fee": rm.fee(p, t["qty"]), "slippage": 0.0}
