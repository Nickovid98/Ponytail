"""Comprobación de extremo a extremo, sin red ni claves:  python selfcheck.py

Usa una base de datos temporal y mercados falsos. Si algo de la lógica de
riesgo, costes, decisión, ejecución, evolución o aprendizaje se rompe, este
script falla.
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
    ANTHROPIC_API_KEY="", ANTHROPIC_AUTH_TOKEN="", MACRO_EVENTS_FILE=os.path.join(TMP, "eventos.csv"))

import numpy as np
import pandas as pd

import backtest
import config
import optimizer
from agents import AGENT_LABELS, Signal
from agents.boss_agent import BossAgent
from agents.evolution_manager import EvolutionManager, Mutation
from agents.historian_agent import HistorianAgent
from agents.macro_agent import MacroAgent, MacroOutput, Verdict, upcoming_event
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent import SMCAgent
from agents.wyckoff_agent import WyckoffAgent
from utils import alpha_vantage, cache_manager, db, notifier, token_budget, trade_logger
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
    assert sum("BOT_DEATH" in n for n in notes) == 5 and sum("REBIRTH" in n for n in notes) == 5
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
    config.MACRO_EVENTS_FILE.write_text(
        f"# fecha UTC, evento, impacto\n{now + dt.timedelta(minutes=10):%Y-%m-%dT%H:%M:%SZ},IPC EEUU,high\n", "utf-8")
    assert upcoming_event() == "IPC EEUU"
    s = await MacroAgent().run(SYM)
    assert s.veto and s.direction == "NEUTRAL"
    config.MACRO_EVENTS_FILE.write_text(f"{now + dt.timedelta(hours=3):%Y-%m-%dT%H:%M:%SZ},FOMC,high\nbasura\n", "utf-8")
    assert upcoming_event() is None

    calls = []

    def fake_get(payload):
        def _get(url):
            calls.append(url)
            return json.dumps(payload).encode()
        return _get

    # Respuesta "endpoint premium": sin titulares y SIN reintentar en cada ciclo.
    alpha_vantage._get = fake_get({"Information": "This is a premium endpoint."})
    assert await alpha_vantage.headlines() == [] and await alpha_vantage.headlines() == [] and len(calls) == 1
    assert "sin titulares" in (await MacroAgent().run(SYM)).rationale.lower()

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


def check_optimizer():
    m = optimizer.metrics([{"pnl": p, "entry": 100, "qty": 1} for p in (10, 10, -5, -5, 20)])
    assert m["n"] == 5 and m["win_rate"] == 0.6 and m["profit_factor"] == 4.0 and m["sharpe"] > 0, m
    assert optimizer.metrics([]) == {"n": 0}

    def trade(win, votes):
        trade_logger.log_open("X/USDT", "LONG", 1, 100, 98, 104, 0.5, votes)
        t = [x for x in trade_logger.open_trades() if x["symbol"] == "X/USDT"][-1]
        trade_logger.log_close(t, 104 if win else 98, "TP" if win else "SL")

    n = lambda d: {"direction": d, "confidence": 60}
    for win in (True,) * 5 + (False,) * 3:  # "bueno" acierta siempre, "malo" nunca
        trade(win, {"bueno": n("LONG" if win else "SHORT"), "malo": n("SHORT" if win else "LONG"), "mudo": n("NEUTRAL")})
    trade(True, {"novato": n("LONG")})
    before = db.get_state("weights", {})
    rep = optimizer.run(dry_run=True)
    assert db.get_state("weights", {}) == before and rep["metrics"]["n"] >= 9
    optimizer.run()
    w = db.get_state("weights")
    assert w["bueno"]["X/USDT"] == round(2 * 13 / 18, 3) and w["malo"]["X/USDT"] == round(2 * 5 / 18, 3), w
    assert "mudo" not in w and "novato" not in w  # neutral o con pocas muestras: peso intacto
    assert db.get_state("optimizer:report")["agents"]["malo"]["X/USDT"]["precision"] == 0.0


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
    cache_manager.cache_set("k", {"a": 1}, 0.05)  # diskcache: persistente y con caducidad
    assert cache_manager.cache_get("k") == {"a": 1}
    time.sleep(0.1)
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


if __name__ == "__main__":
    check_cache()
    check_risk()
    check_agents()
    check_boss_decision()
    asyncio.run(check_lifecycle())
    asyncio.run(check_evolution())
    check_tokens_and_alerts()
    asyncio.run(check_ccxt_broker())
    asyncio.run(check_macro())
    check_optimizer()
    asyncio.run(check_backtest())
    asyncio.run(check_alpaca())
    asyncio.run(check_topstepx())
    print("selfcheck OK: caché, riesgo y costes, 5 agentes, jefe, ciclo completo de operación, evolución "
          "(muerte, mutación, persistencia), tokens, alertas, broker CCXT, macro, optimizador, backtest, datos de Alpaca y TopstepX (API falsa local, kill switch, sesión y frecuencia)")
