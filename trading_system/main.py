"""Orquestador asíncrono: un bucle por agente, el bucle del jefe y el optimizador semanal.

    python main.py          # arranca el motor
    python main.py --once   # un solo ciclo completo y sale (para comprobar la instalación)
"""
import asyncio
import logging
import logging.handlers
import os
import sys
import threading
import time

import ccxt

import config
import optimizer
from agents import macro_agent
from agents.boss_agent import BossAgent
from agents.evolution_manager import EvolutionManager
from agents.historian_agent import HistorianAgent
from agents.orb_agent import ORBAgent
from agents.macro_agent import MacroAgent
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent import SMCAgent
from agents.wyckoff_agent import WyckoffAgent
from utils import db, notifier, token_budget, trade_logger
from utils.exchange import CcxtBroker, Market, PaperBroker

log = logging.getLogger("main")
# Fallos del exchange o de la red, no del agente: no son motivo de muerte.
TRANSIENT = (ccxt.NetworkError, asyncio.TimeoutError, TimeoutError, ConnectionError)


class Stop(Exception):
    """Parada ordenada pedida desde la app."""


async def run_agent(agent, symbol: str, market, evo: EvolutionManager) -> None:
    key = f"{agent.name}:{symbol}"
    if not evo.alive(agent.name):  # muerto y a la espera de su sucesor: no vota
        db.set_state(f"status:{key}", {"state": "muerto", "ts": time.time()})
        return
    if agent.name in db.get_state("ctl:bench", []):  # el operador lo mandó al banquillo: ni analiza ni vota
        db.del_state(f"signal:{key}")
        db.set_state(f"status:{key}", {"state": "banquillo", "ts": time.time()})
        return
    db.set_state(f"status:{key}", {"state": "analizando", "ts": time.time()})
    try:
        sig = evo.apply(await agent.run(symbol, market))
        prev = db.get_state(f"signal:{key}")
        if not prev or token_budget.unpack(prev).direction != sig.direction:  # cambió de opinión: a la bitácora
            trade_logger.log_event("signal", agent.name, f"{sig.direction} {sig.confidence} · {sig.rationale}",
                                   symbol, sig.confidence)
        db.set_state(f"signal:{key}", token_budget.pack(sig))  # lo que lee el jefe: JSON minificado
        state, error = "descanso", None
    except TRANSIENT as e:
        # La señal anterior se queda y caduca sola: el agente deja de votar.
        log.warning("Agente %s sin datos en %s: %s", agent.name, symbol, type(e).__name__)
        state, error = "error", f"{type(e).__name__}: {e}"[:200]
    except Exception as e:
        log.exception("Agente %s falló en %s", agent.name, symbol)
        state, error = "error", f"{type(e).__name__}: {e}"[:200]
        evo.on_error(agent.name, e)
    db.set_state(f"status:{key}", {"state": state, "error": error, "ts": time.time(),
                                   "next": time.time() + agent.interval})


async def agent_loop(agent, market, evo) -> None:
    while True:
        for symbol in config.SYMBOLS:
            await run_agent(agent, symbol, market, evo)
        await asyncio.sleep(agent.interval)


async def boss_loop(boss: BossAgent) -> None:
    while True:
        try:
            await boss.tick()
            db.del_state("boss:error")
        except Exception as e:
            log.exception("Ciclo del jefe fallido; se reintenta")
            db.set_state("boss:error", {"ts": time.time(), "error": type(e).__name__})  # la app lo enseña
        for _ in range(max(1, int(config.BOSS_INTERVAL))):  # la parada llega entre ciclos, nunca a mitad de una orden
            if db.get_state("ctl:engine") == "stop":
                raise Stop
            await asyncio.sleep(1)


async def alive_loop() -> None:
    """Latido: la app sabe que el motor está encendido y un segundo motor se niega a arrancar."""
    while True:
        db.set_state("engine", {"pid": os.getpid(), "ts": time.time()})
        await asyncio.sleep(3)


async def optimizer_loop() -> None:
    if db.get_state("optimizer:last_run") is None:
        db.set_state("optimizer:last_run", time.time())  # la primera semana empieza hoy
    while True:
        if time.time() - db.get_state("optimizer:last_run", 0) >= 7 * 86400:
            rep = optimizer.run()
            log.info("Optimizador semanal: %s", rep["metrics"])
        await asyncio.sleep(3600)


async def main(once: bool = False) -> None:
    engine = db.get_state("engine")
    if engine and time.time() - engine["ts"] < 20:
        # ponytail: el candado es el latido. Techo: dos arranques en el mismo instante se cuelan. Mejora: bloqueo de archivo.
        raise SystemExit("Ya hay un motor en marcha. Apágalo desde la app, o espera 20 segundos si se cerró mal.")
    db.set_state("engine", {"pid": os.getpid(), "ts": time.time()})
    db.del_state("ctl:engine")  # una orden de parada vieja no apaga un motor nuevo
    try:
        await _main(once)
    finally:
        db.del_state("engine")


async def _main(once: bool) -> None:
    if config.BROKER == "topstepx":
        from utils.topstepx import TopstepBroker, TopstepMarket, TopstepPaperBroker
        market = TopstepMarket()
        make_broker = TopstepPaperBroker if config.PAPER_TRADING else TopstepBroker
    else:
        market = Market()
        make_broker = PaperBroker if config.PAPER_TRADING else CcxtBroker
    try:
        await market.start()
        # Carga de SQLite la versión más alta de cada agente, con sus reglas.
        evo = EvolutionManager(notify=notifier.send, base_prompts={"macro": macro_agent.SYSTEM})
        broker = make_broker(market)
        if config.STRATEGY == "orb":
            agents = [ORBAgent()]
        else:
            agents = [HistorianAgent(), MacroAgent(system=lambda: evo.instructions("macro")), SMCAgent(),
                      WyckoffAgent(), SentimentAgent()]
        boss = BossAgent(market, broker, {a.name: 3 * a.interval + 60 for a in agents}, evo, notifier.send)
        log.info("%s en %s · %s · riesgo %.1f%%/op · comisión %.3f%% + deslizamiento %.3f%% por lado · Claude %s",
                 {"paper": "PAPER TRADING", "sandbox": "SANDBOX", "live": "REAL TRADING"}[config.EXECUTION_MODE],
                 "TopstepX" if config.BROKER == "topstepx" else config.EXCHANGE_ID, ", ".join(config.SYMBOLS), config.RISK_PER_TRADE_PCT, config.FEE_PCT,
                 config.SLIPPAGE_PCT,
                 config.CLAUDE_MODEL if config.USE_CLAUDE else "desactivado (sin ANTHROPIC_API_KEY)")
        log.info("Generaciones activas: %s", ", ".join(g["name"] for g in evo.active.values()))
        if once:
            for agent in agents:
                for symbol in config.SYMBOLS:
                    await run_agent(agent, symbol, market, evo)
            await boss.tick()
            await evo.drain()
            for symbol in config.SYMBOLS:
                b = db.get_state(f"boss:{symbol}")
                print(f"\n{symbol}: {b['action']} (score {b['score']:+.2f}) · {b['reason']} · {b['status']}")
                for v in b["votes"].values():
                    print(f"  {v['version']:<22} {v['direction']:<8} {v['confidence']:>3}  {v['rationale']}")
            return
        await asyncio.gather(*(agent_loop(a, market, evo) for a in agents), boss_loop(boss), optimizer_loop(),
                             alive_loop())
    finally:
        await market.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),  # auditoría: consola y trading.log
                                  logging.handlers.RotatingFileHandler(
                                      config.LOG_FILE, maxBytes=20_000_000, backupCount=10, encoding="utf-8")])
    if "--child" in sys.argv:
        # Arrancado por la app, que le deja una tubería abierta: si la app se cierra, la tubería
        # se cierra y el motor se apaga en orden en vez de seguir operando sin que nadie lo vea.
        threading.Thread(target=lambda: (sys.stdin.read(), db.set_state("ctl:engine", "stop")), daemon=True).start()
    try:
        asyncio.run(main(once="--once" in sys.argv))
    except KeyboardInterrupt:
        print("\nMotor detenido. Las posiciones abiertas siguen con su SL/TP.")
    except Stop:
        log.info("Motor apagado desde la app.")
