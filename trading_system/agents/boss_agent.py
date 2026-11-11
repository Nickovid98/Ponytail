"""Agente Jefe (Chief Risk & Execution Manager).

Recibe los JSON minificados de los sub-agentes, pondera sus votos, aplica los
filtros de riesgo y ejecuta. Es determinista a propósito: ninguna decisión de
riesgo o de ejecución pasa por un LLM, así cada orden se puede reproducir a
partir de los votos que quedan en el registro.
"""
import datetime as dt
import logging
import time

from pydantic import BaseModel

import config
from agents import Direction, Signal
from utils import db, token_budget, trade_logger
from utils import risk_manager as risk

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
    def __init__(self, market, broker, max_age: dict[str, float], evolution=None, notify=None):
        self.market, self.broker = market, broker
        self.max_age = max_age  # antigüedad máxima de la señal de cada agente
        self.evolution = evolution  # gestor de selección natural (opcional)
        self.notify = notify or (lambda text: None)
        self._payload_tokens = 0

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
        today = risk.trading_day()
        day_start = db.get_state(f"day_start:{mode}:{today}")
        if day_start is None:
            if hasattr(self.broker, "day_start_equity"):
                total_0 = await self.broker.day_start_equity()  # descuenta lo ya realizado hoy
            else:
                total_0 = total
            db.set_state(f"day_start:{mode}:{today}", day_start := total_0)
        start = db.get_state(f"start_equity:{mode}")
        if start is None:
            db.set_state(f"start_equity:{mode}", start := total)

        kill = db.get_state("ctl:kill", {"on": False})
        breach = risk.check_limits(total, day_start, start)
        if breach and breach[0] == "total" and not kill.get("on"):
            db.set_state("ctl:kill", kill := {"on": True, "flatten": True, "reason": breach[1]})
        first_halt = bool(breach) and breach[0] == "daily" and not db.get_state(f"halt:{mode}:{today}")
        if breach and breach[0] == "daily":
            if first_halt:
                log.critical("KILL SWITCH ACTIVADO · %s · sin operar hasta el próximo día operativo", breach[1])
                self.notify(f"🛑 KILL SWITCH · {breach[1]}")
            db.set_state(f"halt:{mode}:{today}", breach[1])  # persiste: reiniciar el programa no lo levanta
        if breach or (kill.get("on") and kill.get("flatten", True)):
            open_before = bool(trade_logger.open_trades()) or first_halt
            await self._flatten("LIMITE" if breach else "KILL")
            if open_before and hasattr(self.broker, "flatten_all"):
                await self.broker.flatten_all()  # también lo que no abrió este programa
        allow_entries, must_flatten = risk.session()
        if must_flatten:
            await self._flatten("SESION")  # fin de la sesión: nada queda abierto
        blocked = (kill.get("reason") or "kill switch activado") if kill.get("on") \
            else db.get_state(f"halt:{mode}:{today}") or (None if allow_entries else "fuera del horario de entradas")

        db.set_state("account", {
            "equity": equity, "floating": floating, "day_pnl": total - day_start,
            "total_pnl": total - start, "blocked": blocked, "mode": mode, "ts": time.time()})

        # 3) Decisión y, si procede, entrada por cada activo.
        self._payload_tokens = 0
        for symbol in config.SYMBOLS:
            await self._step(symbol, equity, blocked)
        token_budget.record_cycle(self._payload_tokens)
        db.set_state("heartbeat", time.time())

    async def _step(self, symbol: str, equity: float, blocked: str | None) -> None:
        payloads = [p for p in (db.get_state(f"signal:{name}:{symbol}") for name in self.max_age) if p]
        self._payload_tokens += sum(token_budget.estimate(p) for p in payloads)
        d = self.decide(symbol, [token_budget.unpack(p) for p in payloads], db.get_state("weights", {}), self.max_age)
        try:
            status, plan = await self._maybe_trade(d, equity, blocked)
        except Exception as e:
            log.exception("Fallo al operar %s", symbol)
            status, plan = f"error: {e}"[:200], None
        db.set_state(f"boss:{symbol}", d.model_dump() | {"status": status, "plan": plan})

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
        # Frecuencia: tope de operaciones por día y espera mínima entre entradas. Son límites
        # reales de la operativa, no un disfraz: este motor decide cada 30-60 s, no en milisegundos.
        entries = trade_logger.entries_since(time.time() - 86400)
        today = risk.trading_day()
        if config.MAX_TRADES_PER_DAY and sum(
                risk.trading_day(dt.datetime.fromtimestamp(ts, dt.timezone.utc)) == today
                for ts in entries) >= config.MAX_TRADES_PER_DAY:
            return "máximo de operaciones del día alcanzado", None
        last = max(entries[-1] if entries else 0, db.get_state("order:last_attempt", 0))
        if time.time() - last < config.MIN_ORDER_GAP_S:
            return "espera mínima entre órdenes", None

        price = await self.market.price(d.symbol)
        atr_v = risk.atr(await self.market.ohlcv(d.symbol, config.ATR_TIMEFRAME, 100))
        plan = risk.build_plan(d.action, price, atr_v, equity, self._structure(d, price))
        if plan is None:
            return "plan de riesgo inválido (ATR, capital o tamaño mínimo)", None

        if db.get_state("ctl:mode", config.START_MODE) == "MANUAL":
            # La aprobación caduca y se consume: lo que se ejecuta es siempre la
            # decisión vigente en este ciclo, nunca una propuesta vieja.
            if time.time() - db.get_state(f"approve:{d.symbol}", 0) > config.APPROVAL_TTL_S:
                return "esperando aprobación manual", plan
            db.set_state(f"approve:{d.symbol}", 0)

        db.set_state("order:last_attempt", time.time())
        try:
            fill = await self.broker.open(d.symbol, d.action, plan["qty"], plan["sl"], plan["tp"], price)
        except ValueError as e:  # la orden no cumple las reglas del broker: no llegó a enviarse
            return f"orden descartada: {e}", None
        except Exception as e:
            # A un broker que rechaza no se le insiste: a la tercera seguida se para hasta mañana.
            fails = db.get_state("order:fails", 0) + 1
            log.exception("Orden fallida en %s (%d seguidas)", d.symbol, fails)
            if fails >= 3:
                fails = 0
                db.set_state(f"halt:{config.EXECUTION_MODE}:{risk.trading_day()}", "3 órdenes fallidas seguidas")
                self.notify("🛑 Motor detenido hasta mañana: 3 órdenes fallidas seguidas")
            db.set_state("order:fails", fails)
            return f"orden rechazada ({type(e).__name__})", None
        db.set_state("order:fails", 0)
        plan |= {"sl": fill.get("sl", plan["sl"]), "tp": fill.get("tp", plan["tp"])}  # niveles reales si el broker los da
        trade_logger.log_open(d.symbol, d.action, fill["qty"], fill["price"], plan["sl"], plan["tp"],
                              d.score, d.votes, fill["meta"], fill["fee"], fill["slippage"])
        msg = (f"ABIERTA {d.action} {d.symbol} · qty {fill['qty']:.6g} · entrada {fill['price']:.6g} · "
               f"SL {plan['sl']:.6g} · TP {plan['tp']:.6g} · riesgo neto {plan['risk_usd']:.2f} {config.QUOTE}")
        log.info(msg)
        self.notify(("📈 " if d.action == "LONG" else "📉 ") + msg)
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
        # El PnL que sale de aquí ya es neto de comisiones y deslizamiento, y
        # es el único que ven las alertas y el gestor de evolución.
        c = trade_logger.log_close(t, res["price"], reason, res.get("fee", 0.0), res.get("slippage", 0.0))
        msg = (f"CERRADA {c['side']} {c['symbol']} por {reason} a {c['exit']:.6g} · PnL neto {c['pnl']:+.2f} "
               f"{config.QUOTE} (comisiones {c['fees']:.2f}, deslizamiento {c['slippage']:.2f})")
        log.info(msg)
        self.notify(("✅ " if c["pnl"] > 0 else "❌ ") + msg)
        if self.evolution:
            self.evolution.on_trade_closed(c)

    async def _flatten(self, reason: str) -> None:
        for t in trade_logger.open_trades():
            try:
                self._close(t, await self.broker.close(t), reason)
            except Exception:
                log.exception("No se pudo cerrar %s: revisa la posición en el exchange", t["symbol"])
