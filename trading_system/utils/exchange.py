"""Conexión de mercado (CCXT) y los dos brokers: simulado y real.

Ambos brokers exponen lo mismo: equity(), open(), check_exit(), close().
"""
import logging
import math

import ccxt.async_support as ccxt
import pandas as pd

import config
from utils import cache_manager as cache
from utils import risk_manager as rm
from utils import trade_logger

log = logging.getLogger("exchange")


def make_exchange():
    """Cliente CCXT configurado. En modo paper no usa claves, salvo en Alpaca: allí
    solo sirven para leer datos sin el límite de peticiones de los accesos anónimos."""
    opts = {"enableRateLimit": True}
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
                             "limits": {"amount": {}, "price": {}, "cost": {}}} for s in config.SYMBOLS])
    return ex


class Market:
    """Datos de mercado cacheados."""

    def __init__(self):
        self.ex = make_exchange()
        if config.EXECUTION_MODE == "sandbox" and config.EXCHANGE_ID != "alpaca":
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
        await self.ex.load_markets()
        missing = [s for s in config.SYMBOLS if s not in self.ex.markets]
        if missing:
            raise SystemExit(f"Símbolos desconocidos en {config.EXCHANGE_ID}: {missing}")

    async def ohlcv(self, symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
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
        key = f"price:{config.EXCHANGE_ID}:{config.EXECUTION_MODE}:{symbol}"
        p = cache.cache_get(key)
        if p is None:
            p = float((await self.ex.fetch_ticker(symbol))["last"])
            cache.cache_set(key, p, 5)
        return p

    async def book_imbalance(self, symbol: str, depth: int = 20) -> float:
        """(bids - asks) / (bids + asks) en los primeros niveles del libro: [-1, 1]."""
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
        await self.ex.close()


class PaperBroker:
    """Simula las órdenes contra precios reales, con comisión y deslizamiento.
    No envía nada al exchange."""

    def __init__(self, market):
        self.market = market

    async def equity(self) -> float:
        return config.CAPITAL + trade_logger.realized_pnl()

    # Tres ganchos que redefine la simulación de futuros (contratos enteros, costes por contrato).
    def _size(self, symbol, qty):
        return qty

    def _slip(self, symbol, ref, buying):
        return rm.slip(ref, buying)

    def _fee(self, symbol, price, qty):
        return rm.fee(price, qty)

    def _fill(self, symbol, ref: float, qty: float, buying: bool, market_order: bool = True, **extra) -> dict:
        price = self._slip(symbol, ref, buying) if market_order else ref
        return {"price": price, "qty": qty, "fee": self._fee(symbol, price, qty),
                "slippage": abs(price - ref) * qty, **extra}

    async def open(self, symbol, side, qty, sl, tp, ref_price) -> dict:
        return self._fill(symbol, ref_price, self._size(symbol, qty), buying=side == "LONG", meta={})

    async def check_exit(self, t: dict) -> dict | None:
        # ponytail: SL/TP se evalúan con velas de 1m (cacheadas 60 s) más el
        # último precio en cada ciclo del jefe. Techo: resolución de 1 minuto y
        # no modela huecos de precio más allá del deslizamiento fijo.
        # Mejora: stream de ticks por websocket.
        df = await self.market.ohlcv(t["symbol"], "1m", 10)
        since = df[df.ts >= pd.Timestamp(t["opened_ts"], unit="s", tz="UTC")]
        last = await self.market.price(t["symbol"])
        lo = min([last, *since.low])
        hi = max([last, *since.high])
        long = t["side"] == "LONG"
        # Si una misma vela toca ambos niveles se asume el stop: lo conservador.
        if (lo <= t["sl"]) if long else (hi >= t["sl"]):
            return self._fill(t["symbol"], t["sl"], t["qty"], buying=not long, reason="SL")  # el stop es orden a mercado
        if (hi >= t["tp"]) if long else (lo <= t["tp"]):
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
