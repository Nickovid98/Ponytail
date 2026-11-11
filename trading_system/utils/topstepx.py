"""TopstepX (API de ProjectX): cliente REST, datos de mercado y ejecución de futuros.

Se ejecuta SOLO en tu equipo. Topstep exige que toda la actividad salga de un
dispositivo propio, sin VPS, VPN ni herramientas de acceso remoto.

Cada petición, respuesta, error de conexión y cambio de estado de una orden se
registra en la consola y en trading.log. Nunca se registran el token ni la clave.
"""
import asyncio
import datetime as dt
import logging
import re
import time

import pandas as pd
import requests

import config
from utils import cache_manager as cache
from utils import risk_manager as risk
from utils.exchange import PaperBroker

log = logging.getLogger("topstepx")
LIMIT, MARKET, STOP = 1, 2, 4  # OrderType de ProjectX
BUY, SELL = 0, 1  # OrderSide
LONG_POS = 1  # PositionType: 1 = largo, 2 = corto
_UNIT = {"m": 2, "h": 3, "d": 4}
_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_SECRET = ("apiKey", "token", "newToken")


class TopstepError(RuntimeError):
    """La API respondió, pero rechazó la petición."""


def _http(url: str, body: dict, headers: dict) -> tuple[int, dict]:
    r = requests.post(url, json=body, headers=headers, timeout=15)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


class TopstepClient:
    def __init__(self):
        self.token, self.token_ts, self._last = None, 0.0, 0.0
        self.gap = 0.35  # segundos entre peticiones: lejos del límite de la API y de cualquier ráfaga
        self._lock = asyncio.Lock()

    async def login(self) -> None:
        if not (config.TOPSTEPX_USERNAME and config.TOPSTEPX_API_KEY):
            raise SystemExit("Faltan TOPSTEPX_USERNAME y TOPSTEPX_API_KEY en el archivo .env")
        data = await self.post("/api/Auth/loginKey", {"userName": config.TOPSTEPX_USERNAME,
                                                      "apiKey": config.TOPSTEPX_API_KEY}, auth=False)
        self.token, self.token_ts = data["token"], time.time()

    async def post(self, path: str, body: dict | None = None, auth: bool = True) -> dict:
        body = body or {}
        if auth and (not self.token or time.time() - self.token_ts > 20 * 3600):  # el token dura 24 h
            await self.login()
        for attempt in (1, 2):
            async with self._lock:
                await asyncio.sleep(max(0.0, self.gap - (time.monotonic() - self._last)))
                log.info("-> POST %s %s", path, {k: "***" if k in _SECRET else v for k, v in body.items()})
                try:
                    status, data = await asyncio.to_thread(
                        _http, config.TOPSTEPX_API_URL + path, body,
                        {"Authorization": f"Bearer {self.token}"} if auth else {})
                except requests.RequestException as e:
                    log.error("xx %s sin conexión (%s)", path, type(e).__name__)
                    raise ConnectionError(f"TopstepX no responde en {path}") from None
                finally:
                    self._last = time.monotonic()
            log.info("<- %s HTTP %s success=%s errorCode=%s %s", path, status, data.get("success"),
                     data.get("errorCode"), data.get("errorMessage") or "")
            if status == 401 and auth and attempt == 1:
                await self.login()  # token caducado: se renueva y se repite una sola vez
                continue
            if status == 429 or status >= 500:  # saturación o caída del servicio: pasajero, se reintenta luego
                raise ConnectionError(f"TopstepX devolvió HTTP {status} en {path}")
            if status != 200 or not data.get("success"):
                raise TopstepError(f"{path}: HTTP {status}, código {data.get('errorCode')}: {data.get('errorMessage')}")
            return data
        raise TopstepError(f"{path}: no autorizado tras renovar el token")


class TopstepMarket:
    """Misma interfaz que exchange.Market, con velas de ProjectX."""

    def __init__(self, client: TopstepClient | None = None):
        self.api = client or TopstepClient()
        self.account_id, self.contracts = None, {}

    async def start(self) -> None:
        accounts = [a for a in (await self.api.post("/api/Account/search", {"onlyActiveAccounts": True}))["accounts"]
                    if a.get("canTrade")]
        if config.TOPSTEPX_ACCOUNT_ID:
            accounts = [a for a in accounts if str(a["id"]) == config.TOPSTEPX_ACCOUNT_ID]
        if len(accounts) != 1:
            raise SystemExit(f"Hay {len(accounts)} cuentas operables; indica una en TOPSTEPX_ACCOUNT_ID. "
                             f"Disponibles: {[(a['id'], a.get('name')) for a in accounts]}")
        self.account_id = accounts[0]["id"]
        for symbol in config.SYMBOLS:  # p. ej. MNQ, MES: se resuelve al contrato activo
            found = (await self.api.post("/api/Contract/search", {"searchText": symbol, "live": False}))["contracts"]
            match = [c for c in found if c.get("activeContract")
                     and re.fullmatch(re.escape(symbol) + r"[FGHJKMNQUVXZ]\d{1,2}", c.get("name", ""))]
            if len(match) != 1:
                raise SystemExit(f"No se pudo resolver {symbol!r} a un único contrato activo en TopstepX "
                                 f"(candidatos: {[c.get('name') for c in found]}). Revisa SYMBOLS.")
            self.contracts[symbol] = match[0]
            log.info("Contrato %s -> %s (tick %s = %s USD)", symbol, match[0]["id"], match[0]["tickSize"],
                     match[0]["tickValue"])

    def tick(self, symbol: str) -> float:
        return float(self.contracts[symbol]["tickSize"])

    def point_value(self, symbol: str) -> float:
        """Dólares por punto y contrato."""
        c = self.contracts[symbol]
        return float(c["tickValue"]) / float(c["tickSize"])

    async def _bars(self, symbol: str, tf: str, seconds_back: float) -> list:
        end = dt.datetime.now(dt.timezone.utc)
        data = await self.api.post("/api/History/retrieveBars", {
            "contractId": self.contracts[symbol]["id"], "live": False,
            "startTime": (end - dt.timedelta(seconds=seconds_back)).isoformat(), "endTime": end.isoformat(),
            "unit": _UNIT[tf[-1]], "unitNumber": int(tf[:-1]), "limit": 20000, "includePartialBar": True})
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in data.get("bars") or []]

    async def ohlcv(self, symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
        key = f"tsx:{symbol}:{tf}:{limit}"
        rows = cache.cache_get(key)
        if rows is None:
            sec = int(tf[:-1]) * _SECONDS[tf[-1]]
            # El mercado cierra a diario y el fin de semana: se pide una ventana holgada y se recorta.
            rows = await self._bars(symbol, tf, limit * sec * 1.6 + 3 * 86400)
            cache.cache_set(key, rows, cache.window(sec / 4))
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df.ts, utc=True)
        return df.sort_values("ts").tail(limit).reset_index(drop=True)

    async def price(self, symbol: str) -> float:
        # ponytail: el precio sale de la última vela de 1 minuto, consultada cada 5 s. Techo: hasta 5 s
        # de retraso y una petición por símbolo en cada consulta. Mejora: el hub de mercado en tiempo
        # real (SignalR, rtc.topstepx.com/hubs/market, evento GatewayQuote).
        key = f"tsxpx:{symbol}"
        p = cache.cache_get(key)
        if p is None:
            rows = await self._bars(symbol, "1m", 1200)
            p = float(max(rows)[4]) if rows else float((await self.ohlcv(symbol, config.ATR_TIMEFRAME, 5)).close.iloc[-1])
            cache.cache_set(key, p, 5)
        return p

    async def close(self) -> None:
        pass


def contracts(market, symbol: str, qty: float) -> int:
    """Contratos enteros que caben en `qty` (dólares por punto), con el tope de la cuenta."""
    n = min(int(qty // market.point_value(symbol)), config.PROP_MAX_CONTRACTS)
    if n < 1:
        raise ValueError(f"el riesgo por operación no alcanza para 1 contrato de {symbol}")
    return n


class TopstepPaperBroker(PaperBroker):
    """Simulación con contratos enteros, comisión por contrato y deslizamiento en ticks."""

    def _size(self, symbol, qty):
        return contracts(self.market, symbol, qty) * self.market.point_value(symbol)

    def _slip(self, symbol, ref, buying):
        return ref + (1 if buying else -1) * config.PROP_SLIPPAGE_TICKS * self.market.tick(symbol)

    def _fee(self, symbol, price, qty):
        return config.PROP_COMMISSION * qty / self.market.point_value(symbol)


class TopstepBroker:
    """Órdenes reales en la cuenta de Topstep. El stop y el objetivo viajan en la misma orden
    (brackets), así que quedan en el servidor aunque este programa se caiga."""

    def __init__(self, market: TopstepMarket):
        self.market, self.api = market, market.api

    def _acct(self, **extra) -> dict:
        return {"accountId": self.market.account_id, **extra}

    async def equity(self) -> float:
        accounts = (await self.api.post("/api/Account/search", {"onlyActiveAccounts": True}))["accounts"]
        return float(next(a["balance"] for a in accounts if a["id"] == self.market.account_id))

    async def _position(self, cid: str) -> dict | None:
        open_ = (await self.api.post("/api/Position/searchOpen", self._acct()))["positions"]
        return next((p for p in open_ if p["contractId"] == cid and p.get("size")), None)

    async def _working(self, cid: str | None = None) -> list[dict]:
        orders = (await self.api.post("/api/Order/searchOpen", self._acct()))["orders"]
        return [o for o in orders if cid is None or o["contractId"] == cid]

    async def _cancel(self, orders: list[dict]) -> None:
        for o in orders:
            try:
                await self.api.post("/api/Order/cancel", self._acct(orderId=o["id"]))
            except TopstepError:
                pass  # ya ejecutada o cancelada

    async def open(self, symbol, side, qty, sl, tp, ref_price) -> dict:
        c, tick, long = self.market.contracts[symbol], self.market.tick(symbol), side == "LONG"
        n = contracts(self.market, symbol, qty)
        sl_ticks, tp_ticks = round(abs(ref_price - sl) / tick), round(abs(tp - ref_price) / tick)
        if min(sl_ticks, tp_ticks) <= 5:
            raise ValueError(f"stop u objetivo a 5 ticks o menos en {symbol}: TopstepX lo rechaza")
        # Con signo: el stop de un largo va en ticks negativos y el objetivo en positivos; al revés en corto.
        d = (1 if long else -1) if config.PROP_BRACKET_SIGNED else 0
        try:
            order = await self.api.post("/api/Order/place", self._acct(
                contractId=c["id"], type=MARKET, side=BUY if long else SELL, size=n,
                stopLossBracket={"ticks": -d * sl_ticks if d else sl_ticks, "type": STOP},
                takeProfitBracket={"ticks": d * tp_ticks if d else tp_ticks, "type": LIMIT}))
        except ConnectionError:
            order = {}  # sin respuesta: la orden pudo entrar igualmente; lo decide la verificación de abajo
        log.info("ORDEN %s enviada: %s %d x %s, stop a %d ticks, objetivo a %d", order.get("orderId"), side, n,
                 symbol, sl_ticks, tp_ticks)

        # No se da por buena hasta ver en la cuenta la posición, un stop del lado de la pérdida
        # y un objetivo del lado de la ganancia.
        pos, stop, target = None, None, None
        losing = lambda px, avg: px is not None and ((px < avg) if long else (px > avg))
        for _ in range(5):
            await asyncio.sleep(config.PROP_VERIFY_WAIT_S)
            pos = await self._position(c["id"])
            if pos:
                avg, working = float(pos["averagePrice"]), await self._working(c["id"])
                stop = next((o for o in working if o.get("type") == STOP and losing(o.get("stopPrice"), avg)), None)
                target = next((o for o in working if o.get("type") == LIMIT and o.get("limitPrice") is not None
                               and not losing(o["limitPrice"], avg) and o["limitPrice"] != avg), None)
                if stop and target:
                    break
        if not pos:
            raise TopstepError(f"la orden de {symbol} no abrió posición")
        if not (stop and target):
            log.critical("Posición en %s SIN STOP u objetivo verificados: se cierra de inmediato", symbol)
            await self.flatten_all()
            raise TopstepError(f"la posición de {symbol} quedó sin stop u objetivo válidos y se cerró. Revisa "
                               "PROP_BRACKET_SIGNED y los brackets automáticos de la plataforma.")
        pv = self.market.point_value(symbol)
        log.info("POSICIÓN ABIERTA %s %d x %s a %s, stop en %s", side, n, symbol, avg, stop["stopPrice"])
        return {"price": avg, "qty": n * pv, "fee": config.PROP_COMMISSION * n, "slippage": abs(avg - ref_price) * n * pv,
                "sl": float(stop["stopPrice"]), "tp": avg + (1 if long else -1) * tp_ticks * tick,
                "meta": {"contracts": n, "contract_id": c["id"], "order_id": order.get("orderId")}}

    async def check_exit(self, t: dict) -> dict | None:
        cid = t["meta"]["contract_id"]
        if await self._position(cid):
            return None
        # La posición ya no existe: saltó el stop o el objetivo. El precio sale de las ejecuciones.
        since = dt.datetime.fromtimestamp(t["opened_ts"] - 5, dt.timezone.utc).isoformat()
        fills = [x for x in (await self.api.post("/api/Trade/search", self._acct(startTimestamp=since)))["trades"]
                 if x["contractId"] == cid and x.get("profitAndLoss") is not None and not x.get("voided")]
        size = sum(x["size"] for x in fills)
        px = sum(x["price"] * x["size"] for x in fills) / size if size else await self.market.price(t["symbol"])
        reason = ("SL" if abs(px - t["sl"]) <= abs(px - t["tp"]) else "TP") + ("" if size else "?")
        await self._cancel(await self._working(cid))  # ninguna orden huérfana
        log.info("POSICIÓN CERRADA %s por %s a %s", t["symbol"], reason, px)
        return {"price": px, "reason": reason, "slippage": 0.0,
                "fee": sum(x.get("fees") or 0 for x in fills) or config.PROP_COMMISSION * t["meta"]["contracts"]}

    async def day_start_equity(self) -> float:
        """Saldo con el que empezó el día operativo: el actual menos lo ya realizado hoy. Así, reiniciar
        el programa a media sesión no estrena límite de pérdida.
        ponytail: supone que el saldo ya descuenta las comisiones de cada ejecución. Si no fuera así,
        el error es del tamaño de las comisiones del día y va en el sentido prudente."""
        trades = (await self.api.post("/api/Trade/search", self._acct(
            startTimestamp=risk.day_open().isoformat())))["trades"]
        return await self.equity() - sum((x.get("profitAndLoss") or 0) - (x.get("fees") or 0)
                                         for x in trades if not x.get("voided"))

    async def close(self, t: dict) -> dict:
        cid = t["meta"]["contract_id"]
        await self._cancel(await self._working(cid))
        if await self._position(cid):
            await self.api.post("/api/Position/closeContract", self._acct(contractId=cid))
        px = await self.market.price(t["symbol"])
        log.info("POSICIÓN CERRADA A MERCADO %s (precio aproximado %s)", t["symbol"], px)
        return {"price": px, "fee": config.PROP_COMMISSION * t["meta"]["contracts"], "slippage": 0.0}

    async def flatten_all(self) -> None:
        """Cierre total: cancela todas las órdenes y cierra todas las posiciones de la cuenta,
        también las que no abrió este programa."""
        log.critical("CIERRE TOTAL de la cuenta %s", self.market.account_id)
        await self._cancel(await self._working())
        for p in (await self.api.post("/api/Position/searchOpen", self._acct()))["positions"]:
            await self.api.post("/api/Position/closeContract", self._acct(contractId=p["contractId"]))
