"""Orquestador asíncrono: un bucle por agente, el bucle del jefe y el optimizador semanal.

    python main.py          # arranca el motor
    python main.py --once   # un solo ciclo completo y sale (para comprobar la instalación)
"""
import asyncio
import logging
import sys
import time

import config
import optimizer
from agents.boss_agent import BossAgent
from agents.historian_agent import HistorianAgent
from agents.macro_agent import MacroAgent
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent import SMCAgent
from agents.wyckoff_agent import WyckoffAgent
from utils import cache
from utils.exchange import CcxtBroker, Market, PaperBroker

log = logging.getLogger("main")


async def run_agent(agent, symbol: str, market) -> None:
    key = f"{agent.name}:{symbol}"
    cache.set_state(f"status:{key}", {"state": "analizando", "ts": time.time()})
    try:
        cache.set_state(f"signal:{key}", (await agent.run(symbol, market)).model_dump())
        state, error = "descanso", None
    except Exception as e:
        # La señal anterior se queda y caduca sola: un agente caído deja de
        # votar, no vota basura.
        log.warning("Agente %s falló en %s: %s: %s", agent.name, symbol, type(e).__name__, e)
        state, error = "error", f"{type(e).__name__}: {e}"[:200]
    cache.set_state(f"status:{key}", {"state": state, "error": error, "ts": time.time(),
                                      "next": time.time() + agent.interval})


async def agent_loop(agent, market) -> None:
    while True:
        for symbol in config.SYMBOLS:
            await run_agent(agent, symbol, market)
        await asyncio.sleep(agent.interval)


async def boss_loop(boss: BossAgent) -> None:
    while True:
        try:
            await boss.tick()
        except Exception:
            log.exception("Ciclo del jefe fallido; se reintenta")
        await asyncio.sleep(config.BOSS_INTERVAL)


async def optimizer_loop() -> None:
    if cache.get_state("optimizer:last_run") is None:
        cache.set_state("optimizer:last_run", time.time())  # la primera semana empieza hoy
    while True:
        if time.time() - cache.get_state("optimizer:last_run", 0) >= 7 * 86400:
            rep = optimizer.run()
            log.info("Optimizador semanal: %s", rep["metrics"])
        await asyncio.sleep(3600)


async def main(once: bool = False) -> None:
    market = Market()
    try:
        await market.ex.load_markets()
        missing = [s for s in config.SYMBOLS if s not in market.ex.markets]
        if missing:
            raise SystemExit(f"Símbolos desconocidos en {config.EXCHANGE_ID}: {missing}")
        broker = PaperBroker(market) if config.EXECUTION_MODE == "paper" else CcxtBroker(market)
        agents = [HistorianAgent(), MacroAgent(), SMCAgent(), WyckoffAgent(), SentimentAgent()]
        boss = BossAgent(market, broker, {a.name: 3 * a.interval + 60 for a in agents})
        log.info("Modo %s en %s · %s · riesgo %.1f%%/op · Claude %s", config.EXECUTION_MODE.upper(),
                 config.EXCHANGE_ID, ", ".join(config.SYMBOLS), config.RISK_PER_TRADE_PCT,
                 config.CLAUDE_MODEL if config.USE_CLAUDE else "desactivado (sin ANTHROPIC_API_KEY)")
        if once:
            for agent in agents:
                for symbol in config.SYMBOLS:
                    await run_agent(agent, symbol, market)
            await boss.tick()
            for symbol in config.SYMBOLS:
                b = cache.get_state(f"boss:{symbol}")
                print(f"\n{symbol}: {b['action']} (score {b['score']:+.2f}) · {b['reason']} · {b['status']}")
                for name, v in b["votes"].items():
                    print(f"  {name:<10} {v['direction']:<8} {v['confidence']:>3}  {v['rationale']}")
            return
        await asyncio.gather(*(agent_loop(a, market) for a in agents), boss_loop(boss), optimizer_loop())
    finally:
        await market.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main(once="--once" in sys.argv))
    except KeyboardInterrupt:
        print("\nMotor detenido. Las posiciones abiertas siguen con su SL/TP.")
