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
import optimizer
from agents import Direction, Signal
from utils import db, token_budget, trade_logger
from utils import risk_manager as risk

log = logging.getLogger("boss")
SIGN = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}


def _seconds(tf: str) -> int:
    return int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}[tf[-1]]


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
        self._room = None  # cuenta de fondeo: lo que aún se puede perder hoy sin llegar al kill switch ni al suelo
        self._marks: dict = {}  # último precio de cada posición abierta (id -> [precio, flotante])

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
        optimizer.apply_params()  # lo que el operador haya ajustado en la app, ya recortado a su rango
        if config.FUNDED and db.get_state("fund") is None:
            self._fund(False, config.CAPITAL)  # antes de leer el saldo: la cuenta 1 empieza en CAPITAL desde el primer ciclo
        if config.FUNDED and db.get_state("fund", {}).get("atr_tf"):
            config.ATR_TIMEFRAME = db.get_state("fund")["atr_tf"]  # lección de una cuenta perdida: stop más ancho
        mode = config.EXECUTION_MODE
        # 1) Salidas. Se vigilan siempre, también con el bot detenido, y una a una: si falta el precio de un
        #    activo (Yahoo o el exchange fallan), los demás siguen vigilados y los límites siguen corriendo.
        for t in trade_logger.open_trades():
            try:
                res = await self.broker.check_exit(t)
            except Exception as e:
                log.warning("Sin datos para vigilar %s: %s", t["symbol"], e)
                continue
            if res:
                self._close(t, res, res["reason"])

        # 2) Cuenta y límites de pérdida (con el PnL flotante incluido).
        equity = await self.broker.equity()
        floating, marks = 0.0, {}  # marks: precio y resultado flotante de cada posición, para la app
        last_marks = db.get_state("account", {}).get("marks", {})
        for t in trade_logger.open_trades():
            try:
                px = await self.market.price(t["symbol"])
            except Exception as e:  # sin precio: el último conocido (o la entrada) para no cegar los límites
                px = (last_marks.get(str(t["id"])) or [t["entry"]])[0]
                log.warning("Sin precio de %s: se usa el último conocido (%s)", t["symbol"], e)
            marks[str(t["id"])] = [px, (px - t["entry"]) * t["qty"] * SIGN[t["side"]]]
            floating += marks[str(t["id"])][1]
        total = equity + floating
        self._marks = marks
        today = risk.trading_day()
        day_start = db.get_state(f"day_start:{mode}:{today}")
        new_day = day_start is None
        if new_day:
            if hasattr(self.broker, "day_start_equity"):
                total_0 = await self.broker.day_start_equity()  # descuenta lo ya realizado hoy
            else:
                total_0 = total
            db.set_state(f"day_start:{mode}:{today}", day_start := total_0)
        start = db.get_state(f"start_equity:{mode}")
        if start is None:
            db.set_state(f"start_equity:{mode}", start := total)

        fund = self._fund(new_day, total) if config.FUNDED else None
        if fund and (fund.get("lost") or total <= self._floor(fund)):
            if await self._relevo(fund, total, today):
                equity, floating, marks = await self.broker.equity(), 0.0, {}
                total = day_start = start = equity  # la cuenta nueva estrena capital y día
            fund = db.get_state("fund")
        if fund:  # lo que aún se puede perder hoy sin tocar el kill switch ni el suelo
            kill_at = config.PROP_DAILY_LOSS_LIMIT_USD * config.PROP_KILL_AT_PCT / 100
            self._room = min(kill_at - max(0.0, day_start - total), total - self._floor(fund))

        kill = db.get_state("ctl:kill", {"on": False})
        breach = risk.check_limits(total, day_start, start)
        if breach and breach[0] == "total" and not kill.get("on"):
            db.set_state("ctl:kill", kill := {"on": True, "flatten": True, "reason": breach[1]})
        first_halt = bool(breach) and breach[0] == "daily" and not db.get_state(f"halt:{mode}:{today}")
        if breach and breach[0] == "daily":
            if first_halt:
                log.critical("KILL SWITCH ACTIVADO · %s · sin operar hasta el próximo día operativo", breach[1])
                self.notify(f"🛑 KILL SWITCH · {breach[1]}")
                trade_logger.log_event("risk", "boss", f"Kill switch: {breach[1]}. Sin operar hasta el próximo día operativo")
            db.set_state(f"halt:{mode}:{today}", breach[1])  # persiste: reiniciar el programa no lo levanta
        if breach or (kill.get("on") and kill.get("flatten", True)):
            open_before = bool(trade_logger.open_trades()) or first_halt
            await self._flatten("LIMITE" if breach else "KILL")
            if open_before and hasattr(self.broker, "flatten_all"):
                await self.broker.flatten_all()  # también lo que no abrió este programa
        allow_entries, must_flatten = risk.session()
        if must_flatten:
            await self._flatten("SESION")  # fin de la sesión: nada queda abierto
        else:
            # Acciones: se cierran en los últimos minutos de la sesión de Nueva York (cripto sigue abierta). Lo que
            # siga abierto de una sesión anterior (el motor estaba apagado al cierre) se cierra en cuanto abre la bolsa,
            # a precio de ahora: con la bolsa cerrada no hay precio al que cerrar. Divisas: igual, con la semana
            # (cierran el viernes antes de las 17:00 de Nueva York; lo que pasara el fin de semana, en cuanto Yahoo da
            # precios de la semana nueva: hasta entonces _flatten no las toca).
            closing, since = risk.nyse_session()[1], risk.nyse_open_since()
            fx_closing, fx_since = risk.fx_session()[1], risk.fx_open_since()
            await self._flatten("SESION", only=lambda t: (risk.is_stock(t["symbol"]) and (
                closing or (since is not None and t["opened_ts"] < since))) or (risk.is_fx(t["symbol"]) and (
                fx_closing or (fx_since is not None and t["opened_ts"] < fx_since))))
        lost = fund.get("lost") and f"cuenta {fund['gen']} perdida: se cierra lo que queda abierto" if fund else None
        blocked = (kill.get("reason") or "kill switch activado") if kill.get("on") \
            else lost or db.get_state(f"halt:{mode}:{today}") or (None if allow_entries else "fuera del horario de entradas")

        db.set_state("account", {
            "equity": equity, "floating": floating, "day_pnl": total - day_start,
            "total_pnl": total - start, "blocked": blocked, "mode": mode, "ts": time.time(), "marks": marks}
            | ({"gen": fund["gen"], "floor": self._floor(fund), "peak": fund["peak"]} if fund else {}))

        # 3) Decisión de cada activo. Con los huecos limitados entra primero la señal más fuerte del ciclo, no el
        # primer activo de la lista, como en el backtest. No reserva huecos para las acciones: con la bolsa cerrada,
        # cripto puede ocuparlos todos (con 3 posiciones y 2 criptos siempre queda uno).
        self._payload_tokens = 0
        decisions = [self._decide(symbol) for symbol in config.SYMBOLS]
        for d in sorted(decisions, key=lambda d: -abs(d.score)):
            await self._step(d, equity, blocked)
        token_budget.record_cycle(self._payload_tokens)
        db.set_state("heartbeat", time.time())

    # --- Cuenta de fondeo simulada (PROP_MAX_LOSS_USD) ---------------------
    @staticmethod
    def _floor(fund: dict) -> float:
        """Saldo en el que se pierde la cuenta: el máximo de pérdida bajo su mejor cierre diario, sin pasar de CAPITAL."""
        return min(fund["peak"] - config.PROP_MAX_LOSS_USD, config.CAPITAL)

    @staticmethod
    def _fund(new_day: bool, total: float) -> dict:
        """Estado de la cuenta en curso. El mejor cierre diario es el saldo del último ciclo del día anterior,
        como en el backtest. La primera cuenta empieza en CAPITAL aunque la base de datos ya tuviera operaciones."""
        fund = db.get_state("fund") or {"gen": 1, "born": time.time(), "base": trade_logger.realized_pnl(),
                                        "peak": config.CAPITAL, "last_total": config.CAPITAL,
                                        "lesson": "configuración inicial", "blocked": [], "atr_tf": None}
        if new_day:
            fund["peak"] = max(fund["peak"], fund.get("last_total", fund["peak"]))
        fund["last_total"] = total
        db.set_state("fund", fund)
        return fund

    async def _relevo(self, fund: dict, total: float, today: str) -> bool:
        """La cuenta tocó su suelo: queda perdida aunque el precio rebote (como en una prueba real), se cierra
        todo, se apunta y nace otra que hereda lo aprendido y corrige UNA causa de la pérdida, con el mismo
        diagnóstico que el backtest (backtest.successor)."""
        from backtest import STOP_TFS, successor  # aquí y no arriba: backtest importa este módulo
        mode = config.EXECUTION_MODE
        if not fund.get("lost"):
            fund["lost"] = {"ts": time.time(), "total": total, "floor": self._floor(fund)}
            db.set_state("fund", fund)
        total, floor = fund["lost"]["total"], fund["lost"]["floor"]
        await self._flatten("MUERTE")
        if trade_logger.open_trades():
            return False  # algo no se pudo cerrar (sin precio): sin entradas nuevas, se reintenta en el próximo ciclo
        mine = [t | {"risk": abs(t["entry"] - t["sl"]) * t["qty"]}
                for t in trade_logger.closed_trades(since=fund["born"], mode=mode)]
        bot = {"gen": fund["gen"], "born": fund["born"], "weights": {}, "lesson": fund["lesson"],
               "blocked": {tuple(b) for b in fund["blocked"]}}
        # ponytail: el relevo hereda los pesos que el optimizador ya recalcula tras cada cierre, no los del
        # backtest (fijos por generación). Techo: el umbral y el riesgo de la lección viven en los ajustes de la app
        # y el operador puede deshacerlos; entonces el panel anuncia una lección que ya no se aplica.
        # ponytail: con las reglas fijas (evidence=False) hasta que la comparación de ESTADO.md ("Relevo con pruebas,
        # divisas y copiar a políticos y fondos", A) diga si el relevo con pruebas pierde menos.
        new = successor(bot, mine, trade_logger.closed_trades(mode=mode), set(STOP_TFS), time.time(), evidence=False)
        net = sum(t["pnl"] for t in mine)
        # Primero la cuenta nueva: si el programa se cae a mitad, el relevo y su lección no se repiten.
        db.set_state("fund", {"gen": fund["gen"] + 1, "born": time.time(), "base": trade_logger.realized_pnl(),
                              "peak": config.CAPITAL, "last_total": config.CAPITAL, "lesson": new["lesson"],
                              "blocked": sorted(map(list, new["blocked"])), "atr_tf": config.ATR_TIMEFRAME})
        db.set_state("fund:history", db.get_state("fund:history", []) + [{
            "gen": fund["gen"], "born": fund["born"], "end": time.time(), "n": len(mine), "net": net,
            "floor": floor, "lesson": fund["lesson"]}])
        # Umbral y riesgo van a los ajustes de la app: allí se ven y el operador puede deshacerlos.
        db.set_state("ctl:params", db.get_state("ctl:params", {}) | {
            "DECISION_THRESHOLD": config.DECISION_THRESHOLD, "RISK_PER_TRADE_PCT": config.RISK_PER_TRADE_PCT})
        db.set_state(f"start_equity:{mode}", config.CAPITAL)
        db.set_state(f"day_start:{mode}:{today}", config.CAPITAL)
        db.del_state(f"halt:{mode}:{today}")
        msg = (f"Cuenta {fund['gen']} perdida: el saldo ({total:,.2f}) tocó su suelo ({floor:,.2f}) tras {len(mine)} "
               f"operaciones y {net:+,.2f} {config.QUOTE}. Nace la cuenta {fund['gen'] + 1} con {config.CAPITAL:,.0f}: "
               f"{new['lesson']}")
        log.critical(msg)
        self.notify("💀 " + msg)
        trade_logger.log_event("death", "boss", msg, value=net)
        return True

    def _decide(self, symbol: str) -> Decision:
        bench = set(db.get_state("ctl:bench", []))  # bots que el operador ha mandado al banquillo: no votan
        payloads = [p for p in (db.get_state(f"signal:{name}:{symbol}") for name in self.max_age if name not in bench) if p]
        self._payload_tokens += sum(token_budget.estimate(p) for p in payloads)
        return self.decide(symbol, [token_budget.unpack(p) for p in payloads], db.get_state("weights", {}), self.max_age)

    async def _step(self, d: Decision, equity: float, blocked: str | None) -> None:
        symbol = d.symbol
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
        if risk.is_stock(d.symbol) and not risk.nyse_session()[0]:
            return "bolsa de Nueva York cerrada o fuera del horario de entradas", None
        if risk.is_fx(d.symbol) and not risk.fx_session()[0]:
            return "mercado de divisas cerrado o fuera del horario de entradas", None
        fund = db.get_state("fund", {}) if config.FUNDED else {}
        if [d.symbol, d.action] in fund.get("blocked", []):
            return f"vetado por la cuenta {fund['gen']}: {d.action} en {d.symbol} perdía", None
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
        # Acciones y divisas: velas de 1 h, o las del stop más ancho si una cuenta perdida lo enseñó (como el backtest).
        atr_tf = max(config.STOCK_TIMEFRAME, config.ATR_TIMEFRAME, key=_seconds) if risk.on_yahoo(d.symbol) \
            else config.ATR_TIMEFRAME
        atr_v = risk.atr(await self.market.ohlcv(d.symbol, atr_tf, 100))
        plan = risk.build_plan(d.action, price, atr_v, equity, self._structure(d, price), symbol=d.symbol)
        if plan is None:
            return "plan de riesgo inválido (ATR, capital o tamaño mínimo)", None
        if self._room is not None:
            # Cuenta de fondeo: no se abre nada cuyo stop, sumado al de lo ya abierto, pase del kill switch de
            # hoy o del suelo de la cuenta. Así un mal día se queda en el límite en vez de rebasarlo.
            at_risk = sum(self._open_risk(t) for t in open_) + plan["risk_usd"]
            if at_risk > self._room:
                return (f"sin margen: con esta operación habría {at_risk:,.0f} en juego y solo quedan "
                        f"{max(0.0, self._room):,.0f} hasta el kill switch o el suelo"), None

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
        trade_logger.log_event("trade", "boss", msg, d.symbol)
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
        trade_logger.log_event("trade", "boss", msg, c["symbol"], c["pnl"])
        # Recompensas: cada bot cobra o paga según lo que votó. Con ellas el jefe reajusta su confianza.
        for agent, pts in optimizer.reward(c).items():
            trade_logger.log_event("reward", agent, f"{pts:+.2f} R por {'acertar' if pts > 0 else 'fallar'} en "
                                   f"{c['symbol']} ({c['side']} cerrada por {reason})", c["symbol"], pts)
        if db.get_state("ctl:auto_opt", True):
            optimizer.run()
        if self.evolution:
            self.evolution.on_trade_closed(c)

    def _open_risk(self, t: dict) -> float:
        """Lo que aún puede costar una posición abierta si salta su stop, en la misma base que el saldo con lo
        flotante: desde su último precio (una ganancia flotante también está en juego), más su comisión de
        entrada (el saldo aún no la descuenta), la de salida y el deslizamiento del stop."""
        px = (self._marks.get(str(t["id"])) or [t["entry"]])[0]
        to_stop = max(0.0, (px - t["sl"]) * SIGN[t["side"]] * t["qty"])
        slip = abs(t["sl"] - risk.slip(t["sl"], buying=t["side"] != "LONG", symbol=t["symbol"])) * t["qty"]
        return to_stop + (t["fees"] or 0.0) + risk.fee(t["sl"], t["qty"], t["symbol"]) + slip

    async def _flatten(self, reason: str, only=None) -> None:
        """Cierra lo abierto; con `only`, solo las operaciones para las que only(operación) es cierto. Con la
        bolsa de Nueva York cerrada las acciones no se tocan (no hay precio al que cerrarlas), ni las divisas sin
        precio al día (fin de semana y primeras horas del domingo): se cierran cuando lo hay, y mientras tanto una
        cuenta perdida sigue sin operar."""
        nyse_closed, fx_closed = risk.nyse_open_since() is None, not risk.fx_quoted()
        for t in trade_logger.open_trades():
            if only and not only(t):
                continue
            if (nyse_closed and risk.is_stock(t["symbol"])) or (fx_closed and risk.is_fx(t["symbol"])):
                continue
            try:
                self._close(t, await self.broker.close(t), reason)
            except Exception:
                log.exception("No se pudo cerrar %s: revisa la posición en el exchange", t["symbol"])
