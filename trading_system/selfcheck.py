"""Comprobación de extremo a extremo, sin red ni claves:  python selfcheck.py

Usa una base de datos temporal y mercados falsos. Si algo de la lógica de
riesgo, decisión, ejecución o aprendizaje se rompe, este script falla.
"""
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import types

TMP = tempfile.mkdtemp()
os.environ.update(
    DB_PATH=os.path.join(TMP, "check.db"), EXECUTION_MODE="paper", SYMBOLS="BTC/USDT:USDT",
    CAPITAL="3550", RISK_PER_TRADE_PCT="1", MAX_SL_PCT="5", ATR_SL_MULT="1.5", REWARD_RISK="2",
    MAX_LEVERAGE="3", MAX_OPEN_POSITIONS="2", MIN_NOTIONAL="10", DAILY_LOSS_LIMIT_PCT="3",
    TOTAL_LOSS_LIMIT_USD="500", DECISION_THRESHOLD="0.25", MIN_AGENTS="3", COOLDOWN_S="900",
    FEE_PCT="0.05", START_MODE="AUTO", NEWS_RSS_URLS="", ALPHAVANTAGE_API_KEY="prueba",
    ANTHROPIC_API_KEY="", ANTHROPIC_AUTH_TOKEN="", MACRO_EVENTS_FILE=os.path.join(TMP, "eventos.csv"))

import numpy as np
import pandas as pd

import config
import optimizer
from agents import AGENT_LABELS, Signal
from agents.boss_agent import BossAgent
from agents.historian_agent import HistorianAgent
from agents.macro_agent import MacroAgent, MacroOutput, Verdict, upcoming_event
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent import SMCAgent
from agents.wyckoff_agent import WyckoffAgent
from utils import alpha_vantage, cache, risk, trade_logger
from utils.exchange import CcxtBroker, PaperBroker

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
    p = risk.build_plan("LONG", 100.0, 1.0, 3550)
    assert abs(p["risk_usd"] - 35.5) < 1e-9 and p["sl"] == 98.5 and p["tp"] == 103.0, p
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
        [sys.executable, "-c", "import config; print(config.RISK_PER_TRADE_PCT)"],
        cwd=config.ROOT, env=os.environ | env, capture_output=True, text=True)
    assert run(RISK_PER_TRADE_PCT="50").stdout.strip() == "2.0"
    assert run(EXECUTION_MODE="live", CONFIRM_LIVE="").returncode != 0
    assert run(EXECUTION_MODE="live", CONFIRM_LIVE="SI_ARRIESGO_DINERO_REAL").returncode == 0


def check_agents():
    up, down, flat = trend(0.003), trend(-0.003), trend(0.0)
    for df in (up, down, flat, candles(np.full(400, 100.0))):  # ningún dato rompe a ningún agente
        for s in (HistorianAgent.analyze(SYM, df), SMCAgent.analyze(SYM, df), WyckoffAgent.analyze(SYM, df),
                  SentimentAgent.analyze(SYM, df, df, None)):
            assert 0 <= s.confidence <= 100 and "\n" not in s.rationale and len(s.rationale) <= 160
            json.dumps(s.model_dump())
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
    fm = FakeMarket()
    boss = BossAgent(fm, PaperBroker(fm), {n: 600 for n in AGENT_LABELS})
    status = lambda: cache.get_state(f"boss:{SYM}")["status"]
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for name in AGENT_LABELS:
        cache.set_state(f"signal:{name}:{SYM}", sig(name, "LONG", 80).model_dump())

    await boss.tick()
    (t,) = trade_logger.open_trades()
    assert status() == "orden ejecutada" and t["side"] == "LONG" and t["sl"] == 98.5 and t["tp"] == 103.0, t
    assert abs(t["qty"] - 35.5 / 1.5) < 1e-9 and set(t["votes"]) == set(AGENT_LABELS)  # 1 % en riesgo, votos guardados
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1 and status() == "posición ya abierta"

    fm.p = 103.5  # toca el take profit
    await boss.tick()
    (c,) = trade_logger.closed_trades()
    gross, fees = 3 * t["qty"], (100 + 103) * t["qty"] * 0.0005
    assert c["exit_reason"] == "TP" and c["exit"] == 103.0 and abs(c["pnl"] - (gross - fees)) < 1e-9, c
    assert c["votes"]["smc"]["direction"] == "LONG" and c["votes"]["smc"]["weight"] == 1.0
    assert status() == "enfriamiento tras el último cierre" and not trade_logger.open_trades()
    assert abs(cache.get_state("account")["equity"] - (3550 + c["pnl"])) < 1e-9

    config.COOLDOWN_S = 0
    cache.set_state("ctl:mode", "MANUAL")  # en manual propone y espera
    await boss.tick()
    b = cache.get_state(f"boss:{SYM}")
    assert b["status"] == "esperando aprobación manual" and b["plan"]["side"] == "LONG" and not trade_logger.open_trades()
    cache.set_state(f"approve:{SYM}", time.time())
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1 and cache.get_state(f"approve:{SYM}") == 0  # la aprobación se consume
    cache.set_state("ctl:mode", "AUTO")

    fm.p = 90.0  # salto por debajo del stop: cierra por SL
    await boss.tick()
    assert trade_logger.closed_trades()[-1]["exit_reason"] == "SL" and trade_logger.closed_trades()[-1]["pnl"] < 0
    fm.p = 100.0
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1

    # Límite diario: bloquea, cierra lo abierto y sigue bloqueado el resto del día.
    key = f"day_start:paper:{today}"
    real_start = cache.get_state(key)
    cache.set_state(key, cache.get_state("account")["equity"] * 1.05)  # como si el día hubiera empezado un 5 % más arriba
    await boss.tick()
    assert not trade_logger.open_trades() and trade_logger.closed_trades()[-1]["exit_reason"] == "LIMITE"
    assert status().startswith("bloqueado: pérdida del día"), status()
    cache.set_state(key, real_start)
    await boss.tick()
    assert status().startswith("bloqueado") and not trade_logger.open_trades()
    cache.del_state(f"halt:paper:{today}")

    # Kill switch manual, y reanudación.
    await boss.tick()
    assert len(trade_logger.open_trades()) == 1
    cache.set_state("ctl:kill", {"on": True, "flatten": True, "reason": "kill switch manual"})
    await boss.tick()
    assert not trade_logger.open_trades() and trade_logger.closed_trades()[-1]["exit_reason"] == "KILL"
    assert status() == "bloqueado: kill switch manual"
    cache.set_state("ctl:kill", {"on": False})

    # Límite de pérdida total: activa el kill switch por sí solo.
    cache.set_state("start_equity:paper", cache.get_state("account")["equity"] + 600)
    await boss.tick()
    kill = cache.get_state("ctl:kill")
    assert kill["on"] and "pérdida total" in kill["reason"] and not trade_logger.open_trades(), kill
    cache.set_state("ctl:kill", {"on": False})
    cache.del_state("start_equity:paper")


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

    cache.q("DELETE FROM cache")
    alpha_vantage._av_last = 0.0
    alpha_vantage._get = fake_get({"feed": [
        {"title": "ETF inflows surge", "source": "X", "time_published": "t", "overall_sentiment_score": 0.1,
         "ticker_sentiment": [{"ticker": "CRYPTO:BTC", "ticker_sentiment_score": "0.4"}]},
        {"title": "Miners accumulate", "source": "Y", "time_published": "t", "overall_sentiment_score": 0.3}]})
    s = await MacroAgent().run(SYM)  # sin Claude: media de puntuaciones de Alpha Vantage
    assert s.direction == "LONG" and s.confidence == 70, s

    class FakeClaude:
        def __init__(self, stop="end_turn"):
            self.n, self.stop = 0, stop
            self.messages = types.SimpleNamespace(parse=self.parse)

        async def parse(self, **kw):
            self.n, self.kw = self.n + 1, kw
            out = MacroOutput(verdicts=[Verdict(symbol=SYM, direction="SHORT", confidence=65, rationale="hackeo\ngrave")])
            return types.SimpleNamespace(stop_reason=self.stop, parsed_output=out)

    refused = FakeClaude(stop="refusal")  # negativa: cae al respaldo y no se cachea
    assert (await MacroAgent(refused).run(SYM)).direction == "LONG" and refused.n == 1
    fake = FakeClaude()
    agent = MacroAgent(fake)
    s = await agent.run(SYM)
    assert (s.direction, s.confidence, s.rationale) == ("SHORT", 65, "hackeo grave"), s
    await agent.run(SYM)
    assert fake.n == 1  # mismos titulares: no se vuelve a pagar la llamada
    assert fake.kw["model"] == config.CLAUDE_MODEL and "ETF inflows surge" in fake.kw["messages"][0]["content"]


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
    before = cache.get_state("weights", {})
    rep = optimizer.run(dry_run=True)
    assert cache.get_state("weights", {}) == before and rep["metrics"]["n"] >= 9
    optimizer.run()
    w = cache.get_state("weights")
    assert w["bueno"]["X/USDT"] == round(2 * 13 / 18, 3) and w["malo"]["X/USDT"] == round(2 * 5 / 18, 3), w
    assert "mudo" not in w and "novato" not in w  # neutral o con pocas muestras: peso intacto
    assert cache.get_state("optimizer:report")["agents"]["malo"]["X/USDT"]["precision"] == 0.0


def check_cache():
    cache.cache_set("k", {"a": 1}, 0.05)
    assert cache.cache_get("k") == {"a": 1}
    time.sleep(0.06)
    assert cache.cache_get("k") is None
    cache.set_state("s", [1, 2])
    assert cache.get_state("s") == [1, 2] and cache.get_state("nada", "x") == "x"


if __name__ == "__main__":
    check_cache()
    check_risk()
    check_agents()
    check_boss_decision()
    asyncio.run(check_lifecycle())
    asyncio.run(check_ccxt_broker())
    asyncio.run(check_macro())
    check_optimizer()
    print("selfcheck OK: caché, riesgo, 5 agentes, jefe, ciclo completo de operación, broker CCXT, macro y optimizador")
