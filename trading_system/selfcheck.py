"""Comprobación de extremo a extremo, sin red ni claves:  python selfcheck.py

Usa una base de datos temporal y mercados falsos. Si algo de la lógica de
riesgo, costes, decisión, ejecución, evolución o aprendizaje se rompe, este
script falla.

Para iterar deprisa se le pueden dar nombres (`python selfcheck.py optimizer app`)
y ejecuta solo esas comprobaciones; esa pasada parcial no sustituye a la completa.
"""
import asyncio
import datetime as dt
import http.server
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import types

TMP = tempfile.mkdtemp()
os.environ.update(
    DB_PATH=os.path.join(TMP, "check.db"), CACHE_DIR=os.path.join(TMP, "cache"), PAPER_TRADING="true",
    SYMBOLS="BTC/USDT:USDT", SLIPPAGE_PCT="0.02", MAX_RULES="8", DAILY_TOKEN_BUDGET="200000",
    PAYLOAD_TOKEN_LIMIT="150", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="", DISCORD_WEBHOOK_URL="",
    CAPITAL="3550", RISK_PER_TRADE_PCT="1", MAX_SL_PCT="5", ATR_SL_MULT="1.5", REWARD_RISK="2",
    MAX_LEVERAGE="3", MAX_OPEN_POSITIONS="2", MIN_NOTIONAL="10", DAILY_LOSS_LIMIT_PCT="3",
    TOTAL_LOSS_LIMIT_USD="500", DECISION_THRESHOLD="0.25", MIN_AGENTS="3", COOLDOWN_S="900",
    FEE_PCT="0.075", START_MODE="AUTO", NEWS_RSS_URLS="", ALPHAVANTAGE_API_KEY="prueba",
    ANTHROPIC_API_KEY="", ANTHROPIC_AUTH_TOKEN="", MACRO_EVENTS_FILE=os.path.join(TMP, "eventos.csv"),
    PUBLIC_DATA_DIR=os.path.join(TMP, "public"))
os.makedirs(os.path.join(TMP, "public"))
with open(os.path.join(TMP, "public", "fomc.json"), "w") as _f:  # copia reciente del calendario: nada sale a la red
    _f.write('["2030-01-30"]')

import numpy as np
import pandas as pd

import backtest
import config
import main as engine
import optimizer
from agents import AGENT_LABELS, Signal
from agents.boss_agent import BossAgent
from agents.evolution_manager import EvolutionManager, Mutation
from agents.historian_agent import HistorianAgent
from agents.orb_agent import ORBAgent
from agents import orb_agent
from agents.macro_agent import MacroAgent, MacroOutput, Verdict, upcoming_event
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent import SMCAgent
from agents.wyckoff_agent import WyckoffAgent
from utils import alpha_vantage, cache_manager, db, notifier, public_data, token_budget, trade_logger
from utils import risk_manager as risk
from utils.exchange import CcxtBroker, Market, PaperBroker, make_exchange
from utils.topstepx import (TopstepBroker, TopstepClient, TopstepError, TopstepMarket,
                            TopstepPaperBroker)

SYM = "BTC/USDT:USDT"
rng = np.random.default_rng(7)


def candles(closes, volume=None, wick=0.001, step=3600):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    ts = pd.to_datetime(time.time() - step * np.arange(len(c))[::-1], unit="s", utc=True)
    return pd.DataFrame({"ts": ts, "open": o, "high": np.maximum(o, c) * (1 + wick),
                         "low": np.minimum(o, c) * (1 - wick), "close": c,
                         "volume": np.ones(len(c)) if volume is None else volume})


def trend(drift, n=500, noise=0.004):
    return candles(100 * np.exp(np.cumsum(drift + rng.normal(0, noise, n))))


def sig(agent, direction, conf, **kw):
    return Signal(agent=agent, symbol=SYM, direction=direction, confidence=conf, rationale="prueba", **kw)


def check_risk():
    # Costes: 0,075 % de comisión y 0,02 % de deslizamiento por lado.
    assert abs(risk.fee(100.0, 2) - 0.15) < 1e-12 and abs(risk.round_trip_cost_pct() - 0.19) < 1e-12
    assert abs(risk.slip(100.0, True) - 100.02) < 1e-9 and abs(risk.slip(100.0, False) - 99.98) < 1e-9
    p = risk.build_plan("LONG", 100.0, 1.0, 3550)
    assert p["sl"] == 98.5 and p["tp"] == 103.0 and abs(p["qty"] - 35.5 / 1.69) < 1e-9, p
    assert abs(p["risk_usd"] - 35.5) < 1e-9 and abs(p["cost_est"] - p["qty"] * 0.19) < 1e-9  # el 1 % ya incluye costes
    p = risk.build_plan("SHORT", 100.0, 10.0, 3550)  # ATR enorme: el stop se limita al 5 %
    assert abs(p["sl"] - 105) < 1e-9 and abs(p["tp"] - 90) < 1e-9 and p["risk_usd"] <= 35.5 + 1e-9, p
    p = risk.build_plan("LONG", 100.0, 0.01, 3550)  # stop minúsculo: manda el tope de apalancamiento
    assert abs(p["leverage"] - 3) < 1e-9 and p["risk_usd"] < 35.5, p
    p = risk.build_plan("LONG", 100.0, 1.0, 3550, structure=98.9)  # stop bajo el soporte + 0,2 ATR
    assert abs(p["sl"] - 98.7) < 1e-9, p
    assert risk.build_plan("LONG", 100.0, 1.0, 3550, structure=90)["sl"] == 98.5  # soporte lejano: vuelve al ATR
    for bad in [("LONG", 100, 0, 3550), ("LONG", 100, float("nan"), 3550), ("LONG", 100, 1, 0),
                ("NEUTRAL", 100, 1, 3550), ("LONG", 100, 1, 5)]:  # el último: nocional bajo el mínimo
        assert risk.build_plan(*bad) is None, bad
    assert risk.check_limits(3550, 3550, 3550) is None
    assert risk.check_limits(3443, 3550, 3550)[0] == "daily"
    assert risk.check_limits(3040, 3050, 3550)[0] == "total"
    # Los candados de config no se pueden saltar desde el entorno.
    run = lambda **env: subprocess.run(
        [sys.executable, "-c", "import config; print(config.RISK_PER_TRADE_PCT, config.EXECUTION_MODE)"],
        cwd=config.ROOT, env=os.environ | env, capture_output=True, text=True)
    assert risk.PAPER_TRADING is True
    assert run(RISK_PER_TRADE_PCT="50").stdout.split() == ["2.0", "paper"]
    assert run(PAPER_TRADING="Flase").stdout.split()[1] == "paper"  # una errata no activa el dinero real
    assert run(PAPER_TRADING="false").stdout.split()[1] == "sandbox"
    assert run(PAPER_TRADING="false", EXCHANGE_SANDBOX="false", CONFIRM_LIVE="").returncode != 0
    live = run(PAPER_TRADING="false", EXCHANGE_SANDBOX="false", CONFIRM_LIVE="SI_ARRIESGO_DINERO_REAL")
    assert live.returncode == 0 and live.stdout.split()[1] == "live"


def check_agents():
    up, down, flat = trend(0.003), trend(-0.003), trend(0.0)
    for df in (up, down, flat, candles(np.full(400, 100.0))):  # ningún dato rompe a ningún agente
        for s in (HistorianAgent.analyze(SYM, df), SMCAgent.analyze(SYM, df), WyckoffAgent.analyze(SYM, df),
                  SentimentAgent.analyze(SYM, df, df, None)):
            assert 0 <= s.confidence <= 100 and "\n" not in s.rationale and len(s.rationale) <= 160
            back = token_budget.unpack(token_budget.pack(s))  # el mensaje al jefe: compacto y sin pérdida
            assert token_budget.estimate(token_budget.pack(s)) <= 150
            assert (back.direction, back.confidence, back.agent, set(back.features)) == \
                (s.direction, s.confidence, s.agent, set(s.features))
    assert HistorianAgent.analyze(SYM, up).direction == "LONG"
    assert HistorianAgent.analyze(SYM, down).direction == "SHORT"
    assert SentimentAgent.analyze(SYM, up, up, 0.3).direction == "LONG"
    assert SentimentAgent.analyze(SYM, down, down, -0.3).direction == "SHORT"
    assert WyckoffAgent.analyze(SYM, up).direction == "LONG" and WyckoffAgent.analyze(SYM, down).direction == "SHORT"
    assert HistorianAgent.analyze(SYM, up.iloc[:50]).direction == "NEUTRAL"  # histórico corto

    # Perfil de volumen: el POC cae donde se negoció el volumen.
    vp = WyckoffAgent.volume_profile(candles(np.r_[np.full(100, 100.0), np.full(100, 110.0)],
                                             volume=np.r_[np.full(100, 10.0), np.full(100, 1.0)]))
    assert abs(vp["poc"] - 100) < 1 and vp["val"] <= vp["poc"] <= vp["vah"], vp

    # SMC: mínimo roto (bajista) y después ruptura del último máximo => CHoCH alcista.
    legs = [(100, 96, 12), (96, 99, 10), (99, 94, 12), (94, 97.5, 10), (97.5, 95.5, 8), (95.5, 101, 12)]
    path = np.concatenate([np.linspace(a, b, n, endpoint=False) for a, b, n in legs] + [[101.0]])
    s = SMCAgent.analyze(SYM, candles(path, step=300))
    assert s.direction == "LONG" and "CHoCH alcista" in s.rationale and s.support < 97.5, s
    assert s.features["fvg"] == 1 and "FVG" in s.rationale and s.features["choch"] == 1, s  # el impulso deja hueco
    s = SMCAgent.analyze(SYM, candles(200 - path, step=300))  # espejo
    assert s.direction == "SHORT" and "CHoCH bajista" in s.rationale and s.resistance > 102.5, s


def check_boss_decision():
    ages = {n: 600 for n in AGENT_LABELS}
    votes = [sig("historian", "LONG", 80), sig("smc", "LONG", 70), sig("wyckoff", "LONG", 60),
             sig("sentiment", "NEUTRAL", 0), sig("macro", "NEUTRAL", 0)]
    d = BossAgent.decide(SYM, votes, {}, ages)
    assert d.action == "LONG" and abs(d.score - 0.42) < 1e-9 and len(d.votes) == 5, d
    assert BossAgent.decide(SYM, votes[:4] + [sig("macro", "NEUTRAL", 0, veto=True)], {}, ages).action == "NEUTRAL"
    old = [s.model_copy(update={"ts": time.time() - 10_000}) for s in votes[:3]] + votes[3:]
    d = BossAgent.decide(SYM, old, {}, ages)  # señales caducadas no votan
    assert d.action == "NEUTRAL" and "solo 2" in d.reason, d
    split = [sig("historian", "LONG", 60), sig("smc", "SHORT", 60), sig("wyckoff", "LONG", 30),
             sig("sentiment", "NEUTRAL", 0), sig("macro", "NEUTRAL", 0)]
    assert BossAgent.decide(SYM, split, {}, ages).action == "NEUTRAL"
    w = {"historian": {SYM: 2.0}, "smc": {SYM: 0.25}}  # los pesos aprendidos cambian el resultado
    assert BossAgent.decide(SYM, split, w, ages).action == "LONG"


class FakeMarket:
    def __init__(self):
        self.p = 100.0
        self.df = pd.DataFrame({"ts": pd.to_datetime(np.arange(120), unit="s", utc=True), "open": 100.0,
                                "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0})  # ATR = 1

    async def price(self, symbol):
        return self.p

    async def ohlcv(self, symbol, tf, limit=300):
        if tf != "1m":
            return self.df
        return pd.DataFrame({"ts": [pd.Timestamp.now(tz="UTC")], "open": self.p, "high": self.p, "low": self.p,
                             "close": self.p, "volume": 1.0})


async def check_lifecycle():
    fm, notes = FakeMarket(), []
    evo = EvolutionManager(notify=notes.append)
    boss = BossAgent(fm, PaperBroker(fm), {n: 600 for n in AGENT_LABELS}, evo, notes.append)
    status = lambda: db.get_state(f"boss:{SYM}")["status"]
    gens = lambda: {g["version"] for g in evo.active.values()}
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()

    def publish():  # cada agente manda al jefe su JSON minificado, sellado con su versión
        for name in AGENT_LABELS:
            db.set_state(f"signal:{name}:{SYM}", token_budget.pack(evo.apply(sig(name, "LONG", 80))))

    assert gens() == {1} and evo.active["smc"]["name"] == "SMC_Scalper_v1"
    publish()
    await boss.tick()
    (t,) = trade_logger.open_trades()
    qty = 35.5 / (1.5 + 100 * 0.0019)  # el 1 % de riesgo ya descuenta comisiones y deslizamiento
    assert status() == "orden ejecutada" and t["side"] == "LONG" and t["sl"] == 98.5 and t["tp"] == 103.0, t
    assert abs(t["entry"] - 100.02) < 1e-9 and abs(t["qty"] - qty) < 1e-9  # entra peor que el precio visto
    assert abs(t["slippage"] - 0.02 * qty) < 1e-9 and abs(t["fees"] - 100.02 * qty * 0.00075) < 1e-9
    assert set(t["votes"]) == set(AGENT_LABELS) and t["votes"]["smc"]["version"] == "SMC_Scalper_v1"
    assert any(n.startswith("📈 ABIERTA LONG") for n in notes), notes
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1 and status() == "posición ya abierta"
    tok = token_budget.today()
    assert tok["cycles"] == 2 and 0 < tok["last_cycle"] <= 5 * 150 and tok["payload"] == 2 * tok["last_cycle"]

    fm.p = 103.5  # toca el take profit (orden límite: sin deslizamiento)
    await boss.tick()
    await evo.drain()
    (c,) = trade_logger.closed_trades()
    fees = (100.02 + 103) * qty * 0.00075
    assert c["exit_reason"] == "TP" and c["exit"] == 103.0 and abs(c["pnl"] - ((103 - 100.02) * qty - fees)) < 1e-9, c
    assert gens() == {1}  # ganar no mata a nadie
    assert any("CERRADA LONG" in n and "PnL neto +" in n for n in notes)
    assert status() == "enfriamiento tras el último cierre" and not trade_logger.open_trades()
    assert abs(db.get_state("account")["equity"] - (3550 + c["pnl"])) < 1e-9

    config.COOLDOWN_S = 0
    db.set_state("ctl:mode", "MANUAL")  # en manual propone y espera
    await boss.tick()
    b = db.get_state(f"boss:{SYM}")
    assert b["status"] == "esperando aprobación manual" and b["plan"]["side"] == "LONG" and not trade_logger.open_trades()
    db.set_state(f"approve:{SYM}", time.time())
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1 and db.get_state(f"approve:{SYM}") == 0  # la aprobación se consume
    db.set_state("ctl:mode", "AUTO")

    # Stop loss: pérdida neta ~1 % del capital, y mueren los cinco agentes que la recomendaron.
    equity = db.get_state("account")["equity"]
    fm.p = 90.0
    await boss.tick()
    assert not any(evo.alive(a) for a in AGENT_LABELS)  # 💀 en el acto...
    assert db.get_state(f"signal:smc:{SYM}") is None  # ...y un muerto no vota
    b = db.get_state(f"boss:{SYM}")
    assert b["action"] == "NEUTRAL" and "solo 0 agentes" in b["reason"] and not trade_logger.open_trades()
    await evo.drain()
    loss = trade_logger.closed_trades()[-1]
    assert loss["exit_reason"] == "SL" and abs(loss["exit"] - 102 * 0.9998) < 1e-9  # el stop también desliza
    assert 0.0097 * equity < -loss["pnl"] < 0.0103 * equity, loss["pnl"]
    assert gens() == {2} and all(evo.alive(a) for a in AGENT_LABELS)  # ...y renacen como v2
    rule = evo.active["smc"]["rules"][0]
    assert (rule["feature"], rule["op"], rule["value"], rule["direction"], rule["penalty"]) == ("conf", "<", 85, "LONG", 15)
    assert sum("BOT_DEATH" in n for n in notes) == len(AGENT_LABELS) == sum("REBIRTH" in n for n in notes)
    assert any("SMC_Scalper_v1 → SMC_Scalper_v2" in n for n in notes)

    publish()  # la v2 aplica la lección: la misma señal llega con menos confianza
    s2 = token_budget.unpack(db.get_state(f"signal:smc:{SYM}"))
    assert (s2.version, s2.confidence, s2.direction) == ("SMC_Scalper_v2", 65, "LONG") and "[reglas -15]" in s2.rationale
    fm.p = 100.0
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1

    # Límite diario: bloquea, cierra lo abierto y sigue bloqueado el resto del día.
    key = f"day_start:paper:{today}"
    real_start = db.get_state(key)
    db.set_state(key, db.get_state("account")["equity"] * 1.05)  # como si el día hubiera empezado un 5 % más arriba
    await boss.tick()
    assert not trade_logger.open_trades() and trade_logger.closed_trades()[-1]["exit_reason"] == "LIMITE"
    assert status().startswith("bloqueado: pérdida del día"), status()
    db.set_state(key, real_start)
    await boss.tick()
    assert status().startswith("bloqueado") and not trade_logger.open_trades()
    db.del_state(f"halt:paper:{today}")

    # Kill switch manual, y reanudación.
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1
    db.set_state("ctl:kill", {"on": True, "flatten": True, "reason": "kill switch manual"})
    await boss.tick()
    assert not trade_logger.open_trades() and trade_logger.closed_trades()[-1]["exit_reason"] == "KILL"
    assert status() == "bloqueado: kill switch manual"
    db.set_state("ctl:kill", {"on": False})

    # Límite de pérdida total: activa el kill switch por sí solo.
    db.set_state("start_equity:paper", db.get_state("account")["equity"] + 600)
    await boss.tick()
    kill = db.get_state("ctl:kill")
    assert kill["on"] and "pérdida total" in kill["reason"] and not trade_logger.open_trades(), kill
    db.set_state("ctl:kill", {"on": False})
    db.del_state("start_equity:paper")
    await evo.drain()
    assert gens() == {2}  # cierres por límite o por kill switch (aunque pierdan) no matan a nadie


class FakeClaude:
    """Cliente falso de Anthropic: devuelve lo que se le diga y cuenta las llamadas."""

    def __init__(self, output=None, stop="end_turn"):
        self.n, self.stop, self.output = 0, stop, output
        self.messages = types.SimpleNamespace(parse=self.parse)

    async def parse(self, **kw):
        self.n, self.kw = self.n + 1, kw
        return types.SimpleNamespace(stop_reason=self.stop, parsed_output=self.output,
                                     usage=types.SimpleNamespace(input_tokens=100, output_tokens=50))


async def check_evolution():
    notes = []
    evo = EvolutionManager(notify=notes.append)  # "reinicio": todo sale de trades_history.db
    assert {g["version"] for g in evo.active.values()} == {2}  # no vuelve a empezar en v1
    assert evo.active["smc"]["name"] == "SMC_Scalper_v2" and len(evo.active["smc"]["rules"]) == 1
    assert "Lecciones de tus versiones anteriores" in evo.instructions("smc")
    ver = lambda a: evo.active[a]["version"]

    def vote(agent, direction, version=None, **features):
        return {"direction": direction, "confidence": 70, "rationale": "x", "features": features,
                "version": version or evo.active[agent]["name"]}

    def closed(votes, reason="SL", pnl=-30.0):
        return {"id": 99, "symbol": SYM, "side": "LONG", "entry": 100, "exit": 98, "sl": 98, "tp": 104, "pnl": pnl,
                "fees": 1.0, "slippage": 0.5, "exit_reason": reason, "votes": votes}

    # Sobreviven: quien votó en contra, quien se abstuvo y las versiones ya sustituidas.
    evo.on_trade_closed(closed({"smc": vote("smc", "SHORT"), "wyckoff": vote("wyckoff", "NEUTRAL"),
                                "historian": vote("historian", "LONG", version="Market_Historian_v1")}))
    evo.on_trade_closed(closed({"smc": vote("smc", "LONG")}, reason="KILL"))
    evo.on_trade_closed(closed({"smc": vote("smc", "LONG")}, reason="TP", pnl=5.0))
    await evo.drain()
    assert {g["version"] for g in evo.active.values()} == {2}
    # Muere: salió por TP pero los costes dejaron el PnL NETO en negativo.
    evo.on_trade_closed(closed({"smc": vote("smc", "LONG")}, reason="TP", pnl=-0.4))
    await evo.drain()
    assert ver("smc") == 3 and evo.alive("smc")

    # Error de código: muere y renace; el mismo error no vuelve a matar (evita un bucle de versiones).
    evo.on_error("wyckoff", ValueError("división por cero"))
    assert not evo.alive("wyckoff")
    await evo.drain()
    r = evo.active["wyckoff"]["rules"][-1]
    assert ver("wyckoff") == 3 and r["feature"] is None and "división por cero" in r["why"]
    evo.on_error("wyckoff", ValueError("división por cero"))
    await evo.drain()
    assert ver("wyckoff") == 3
    evo.on_error("wyckoff", KeyError("close"))
    await evo.drain()
    assert ver("wyckoff") == 4

    # Post-mortem con Claude: la regla medible se valida antes de aceptarla.
    mut = lambda **kw: Mutation(**({"root_cause": "RSI 4H en sobrecompra", "lesson": "No comprar con RSI 4H > 75",
                                    "feature": "rsi4", "op": ">", "value": 75, "penalty": 90} | kw))
    fake = FakeClaude(mut())
    evo = EvolutionManager(client=fake, notify=notes.append)
    evo.on_trade_closed(closed({"sentiment": vote("sentiment", "LONG", rsi4=91.0, s4=0.7)}))
    await evo.drain()
    r = evo.active["sentiment"]["rules"][-1]
    assert (r["feature"], r["op"], r["value"], r["direction"], r["penalty"]) == ("rsi4", ">", 75, "LONG", 60), r
    assert r["why"] == "No comprar con RSI 4H > 75" and r["from"] == "Trend_Sentiment_v2"
    sent = json.loads(fake.kw["messages"][0]["content"])
    assert sent["senal"]["features"] == {"rsi4": 91.0, "s4": 0.7, "conf": 70} and fake.kw["model"] == config.CLAUDE_MODEL
    assert token_budget.today()["by_kind"]["evolution"] == 150  # el gasto real queda auditado
    s = lambda d, rsi: evo.apply(Signal(agent="sentiment", symbol=SYM, direction=d, confidence=70, rationale="x",
                                        features={"rsi4": rsi}))
    hot, cool, short = s("LONG", 80.0), s("LONG", 60.0), s("SHORT", 80.0)
    assert (hot.direction, hot.confidence) == ("NEUTRAL", 0)  # -60 de Claude y -15 de la v1: no repite el error
    assert (cool.direction, cool.confidence) == ("LONG", 55) and (short.direction, short.confidence) == ("SHORT", 70)
    assert hot.version == "Trend_Sentiment_v3"
    for bad in (mut(feature="inventada"), mut(value=95)):  # feature inexistente / regla que no habría evitado la pérdida
        fake.output = bad
        evo.on_trade_closed(closed({"sentiment": vote("sentiment", "LONG", rsi4=91.0)}))
        await evo.drain()
        assert evo.active["sentiment"]["rules"][-1]["feature"] is None
    config.DAILY_TOKEN_BUDGET, calls = 0, fake.n  # presupuesto agotado: muta sin gastar tokens
    evo.on_trade_closed(closed({"sentiment": vote("sentiment", "LONG", rsi4=91.0)}))
    await evo.drain()
    assert fake.n == calls and evo.active["sentiment"]["rules"][-1]["feature"] == "conf"
    config.DAILY_TOKEN_BUDGET = 200_000

    for _ in range(10):  # las lecciones no crecen sin fin: se conservan las últimas MAX_RULES
        evo.on_trade_closed(closed({"macro": vote("macro", "LONG")}))
        await evo.drain()
    assert ver("macro") == 12 and len(evo.active["macro"]["rules"]) == 8

    # Corte entre la muerte y el renacimiento: al arrancar se completa.
    g = evo.active["historian"]
    trade_logger.mark_dead(g["id"], "ERROR", "corte de luz")
    again = EvolutionManager()
    assert again.active["historian"]["version"] == g["version"] + 1 and again.alive("historian")
    assert again.active["macro"]["version"] == 12 and again.active["sentiment"]["rules"] == evo.active["sentiment"]["rules"]

    tree = [r for r in trade_logger.genealogy() if r["agent"] == "smc"]
    assert [r["name"] for r in tree] == ["SMC_Scalper_v1", "SMC_Scalper_v2", "SMC_Scalper_v3"]
    assert [r["status"] for r in tree] == ["dead", "dead", "alive"] and all(r["post_mortem"] for r in tree[:2])
    assert tree[1]["parent_id"] == tree[0]["id"] and "PnL neto" in tree[0]["death_cause"]
    assert any("BOT_DEATH · SMC_Scalper_v2 (LOSS)" in n for n in notes)


def check_tokens_and_alerts():
    long = Signal(agent="sentiment", symbol=SYM, direction="LONG", confidence=77, rationale="palabra " * 40,
                  support=12345.678912, resistance=23456.789123, version="Trend_Sentiment_v12",
                  features={f"medida_{i}": 1.2345678 * i for i in range(12)})
    packed = token_budget.pack(long)
    assert token_budget.estimate(packed) <= 150 and "\n" not in packed and ": " not in packed
    back = token_budget.unpack(packed)
    assert (back.direction, back.confidence, back.version, back.support) == ("LONG", 77, "Trend_Sentiment_v12", 12345.7)
    before = token_budget.today()["llm"]
    token_budget.record_llm("macro", types.SimpleNamespace(input_tokens=900, output_tokens=100))
    assert token_budget.today()["llm"] == before + 1000 and token_budget.llm_allowed()
    config.DAILY_TOKEN_BUDGET = before + 1000
    assert not token_budget.llm_allowed()  # presupuesto diario agotado: Claude se apaga
    config.DAILY_TOKEN_BUDGET = 200_000

    sent = []

    class Resp:
        def raise_for_status(self):
            pass

    notifier.requests = types.SimpleNamespace(post=lambda url, json, timeout: sent.append((url, json)) or Resp())
    notifier.send("nada configurado")  # sin destinos no llama a nadie
    assert sent == []
    config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID = "123:SECRETO", "42"
    config.DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1/SECRETO"
    notifier.send("💀 BOT_DEATH · SMC_Scalper_v1")
    assert sent[0] == ("https://api.telegram.org/bot123:SECRETO/sendMessage",
                       {"chat_id": "42", "text": "💀 BOT_DEATH · SMC_Scalper_v1"})
    assert sent[1] == (config.DISCORD_WEBHOOK_URL, {"content": "💀 BOT_DEATH · SMC_Scalper_v1"})

    def boom(url, json, timeout):
        raise ConnectionError(f"no se pudo conectar a {url}")

    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    notifier.log.addHandler(handler)
    notifier.requests = types.SimpleNamespace(post=boom)
    notifier.send("aviso")  # un fallo de red no rompe nada...
    assert len(records) == 2 and not any("SECRETO" in m for m in records), records  # ...ni filtra el token al log
    config.TELEGRAM_BOT_TOKEN = config.TELEGRAM_CHAT_ID = config.DISCORD_WEBHOOK_URL = ""


class FakeEx:
    def __init__(self, fail_tp=False):
        self.orders, self.cancelled, self.fail_tp, self.pos, self.filled = [], [], fail_tp, 1.0, None

    def market(self, s):
        return {"contractSize": 0.01, "limits": {"amount": {"min": 1}, "cost": {"min": 5}}}

    def amount_to_precision(self, s, a):
        return str(int(a))

    def price_to_precision(self, s, p):
        return f"{p:.1f}"

    async def set_leverage(self, lev, s):
        self.lev = lev

    async def create_order(self, s, type_, side, amount, price=None, params=None):
        if self.fail_tp and "takeProfitPrice" in (params or {}):
            raise RuntimeError("orden rechazada")
        o = {"id": str(len(self.orders) + 1), "side": side, "amount": amount, "params": params or {},
             "average": None if params else 100.2}
        self.orders.append(o)
        return o

    async def cancel_order(self, oid, s):
        self.cancelled.append(oid)

    async def fetch_positions(self, symbols):
        return [{"symbol": symbols[0], "contracts": self.pos}]

    async def fetch_order(self, oid, s):
        return {"id": oid, "status": "closed" if oid == self.filled else "open", "average": 98.4}

    async def fetch_balance(self):
        return {"total": {"USDT": 1234.5}}


async def check_ccxt_broker():
    async def price(s):
        return 100.0

    ex = FakeEx()
    b = CcxtBroker(types.SimpleNamespace(ex=ex, price=price))
    assert await b.equity() == 1234.5
    fill = await b.open(SYM, "LONG", 2.357, 98.5, 103.0, 100.0)
    entry, sl, tp = ex.orders
    assert (entry["side"], entry["amount"], entry["params"]) == ("buy", 235.0, {}) and ex.lev == 3
    assert sl["side"] == "sell" and sl["params"] == {"stopLossPrice": 98.5, "reduceOnly": True}
    assert tp["params"] == {"takeProfitPrice": 103.0, "reduceOnly": True}
    assert fill["price"] == 100.2 and abs(fill["qty"] - 2.35) < 1e-9 and fill["meta"] == {"sl_id": "2", "tp_id": "3"}
    assert abs(fill["slippage"] - 0.2 * 2.35) < 1e-9 and abs(fill["fee"] - 100.2 * 2.35 * 0.00075) < 1e-9  # deslizamiento real

    t = {"symbol": SYM, "side": "LONG", "qty": 2.35, "sl": 98.5, "tp": 103.0, "meta": fill["meta"]}
    assert await b.check_exit(t) is None  # la posición sigue viva
    ex.pos, ex.filled = 0.0, "2"
    res = await b.check_exit(t)
    assert res["reason"] == "SL" and res["price"] == 98.4 and "3" in ex.cancelled, res

    ex = FakeEx(fail_tp=True)  # si no entra el TP, no se deja la posición sin protección
    try:
        await CcxtBroker(types.SimpleNamespace(ex=ex, price=price)).open(SYM, "SHORT", 2.357, 101.5, 97.0, 100.0)
        raise AssertionError("debía fallar")
    except RuntimeError:
        pass
    assert [o["side"] for o in ex.orders] == ["sell", "buy", "buy"] and ex.cancelled == ["2"]
    assert ex.orders[-1]["params"] == {"reduceOnly": True}
    try:
        await CcxtBroker(types.SimpleNamespace(ex=FakeEx(), price=price)).open(SYM, "LONG", 0.001, 98.5, 103, 100.0)
        raise AssertionError("debía rechazar el tamaño")
    except ValueError:
        pass


async def check_macro():
    now = dt.datetime.now(dt.timezone.utc)
    official = []  # el calendario oficial (Reserva Federal y BLS) bajo control: la hora real no influye
    real_calendar, public_data.calendar = public_data.calendar, lambda days=7: list(official)
    config.MACRO_EVENTS_FILE.write_text(
        f"# fecha UTC, evento, impacto\n{now + dt.timedelta(minutes=10):%Y-%m-%dT%H:%M:%SZ},IPC EEUU,high\n", "utf-8")
    assert upcoming_event() == "IPC EEUU"
    s = await MacroAgent().run(SYM)
    assert s.veto and s.direction == "NEUTRAL"
    config.MACRO_EVENTS_FILE.write_text(f"{now + dt.timedelta(hours=3):%Y-%m-%dT%H:%M:%SZ},FOMC,high\nbasura\n", "utf-8")
    assert upcoming_event() is None
    # Los anuncios oficiales vetan entradas ±BLACKOUT_MIN sin tocar el CSV; fuera de ese margen, no.
    official[:] = [(now + dt.timedelta(minutes=25), "Decisión de tipos de la Reserva Federal (FOMC)")]
    assert upcoming_event() == "Decisión de tipos de la Reserva Federal (FOMC)"
    assert (await MacroAgent().run(SYM)).veto
    official[:] = [(now - dt.timedelta(minutes=31), "IPC de EE. UU. (BLS)"), (now + dt.timedelta(minutes=31), "x")]
    assert upcoming_event() is None
    official[:] = [(now + dt.timedelta(days=2), "IPC de EE. UU. (BLS)")]

    calls = []

    def fake_get(payload):
        def _get(url):
            calls.append(url)
            return json.dumps(payload).encode()
        return _get

    # Respuesta "endpoint premium": sin titulares y SIN reintentar en cada ciclo.
    alpha_vantage._get = fake_get({"Information": "This is a premium endpoint."})
    assert await alpha_vantage.headlines() == [] and await alpha_vantage.headlines() == [] and len(calls) == 1
    quiet = (await MacroAgent().run(SYM)).rationale
    assert "sin titulares" in quiet.lower() and "próximo anuncio oficial: IPC de EE. UU. (BLS)" in quiet, quiet

    cache_manager.clear()
    alpha_vantage._av_last = 0.0
    alpha_vantage._get = fake_get({"feed": [
        {"title": "ETF inflows surge", "source": "X", "time_published": "t", "overall_sentiment_score": 0.1,
         "ticker_sentiment": [{"ticker": "CRYPTO:BTC", "ticker_sentiment_score": "0.4"}]},
        {"title": "Miners accumulate", "source": "Y", "time_published": "t", "overall_sentiment_score": 0.3}]})
    s = await MacroAgent().run(SYM)  # sin Claude: media de puntuaciones de Alpha Vantage
    assert s.direction == "LONG" and s.confidence == 70, s

    verdict = MacroOutput(verdicts=[Verdict(symbol=SYM, direction="SHORT", confidence=65, rationale="hackeo\ngrave")])
    refused = FakeClaude(verdict, stop="refusal")  # negativa: cae al respaldo y no se cachea
    assert (await MacroAgent(refused).run(SYM)).direction == "LONG" and refused.n == 1
    fake = FakeClaude(verdict)
    agent = MacroAgent(fake, system=lambda: "PROMPT DE LA v3")  # el prompt lo dicta el gestor de evolución
    s = await agent.run(SYM)
    assert (s.direction, s.confidence, s.rationale) == ("SHORT", 65, "hackeo grave"), s
    await agent.run(SYM)
    assert fake.n == 1  # mismos titulares: no se vuelve a pagar la llamada
    assert fake.kw["model"] == config.CLAUDE_MODEL and "ETF inflows surge" in fake.kw["messages"][0]["content"]
    assert fake.kw["system"] == "PROMPT DE LA v3" and token_budget.today()["by_kind"]["macro"] >= 150
    agent.system = lambda: "PROMPT DE LA v4"  # si el agente muta, se vuelve a evaluar con el prompt nuevo
    await agent.run(SYM)
    assert fake.n == 2 and fake.kw["system"] == "PROMPT DE LA v4"
    config.DAILY_TOKEN_BUDGET = 0  # sin presupuesto no se llama a Claude: respaldo de Alpha Vantage
    agent.system = lambda: "PROMPT DE LA v5"
    assert (await agent.run(SYM)).direction == "LONG" and fake.n == 2
    config.DAILY_TOKEN_BUDGET = 200_000
    public_data.calendar = real_calendar
    # El calendario real, desde la copia de la Reserva Federal (sin red) y las fechas de la BLS: hora de Nueva York.
    cal = public_data.calendar()
    assert (dt.datetime(2030, 1, 30, 19, 0, tzinfo=dt.timezone.utc), "Decisión de tipos de la Reserva Federal (FOMC)") in cal
    assert (dt.datetime(2026, 12, 10, 13, 30, tzinfo=dt.timezone.utc), "IPC de EE. UU. (BLS)") in cal  # 8:30 en invierno
    assert (dt.datetime(2027, 7, 2, 12, 30, tzinfo=dt.timezone.utc), "Empleo de EE. UU. (BLS)") in cal  # 8:30 en verano


def sessions(today, quiet=6):  # `quiet` sesiones tranquilas (volumen 10 por vela) y la de hoy
    t0, bars = pd.Timestamp("2026-06-10 13:30", tz="UTC"), []
    for d in range(quiet, 0, -1):
        bars += [(t0 - pd.Timedelta(days=d) + pd.Timedelta(minutes=5 * i), 100, 100.5, 99.5, 100, 10) for i in range(6)]
    bars += [(t0 + pd.Timedelta(minutes=5 * i), *r) for i, r in enumerate(today)]
    return pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])


def check_orb():
    """Ruptura del rango de apertura: las reglas de la skill trader-entry, vela a vela."""
    def frame(rows):  # tres velas de ayer, el hueco de la noche y la sesión de hoy en velas de 5 minutos
        t0 = pd.Timestamp("2026-06-01 13:30", tz="UTC")
        bars = [(t0 - pd.Timedelta(days=1) + pd.Timedelta(minutes=5 * i), 100, 100.5, 99.5, 100, 10) for i in range(3)]
        bars += [(t0 + pd.Timedelta(minutes=5 * i), *r) for i, r in enumerate(rows)]
        return pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])

    rng = [(100, 101, 99.5, 100.5, 10), (100.5, 100.8, 99, 100, 10), (100, 100.9, 99.6, 100.4, 10)]  # rango 99-101
    A = lambda rows, **genes: ORBAgent.analyze(SYM, frame(rng + rows), orb_agent.DEFAULT | genes, 300)
    wick, level = (100.4, 102, 100.2, 100.9, 30), (100.4, 101.2, 100.2, 101.0, 30)
    up, down, inside = (100.9, 101.5, 100.8, 101.2, 30), (99.5, 99.6, 98, 98.5, 30), (100.2, 100.6, 100, 100.3, 10)
    assert "formación" in A([]).rationale and A([inside]).direction == "NEUTRAL"
    assert A([wick]).direction == "NEUTRAL" and A([level]).direction == "NEUTRAL"  # ni una mecha ni un cierre en el nivel
    s = A([wick, up])
    assert s.direction == "LONG" and s.support == 100.0 and s.confidence >= 50 and s.features["volx"] == 3.0, s
    assert A([up], stop="atr").support is None  # stop "atr": lo decide el gestor de riesgo
    assert A([up], stop="low").support == 99.0  # el stop puede ir al extremo opuesto del rango
    assert A([up, (101.2, 102, 101, 101.8, 30)]).direction == "NEUTRAL"  # una vela después ya es tarde
    assert A([down]).direction == "NEUTRAL"  # la skill solo opera largos
    s = A([down], sides="both")
    assert s.direction == "SHORT" and s.resistance == 100.0 and A([down], sides="both", stop="low").resistance == 101.0
    assert A([up, inside, down], sides="both").direction == "NEUTRAL"  # solo cuenta la primera ruptura del día
    assert A([(100.9, 101.5, 100.8, 101.2, 10)], vol_x=1.5).direction == "NEUTRAL" and A([up], vol_x=1.5).direction == "LONG"
    assert A([inside] * 3, or_min=30).rationale.startswith("rango") and A([inside] * 3 + [up], or_min=30).direction == "LONG"
    nonstop = frame(rng + [up])
    nonstop["ts"] = pd.date_range("2026-06-01", periods=len(nonstop), freq="5min", tz="UTC")
    assert "continuo" in ORBAgent.analyze(SYM, nonstop, orb_agent.DEFAULT, 300).rationale  # cripto: no hay apertura
    assert len(orb_agent.genomes(300)) == 192 and {g["or_min"] for g in orb_agent.genomes(3600)} == {60}
    # "Acciones en juego": volumen relativo del rango frente a las sesiones anteriores, y sentido de la vela de apertura.
    P = lambda today, quiet=6, **genes: ORBAgent.analyze(SYM, sessions(today, quiet), orb_agent.DEFAULT | genes, 300)
    hot = [(o, h, l, c, 50) for o, h, l, c, _ in rng]  # mismo rango con 5 veces el volumen habitual
    s = P(hot + [up], rvol=2.0)
    assert s.direction == "LONG" and s.features["rvol"] == 5.0 and s.confidence > P(rng + [up]).confidence, s
    assert "en juego" in P(rng + [up], rvol=2.0).rationale  # volumen normal: no opera
    assert P(hot + [up], quiet=3, rvol=1.0).direction == "NEUTRAL"  # sin historia para medirlo, no se da por bueno
    assert P(hot + [up], **{"dir": "candle"}).direction == "LONG"  # la vela de apertura cerró arriba (100 -> 100,4)
    assert P(hot + [down], sides="both", **{"dir": "candle"}).direction == "NEUTRAL"  # y por eso no vende
    bear = [(100.4, 101, 99.5, 100.2, 50), (100.2, 100.8, 99, 100, 50), (100, 100.9, 99.6, 99.8, 50)]  # 100,4 -> 99,8
    assert P(bear + [down], sides="both", **{"dir": "candle"}).direction == "SHORT"
    assert P(bear + [up], sides="both", **{"dir": "candle"}).direction == "NEUTRAL"
    plan = risk.build_plan("LONG", 101.2, 1.0, 3550, structure=100.0)  # stop bajo el punto medio, con 0,2 ATR de margen
    assert abs(plan["sl"] - 99.8) < 1e-9 and abs(plan["tp"] - (101.2 + 2 * 1.4)) < 1e-9, plan


def check_optimizer():
    m = optimizer.metrics([{"pnl": p, "entry": 100, "qty": 1} for p in (10, 10, -5, -5, 20)])
    assert m["n"] == 5 and m["win_rate"] == 0.6 and m["profit_factor"] == 4.0 and m["sharpe"] > 0, m
    assert optimizer.metrics([]) == {"n": 0}

    def trade(win, votes):
        trade_logger.log_open("X/USDT", "LONG", 1, 100, 98, 104, 0.5, votes)
        t = [x for x in trade_logger.open_trades() if x["symbol"] == "X/USDT"][-1]
        trade_logger.log_close(t, 104 if win else 98, "TP" if win else "SL")

    n = lambda d: {"direction": d, "confidence": 60}
    # Recompensas en R: gana quien votó la dirección rentable, paga quien votó la contraria.
    won = {"entry": 100, "sl": 98, "qty": 1, "pnl": 4.0, "side": "LONG", "exit_reason": "TP", "closed_ts": 1,
           "votes": {"a": n("LONG"), "b": n("SHORT"), "c": n("NEUTRAL")}}
    lost = won | {"pnl": -2.0, "exit_reason": "SL", "closed_ts": 2}
    assert optimizer.reward(won) == {"a": 2.0, "b": -2.0}  # arriesgó 2 y ganó 4: 2 R; el neutral no puntúa
    assert optimizer.reward(lost) == {"a": -1.0, "b": 1.0}  # el que avisó en contra cobra
    assert optimizer.reward(won | {"side": "SHORT"}) == {"a": -2.0, "b": 2.0}
    assert optimizer.reward(won | {"pnl": 40.0}) == {"a": 3.0, "b": -3.0}  # una operación no da más de 3 R
    assert optimizer.reward(won | {"exit_reason": "LIMITE"}) == {} and optimizer.reward(won | {"sl": None}) == {}
    board = optimizer.scoreboard([won | {"closed_ts": 3}, lost, won])
    assert board["a"] == {"points": 3.0, "hits": 2, "misses": 1, "streak": 1, "best": 2.0, "last": 2.0,
                          "history": [(1, 2.0), (2, 1.0), (3, 3.0)]}, board["a"]
    assert board["b"]["points"] == -3.0 and board["b"]["streak"] == 0 and "c" not in board

    # La confianza del jefe sale de esas recompensas: "bueno" acierta siempre, "malo" nunca, "regular" siempre compra.
    for win in (True,) * 5 + (False,) * 3:
        trade(win, {"bueno": n("LONG" if win else "SHORT"), "malo": n("SHORT" if win else "LONG"), "mudo": n("NEUTRAL"),
                    "regular": n("LONG")})
    trade(True, {"novato": n("LONG")})
    before, logged = db.get_state("weights", {}), len(trade_logger.events(500, kinds=("trust",)))
    rep = optimizer.run(dry_run=True)
    assert db.get_state("weights", {}) == before and rep["metrics"]["n"] >= 9
    assert len(trade_logger.events(500, kinds=("trust",))) == logged  # un ensayo no deja rastro
    optimizer.run()
    w = db.get_state("weights")
    assert w["bueno"]["X/USDT"] == 2.0 and w["malo"]["X/USDT"] == 0.25, w  # +13 R y -13 R en 8 votos: tope y suelo
    assert w["regular"]["X/USDT"] == round(1 + 2 * 7 / 18, 3)  # +7 R en 8 votos, frenado por 10 votos fantasma
    assert "mudo" not in w and "novato" not in w  # neutral o con pocas muestras: peso intacto
    r = db.get_state("optimizer:report")["agents"]["malo"]["X/USDT"]
    assert r["precision"] == 0.0 and r["puntos"] == -13.0
    trust = {e["who"]: e["text"] for e in trade_logger.events(500, kinds=("trust",)) if e["symbol"] == "X/USDT"}
    assert "sube" in trust["bueno"] and "baja" in trust["malo"] and "×2.00" in trust["bueno"], trust  # queda en la bitácora

    # Ajustes desde la app: fuera de rango se recortan, lo que no es un número se ignora y lo demás no se toca.
    db.set_state("ctl:params", {"RISK_PER_TRADE_PCT": 50, "DECISION_THRESHOLD": "alto", "MAX_OPEN_POSITIONS": 3.7,
                                "MAX_SL_PCT": 99, "PAPER_TRADING": False})
    got = optimizer.params()
    assert got["RISK_PER_TRADE_PCT"] == 2.0 and got["MAX_OPEN_POSITIONS"] == 3 and set(got) == set(optimizer.TUNABLE)
    assert got["DECISION_THRESHOLD"] == optimizer.DEFAULTS["DECISION_THRESHOLD"]
    keep = config.COOLDOWN_S, config.MAX_SL_PCT, config.PAPER_TRADING
    config.COOLDOWN_S = 123.0
    try:
        optimizer.apply_params()
        assert config.RISK_PER_TRADE_PCT == 2.0 and config.MAX_OPEN_POSITIONS == 3
        assert (config.COOLDOWN_S, config.MAX_SL_PCT, config.PAPER_TRADING) == (123.0, keep[1], keep[2])
    finally:
        db.del_state("ctl:params")
        config.RISK_PER_TRADE_PCT, config.MAX_OPEN_POSITIONS, config.COOLDOWN_S = 1.0, 2, keep[0]


async def check_app():
    """Lo que la app enseña y controla: banquillo, bitácora, recompensas al cerrar y parada ordenada."""
    mode, config.EXECUTION_MODE = config.EXECUTION_MODE, "app"  # modo propio: sin operaciones ni bloqueos de antes
    cooldown, config.COOLDOWN_S = config.COOLDOWN_S, 900
    for key in ("ctl:kill", "ctl:bench", "ctl:params", "ctl:engine", "ctl:auto_opt", "engine", "order:fails",
                "order:last_attempt"):
        db.del_state(key)
    db.set_state("ctl:mode", "AUTO")
    try:
        fm, notes = FakeMarket(), []
        evo = EvolutionManager(notify=notes.append)
        boss = BossAgent(fm, PaperBroker(fm), {n: 600 for n in AGENT_LABELS}, evo, notes.append)
        said = lambda *kinds, **kw: trade_logger.events(500, kinds=kinds, **kw)

        async def cycle():
            for name in AGENT_LABELS:
                db.set_state(f"signal:{name}:{SYM}", token_budget.pack(evo.apply(sig(name, "LONG", 80))))
            await boss.tick()
            await evo.drain()
            return db.get_state(f"boss:{SYM}")

        # Banquillo: el jefe no cuenta el voto de un bot apartado, y ese bot ni analiza ni deja señal.
        db.set_state("ctl:bench", ["smc", "wyckoff", "sentiment", "scenarios"])
        b = await cycle()
        assert b["action"] == "NEUTRAL" and "solo 2 agentes" in b["reason"] and set(b["votes"]) == {"historian", "macro"}, b

        class Bot:
            name, interval, mood = "smc", 60, "LONG"

            async def run(self, symbol, market):
                return sig("smc", self.mood, 70)

        bot = Bot()
        await engine.run_agent(bot, SYM, fm, evo)
        assert db.get_state(f"signal:smc:{SYM}") is None and db.get_state(f"status:smc:{SYM}")["state"] == "banquillo"
        db.set_state("ctl:bench", [])
        for bot.mood in ("LONG", "LONG", "SHORT"):  # la bitácora anota los cambios de opinión, no cada análisis
            await engine.run_agent(bot, SYM, fm, evo)
        flips = said("signal", who="smc")
        assert len(flips) == 2 and flips[0]["text"].startswith("SHORT") and flips[1]["text"].startswith("LONG"), flips

        # Operación ganadora: cada bot que votó a favor cobra en R y el jefe reajusta la confianza él solo.
        assert (await cycle())["status"] == "orden ejecutada"
        fm.p, t0 = 103.5, time.time()
        await cycle()
        paid, voters = said("reward"), lambda: {a for a, v in trade_logger.closed_trades(mode="app")[-1]["votes"].items()
                                                if v["direction"] == "LONG"}
        assert {e["who"] for e in paid} == voters() and len(paid) >= 3 and all(1.5 < e["value"] < 2.0 for e in paid), paid
        assert "acertar" in paid[0]["text"] and [e["text"][:7] for e in said("trade")] == ["CERRADA", "ABIERTA"]
        assert db.get_state("optimizer:last_run") >= t0
        # Con la auto-optimización apagada el jefe no toca los pesos; la pérdida se cobra igual y hay relevo.
        db.set_state("ctl:auto_opt", False)
        fm.p, ran, config.COOLDOWN_S = 100.0, db.get_state("optimizer:last_run"), 0
        assert (await cycle())["status"] == "orden ejecutada"
        fm.p, config.COOLDOWN_S = 98.0, 900
        await cycle()
        fined = said("reward")[:-len(paid)]
        assert db.get_state("optimizer:last_run") == ran and {e["who"] for e in fined} == voters()
        assert all(e["value"] < 0 and "fallar" in e["text"] for e in fined)
        assert said("death") and said("rebirth") and "lección" in said("rebirth")[0]["text"]

        # Parada desde la app: termina el ciclo en curso y sale antes del siguiente. Y no arrancan dos motores.
        class Ticker:
            n, broken = 0, True

            async def tick(self):
                Ticker.n += 1
                if self.broken:
                    raise RuntimeError("sin mercado")

        db.set_state("ctl:engine", "stop")
        for broken, seen in ((True, "RuntimeError"), (False, None)):  # un ciclo roto se anota; uno bueno lo borra
            ticker = Ticker()
            ticker.broken = broken
            try:
                await asyncio.wait_for(engine.boss_loop(ticker), 10)
                raise AssertionError("debió parar")
            except engine.Stop:
                assert (db.get_state("boss:error") or {}).get("error") == seen
        assert Ticker.n == 2  # cada vez terminó su ciclo y salió antes del siguiente
        db.set_state("engine", {"pid": 1, "ts": time.time()})
        try:
            await engine.main(once=True)
            raise AssertionError("dos motores a la vez")
        except SystemExit as e:
            assert "Ya hay un motor" in str(e) and db.get_state("engine")["pid"] == 1  # el candado ajeno sigue ahí
    finally:
        for key in ("ctl:bench", "ctl:auto_opt", "ctl:engine", "engine"):
            db.del_state(key)
        config.EXECUTION_MODE, config.COOLDOWN_S = mode, cooldown


async def check_backtest():
    # 74 días de velas de 5m en tendencia alcista y sus agregados; se simulan los 4 últimos.
    n, r = 74 * 288, np.random.default_rng(11)
    c = 100 * np.exp(np.cumsum(0.0002 + r.normal(0, 0.002, n)))
    o = np.r_[c[0], c[:-1]]
    m5 = pd.DataFrame({"ts": pd.date_range("2026-03-01", periods=n, freq="5min", tz="UTC"), "open": o,
                       "high": np.maximum(o, c) * 1.0005, "low": np.minimum(o, c) * 0.9995, "close": c, "volume": 1.0})
    agg = lambda rule, src=m5: src.set_index("ts").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    data = {SYM: {"5m": m5, "15m": agg("15min"), "1h": agg("1h"), "4h": agg("4h"), "1d": agg("1D")}}

    hm = backtest.HistoryMarket(data)  # nunca entrega una vela que aún no ha cerrado
    hm.now = pd.Timestamp("2026-05-01 10:07", tz="UTC")
    for tf in ("5m", "15m", "1h", "4h", "1d"):
        last = (await hm.ohlcv(SYM, tf, 5)).ts.iloc[-1]
        assert last + pd.Timedelta(seconds=backtest.seconds(tf)) <= hm.now < last + 2 * pd.Timedelta(seconds=backtest.seconds(tf)), tf

    config.COOLDOWN_S = 900
    res = await backtest.run(data, 4)
    again = await backtest.run(data, 4)
    m = res["metrics"]
    assert m["n"] >= 3 and res["trades"][0]["side"] == "LONG", m  # tendencia alcista: opera, y en largo
    assert [t["pnl"] for t in res["trades"]] == [t["pnl"] for t in again["trades"]]  # determinista
    assert abs(res["equity"] - (config.CAPITAL + m["pnl"])) < 1e-6 and res["max_drawdown_pct"] <= 0
    for t in res["trades"]:
        assert t["fees"] > 0 and t["slippage"] >= 0  # toda operación paga costes
        if t["exit_reason"] == "SL":  # la pérdida neta por stop es el 1 % del capital de ese momento
            assert 0.009 * t["equity_at_entry"] < -t["pnl"] < 0.0105 * t["equity_at_entry"], t["pnl"]
    assert "Operaciones" in backtest.report(res, 4, False)
    out = json.loads(json.dumps(backtest.export(res)))  # lo que el laboratorio de la app lee: JSON puro
    assert len(out["trades"]) == m["n"] and out["trades"][0]["closed_ts"] > out["trades"][0]["opened_ts"]
    assert 2 <= len(out["curve"]) <= 401 and out["curve"][0][1] == config.CAPITAL and out["lineage"] is None
    assert set(optimizer.scoreboard(out["trades"])) <= {"historian", "smc", "wyckoff", "sentiment"}
    assert out["setup"]["params"]["RISK_PER_TRADE_PCT"] == config.RISK_PER_TRADE_PCT
    evo = await backtest.run(data, 4, evolution=True)
    assert evo["versions"]["smc"].startswith("SMC_Scalper_v") and evo["metrics"]["n"] >= 1

    # Cuenta fondeada por generaciones: el bot muere al tocar su suelo, el sucesor hereda y corrige una
    # causa, y la configuración global queda como estaba al terminar.
    params = lambda x: (x["threshold"], x["atr_tf"], x["risk_pct"], x["blocked"])
    before = config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT
    config.FEE_PCT = 1.0  # comisión absurda: toda operación pierde y los bots mueren uno tras otro
    lin = await backtest.run(data, 4, lineage={"max_loss": 30})
    g = lin["lineage"]["generations"]
    assert len(g) >= 2 and [x["gen"] for x in g] == list(range(1, len(g) + 1)), g
    assert all(x["dead"] and x["net"] < 0 for x in g[:-1]) and not g[-1]["dead"]
    for a, b in zip(g, g[1:]):
        assert params(a) != params(b) and b["lesson"] != "configuración inicial" and a["end"] == b["born"], (a, b)
    assert (config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT) == before
    assert abs(sum(lin["lineage"]["weekly"]) - sum(t["pnl"] for t in lin["trades"])) < 1e-6
    assert all((t["symbol"], t["side"]) not in map(tuple, g[t["gen"] - 1]["blocked"]) for t in lin["trades"])
    assert "Linaje:" in backtest.report(lin, 4, False)
    same = (await backtest.run(data, 4, lineage={"max_loss": 30, "learn": False}))["lineage"]["generations"]
    assert len(same) >= 2 and len({str(params(x)) for x in same}) == 1  # sin aprendizaje, el sucesor es idéntico
    config.FEE_PCT = 0.075

    # Las cinco correcciones del sucesor, una por causa de muerte y en este orden de prioridad.
    mk = lambda side, pnl, cost=1.0: {"symbol": SYM, "side": side, "pnl": pnl, "fees": cost, "slippage": 0.0,
                                      "risk": 100.0, "votes": {}}
    bot0, tfs = {"gen": 1, "born": None, "weights": {}, "blocked": set(), "lesson": ""}, {"5m", "15m", "1h", "4h", "1d"}
    born = lambda bot, mine: backtest.successor(bot, mine, [], tfs, None)
    try:
        b = born(bot0, [mk("LONG", -60, cost=30)] * 3)  # costes = 30 % de lo arriesgado
        assert config.ATR_TIMEFRAME == "1h" and b["gen"] == 2 and "stop más ancho" in b["lesson"]
        born(bot0, [mk("LONG", -0.5)] * 3)  # gana antes de costes (+0,5 cada una), pierde después
        assert config.DECISION_THRESHOLD == 0.35 and config.ATR_TIMEFRAME == "1h"
        losing = [mk("SHORT", -50)] * 5 + [mk("LONG", 10)] * 5  # pierde antes de costes, y la culpa es de los cortos
        b = born(bot0, losing)
        assert b["blocked"] == {(SYM, "SHORT")} and bot0["blocked"] == set() and config.DECISION_THRESHOLD == 0.35
        born(b, losing)  # no puede vetar también los largos: exige más acuerdo
        assert config.DECISION_THRESHOLD == 0.4
        config.DECISION_THRESHOLD = 0.75
        born(b, losing)  # sin más filtros: arriesga menos
        assert config.RISK_PER_TRADE_PCT == 0.75 and config.DECISION_THRESHOLD == 0.75
    finally:
        config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT = before
    only_long = await backtest.run(data, 4, lineage={"max_loss": 1e9, "blocked": [(SYM, "SHORT")]})
    assert only_long["trades"] and all(t["side"] == "LONG" for t in only_long["trades"])

    # Mercado con sesiones (acciones): no se entra en la última media hora y nada queda abierto de noche.
    s5 = m5[(m5.ts.dt.hour >= 14) & (m5.ts.dt.hour < 20)].reset_index(drop=True)
    sdata = {SYM: {"5m": s5, "15m": agg("15min", s5), "1h": agg("1h", s5), "4h": agg("4h", s5), "1d": agg("1D", s5)}}
    day = await backtest.run(sdata, 6, lineage={"max_loss": 1e9, "flat": True})
    assert day["trades"] and len(day["lineage"]["generations"]) == 1 and day["open_at_end"] == 0
    for t in day["trades"]:
        assert t["opened"].date() == t["closed"].date() and 14 <= t["opened"].hour and t["opened"].strftime("%H:%M") <= "19:30", t
    assert {t["exit_reason"] for t in day["trades"]} <= {"SL", "TP", "SESION", "LIMITE"}
    assert any(t["exit_reason"] == "SESION" for t in day["trades"])

    # ORB entrenado en un periodo y examinado en el siguiente, que el entrenamiento no ve.
    config.STRATEGY, config.MIN_AGENTS = "orb", 1
    try:
        # Dos activos rompen en la misma vela y solo hay un hueco: entra el de más volumen relativo, no el primero.
        flat = [(100, 100.5, 99.5, 100, 10)] * 3
        mk = lambda vol: pd.concat([sessions(flat * 4, quiet=12), sessions([(100, 100.5, 99.5, 100, vol)] * 3 + [
            (100, 101.4, 100, 101.2, vol)] + flat, quiet=0).assign(ts=lambda d: d.ts + pd.Timedelta(days=1))], ignore_index=True)
        two = {name: {"5m": df, "15m": agg("15min", df), "1h": agg("1h", df)} for name, df in (("AAA", mk(10)), ("BBB", mk(60)))}
        symbols, slots, config.SYMBOLS, config.MAX_OPEN_POSITIONS = config.SYMBOLS, config.MAX_OPEN_POSITIONS, ["AAA", "BBB"], 1
        try:
            ranked = await backtest.run(two, 1, genes={"or_min": 15, "sides": "both"})
        finally:
            config.SYMBOLS, config.MAX_OPEN_POSITIONS = symbols, slots
        assert [t["symbol"] for t in ranked["trades"]] == ["BBB"], [(t["symbol"], t["opened"]) for t in ranked["trades"]]

        pop = [orb_agent.DEFAULT | {"or_min": m, "sides": side} for m, side in ((15, "long"), (30, "long"), (15, "both"))]
        rules, rr = {"max_loss": 1e9, "flat": True, "min_trades": 3}, config.REWARD_RISK
        end = s5.ts.iloc[-1] + pd.Timedelta(minutes=5)
        f = await backtest.fold(sdata, end, 20, 10, rules, pop)
        assert f["best"] and f["population"] == 3 and f["train"][1] == f["test"][0] == end - pd.Timedelta(days=10)
        assert f["n"] == len(f["trades"]) > 0 and abs(sum(f["weekly"]) - f["net"]) < 1e-6 and config.REWARD_RISK == rr
        assert all(f["test"][0] <= t["opened"] and t["closed"] <= f["test"][1] for t in f["trades"])  # solo en su periodo
        # Sin fugas: si los precios del examen fueran otros, el entrenamiento elegiría exactamente lo mismo.
        px = ["open", "high", "low", "close"]
        other = {SYM: {tf: df.assign(**{c: np.where(df.ts >= f["test"][0], df[c] * 3, df[c]) for c in px})
                       for tf, df in sdata[SYM].items()}}
        g = await backtest.fold(other, end, 20, 10, rules, pop)
        assert g["best"] == f["best"] and g["net"] != f["net"]
        # Selección natural: quien pierde la cuenta entrenando queda eliminado; sin supervivientes no se opera.
        none = await backtest.fold(sdata, end, 20, 10, rules | {"max_loss": 1}, pop)
        assert none["best"] is None and none["dead"] >= 1 and none["n"] == 0 and none["net"] == 0
        wf = await backtest.walk_forward(sdata, 20, 10, rules, pop)
        assert len(wf) == 4 and all(a["test"][1] == b["test"][0] for a, b in zip(wf, wf[1:]))  # exámenes consecutivos
        assert wf[-1]["net"] == f["net"] and wf[-1]["best"] == f["best"]
        assert "fuera de muestra" in backtest.report_wf(wf)
    finally:
        config.STRATEGY, config.MIN_AGENTS = "ensemble", 3

    chart = {"chart": {"result": [{"timestamp": [1780000200, 1780003800, 1780007400], "indicators": {"quote": [
        {"open": [1, None, 3], "high": [2, 2, 4], "low": [0.5, 1, 2], "close": [1.5, 2, 3.5], "volume": [10, 5, 0]}]}}]}}
    assert backtest.yahoo_frame(chart).close.tolist() == [1.5]  # fuera las velas incompletas y las de volumen cero

    class Ex:  # el descargador pagina de 1000 en 1000 y no deja huecos ni duplicados
        def milliseconds(self):
            return 1_780_000_000_000

        async def fetch_ohlcv(self, symbol, tf, since=None, limit=1000):
            step = backtest.seconds(tf) * 1000
            first = -(-since // step) * step
            return [[t, 1, 2, 0.5, 1.5, 9] for t in range(first, min(first + limit * step, self.milliseconds()), step)]

    got = await backtest.download(Ex(), SYM, 5, pathlib.Path(TMP) / "velas")
    d5 = got["5m"].ts.diff().dropna()
    assert len(got["5m"]) >= 5 * 288 + backtest.WARMUP - 1 and (d5 == pd.Timedelta(minutes=5)).all()

    class Offline(Ex):  # la segunda vez sale del CSV: no vuelve a pedir nada al exchange
        async def fetch_ohlcv(self, *a, **kw):
            raise AssertionError("no debía descargar")

    assert len((await backtest.download(Offline(), SYM, 5, pathlib.Path(TMP) / "velas"))["5m"]) == len(got["5m"])


def check_cache():
    # diskcache: persistente y con caducidad. Con 50 ms fallaba 1 de cada 6 veces en Windows (disco y antivirus).
    cache_manager.cache_set("k", {"a": 1}, 0.5)
    assert cache_manager.cache_get("k") == {"a": 1}
    time.sleep(0.7)
    assert cache_manager.cache_get("k") is None and cache_manager.window(5) == 60 and cache_manager.window(900) == 180
    db.set_state("s", [1, 2])
    assert db.get_state("s") == [1, 2] and db.get_state("nada", "x") == "x"


async def check_alpaca():
    """Alpaca: solo datos. Sin clave se declaran los pares a mano; las velas se piden con fecha de inicio."""
    old = config.EXCHANGE_ID, config.SYMBOLS, config.EXCHANGE_API_KEY
    config.EXCHANGE_ID, config.SYMBOLS, config.EXCHANGE_API_KEY = "alpaca", ["BTC/USD", "ETH/USD"], ""
    try:
        ex = make_exchange()
        assert set(ex.markets) == {"BTC/USD", "ETH/USD"} and not ex.apiKey  # sin red y sin clave
        await ex.close()
        config.EXCHANGE_API_KEY = "PK-PRUEBA"
        ex = make_exchange()  # con clave: se usa para leer, contra el entorno paper
        assert ex.apiKey == "PK-PRUEBA" and "paper-api" in str(ex.urls["api"]) and not ex.markets
        await ex.close()
        config.EXCHANGE_API_KEY = ""
        m, calls = Market(), []

        async def fetch(symbol, tf, since=None, limit=None):
            calls.append((since, limit))  # como Alpaca: páginas cortas (aquí 100 velas) desde lo más antiguo
            first = -(-since // 300_000) * 300_000
            return [[t, 1, 2, 0.5, 1.5, 9] for t in range(first, min(first + 100 * 300_000, m.ex.milliseconds()), 300_000)]

        m.ex.fetch_ohlcv = fetch
        df = await m.ohlcv("BTC/USD", "5m", 300)
        (since, limit), now = calls[0], m.ex.milliseconds()
        assert limit == 450 and abs(now - since - 450 * 300_000) < 5000 and len(calls) == 5, calls
        assert len(df) == 300 and df.ts.is_monotonic_increasing and df.ts.is_unique
        assert now - df.ts.iloc[-1].timestamp() * 1000 < 300_000  # llega hasta la vela en curso, no se queda en la 1.ª página
        await m.close()
    finally:
        config.EXCHANGE_ID, config.SYMBOLS, config.EXCHANGE_API_KEY = old
    r = subprocess.run([sys.executable, "-c", "import config"], cwd=config.ROOT, capture_output=True, text=True,
                       env=os.environ | {"EXCHANGE_ID": "alpaca", "PAPER_TRADING": "false"})
    assert r.returncode != 0 and "solo simula" in r.stderr  # a Alpaca no se le envían órdenes


class FakeTopstep(http.server.BaseHTTPRequestHandler):
    """La API de ProjectX en miniatura, en 127.0.0.1. Nada de esta prueba sale a internet."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        s, path = self.server.state, self.path
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        s["calls"].append((path, body))
        status, out = 200, {"success": True, "errorCode": 0, "errorMessage": None}
        if path == "/api/Auth/loginKey":
            if body == {"userName": "yo", "apiKey": "CLAVE-SECRETA"}:
                s["n"] += 1
                out["token"] = f"JWT-{s['n']}"
            else:
                out = {"success": False, "errorCode": 3, "errorMessage": "credenciales"}
        elif s.pop("expire", False) or self.headers.get("Authorization") != f"Bearer JWT-{s['n']}":
            status, out = 401, {}
        elif s.get("busy"):
            status, out = 429, {}
        elif path == "/api/Account/search":
            out["accounts"] = s["accounts"]
        elif path == "/api/Contract/search":
            out["contracts"] = [c for c in s["contracts"] if body["searchText"] in c["name"]]
        elif path == "/api/History/retrieveBars":
            out["bars"] = s["bars"]
        elif path == "/api/Order/place":
            if s.get("reject"):
                out = {"success": False, "errorCode": 2, "errorMessage": "orden rechazada"}
            else:
                out["orderId"], cid = 77, body["contractId"]
                s["positions"] = [{"contractId": cid, "size": body["size"], "averagePrice": 20000.5,
                                   "type": 1 if body["side"] == 0 else 2}]
                # Semántica con signo, como la plataforma: precio = entrada + ticks x tamaño del tick.
                s["working"] = [] if s.get("no_brackets") else [
                    {"id": 78, "contractId": cid, "type": 4,
                     "stopPrice": 20000.5 + body["stopLossBracket"]["ticks"] * 0.25},
                    {"id": 79, "contractId": cid, "type": 1,
                     "limitPrice": 20000.5 + body["takeProfitBracket"]["ticks"] * 0.25}]
        elif path == "/api/Order/searchOpen":
            out["orders"] = s["working"]
        elif path == "/api/Order/cancel":
            s["working"] = [o for o in s["working"] if o["id"] != body["orderId"]]
        elif path == "/api/Position/searchOpen":
            out["positions"] = s["positions"]
        elif path == "/api/Position/closeContract":
            s["positions"] = [p for p in s["positions"] if p["contractId"] != body["contractId"]]
            s["closed"].append(body["contractId"])
        elif path == "/api/Trade/search":
            out["trades"] = s["trades"]
        else:
            status = 404
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


async def check_topstepx():
    cfg = lambda **kw: [setattr(config, k, v) for k, v in kw.items()]
    # 1) Candados de configuración: sin el límite de tu cuenta no arranca, y las órdenes reales exigen confirmación.
    run = lambda **env: subprocess.run(
        [sys.executable, "-c", "import config as c; print(c.EXECUTION_MODE, c.FEE_PCT, c.PROP_KILL_AT_PCT, "
                               "c.TOTAL_LOSS_LIMIT_USD, c.MAX_TRADES_PER_DAY)"],
        cwd=config.ROOT, env=os.environ | {"BROKER": "topstepx", "TOTAL_LOSS_LIMIT_USD": ""} | env,
        capture_output=True, text=True)
    assert "PROP_DAILY_LOSS_LIMIT_USD" in run().stderr and run().returncode != 0
    ok = run(PROP_DAILY_LOSS_LIMIT_USD="1000", PROP_KILL_AT_PCT="95")  # el 95 % pedido se queda en el 80 %
    assert ok.stdout.split() == ["paper", "0.0", "80.0", "0.0", "10"], (ok.stdout, ok.stderr)
    assert run(PROP_DAILY_LOSS_LIMIT_USD="1000", PAPER_TRADING="false").returncode != 0
    live = run(PROP_DAILY_LOSS_LIMIT_USD="1000", PAPER_TRADING="false", CONFIRM_LIVE="SI_ARRIESGO_DINERO_REAL")
    assert live.stdout.split()[0] == "live"

    cfg(BROKER="topstepx", EXECUTION_MODE="prop", FEE_PCT=0.0, SLIPPAGE_PCT=0.0, MIN_NOTIONAL=0.0, MAX_LEVERAGE=1e9,
        TOTAL_LOSS_LIMIT_USD=0.0, CAPITAL=50000.0, PROP_DAILY_LOSS_LIMIT_USD=1000.0, PROP_KILL_AT_PCT=75.0,
        PROP_ACCOUNT_FLOOR_USD=0.0, PROP_MAX_CONTRACTS=3, PROP_VERIFY_WAIT_S=0.01, MAX_TRADES_PER_DAY=10,
        MIN_ORDER_GAP_S=60.0, TOPSTEPX_USERNAME="yo", TOPSTEPX_API_KEY="CLAVE-SECRETA", TOPSTEPX_ACCOUNT_ID="")

    # 2) Día operativo y horario, en hora de Chicago (noviembre: UTC-6).
    utc = lambda *a: dt.datetime(*a, tzinfo=dt.timezone.utc)
    assert risk.trading_day(utc(2026, 11, 11, 22, 59)) == "2026-11-11"  # 16:59 en Chicago
    assert risk.trading_day(utc(2026, 11, 11, 23, 0)) == "2026-11-12"  # 17:00: empieza el día siguiente
    assert risk.day_open(utc(2026, 11, 11, 22, 59)) == utc(2026, 11, 10, 23, 0)
    assert risk.session(utc(2026, 11, 11, 16, 0)) == (True, False)  # 10:00
    assert risk.session(utc(2026, 11, 11, 20, 45)) == (False, False)  # 14:45: ya no se entra
    assert risk.session(utc(2026, 11, 11, 21, 5)) == (False, True)  # 15:05: todo fuera
    assert risk.session(utc(2026, 11, 11, 23, 30)) == (True, False)  # 17:30: sesión nueva
    assert risk.session(utc(2026, 11, 14, 18, 0)) == (False, False)  # sábado
    assert risk.session(utc(2026, 11, 13, 23, 30)) == (False, False)  # viernes por la tarde: cerrado

    # 3) Kill switch: salta al 75 % del límite diario, o de lo que quede hasta el suelo de la cuenta.
    assert risk.check_limits(50000 - 749, 50000, 50000) is None
    assert risk.check_limits(50000 - 750, 50000, 50000)[0] == "daily"
    assert risk.check_limits(45000, 45000, 50000) is None  # sin límite total fijo: manda el suelo
    config.PROP_ACCOUNT_FLOOR_USD = 49600.0
    assert risk.check_limits(50000 - 299, 50000, 50000) is None
    assert risk.check_limits(50000 - 300, 50000, 50000)[0] == "daily"  # quedan 400 hasta el suelo: 75 % = 300
    config.PROP_ACCOUNT_FLOOR_USD = 0.0

    # 4) Cliente REST contra la API falsa: registro de auditoría sin secretos, token renovado, errores de red.
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeTopstep)
    s = srv.state = {
        "calls": [], "n": 0, "positions": [], "working": [], "trades": [], "closed": [],
        "accounts": [{"id": 9, "name": "50K-EVAL", "balance": 50000.0, "canTrade": True},
                     {"id": 8, "name": "VIEJA", "balance": 1.0, "canTrade": False}],
        "contracts": [{"id": "CON.F.US.MNQ.Z26", "name": "MNQZ6", "tickSize": 0.25, "tickValue": 0.5,
                       "activeContract": True},
                      {"id": "CON.F.US.MNQ.H27", "name": "MNQH7", "tickSize": 0.25, "tickValue": 0.5,
                       "activeContract": False},
                      {"id": "CON.F.US.ENQ.Z26", "name": "NQZ6", "tickSize": 0.25, "tickValue": 5.0,
                       "activeContract": True}],
        "bars": [{"t": f"2026-11-11T15:{m:02d}:00+00:00", "o": 20000, "h": 20002, "l": 19998, "c": 20000 + m, "v": 5}
                 for m in range(30)]}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    config.TOPSTEPX_API_URL = f"http://127.0.0.1:{srv.server_port}"
    cap = Capture()
    logging.getLogger().addHandler(cap)
    logging.getLogger().setLevel(logging.INFO)
    said = lambda text, level=None: any(text in m and level in (None, lv) for lv, m in cap.lines)

    api = TopstepClient()
    api.gap = 0
    config.SYMBOLS = ["MNQ", "NQ"]
    market = TopstepMarket(api)
    await market.start()
    assert market.account_id == 9  # la única cuenta operable
    assert market.contracts["MNQ"]["id"] == "CON.F.US.MNQ.Z26" and market.contracts["NQ"]["id"] == "CON.F.US.ENQ.Z26"
    assert market.point_value("MNQ") == 2.0 and market.point_value("NQ") == 20.0 and market.tick("MNQ") == 0.25
    assert said("-> POST /api/Auth/loginKey") and said("<- /api/Account/search HTTP 200")
    assert not said("CLAVE-SECRETA") and not said("JWT-"), "un secreto llegó al log"
    s["expire"] = True  # el token caduca: se renueva solo y la petición se repite una vez
    df = await market.ohlcv("MNQ", "15m", 20)
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"] and len(df) == 20 and s["n"] == 2
    assert df.ts.is_monotonic_increasing and df.close.iloc[-1] == 20029
    assert await market.price("MNQ") == 20029.0
    s["busy"] = True
    try:
        await api.post("/api/Account/search")
        raise AssertionError("un 429 debe tratarse como fallo pasajero")
    except ConnectionError:
        pass
    s["busy"] = False
    dead = TopstepClient()
    dead.gap, dead.token, dead.token_ts = 0, "x", time.time()
    config.TOPSTEPX_API_URL, url = "http://127.0.0.1:9", config.TOPSTEPX_API_URL
    try:
        await dead.post("/api/Account/search")
        raise AssertionError("sin conexión debe fallar")
    except ConnectionError:
        assert said("sin conexión", "ERROR")
    config.TOPSTEPX_API_URL = url
    config.TOPSTEPX_ACCOUNT_ID = "8"  # cuenta no operable: no arranca
    try:
        await TopstepMarket(api).start()
        raise AssertionError("debió negarse")
    except SystemExit:
        config.TOPSTEPX_ACCOUNT_ID = ""

    # 5) Órdenes reales (contra la API falsa): contratos enteros, brackets en el servidor y verificación.
    broker = TopstepBroker(market)
    assert await broker.equity() == 50000.0
    for bad in [("MNQ", "LONG", 1.9, 19990, 20020, 20000),  # no llega a 1 contrato
                ("MNQ", "LONG", 6.0, 19999, 20020, 20000)]:  # stop a 4 ticks
        try:
            await broker.open(*bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert not [c for c in s["calls"] if c[0] == "/api/Order/place"]  # ninguna de las dos llegó a enviarse
    fill = await broker.open("MNQ", "LONG", 99.0, 19990.0, 20020.0, 20000.0)  # 99 $/punto: 49 contratos pedidos
    (place,) = [b for p, b in s["calls"] if p == "/api/Order/place"]
    assert place == {"accountId": 9, "contractId": "CON.F.US.MNQ.Z26", "type": 2, "side": 0, "size": 3,
                     "stopLossBracket": {"ticks": -40, "type": 4}, "takeProfitBracket": {"ticks": 80, "type": 1}}, place
    assert fill["price"] == 20000.5 and fill["qty"] == 6.0 and fill["sl"] == 19990.5 and fill["tp"] == 20020.5
    assert fill["fee"] == 4.5 and fill["meta"]["contracts"] == 3 and said("POSICIÓN ABIERTA LONG 3 x MNQ")
    t = {"symbol": "MNQ", "side": "LONG", "opened_ts": time.time(), "meta": fill["meta"],
         "sl": fill["sl"], "tp": fill["tp"], "qty": fill["qty"]}
    assert await broker.check_exit(t) is None
    s["positions"] = []  # salta el stop en el servidor
    s["working"] = [o for o in s["working"] if o["type"] == 1]
    s["trades"] = [{"contractId": "CON.F.US.MNQ.Z26", "price": 20000.5, "size": 3, "profitAndLoss": None, "fees": 1.1},
                   {"contractId": "CON.F.US.MNQ.Z26", "price": 19990.25, "size": 3, "profitAndLoss": -61.5, "fees": 1.11}]
    res = await broker.check_exit(t)
    assert res["reason"] == "SL" and res["price"] == 19990.25 and res["fee"] == 1.11 and s["working"] == []
    assert abs(await broker.day_start_equity() - (50000 + 61.5 + 2.21)) < 1e-9  # el día empezó antes de la pérdida

    s["no_brackets"] = True  # la plataforma no crea el stop: la posición no se deja viva
    try:
        await broker.open("MNQ", "SHORT", 6.0, 20010.0, 19980.0, 20000.0)
        raise AssertionError("una posición sin stop debe cerrarse")
    except TopstepError:
        assert s["positions"] == [] and s["closed"] == ["CON.F.US.MNQ.Z26"] and said("SIN STOP", "CRITICAL")
    s["no_brackets"], config.PROP_BRACKET_SIGNED = False, False  # signo mal configurado: el stop queda del lado malo
    try:
        await broker.open("MNQ", "SHORT", 6.0, 20010.0, 19980.0, 20000.0)
        raise AssertionError("un stop en el lado equivocado no cuenta como stop")
    except TopstepError:
        assert s["positions"] == [] and s["working"] == []
    config.PROP_BRACKET_SIGNED = True
    fill = await broker.open("MNQ", "SHORT", 6.0, 20010.0, 19980.0, 20000.0)
    assert fill["sl"] == 20010.5 and fill["tp"] == 19980.5
    s["positions"].append({"contractId": "CON.F.US.ENQ.Z26", "size": 1, "averagePrice": 1.0, "type": 1})
    await broker.flatten_all()  # cierre total: órdenes y posiciones, también las ajenas al programa
    assert s["positions"] == [] and s["working"] == [] and said("CIERRE TOTAL", "CRITICAL")
    s["reject"] = True
    try:
        await broker.open("MNQ", "LONG", 6.0, 19990.0, 20020.0, 20000.0)
        raise AssertionError("un rechazo debe propagarse")
    except TopstepError as e:
        assert "orden rechazada" in str(e)
    srv.shutdown()
    assert not said("CLAVE-SECRETA") and not said("JWT-"), "un secreto llegó al log"

    # 6) El jefe con la simulación de futuros: contratos enteros, costes por contrato, frecuencia,
    #    cierre de sesión y kill switch.
    class Futures(FakeMarket):
        tick = lambda self, symbol: 0.25
        point_value = lambda self, symbol: 2.0

    class Sim(TopstepPaperBroker):
        flats, fail = 0, False

        async def flatten_all(self):
            self.flats += 1

        async def open(self, *a):
            if self.fail:
                raise RuntimeError("rechazo del broker")
            return await super().open(*a)

    config.SYMBOLS = [SYM]
    for key in ("ctl:kill", "order:fails", "order:last_attempt"):
        db.del_state(key)
    db.set_state("ctl:mode", "AUTO")
    real_session, real_day = risk.session, risk.trading_day
    risk.session = lambda now=None: (True, False)
    fm, notes = Futures(), []
    evo = EvolutionManager(notify=notes.append)
    sim = Sim(fm)
    boss = BossAgent(fm, sim, {n: 600 for n in AGENT_LABELS}, evo, notes.append)
    status = lambda: db.get_state(f"boss:{SYM}")["status"]
    gens = lambda: {a: g["version"] for a, g in evo.active.items()}

    async def cycle():
        for name in AGENT_LABELS:
            db.set_state(f"signal:{name}:{SYM}", token_budget.pack(evo.apply(sig(name, "LONG", 80))))
        await boss.tick()
        await evo.drain()
        return status()

    born = gens()
    assert await cycle() == "orden ejecutada"
    (t,) = trade_logger.open_trades()
    assert t["qty"] == 6.0 and t["entry"] == 100.25 and t["fees"] == 4.5, t  # 3 contratos, 1 tick peor, 1,5 USD c/u
    risk.session = lambda now=None: (False, True)  # 15:00 de Chicago: nada queda abierto
    assert (await cycle()).startswith("bloqueado: fuera del horario") and not trade_logger.open_trades()
    c = trade_logger.closed_trades(mode="prop")[-1]
    assert c["exit_reason"] == "SESION" and abs(c["pnl"] - ((99.75 - 100.25) * 6 - 9)) < 1e-9, c
    assert gens() == born  # un cierre por horario no mata a ningún agente
    risk.session = lambda now=None: (True, False)
    config.COOLDOWN_S = 0
    assert await cycle() == "espera mínima entre órdenes"
    cfg(MIN_ORDER_GAP_S=0.0, MAX_TRADES_PER_DAY=1)
    assert await cycle() == "máximo de operaciones del día alcanzado"
    config.MAX_TRADES_PER_DAY = 0
    assert await cycle() == "orden ejecutada"
    # Ya se perdieron 12 USD hoy. Con un límite de 26, el cierre forzoso salta al llegar a 19,5.
    config.PROP_DAILY_LOSS_LIMIT_USD = 26.0
    assert await cycle() == "posición ya abierta" and sim.flats == 0
    fm.p = 98.9  # 8,1 USD de pérdida flotante: 20,1 en el día, sin tocar todavía el stop (98,5)
    assert (await cycle()).startswith("bloqueado: kill switch") and not trade_logger.open_trades()
    c = trade_logger.closed_trades(mode="prop")[-1]
    assert c["exit_reason"] == "LIMITE" and sim.flats == 1 and gens() == born, c
    assert said("KILL SWITCH ACTIVADO", "CRITICAL") and any(n.startswith("🛑 KILL SWITCH") for n in notes)
    fm.p = 100.0
    assert (await cycle()).startswith("bloqueado: kill switch") and sim.flats == 1  # sigue parado y no repite el cierre
    boss = BossAgent(fm, sim, {n: 600 for n in AGENT_LABELS}, evo, notes.append)  # reiniciar no lo levanta
    assert (await cycle()).startswith("bloqueado: kill switch")
    risk.trading_day = lambda now=None: "2099-01-01"  # siguiente día operativo: vuelve a operar
    config.PROP_DAILY_LOSS_LIMIT_USD = 1000.0
    sim.fail = True  # y un broker que rechaza tres veces seguidas lo detiene hasta mañana
    assert [await cycle() for _ in range(3)] == ["orden rechazada (RuntimeError)"] * 3
    sim.fail = False
    assert await cycle() == "bloqueado: 3 órdenes fallidas seguidas" and not trade_logger.open_trades()
    risk.session, risk.trading_day = real_session, real_day
    logging.getLogger().removeHandler(cap)


async def check_scenarios():
    """El bot de escenarios: miles de futuros ficticios por activo, el lado bueno según la tendencia, nada si no hay
    ventaja tras costes, solo las 5 mejores apuestas y en orden, los mismos escenarios con los mismos datos, y en
    acciones velas de la misma hora de sesiones completas anteriores."""
    from agents import scenario_agent
    from agents.scenario_agent import ScenarioAgent
    snap = {k: getattr(config, k) for k in ("SYMBOLS", "SCENARIOS", "SCENARIO_TOP", "SCENARIO_HOURS", "FEE_PCT",
                                            "SLIPPAGE_PCT", "REWARD_RISK", "ATR_SL_MULT", "BROKER", "STOCK_FLAT_MIN")}
    real = risk.nyse_open_since, risk.nyse_hours
    config.SCENARIOS, config.SCENARIO_TOP, config.SCENARIO_HOURS, config.BROKER = 4000, 5, 6, "ccxt"
    config.FEE_PCT, config.SLIPPAGE_PCT, config.REWARD_RISK, config.ATR_SL_MULT = 0.075, 0.02, 2.0, 1.5
    gen = np.random.default_rng(3)

    def crypto(drift, n=1200, noise=0.0015):  # velas de 5 min ya cerradas, la última hace 5 min
        c = 100 * np.exp(np.cumsum(drift + gen.normal(0, noise, n)))
        o = np.r_[c[0], c[:-1]]
        ts = pd.to_datetime(time.time() - 600 - 300 * np.arange(n)[::-1], unit="s", utc=True)
        return pd.DataFrame({"ts": ts, "open": o, "high": np.maximum(o, c) * 1.0008, "low": np.minimum(o, c) * 0.9992,
                             "close": c, "volume": 1.0})

    def sessions(drift, days, noise=0.001):  # sesiones completas de 78 velas, de 09:30 a 16:00 en Nueva York
        today, out, d = dt.datetime.now(risk.NEW_YORK).date(), [], 1
        while len(out) < days:
            day = today - dt.timedelta(days=d)
            d += 1
            if day.weekday() >= 5:
                continue
            start = dt.datetime.combine(day, dt.time(9, 30), risk.NEW_YORK)
            c = 100 * np.exp(np.cumsum(drift + gen.normal(0, noise, 78)))
            o = np.r_[c[0], c[:-1]]
            out.append(pd.DataFrame({"ts": pd.date_range(start, periods=78, freq="5min").tz_convert("UTC"), "open": o,
                                     "high": np.maximum(o, c) * 1.0005, "low": np.minimum(o, c) * 0.9995, "close": c,
                                     "volume": 1.0}))
        return pd.concat(out[::-1], ignore_index=True)

    class Scen:
        def __init__(self, data, broken=()):
            self.data, self.broken = data, set(broken)

        async def ohlcv(self, symbol, tf, limit=300):
            if symbol in self.broken:
                raise ConnectionError("sin datos")
            return self.data[symbol].tail(limit)

        async def price(self, symbol):
            return float(self.data[symbol].close.iloc[-1])

    try:
        # 1) Sube, baja o se queda quieto: el lado bueno, y nada cuando los costes se comen la ventaja.
        config.SYMBOLS = ["UP/USDT", "DOWN/USDT", "FLAT/USDT"]
        mk = Scen({"UP/USDT": crypto(0.0012), "DOWN/USDT": crypto(-0.0012), "FLAT/USDT": crypto(0.0)})
        a = ScenarioAgent()
        rows = {r["symbol"]: r for r in await a.rank(mk)}
        up, down, flat = [await a.run(s, mk) for s in config.SYMBOLS]
        assert up.direction == "LONG" and up.confidence >= 50 and "puesto" in up.rationale, up
        assert down.direction == "SHORT" and rows["DOWN/USDT"]["er"] > 0, down
        assert flat.direction == "NEUTRAL" and rows["FLAT/USDT"]["er"] <= 0 and "fuera de las 5" in flat.rationale, flat
        assert "4.000 escenarios (6 h)" in up.rationale and up.features["rank"] in (1.0, 2.0)
        assert db.get_state("scenarios:ranking")["top"] == [r["symbol"] for r in rows.values() if r["rank"]]
        # Los mismos datos dan los mismos escenarios; más costes, peor resultado.
        a._at = 0
        assert {r["symbol"]: r["er"] for r in await a.rank(mk)} == {s: r["er"] for s, r in rows.items()}
        config.FEE_PCT = 0.5
        a._at = 0
        assert next(r for r in await a.rank(mk) if r["symbol"] == "UP/USDT")["er"] < rows["UP/USDT"]["er"]
        config.FEE_PCT = 0.075
        # 2) Siete activos que suben: solo vota por los 5 que más prometen, en orden. Un activo sin datos no
        #    tumba a los demás: ese falla como fallo pasajero.
        config.SYMBOLS = [f"S{i}/USDT" for i in range(7)] + ["MAL/USDT"]
        mk = Scen({f"S{i}/USDT": crypto(0.0004 * (i + 1)) for i in range(7)}, broken={"MAL/USDT"})
        a = ScenarioAgent()
        rows = [r for r in await a.rank(mk) if "er" in r]
        votes = {s: await a.run(s, mk) for s in config.SYMBOLS[:7]}
        best5 = [r["symbol"] for r in sorted(rows, key=lambda r: -r["er"])[:5]]
        assert sorted(s for s, v in votes.items() if v.direction == "LONG") == sorted(best5), (votes, rows)
        assert [int(votes[s].features["rank"]) for s in best5] == [1, 2, 3, 4, 5]  # puestos por resultado medio
        assert all(v.direction == "NEUTRAL" for s, v in votes.items() if s not in best5)
        assert {"S5/USDT", "S6/USDT"} <= set(best5) and "S0/USDT" not in best5  # los que más suben, dentro
        try:
            await a.run("MAL/USDT", mk)
            raise AssertionError("debió fallar")
        except ConnectionError as e:
            assert isinstance(e, engine.TRANSIENT)
        # 3) La cuenta de cada escenario: siempre +1 % por vela. El largo llega al objetivo y el corto a su stop.
        price, atr_v = 100.0, 1.0
        up_bar = np.tile(np.array([1.0, 1.01, 0.9999, 1.01]), (1000, 20, 1))
        out = ScenarioAgent.outcomes(price, atr_v, up_bar, "X/USDT")
        dist, cost = 1.5, 100 * 2 * (0.075 + 0.02) / 100
        assert out["LONG"]["ptp"] == 1 and abs(out["LONG"]["er"] - (2 * dist - cost) / (dist + cost)) < 1e-12, out
        assert out["SHORT"]["psl"] == 1 and out["SHORT"]["er"] == -1.0
        both = np.tile(np.array([1.0, 1.04, 0.96, 1.0]), (1000, 5, 1))  # una vela que toca los dos niveles
        assert ScenarioAgent.outcomes(price, atr_v, both, "X/USDT")["LONG"]["psl"] == 1
        # 4) Acciones: con la bolsa cerrada no simula; con menos de 20 sesiones completas tampoco; con ellas,
        #    cada tramo sale de la misma hora del día (±2 velas) y el escenario acaba antes del cierre.
        config.SYMBOLS = ["NVDA", "META"]
        mk = Scen({"NVDA": sessions(0.0008, 25), "META": sessions(0.0008, 12)})
        risk.nyse_open_since = lambda now=None: None
        a = ScenarioAgent()
        await a.rank(mk)
        assert "cerrada" in (await a.run("NVDA", mk)).rationale
        opens = dt.datetime.now(risk.NEW_YORK) - dt.timedelta(hours=2)
        risk.nyse_open_since = lambda now=None: opens.timestamp()
        risk.nyse_hours = lambda t: (opens, opens + dt.timedelta(hours=6, minutes=30))
        a = ScenarioAgent()
        await a.rank(mk)
        nvda, meta = await a.run("NVDA", mk), await a.run("META", mk)
        assert nvda.direction == "LONG" and "hasta las" in nvda.rationale, nvda
        assert meta.direction == "NEUTRAL" and "solo 12 sesiones" in meta.rationale, meta
        # Un medio día (cierre temprano) y las velas de hoy no entran en los escenarios: solo sesiones completas.
        half = dt.datetime.combine(dt.date(2026, 7, 2), dt.time(9, 30), risk.NEW_YORK)
        today = dt.datetime.combine(dt.datetime.now(risk.NEW_YORK).date(), dt.time(9, 30), risk.NEW_YORK)
        extra = [pd.DataFrame({"ts": pd.date_range(t0, periods=n, freq="5min").tz_convert("UTC"), "open": 100.0,
                               "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}) for t0, n in ((half, 42), (today, 10))]
        pools, _ = ScenarioAgent.stock_pools(pd.concat([mk.data["NVDA"], *extra]).sort_values("ts"))
        marked = {i: np.full((3, 4), float(i)) for i in range(1, scenario_agent.SESSION_BARS)}
        picked = set(ScenarioAgent._draw(np.random.default_rng(0), marked, 10)[:, 0])
        assert len(pools) == 77 and all(len(p) == 25 for p in pools.values()) and picked <= {8.0, 9.0, 10.0, 11.0, 12.0}
        # 4) El examen (scenario_exam.py): tocar el futuro no cambia lo que el bot esperaba al decidir, y el
        # resultado real sale de las velas que vinieron después.
        import scenario_exam
        config.SCENARIO_HOURS, config.SCENARIOS = 1, 500
        m5 = crypto(0.0, n=1300).reset_index(drop=True)
        m5["ts"] = pd.date_range("2026-01-01", periods=len(m5), freq="5min", tz="UTC")
        base = scenario_exam.crypto_bets("X/USDT", m5, 1)
        t = base[-1]["t"]
        jump = m5.copy()
        for col in ("open", "high", "low", "close"):  # se entra a la apertura de t; desde ahí, todo un 10 % más arriba
            jump.loc[jump.ts > t if col == "open" else jump.ts >= t, col] *= 1.10
        moved = scenario_exam.crypto_bets("X/USDT", jump, 1)
        assert len(base) == len(moved) >= 10 and [b["er"] for b in base] == [b["er"] for b in moved], "mira el futuro"
        last = moved[-1]
        assert max(last["r"], last["r_other"]) > 1.0 and min(last["r"], last["r_other"]) == -1.0, last
        picks = scenario_exam.chosen([{**b, "t": 0, "er": e} for b, e in zip(base[:7], (.3, -.1, .2, .5, .1, .4, .05))])
        assert [p["er"] for p in picks] == [.5, .4, .3, .2, .1], picks
    finally:
        risk.nyse_open_since, risk.nyse_hours = real
        for k, v in snap.items():
            setattr(config, k, v)
        db.del_state("scenarios:ranking")


async def check_funded():
    """Acciones con precios de Yahoo y la cuenta de fondeo simulada: horario de Nueva York, costes y acciones
    enteras, datos sin red, entra primero la señal más fuerte, cierre antes del cierre de la bolsa y en la
    apertura de lo que quedó abierto, suelo que sigue al mejor cierre diario, margen hasta el kill switch,
    cuenta perdida aunque rebote, y relevo de cuenta con su lección."""
    # Parte de valores conocidos (la comprobación de Topstep deja los suyos) y lo deja todo como estaba.
    snap = {k: v for k, v in vars(config).items() if k.isupper()}
    for k, v in dict(BROKER="ccxt", EXECUTION_MODE="paper", FEE_PCT=0.075, SLIPPAGE_PCT=0.02, MIN_NOTIONAL=10.0,
                     MAX_LEVERAGE=3.0, MAX_TRADES_PER_DAY=0, MIN_ORDER_GAP_S=0.0, PROP_KILL_AT_PCT=75.0,
                     PROP_ACCOUNT_FLOOR_USD=0.0, RISK_PER_TRADE_PCT=1.0, ATR_SL_MULT=1.5, REWARD_RISK=2.0,
                     DECISION_THRESHOLD=0.25, MIN_AGENTS=3, ATR_TIMEFRAME="15m", MAX_SL_PCT=5.0, STOCK_FEE_PCT=0.005,
                     STOCK_SLIPPAGE_PCT=0.02, STOCK_OPEN_WAIT_MIN=60.0, STOCK_LAST_ENTRY_MIN=30.0, STOCK_FLAT_MIN=5.0,
                     STOCK_TIMEFRAME="1h", PROP_MAX_LOSS_USD=0.0, FUNDED=False).items():
        setattr(config, k, v)
    try:
        await _check_funded()
    finally:
        for k, v in snap.items():
            setattr(config, k, v)


async def _check_funded():
    import requests
    from utils import exchange
    from agents.smc_agent import SMCAgent
    from agents.wyckoff_agent import WyckoffAgent
    u = lambda *a: dt.datetime(*a, tzinfo=dt.timezone.utc)
    # 1) Qué es una acción y su horario. Lunes 16-11-2026: Nueva York va 5 h por detrás de UTC hasta marzo.
    assert risk.is_stock("NVDA") and not risk.is_stock(SYM) and not risk.is_stock("BTC/USD")
    config.BROKER = "topstepx"
    assert not risk.is_stock("MNQ")  # en Topstep un símbolo sin "/" es un contrato de futuros
    config.BROKER = "ccxt"
    S = risk.nyse_session
    assert S(u(2026, 11, 16, 15, 0)) == (False, False)  # 10:00: aún no cerró la primera vela de 1 h
    assert S(u(2026, 11, 16, 15, 31)) == (True, False)  # 10:31
    assert S(u(2026, 11, 16, 20, 31)) == (False, False)  # 15:31: la última media hora no se entra
    assert S(u(2026, 11, 16, 20, 55)) == (False, True)  # 15:55: se cierra todo
    for closed in (u(2026, 11, 16, 13, 0), u(2026, 11, 16, 21, 30), u(2026, 11, 21, 15, 30), u(2026, 11, 26, 15, 30)):
        assert S(closed) == (False, False) and risk.nyse_open_since(closed) is None  # antes, después, sábado, festivo
    assert risk.nyse_open_since(u(2026, 11, 16, 15, 0)) == u(2026, 11, 16, 14, 30).timestamp()
    assert S(u(2026, 11, 27, 17, 0)) == (True, False) and S(u(2026, 11, 27, 17, 55)) == (False, True)  # cierra a las 13:00
    assert S(u(2027, 3, 15, 14, 45)) == (True, False) and S(u(2027, 3, 12, 14, 45)) == (False, False)  # horario de verano

    # 2) Costes de acciones (0,005 % + 0,02 % por lado) en el tamaño, y acciones enteras, siempre hacia abajo.
    p = risk.build_plan("LONG", 100.0, 1.0, 50000, symbol="NVDA")
    assert abs(p["cost_est"] / p["qty"] - 0.05) < 1e-12 and abs(p["risk_usd"] - 500) < 1e-9, p  # cripto: 0,19
    c = risk.build_plan("LONG", 100.0, 1.0, 50000)
    assert abs(c["cost_est"] / c["qty"] - 0.19) < 1e-12
    fm = FakeMarket()
    pb = PaperBroker(fm)
    f = await pb.open("NVDA", "LONG", 129.9, 98.0, 104.0, 100.0)
    assert f["qty"] == 129.0 and abs(f["price"] - 100.02) < 1e-9 and abs(f["fee"] - 100.02 * 129 * 0.00005) < 1e-9, f
    assert (await pb.open(SYM, "LONG", 0.7, 98.0, 104.0, 100.0))["qty"] == 0.7  # cripto: fracciones
    try:
        await pb.open("NVDA", "LONG", 0.7, 98.0, 104.0, 100.0)
        raise AssertionError("menos de una acción no se compra")
    except ValueError:
        pass
    # Un stop de acciones que salta con un hueco se ejecuta donde abrió la vela, no en el stop; en cripto, en el stop.
    gap = {"symbol": "NVDA", "side": "LONG", "qty": 10.0, "sl": 98.0, "tp": 104.0, "opened_ts": time.time() - 600}
    fm.p = 95.0
    res = await pb.check_exit(gap)
    assert res["reason"] == "SL" and abs(res["price"] - 95.0 * 0.9998) < 1e-9, res
    assert abs((await pb.check_exit(gap | {"symbol": SYM}))["price"] - 98.0 * 0.9998) < 1e-9

    class NoBars(FakeMarket):  # en la apertura aún no hay vela cerrada: solo el precio de ahora ve el hueco
        async def ohlcv(self, symbol, tf, limit=300):
            return self.df.iloc[0:0]

    nb = NoBars()
    nb.p = 95.0
    assert abs((await PaperBroker(nb).check_exit(gap))["price"] - 95.0 * 0.9998) < 1e-9
    # Tras un apagado de 20 h, el stop que se tocó hace 19 h y luego se recuperó también cuenta (velas de 5 min).
    old = time.time() - 20 * 3600

    class Downtime(FakeMarket):
        asked = []

        async def ohlcv(self, symbol, tf, limit=300):
            self.asked.append(tf)
            ts = pd.to_datetime([old + 3600, old + 3900], unit="s", utc=True)
            return pd.DataFrame({"ts": ts, "open": 100.0, "high": 100.5, "low": [97.0, 99.5], "close": 100.0,
                                 "volume": 1.0})

    dm = Downtime()
    dm.p = 101.0
    res = await PaperBroker(dm).check_exit(gap | {"symbol": SYM, "opened_ts": old})
    assert res and res["reason"] == "SL" and dm.asked == ["5m"], (res, dm.asked)
    fm.p = 100.0

    # 3) Datos de Yahoo sin red: velas vacías y la vela en curso fuera, 4 h desde 1 h, último precio, caché, y
    #    cualquier fallo (también un formato raro o un 429) como ConnectionError: el motor lo trata como pasajero.
    calls, old_symbols, old_ex = [], config.SYMBOLS, config.EXCHANGE_ID
    base = int(time.time()) // 3600 * 3600 - 12 * 3600

    def fake(symbol, interval, span):
        calls.append((symbol, interval, span))
        if symbol == "ZZZZ":
            return 404, {"chart": {"result": None, "error": {"code": "Not Found"}}}
        if symbol == "LENTO":
            raise requests.ConnectionError("sin red")
        if symbol == "RARO":
            return 200, {"chart": {"result": [{"meta": {}, "timestamp": [1], "indicators": {"quote": [{}]}}]}}
        if symbol == "SINPRECIO":
            return 200, {"chart": {"result": [{"meta": {"regularMarketPrice": None}}]}}
        if symbol == "MUCHAS":
            return 429, {}
        closes = [100.0 + i for i in range(11)]
        closes[3] = None  # una vela sin negocio
        return 200, {"chart": {"result": [{"meta": {"regularMarketPrice": 109.5},
                                           # 10 velas cerradas de 1 h y la que está en curso (empezó hace un minuto)
                                           "timestamp": [base + 3600 * i for i in range(10)] + [int(time.time()) - 60],
                                           "indicators": {"quote": [{
                                               "open": closes, "close": closes, "volume": [1000] * 11,
                                               "high": [c and c + 1 for c in closes], "low": [c and c - 1 for c in closes]}]}}]}}

    real_get, exchange._yahoo_get = exchange._yahoo_get, fake
    try:
        m = Market()
        df = await m.ohlcv("NVDA", "1h", 5)
        assert list(df.close) == [105, 106, 107, 108, 109] and str(df.ts.dt.tz) == "UTC", df  # sin la vela en curso
        full = await m.ohlcv("NVDA", "1h", 50)
        assert len(full) == 9 and 103.0 not in set(full.close) and ("NVDA", "1h", "1y") in calls
        n = len(calls)
        h4 = await m.ohlcv("NVDA", "4h", 300)  # sale de las de 1 h, ya en caché: ni una petición más
        now = pd.Timestamp.now(tz="UTC")
        assert len(calls) == n and 0 < len(h4) < 9 and h4.low.min() == 99 and (h4.ts + pd.Timedelta(hours=4) <= now).all()
        assert await m.price("NVDA") == 109.5 and calls[-1] == ("NVDA", "1d", "1d")
        for bad in ("ZZZZ", "LENTO", "RARO", "SINPRECIO"):
            for ask in (m.ohlcv(bad, "5m", 10), m.price(bad)):
                try:
                    await ask
                    raise AssertionError(bad)
                except ConnectionError as e:
                    assert isinstance(e, engine.TRANSIENT)
        try:
            await m.ohlcv("MUCHAS", "5m", 10)
            raise AssertionError("429")
        except ConnectionError:
            n = len(calls)
        try:  # tras un 429 no se insiste: la siguiente petición ni sale
            await m.ohlcv("META", "5m", 10)
            raise AssertionError("pausa tras el 429")
        except ConnectionError:
            assert len(calls) == n
        exchange._yahoo_pause = 0.0
        try:
            await m.book_imbalance("NVDA")
            raise AssertionError("las acciones no tienen libro")
        except NotImplementedError:
            pass
        # Los bots de estructura y volumen piden velas de 1 h para las acciones.
        seen = []

        class Spy:
            async def ohlcv(self, symbol, tf, limit=300):
                seen.append((symbol, tf))
                return await m.ohlcv("NVDA", "1h", limit)

        await SMCAgent().run("NVDA", Spy())
        await WyckoffAgent().run("NVDA", Spy())
        assert seen == [("NVDA", "1h"), ("NVDA", "1h")], seen
        await m.close()
        config.SYMBOLS = ["NVDA", "LENTO"]
        only = Market()
        assert only.ex is None  # solo acciones: ni se crea el exchange
        await only.start()  # un fallo pasajero al arrancar no impide arrancar
        config.SYMBOLS = ["NVDA", "ZZZZ"]
        try:
            await only.start()
            raise AssertionError("símbolo desconocido")
        except SystemExit as e:
            assert "ZZZZ" in str(e)
        await only.close()
        config.EXCHANGE_ID, config.SYMBOLS = "alpaca", ["BTC/USD", "NVDA"]
        ex = make_exchange()
        assert set(ex.markets) == {"BTC/USD"}  # Alpaca solo declara lo que es cripto
        await ex.close()
    finally:
        exchange._yahoo_get, exchange._yahoo_pause = real_get, 0.0
        config.SYMBOLS, config.EXCHANGE_ID = old_symbols, old_ex

    # 4) El jefe con una cuenta de fondeo de 50.000: 0,4 % de riesgo, límite diario 1.000 (kill switch al 75 %)
    #    y la cuenta se pierde 2.000 bajo su mejor cierre diario.
    real = risk.nyse_session, risk.nyse_open_since, risk.trading_day
    hours, day = {"s": (True, False), "since": time.time() - 3600}, {"d": "2026-11-16"}
    risk.nyse_session = lambda now=None: hours["s"]
    risk.nyse_open_since = lambda now=None: hours["since"]
    risk.trading_day = lambda now=None: day["d"]
    try:
        config.EXECUTION_MODE, config.SYMBOLS = "fondeo", ["AAA/USDT", "NVDA", "BBB/USDT"]
        config.CAPITAL, config.RISK_PER_TRADE_PCT, config.PROP_MAX_LOSS_USD, config.FUNDED = 50000.0, 0.4, 2000.0, True
        config.PROP_DAILY_LOSS_LIMIT_USD, config.TOTAL_LOSS_LIMIT_USD = 1000.0, 0.0
        config.MAX_OPEN_POSITIONS, config.COOLDOWN_S = 1, 0
        for key in ("ctl:kill", "ctl:bench", "ctl:params", "fund", "fund:history", "order:fails", "order:last_attempt",
                    "weights", "account"):
            db.del_state(key)
        db.set_state("ctl:mode", "AUTO")
        db.set_state("ctl:auto_opt", False)
        class Spy(FakeMarket):  # apunta qué velas se piden y deja fallar el precio de un activo
            asked, broken = [], set()

            async def price(self, symbol):
                if symbol in self.broken:
                    raise ConnectionError("Yahoo no responde")
                return await super().price(symbol)

            async def ohlcv(self, symbol, tf, limit=300):
                self.asked.append((symbol, tf))
                if symbol in self.broken:
                    raise ConnectionError("Yahoo no responde")
                return await super().ohlcv(symbol, tf, limit)

        fm, notes = Spy(), []
        broker = PaperBroker(fm)
        boss = BossAgent(fm, broker, {n: 600 for n in AGENT_LABELS}, None, notes.append)
        status = lambda s: db.get_state(f"boss:{s}")["status"]
        acct = lambda: db.get_state("account")

        def say(symbol, direction, conf):
            for name in AGENT_LABELS:
                db.set_state(f"signal:{name}:{symbol}", token_budget.pack(
                    Signal(agent=name, symbol=symbol, direction=direction, confidence=conf, rationale="prueba")))

        def book(pnl):  # una operación ya cerrada con ese resultado neto
            trade_logger.log_open("CCC/USDT", "LONG", 1.0, 100.0, 90.0, 120.0, 0.5, {})
            trade_logger.log_close(trade_logger.open_trades()[-1], 100.0 + pnl, "TP")

        # La base de datos ya tenía resultados: la cuenta 1 empieza igual en 50.000.
        book(300)
        say("AAA/USDT", "LONG", 60)
        say("NVDA", "SHORT", 90)
        await boss.tick()
        assert await broker.equity() == 50000 and db.get_state("fund")["base"] == 300
        assert acct()["equity"] == 50000 and acct()["day_pnl"] == 0, acct()  # sin resultado fantasma el primer día
        # Con un hueco, entra la señal más fuerte aunque vaya segunda en la lista. Acciones enteras.
        (t,) = trade_logger.open_trades()
        assert t["symbol"] == "NVDA" and t["side"] == "SHORT" and t["qty"] == 129.0, t  # 200 / (1,5 + 0,05) = 129,03
        assert status("AAA/USDT") == "máximo de posiciones abiertas" and acct()["gen"] == 1 and acct()["floor"] == 48000
        assert ("NVDA", "1h") in fm.asked and ("NVDA", "15m") not in fm.asked  # el stop de una acción, con velas de 1 h
        # Sin precio de la acción, el ciclo sigue: la posición conserva su último precio y todo lo demás corre.
        await boss.tick()
        fm.broken.add("NVDA")
        beat = db.get_state("heartbeat")
        await boss.tick()
        assert db.get_state("heartbeat") > beat and acct()["marks"][str(t["id"])][0] == 100.0
        assert [x["symbol"] for x in trade_logger.open_trades()] == ["NVDA"]
        fm.broken.clear()
        # Bolsa cerrada (el motor estuvo apagado al cierre): la acción sigue abierta, no hay precio al que cerrarla...
        hours["s"], hours["since"] = (False, False), None
        say("AAA/USDT", "NEUTRAL", 0)
        await boss.tick()
        assert [t["symbol"] for t in trade_logger.open_trades()] == ["NVDA"]
        assert status("NVDA") == "posición ya abierta"
        # ...y se cierra en cuanto abre la bolsa, aunque falten horas para el cierre.
        hours["s"], hours["since"] = (False, False), time.time() + 1
        await boss.tick()
        c = trade_logger.closed_trades(mode="fondeo")[-1]
        assert not trade_logger.open_trades() and c["symbol"] == "NVDA" and c["exit_reason"] == "SESION", c
        # 15:55: la acción abierta hoy se cierra y no se abre otra; cripto sigue su curso.
        hours["s"], hours["since"] = (True, False), time.time() - 3600
        await boss.tick()
        assert trade_logger.open_trades()[0]["symbol"] == "NVDA"
        hours["s"] = (False, True)
        await boss.tick()
        assert not trade_logger.open_trades() and status("NVDA").startswith("bolsa de Nueva York cerrada")
        assert trade_logger.closed_trades(mode="fondeo")[-1]["exit_reason"] == "SESION"
        say("NVDA", "NEUTRAL", 0)
        # El suelo sigue al mejor cierre diario (el último saldo de cada día) y deja de subir en el capital inicial.
        eq0 = await broker.equity()
        book(1500)
        await boss.tick()  # último ciclo del día 16
        day["d"] = "2026-11-17"
        book(-100)  # lo que pase al empezar el día 17 no cambia el cierre del 16
        await boss.tick()
        assert abs(acct()["floor"] - (eq0 + 1500 - 2000)) < 1e-6, acct()
        book(1000)
        await boss.tick()
        day["d"] = "2026-11-18"
        await boss.tick()
        assert acct()["floor"] == 50000 and abs(acct()["peak"] - (eq0 + 2400)) < 1e-6, acct()
        # Un mal día: el saldo baja a 50.350 (350 sobre el suelo). Salta el kill switch y no se abre nada.
        book(50350 - await broker.equity())
        say("AAA/USDT", "LONG", 80)
        hours["s"] = (True, False)
        await boss.tick()
        assert status("AAA/USDT").startswith("bloqueado: kill switch"), status("AAA/USDT")
        # Día nuevo: 750 hasta el kill switch, pero solo 350 hasta el suelo. Cabe una operación de 200, no dos.
        day["d"] = "2026-11-19"
        config.MAX_OPEN_POSITIONS = 3
        await boss.tick()
        assert status("AAA/USDT") == "orden ejecutada", status("AAA/USDT")
        say("BBB/USDT", "LONG", 80)
        await boss.tick()
        assert status("BBB/USDT").startswith("sin margen"), status("BBB/USDT")
        # Una ganancia flotante sube el saldo, pero también está en juego: no abre hueco para otra operación.
        fm.p = 102.5
        await boss.tick()
        assert status("BBB/USDT").startswith("sin margen"), status("BBB/USDT")
        fm.p = 100.0
        # La cuenta toca su suelo con una posición que no se puede cerrar: queda perdida y no entra nada más.
        say("AAA/USDT", "NEUTRAL", 0)
        real_close = broker.close

        async def stuck(t):
            raise ConnectionError("sin precio")

        broker.close = stuck
        book(-600)
        await boss.tick()
        assert db.get_state("fund")["gen"] == 1 and db.get_state("fund")["lost"] and len(trade_logger.open_trades()) == 1
        assert status("BBB/USDT").startswith("bloqueado: cuenta 1 perdida"), status("BBB/USDT")
        # El precio rebota y el saldo vuelve sobre el suelo: la cuenta sigue perdida, como en una prueba real.
        fm.p = 102.8
        await boss.tick()
        assert acct()["equity"] + acct()["floating"] > acct()["floor"] and db.get_state("fund")["lost"]
        assert status("BBB/USDT").startswith("bloqueado: cuenta 1 perdida")
        # En cuanto se puede cerrar lo que quedaba, el relevo se completa.
        broker.close = real_close
        say("BBB/USDT", "NEUTRAL", 0)
        born = db.get_state("fund")["born"]
        await boss.tick()
        fund, (lost,) = db.get_state("fund"), db.get_state("fund:history")
        assert fund["gen"] == 2 and "lost" not in fund and not trade_logger.open_trades()
        assert [c["exit_reason"] for c in trade_logger.closed_trades(mode="fondeo") if c["symbol"] == "AAA/USDT"] == ["MUERTE"]
        assert acct()["gen"] == 2 and acct()["floor"] == 48000 and await broker.equity() == 50000, acct()
        mine = trade_logger.closed_trades(since=born, mode="fondeo")
        assert lost["gen"] == 1 and lost["n"] == len(mine) and abs(lost["net"] - sum(t["pnl"] for t in mine)) < 1e-6
        assert acct()["day_pnl"] == 0 and fund["lesson"] != "configuración inicial", (acct(), fund)
        # La lección del umbral queda en los ajustes de la app, donde se ve y se puede deshacer.
        assert db.get_state("ctl:params")["DECISION_THRESHOLD"] == config.DECISION_THRESHOLD > 0.25, fund
        fm.p = 100.0
        assert any(n.startswith("💀 Cuenta 1 perdida") for n in notes)
        assert trade_logger.events(5, kinds=("death",))[0]["text"].startswith("Cuenta 1 perdida")
        # La cuenta nueva tiene su propio día: una pérdida de 800 salta el kill switch (750) mucho antes del suelo.
        book(-800)
        await boss.tick()
        assert acct()["blocked"].startswith("kill switch") and db.get_state("fund")["gen"] == 2
        # Una lección que veta un par, y otra que ensancha el stop: el jefe las aplica en cada ciclo.
        day["d"] = "2026-11-20"
        db.set_state("fund", db.get_state("fund") | {"blocked": [["AAA/USDT", "LONG"]], "atr_tf": "1h"})
        say("AAA/USDT", "LONG", 80)
        await boss.tick()
        assert status("AAA/USDT").startswith("vetado por la cuenta 2") and config.ATR_TIMEFRAME == "1h"
        # Una acción abierta cuando la cuenta toca su suelo con la bolsa cerrada: no se cierra al precio de ayer.
        # La cuenta queda perdida y sin operar, y el relevo se completa en la apertura, a precio de entonces.
        say("AAA/USDT", "NEUTRAL", 0)
        say("NVDA", "SHORT", 90)
        hours["s"], hours["since"] = (True, False), time.time() - 3600
        await boss.tick()
        assert [t["symbol"] for t in trade_logger.open_trades()] == ["NVDA"]
        say("NVDA", "NEUTRAL", 0)
        hours["s"], hours["since"] = (False, False), None
        book(48000 - 1 - await broker.equity())
        await boss.tick()
        assert db.get_state("fund")["gen"] == 2 and db.get_state("fund")["lost"]
        assert [t["symbol"] for t in trade_logger.open_trades()] == ["NVDA"]
        hours["since"] = time.time() + 1  # abre la bolsa
        await boss.tick()
        assert db.get_state("fund")["gen"] == 3 and not trade_logger.open_trades()
        assert [c["exit_reason"] for c in trade_logger.closed_trades(mode="fondeo") if c["symbol"] == "NVDA"][-1] == "MUERTE"
    finally:
        risk.nyse_session, risk.nyse_open_since, risk.trading_day = real
        for key in ("ctl:params", "ctl:auto_opt", "fund", "fund:history", "account"):
            db.del_state(key)

    # 5) Las acciones y la cuenta de fondeo solo existen en simulación, con su límite diario y sin valores negativos.
    run = lambda **env: subprocess.run([sys.executable, "-c", "import config; print(config.STOCKS, config.TOTAL_LOSS_LIMIT_USD)"],
                                       cwd=config.ROOT, env=os.environ | env, capture_output=True, text=True)
    assert "NVDA" in run(SYMBOLS="BTC/USDT:USDT,NVDA").stdout
    r = run(SYMBOLS="BTC/USDT:USDT,NVDA", PAPER_TRADING="false")
    assert r.returncode != 0 and "solo se simulan" in r.stderr, r.stderr
    r = run(PROP_MAX_LOSS_USD="2000", PROP_DAILY_LOSS_LIMIT_USD="1000", PAPER_TRADING="false")
    assert r.returncode != 0 and "PROP_MAX_LOSS_USD" in r.stderr, r.stderr
    assert run(PROP_MAX_LOSS_USD="2000", BROKER="topstepx", PROP_DAILY_LOSS_LIMIT_USD="1000").returncode != 0
    assert run(PROP_MAX_LOSS_USD="-2000").returncode != 0
    assert "límite diario" in run(PROP_MAX_LOSS_USD="2000", PROP_DAILY_LOSS_LIMIT_USD="0").stderr
    ok = run(PROP_MAX_LOSS_USD="2000", PROP_DAILY_LOSS_LIMIT_USD="1000", TOTAL_LOSS_LIMIT_USD="500")
    assert ok.returncode == 0 and ok.stdout.split()[-1] == "0.0", ok  # la pérdida máxima sustituye al límite total


def check_public():
    """Datos públicos (SEC, State Street, Reserva Federal) y el laboratorio de eventos: lo que se lee de cada fuente y
    que ninguna hipótesis entra antes de que el dato sea público ni mira el futuro."""
    import io
    import zipfile
    import event_lab as lab
    real_fetch, real_filings = public_data.fetch, public_data.filings
    try:
        # 1) Excel de State Street sin openpyxl: cabecera donde esté, BRK.B -> BRK-B, sin efectivo ni guiones.
        def xlsx(rows):
            ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            strings = sorted({c for r in rows for c in r if c})
            cell = lambda r, j, c: f'<c r="{"ABCD"[j]}{r}" t="s"><v>{strings.index(c)}</v></c>' if c else ""
            sheet = "".join(f'<row r="{i + 1}">{"".join(cell(i + 1, j, c) for j, c in enumerate(r))}</row>'
                            for i, r in enumerate(rows))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
                z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet}</sheetData></worksheet>")
            return buf.getvalue()

        fund = xlsx([["Fund Name:", "SPDR"], [], ["Name", "Ticker", "Weight"], ["APPLE INC", "AAPL", "7"],
                     ["BERKSHIRE HATHAWAY INC CL B", "BRK.B", "1"], ["US DOLLAR", "CASH_USD", "0.1"], ["", "-", ""],
                     ["NVIDIA CORP", "", "5"]])
        public_data.fetch = lambda url, kind="web", tries=5: fund
        u = public_data.universe(days=0)
        assert list(u.ticker) == ["AAPL", "BRK-B"] and set(u.group) == {"500"}, u  # las tres listas iguales: gana la 1.ª

        # 2) Formularios 4: compras (P/A) y ventas (S/D) de consejeros y directivos; enmiendas y 10 % fuera.
        def tsv(header, rows):
            return "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("SUBMISSION.tsv", tsv(["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"], [
                ["a1", "31-JAN-2024", "4", "0000000111"], ["a2", "01-FEB-2024", "4/A", "0000000111"],
                ["a3", "02-FEB-2024", "4", "0000000111"], ["a4", "05-FEB-2024", "4", "0000000222"]]))
            f.writestr("REPORTINGOWNER.tsv", tsv(["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"], [
                ["a1", "0000000009", "Director"], ["a2", "0000000009", "Director"], ["a3", "0000000008", "Officer"],
                ["a4", "0000000007", "TenPercentOwner"]]))
            f.writestr("NONDERIV_TRANS.tsv", tsv(["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_ACQUIRED_DISP_CD",
                                                  "TRANS_SHARES", "TRANS_PRICEPERSHARE"], [
                ["a1", "P", "A", "100", "10"], ["a1", "P", "A", "50", "10"], ["a1", "M", "A", "999", "1"],
                ["a2", "P", "A", "1", "1"], ["a3", "S", "D", "10", "20"], ["a3", "P", "D", "5", "5"],
                ["a4", "P", "A", "10", "10"]]))
        public_data.fetch = lambda url, kind="web", tries=5: z.getvalue()
        ins = public_data.insider(2024, 1).sort_values("filed").reset_index(drop=True)
        assert list(ins.code) == ["P", "S", "P"] and list(ins.value) == [1500.0, 200.0, 100.0], ins
        assert list(ins.insider) == [True, True, False] and ins.filed[0] == pd.Timestamp("2024-01-31"), ins

        # 3) Reuniones del FOMC de la web de la Reserva Federal: último día; las de dos meses, en el segundo.
        html = ("<h4>2027 FOMC Meetings</h4>"
                '<div class="fomc-meeting__month x"><strong>January</strong></div><div class="fomc-meeting__date y">26-27</div>'
                '<div class="fomc-meeting__month x"><strong>March</strong></div><div class="fomc-meeting__date y">16-17*</div>'
                '<div class="fomc-meeting__month x"><strong>Apr/May</strong></div><div class="fomc-meeting__date y">30-1</div>'
                '<div class="fomc-meeting__month x"><strong>June</strong></div><div class="fomc-meeting__date y">8 (notation vote)</div>')
        assert public_data.fomc_dates(html) == [dt.date(2027, 1, 27), dt.date(2027, 3, 17), dt.date(2027, 5, 1)]

        # 4) Comunicados de resultados: 8-K con el punto 2.02; si hay dos en menos de 20 días, el primero.
        t0 = pd.Timestamp("2024-05-22 20:21:29", tz="UTC")
        public_data.filings = lambda cik, since="2009-01-01", days=1: pd.DataFrame({
            "form": ["8-K", "8-K", "8-K", "10-Q", "8-K"], "items": ["2.02,9.01", "2.02", "5.02", "", "9.01,2.02"],
            "accepted": [t0, t0 + pd.Timedelta(days=5), t0 + pd.Timedelta(days=30), t0, t0 + pd.Timedelta(days=91)]})
        assert list(public_data.earnings(1)) == [t0, t0 + pd.Timedelta(days=91)]
    finally:
        public_data.fetch, public_data.filings = real_fetch, real_filings

    # 5) Limpieza de precios: fuera los errores demostrables, dentro los saltos reales aunque sean enormes.
    idx = pd.bdate_range("2020-01-01", periods=12)
    base = np.linspace(10, 11, 12)
    adj = pd.DataFrame({"ERR": base.copy(), "DIV": base.copy(), "SPK": base.copy(), "REAL": base.copy(),
                        "PENNY": base.copy()}, index=idx)
    close = adj.copy()
    adj.loc[idx[5]:, "ERR"] *= 1.61  # ajuste roto de Yahoo: el ajustado salta un 61 % y el cierre no
    adj.loc[:idx[4], "DIV"] *= 0.99  # dividendo del 1 %: el ajustado de antes baja un poco
    for frame in (adj, close):
        frame.loc[idx[6], "SPK"] *= 6  # +500 % y vuelta al día siguiente: error
        frame.loc[idx[6]:, "REAL"] *= 2  # +100 % que se queda: real
        frame.loc[:idx[5], "PENNY"] *= 0.05  # por debajo de 1 USD hasta el día 5 (bolsa de chicharros): no cuenta
    r = lab.clean(adj, close)
    assert np.isnan(r.loc[idx[5], "ERR"]) and abs(r.loc[idx[6], "ERR"]) < 0.05
    assert not r.DIV.iloc[1:].isna().any()
    assert r.SPK.iloc[6:8].isna().all() and not r.SPK.iloc[[5, 8]].isna().any()
    assert abs(r.loc[idx[6], "REAL"] - (2 * base[6] / base[5] - 1)) < 1e-9  # una posición en corto lo habría pagado
    assert r.PENNY.iloc[:7].isna().all() and not r.PENNY.iloc[7:].isna().any()

    # 6) Tamaño en cada fecha (SEC): cada fin de mes, puesto entre todas las empresas con dato de los últimos 15 meses.
    days = pd.bdate_range("2012-01-02", "2013-12-31")
    fl = pd.DataFrame({"cik": [1, 2, 3, 4, 1], "end": pd.to_datetime(["2011-06-30"] * 4 + ["2013-02-28"]),
                       "val": [9e9, 5e9, 7e15, 4e5, 1e9]})  # 7e15 y 4e5: valores imposibles, fuera
    real_ranks, lab.RANKS = lab.RANKS, (1, 2, 3)
    try:
        g = lab.pit_groups(fl, days, [1, 2, None, 4])
    finally:
        lab.RANKS = real_ranks
    at = lambda d: list(g[days.get_loc(pd.Timestamp(d))])
    assert at("2012-01-02") == [1, 2, 0, 0] and at("2012-09-28") == [1, 2, 0, 0], g[:3]
    assert at("2012-10-01") == [0, 0, 0, 0]  # el dato de junio de 2011 caduca a los 15 meses (fin de septiembre)
    assert at("2013-03-01") == [1, 0, 0, 0] and at("2013-02-28") == [0, 0, 0, 0]  # vale desde el mes siguiente

    # 7) Panel falso: el primer día del precio que reacciona y las entradas, siempre después de que el dato sea público.
    gen = np.random.default_rng(11)
    names = ["AAA", "AAB", "BBB", "BBC", "CCC", "CCD"]
    dates = pd.bdate_range("2015-01-01", periods=900)
    rets = gen.normal(0, 0.01, (len(dates), 6))
    rets[0] = np.nan
    codes = np.tile(np.array([1, 1, 2, 2, 3, 3], np.int8), (len(dates), 1))

    def panel(r, grp=codes):
        return object.__new__(lab.Panel).build(dates, names, r, np.ones(r.shape, bool), grp,
                                               dict(zip(names, ["500", "500", "400", "400", "600", "600"])),
                                               {k + 1: t for k, t in enumerate(names)})

    p = panel(rets)
    wed = pd.Timestamp("2016-05-18")  # miércoles
    i = p.at_or_after(wed)
    assert p.day0(pd.Timestamp("2016-05-18 20:21", tz="UTC")) == i + 1  # 16:21 en Nueva York: la sesión siguiente
    assert p.day0(pd.Timestamp("2016-05-18 11:00", tz="UTC")) == i  # 7:00: antes de abrir, la misma
    assert p.day0(pd.Timestamp("2016-05-18 15:00", tz="UTC")) == i  # 11:00, con la bolsa abierta: la misma
    assert p.day0(pd.Timestamp("2016-05-21 14:00", tz="UTC")) == p.at_or_after("2016-05-23")  # sábado: el lunes
    assert p.day0(pd.Timestamp("2014-12-20 14:00", tz="UTC")) is None  # antes del primer precio: fuera
    (public_data.DIR / "sec").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"x": [1]}).to_parquet(public_data.DIR / "sec" / "1.parquet")
    real_earnings = public_data.earnings
    public_data.earnings = lambda cik, days=1: pd.Series(pd.to_datetime(
        ["2014-12-20 14:00", "2015-01-01 12:00", "2016-05-18 20:21"], utc=True)) if cik == 1 else pd.Series([], dtype="datetime64[ns, UTC]")
    try:
        assert list(lab.earnings_events(p).d0) == [i + 1]  # sin precio anterior (antes o en el primer día): fuera
    finally:
        public_data.earnings = real_earnings
    clean_r = np.nan_to_num(rets)

    def excess(c, g, a, b):  # rentabilidad menos la media de su grupo, sesiones a+1..b
        return (clean_r[a + 1:b + 1, c] - clean_r[a + 1:b + 1][:, codes[0] == g].mean(1)).sum()

    # H1: compra presentada el día d -> entra al cierre de la sesión siguiente; 2 directivos o 100.000 en 30 días.
    d = p.dates[300]
    buys = pd.DataFrame({"ticker": ["AAA", "AAA"], "filed": [d, p.dates[310]], "owner": [9, 8], "value": [50e3, 60e3]})
    one = lab.h1(p, buys, {"insiders": 1, "usd": 0, "hold": 5})
    assert list(one.e) == [301, 311] and list(one.x) == [306, 316], one
    assert abs(one.ret.iloc[0] - (excess(0, 1, 301, 306) - lab.COST["500"])) < 1e-12
    assert list(lab.h1(p, buys, {"insiders": 2, "usd": 0, "hold": 5}).e) == [311]
    assert list(lab.h1(p, buys, {"insiders": 1, "usd": 100_000, "hold": 5}).e) == [311]
    assert list(lab.h1(p, buys, {"insiders": 1, "usd": 0, "hold": 21}).e) == [301]  # una posición por acción a la vez
    # El tamaño cuenta al entrar: sin grupo ese día no se opera; perder el grupo después no anula la operación.
    moved = codes.copy()
    moved[306:, 0] = 0
    assert list(lab.h1(panel(rets, moved), buys, {"insiders": 1, "usd": 0, "hold": 5}).e) == [301]
    moved[:302, 0] = 0
    assert lab.h1(panel(rets, moved), buys.iloc[:1], {"insiders": 1, "usd": 0, "hold": 5}).empty

    # H2: reacción del cierre anterior al comunicado al cierre del día siguiente, entrada en la sesión posterior; los
    # umbrales solo usan reacciones ya conocidas, así que tocar las de después no cambia ninguna decisión anterior.
    pre = [p.dates[20 + k].tz_localize(public_data.NY) + pd.Timedelta(hours=7) for k in range(700)]
    ev = pd.DataFrame({"ticker": [names[k % 6] for k in range(700)], "d0": [20 + k for k in range(700)], "accepted": pre})
    v = {"tail": 0.2, "hold": 5}
    base_h2 = lab.h2(p, ev, v)
    assert len(base_h2) > 50 and all(e - 2 in set(ev[ev.ticker == t].d0) for t, e in zip(base_h2.ticker, base_h2.e))
    cut = 500
    later = rets.copy()
    later[cut:] *= 10
    keep = lambda df: [(t, e, s) for t, e, s in zip(df.ticker, df.e, df.side) if e < cut]
    assert keep(base_h2) == keep(lab.h2(panel(later), ev, v)), "H2 mira el futuro"
    # Tras el cierre del día A: la reacción es solo la sesión A+1 y se entra al cierre de A+2, como antes de abrir.
    a = 760
    late = p.dates[a].tz_localize(public_data.NY) + pd.Timedelta(hours=16, minutes=30)
    after_close = pd.DataFrame({"ticker": ["AAA"], "accepted": [late], "d0": [a + 1]})
    spiked = rets.copy()
    spiked[a + 1, 0], spiked[a, 0], spiked[a + 2, 0] = 0.5, -0.9, -0.9  # solo cuenta el +50 % de la sesión A+1
    both_ev = pd.concat([ev[ev.d0 < a - 5], after_close], ignore_index=True)
    t2 = lab.h2(panel(spiked), both_ev, v)
    assert [(t, e, s) for t, e, s in zip(t2.ticker, t2.e, t2.side)][-1] == ("AAA", a + 2, 1), t2.tail()

    # H3: fecha esperada = la de hace un año + 364 días; entra `before` sesiones antes y sale el día siguiente.
    h3 = lab.h3(p, pd.DataFrame({"ticker": ["BBB"], "d0": [100]}), {"before": 5})
    due = p.at_or_after(p.dates[100] + pd.Timedelta(days=364))
    assert list(h3.e) == [due - 5] and list(h3.x) == [due + 1]

    # Estadísticas: una sola operación = su resultado; el coste, el primer día.
    s = lab.stats(p, one.iloc[:1], p.dates[290], p.dates[330])
    assert s["n"] == 1 and abs(sum(s["years"].values()) - one.ret.iloc[0]) < 2e-4, (s, one.ret.iloc[0])

    # 8) H4 con velas de 1 hora: hueco >= 3 %, primera hora a favor, entrada 10:30, stop al otro extremo, 2R; el coste
    # dentro de los 200 de riesgo, como el motor.
    def session(day, bars):
        ts = [pd.Timestamp(f"{day} 09:30", tz=public_data.NY) + pd.Timedelta(hours=k) for k in range(len(bars))]
        return pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(ts))

    def h4_with(bars0):
        prev = session("2016-05-18", [(99, 100, 98, 99.5)] * 6 + [(99.5, 100.2, 99.4, 100)])
        h = pd.concat([prev, session("2016-05-19", bars0)])
        public_data.price_file("hourly", "AAA").parent.mkdir(parents=True, exist_ok=True)
        h.to_parquet(public_data.price_file("hourly", "AAA"))
        ev4 = pd.DataFrame({"ticker": ["AAA"], "accepted": [pd.Timestamp("2016-05-18 21:00", tz="UTC")],
                            "d0": [p.at_or_after("2016-05-19")]})
        return lab.h4(p, ev4)

    rest = [(106, 106.5, 105.5, 106)] * 5
    cost = lab.FEE + lab.SLIP["500"]
    win = h4_with([(104, 105.5, 103.5, 105), (105, 108.5, 104.5, 108)] + rest)
    shares = int(200 // (1.5 + (105 + 103.5) * cost))
    assert len(win) == 1 and win.side[0] == 1 and win.exit[0] == 108 and win.shares[0] == shares, win
    assert abs(win.pnl[0] - (shares * 3 - shares * 213 * cost)) < 1e-6
    both = h4_with([(104, 105.5, 103.5, 105), (105, 108.5, 103.0, 104)] + rest)
    assert both.exit[0] == 103.5 and -200 <= both.pnl[0] <= -198.4, both  # toca stop y objetivo: el stop, -200 con costes
    assert h4_with([(104, 105.5, 103.5, 103.8), (105, 108.5, 104.5, 108)] + rest).empty  # primera hora en contra
    assert h4_with([(102, 105.5, 101.5, 105), (105, 108.5, 104.5, 108)] + rest).empty  # hueco de solo 2 %
    flat = h4_with([(104, 105.5, 103.5, 105), (105, 106, 104.5, 105.5)] + rest)
    assert flat.exit[0] == 106 and len(flat.path[0]) == 6, flat  # sin stop ni objetivo: sale al cierre

    # Reglas de la cuenta de fondeo vela a vela: a 750 de pérdida en el día se cierra todo; tocar el suelo con lo
    # flotante pierde la cuenta aunque el día acabe en positivo.
    day1, day2, day3 = (pd.Timestamp(f"2024-01-0{k}", tz=public_data.NY) for k in (2, 3, 4))
    at_h = lambda day, h: day + pd.Timedelta(hours=h)
    kill = pd.DataFrame({"day": [day1.tz_localize(None)] * 3, "pnl": [100.0] * 3, "R": [0.5] * 3,
                         "path": [{at_h(day1, 11): (-260.0, -270.0), at_h(day1, 15): (100.0, 100.0)}] * 3})
    st = lab.h4_stats(kill)
    assert abs(st["total"] + 810) < 1e-9, st  # cerró todo a -780 flotantes (-810 con costes), no esperó al +300
    dip = pd.DataFrame({"day": [x.tz_localize(None) for x in (day1, day2, day3)], "pnl": [-700.0, -700.0, 400.0],
                        "R": [-3.5, -3.5, 2.0],
                        "path": [{at_h(day1, 15): (-700.0, -700.0)}, {at_h(day2, 15): (-700.0, -700.0)},
                                 {at_h(day3, 11): (-650.0, -660.0), at_h(day3, 15): (400.0, 400.0)}]})
    st = lab.h4_stats(dip)
    assert st["accounts_lost"] == 1, st  # 48.600 - 650 toca el suelo de 48.000 aunque el día acabe en +400

    # 9) Segunda ronda (ESTADO.md). Los cortos pagan el préstamo, 3 % al año por sesión abierta, en la operación y en
    # la cartera; los largos no.
    short = lab.trades_from(p, [("AAA", 400, 410, -1)], lab.BORROW)
    assert abs(short.ret[0] - (-excess(0, 1, 400, 410) - lab.COST["500"] - 0.03 / 252 * 10)) < 1e-12, short
    assert abs(sum(lab.stats(p, short, p.dates[395], p.dates[420])["years"].values()) - short.ret[0]) < 2e-4
    long_ = lab.trades_from(p, [("AAA", 400, 410, 1)], lab.BORROW)
    assert abs(long_.ret[0] - (excess(0, 1, 400, 410) - lab.COST["500"])) < 1e-12
    assert lab.tag(1, "train") == "train.json" and lab.tag(2, "exam") == "exam2.json" and lab.EXAM_T == {1: 2.5, 2: 2.8}
    ok = {"n": 150, "t": 2.6, "years_pos": 3, "mean_trade": 0.001}
    assert lab.passes(ok, 1) and not lab.passes(ok, 2) and lab.passes(ok | {"t": 2.85}, 2)  # 2,8 en la segunda ronda
    assert not lab.passes(ok | {"n": 99}, 1) and not lab.passes(ok | {"years_pos": 2}, 1)
    assert lab.confirms(ok | {"t": 1.7}) and not lab.confirms(ok | {"t": 1.6}) and not lab.confirms(ok | {"mean_trade": -0.001})

    # H5: k sesiones después de la fecha esperada sin comunicado de ese trimestre -> corto hasta el día siguiente a la
    # reacción (o M sesiones). Si salió a tiempo, o se adelantó menos de 45 días, no hay operación.
    due = p.at_or_after(p.dates[100] + pd.Timedelta(days=364))
    early = p.at_or_after(p.dates[due] - pd.Timedelta(days=10))
    def h5(nxt, v, filings=(), base=(100,)):
        ev = pd.DataFrame({"ticker": "BBB", "d0": list(base) + list(nxt)})
        rep = pd.DataFrame({"ticker": "BBB", "d0": list(filings)}, columns=["ticker", "d0"])
        out = lab.h5(p, (ev, rep), v)
        return [(t, e, x, s) for t, e, x, s in zip(out.ticker, out.e, out.x, out.side) if e < 500]

    assert h5([due + 7], {"late": 2, "max": 30}) == [("BBB", due + 2, due + 8, -1)]
    assert h5([due + 7], {"late": 2, "max": 3}) == [("BBB", due + 2, due + 5, -1)]
    assert h5([due + 7], {"late": 5, "max": 30}) == [("BBB", due + 5, due + 8, -1)]
    assert h5([due + 2], {"late": 2, "max": 30}) == []  # sale justo el día k: ya se sabe al cierre
    assert h5([due], {"late": 2, "max": 30}) == [] and h5([early], {"late": 2, "max": 30}) == []
    assert h5([], {"late": 2, "max": 30}) == [("BBB", due + 2, due + 32, -1)]  # no llega: M sesiones
    # Enmienda del 24-11: el comunicado sin la etiqueta 2.02 se ve por su 10-Q/10-K (desde 15 días antes de la fecha
    # esperada; el del trimestre anterior no cuenta), y un 2.02 con otro a menos de 50 días no sirve de referencia.
    assert h5([], {"late": 2, "max": 30}, filings=[due + 1]) == []
    assert h5([], {"late": 2, "max": 30}, filings=[due + 4]) == [("BBB", due + 2, due + 5, -1)]
    prev_q = p.at_or_after(p.dates[due] - pd.Timedelta(days=30))
    assert h5([], {"late": 2, "max": 30}, filings=[prev_q]) == [("BBB", due + 2, due + 32, -1)]
    assert h5([], {"late": 2, "max": 30}, base=(100, 115)) == []  # dos 2.02 a 21 días: ninguno es un trimestre
    assert h5([], {"late": 2, "max": 30}, base=(55, 100))[-1:] == [("BBB", due + 2, due + 32, -1)]  # a 63 días: sí

    # H6: NT 10-K, NT 10-Q y 8-K con el punto 4.02 (no las enmiendas ni otros 8-K); corto al cierre del día 0.
    sec = public_data.DIR / "sec"
    pd.DataFrame({"form": pd.Series([], dtype=str), "items": pd.Series([], dtype=str),
                  "accepted": pd.Series([], dtype="datetime64[ns, UTC]")}).to_parquet(sec / "1.parquet")
    when = [p.dates[k].tz_localize(public_data.NY) + pd.Timedelta(hours=17) for k in (300, 320, 340, 360, 380)]
    pd.DataFrame({"form": ["NT 10-K", "NT 10-K/A", "8-K", "8-K", "NT 10-Q"],
                  "items": ["", "", "4.02,9.01", "2.02", ""],
                  "accepted": pd.Series(when).dt.tz_convert("UTC")}).to_parquet(sec / "3.parquet")
    news = lab.bad_news(p)
    assert list(news.ticker) == ["BBB"] * 3 and list(news.d0) == [301, 341, 381], news  # tras el cierre: sesión siguiente
    h6 = lab.h6(p, news, {"hold": 5})
    assert list(h6.e) == [301, 341, 381] and list(h6.x) == [306, 346, 386] and set(h6.side) == {-1}, h6

    # H7: ventas de al menos 2 o 3 directivos distintos en 30 días y 1 millón en total -> corto en la sesión siguiente.
    sells = pd.DataFrame({"ticker": ["CCC"] * 3, "filed": [p.dates[200], p.dates[205], p.dates[210]],
                          "owner": [1, 2, 3], "value": [600e3] * 3})
    two = lab.h7(p, sells, {"insiders": 2, "usd": 1_000_000, "hold": 21})
    three = lab.h7(p, sells, {"insiders": 3, "usd": 1_000_000, "hold": 21})
    assert list(two.e) == [206] and list(three.e) == [211] and set(two.side) == {-1} and two.fee_day[0] > 0, (two, three)
    assert lab.h7(p, sells.assign(value=300e3), {"insiders": 3, "usd": 1_000_000, "hold": 21}).empty  # 900.000: no


if __name__ == "__main__":
    checks = [check_cache, check_risk, check_agents, check_boss_decision, check_lifecycle, check_evolution,
              check_tokens_and_alerts, check_ccxt_broker, check_macro, check_optimizer, check_app, check_orb,
              check_backtest, check_alpaca, check_topstepx, check_funded, check_scenarios, check_public]
    names = [c.__name__[6:] for c in checks]
    wanted = set(sys.argv[1:])  # sin argumentos, todas; `selfcheck.py optimizer app` ejecuta solo esas dos
    if wanted - set(names):
        raise SystemExit(f"No existe: {' '.join(sorted(wanted - set(names)))}. Comprobaciones: {' '.join(names)}")
    if "evolution" in wanted:
        wanted.add("lifecycle")  # evolution comprueba el reinicio sobre la base de datos que deja lifecycle
    noise = []  # avisos del motor mientras se le provocan fallos: se guardan y solo se enseñan si la comprobación falla
    logging.lastResort = logging.Handler(logging.WARNING)
    logging.lastResort.emit = lambda record: noise.append(logging.lastResort.format(record))
    for check, name in zip(checks, names):
        if wanted and name not in wanted:
            continue
        t0 = time.time()
        try:
            res = check()
            if asyncio.iscoroutine(res):
                asyncio.run(res)
        except BaseException:
            print("\n".join(noise[-40:]), file=sys.stderr)
            raise
        noise.clear()
        print(f"{name}: bien ({time.time() - t0:.0f} s)", flush=True)
    if wanted:
        print(f"selfcheck PARCIAL ({' '.join(n for n in names if n in wanted)}): bien. Antes de un commit hay que "
              "pasarlo entero, sin argumentos")
    else:
        print("selfcheck OK: caché, riesgo y costes, 6 agentes, jefe, ciclo completo de operación, evolución "
              "(muerte, mutación, persistencia), tokens, alertas, broker CCXT, macro, recompensas y optimizador, "
              "controles de la app, ORB, backtest con validación fuera de muestra, datos de Alpaca, TopstepX "
              "(API falsa local, kill switch, sesión y frecuencia), acciones con Yahoo, cuenta de fondeo simulada, bot de escenarios "
              "y datos públicos (SEC, Reserva Federal, BLS) con su laboratorio")
