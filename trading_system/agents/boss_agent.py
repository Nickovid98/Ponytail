"""Agente Jefe (Chief Risk & Execution Manager).

Pondera los votos, aplica los filtros de riesgo y ejecuta. Es determinista a
propósito: ninguna decisión de riesgo o de ejecución pasa por un LLM, así cada
orden se puede reproducir a partir de los votos que quedan en el registro.
"""
import datetime as dt
import logging
import time

from pydantic import BaseModel

import config
from agents import Direction, Signal
from utils import cache, risk, trade_logger

log = logging.getLogger("boss")
SIGN = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}


class Decision(BaseModel):
    symbol: str
    action: Direction
    score: float  # [-1, 1]
    reason: str
    votes: dict[str, dict]  # desglose exacto por agente
    ts: float


class BossAgent:
    def __init__(self, market, broker, max_age: dict[str, float]):
        self.market, self.broker = market, broker
        self.max_age = max_age  # antigüedad máxima de la señal de cada agente

    @staticmethod
    def decide(symbol: str, signals: list[Signal], weights: dict, max_age: dict,
               now: float | None = None) -> Decision:
        now = now or time.time()
        fresh = [s for s in signals if now - s.ts <= max_age.get(s.agent, 900)]
        votes = {s.agent: s.model_dump(exclude={"agent", "symbol"})
                 | {"weight": float(weights.get(s.agent, {}).get(symbol, 1.0))} for s in fresh}
        den = sum(v["weight"] for v in votes.values())
        # Los NEUTRAL cuentan en el denominador: sin acuerdo amplio no hay operación.
        score = sum(v["weight"] * SIGN[v["direction"]] * v["confidence"] / 100 for v in votes.values()) / den if den else 0.0
        veto = next((s for s in fresh if s.veto), None)
        if veto:
            action, reason = "NEUTRAL", f"veto de {veto.agent}: {veto.rationale}"
        elif len(fresh) < config.MIN_AGENTS:
            action, reason = "NEUTRAL", f"solo {len(fresh)} agentes con señal vigente (mínimo {config.MIN_AGENTS})"
        elif abs(score) < config.DECISION_THRESHOLD:
            action, reason = "NEUTRAL", f"score {score:+.2f} por debajo del umbral ±{config.DECISION_THRESHOLD}"
        else:
            action = "LONG" if score > 0 else "SHORT"
            reason = f"score {score:+.2f} con {sum(v['direction'] == action for v in votes.values())}/{len(votes)} agentes a favor"
        return Decision(symbol=symbol, action=action, score=round(score, 4), reason=reason, votes=votes, ts=now)

    async def tick(self) -> None:
        """Un ciclo completo: salidas, cuenta, límites y nuevas entradas."""
        mode = config.EXECUTION_MODE
        # 1) Salidas. Se vigilan siempre, también con el bot detenido.
        for t in trade_logger.open_trades():
            res = await self.broker.check_exit(t)
            if res:
                self._close(t, res, res["reason"])

        # 2) Cuenta y límites de pérdida (con el PnL flotante incluido).
        equity = await self.broker.equity()
        floating = 0.0
        for t in trade_logger.open_trades():
            floating += (await self.market.price(t["symbol"]) - t["entry"]) * t["qty"] * SIGN[t["side"]]
        total = equity + floating
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        day_start = cache.get_state(f"day_start:{mode}:{today}")
        if day_start is None:
            cache.set_state(f"day_start:{mode}:{today}", day_start := total)
        start = cache.get_state(f"start_equity:{mode}")
        if start is None:
            cache.set_state(f"start_equity:{mode}", start := total)

        kill = cache.get_state("ctl:kill", {"on": False})
        breach = risk.check_limits(total, day_start, start)
        if breach and breach[0] == "total" and not kill.get("on"):
            cache.set_state("ctl:kill", kill := {"on": True, "flatten": True, "reason": breach[1]})
        if breach and breach[0] == "daily":
            cache.set_state(f"halt:{mode}:{today}", breach[1])
        if breach or (kill.get("on") and kill.get("flatten", True)):
            await self._flatten("LIMITE" if breach else "KILL")
        blocked = (kill.get("reason") or "kill switch activado") if kill.get("on") \
            else cache.get_state(f"halt:{mode}:{today}")

        cache.set_state("account", {
            "equity": equity, "floating": floating, "day_pnl": total - day_start,
            "total_pnl": total - start, "blocked": blocked, "mode": mode, "ts": time.time()})

        # 3) Decisión y, si procede, entrada por cada activo.
        for symbol in config.SYMBOLS:
            await self._step(symbol, equity, blocked)
        cache.set_state("heartbeat", time.time())

    async def _step(self, symbol: str, equity: float, blocked: str | None) -> None:
        raws = (cache.get_state(f"signal:{name}:{symbol}") for name in self.max_age)
        d = self.decide(symbol, [Signal(**r) for r in raws if r], cache.get_state("weights", {}), self.max_age)
        try:
            status, plan = await self._maybe_trade(d, equity, blocked)
        except Exception as e:
            log.exception("Fallo al operar %s", symbol)
            status, plan = f"error: {e}"[:200], None
        cache.set_state(f"boss:{symbol}", d.model_dump() | {"status": status, "plan": plan})

    async def _maybe_trade(self, d: Decision, equity: float, blocked: str | None) -> tuple[str, dict | None]:
        if d.action == "NEUTRAL":
            return "sin operación", None
        open_ = trade_logger.open_trades()
        if any(t["symbol"] == d.symbol for t in open_):
            return "posición ya abierta", None
        if blocked:
            return f"bloqueado: {blocked}", None
        if len(open_) >= config.MAX_OPEN_POSITIONS:
            return "máximo de posiciones abiertas", None
        if time.time() - trade_logger.last_close_ts(d.symbol) < config.COOLDOWN_S:
            return "enfriamiento tras el último cierre", None

        price = await self.market.price(d.symbol)
        atr_v = risk.atr(await self.market.ohlcv(d.symbol, config.ATR_TIMEFRAME, 100))
        plan = risk.build_plan(d.action, price, atr_v, equity, self._structure(d, price))
        if plan is None:
            return "plan de riesgo inválido (ATR, capital o tamaño mínimo)", None

        if cache.get_state("ctl:mode", config.START_MODE) == "MANUAL":
            # La aprobación caduca y se consume: lo que se ejecuta es siempre la
            # decisión vigente en este ciclo, nunca una propuesta vieja.
            if time.time() - cache.get_state(f"approve:{d.symbol}", 0) > config.APPROVAL_TTL_S:
                return "esperando aprobación manual", plan
            cache.set_state(f"approve:{d.symbol}", 0)

        fill = await self.broker.open(d.symbol, d.action, plan["qty"], plan["sl"], plan["tp"], price)
        trade_logger.log_open(d.symbol, d.action, fill["qty"], fill["price"], plan["sl"], plan["tp"],
                              d.score, d.votes, fill["meta"], fill["fee"])
        log.info("ABIERTA %s %s qty=%.6g entrada=%.6g SL=%.6g TP=%.6g riesgo=%.2f",
                 d.action, d.symbol, fill["qty"], fill["price"], plan["sl"], plan["tp"], plan["risk_usd"])
        return "orden ejecutada", plan

    @staticmethod
    def _structure(d: Decision, price: float) -> float | None:
        """Nivel técnico más cercano que defienden los agentes que votaron a favor."""
        long = d.action == "LONG"
        key = "support" if long else "resistance"
        levels = [v[key] for v in d.votes.values()
                  if v["direction"] == d.action and v.get(key) and ((v[key] < price) if long else (v[key] > price))]
        return (max(levels) if long else min(levels)) if levels else None

    def _close(self, t: dict, res: dict, reason: str) -> None:
        pnl = trade_logger.log_close(t, res["price"], reason, res.get("fee", 0.0))
        log.info("CERRADA %s %s por %s a %.6g · PnL %.2f", t["side"], t["symbol"], reason, res["price"], pnl)

    async def _flatten(self, reason: str) -> None:
        for t in trade_logger.open_trades():
            try:
                self._close(t, await self.broker.close(t), reason)
            except Exception:
                log.exception("No se pudo cerrar %s: revisa la posición en el exchange", t["symbol"])
