"""Auto-mejora: recompensa a cada bot por sus votos y, con esas recompensas, reajusta la confianza
(el peso) que el jefe le da en cada activo. También valida los ajustes que el operador cambia en la app.

Uso:
    python optimizer.py              # evalúa los últimos 30 días y guarda los pesos
    python optimizer.py --days 7     # otra ventana
    python optimizer.py --dry-run    # solo muestra el informe

El jefe lo ejecuta tras cada operación cerrada (se puede apagar en la app) y main.py una vez por semana.
"""
import statistics
import sys
import time

import config
from agents import AGENT_LABELS
from utils import db, trade_logger

MIN_SAMPLES = 5  # con menos votos de un agente en un activo no se toca su peso
PRIOR = 10  # votos "fantasma" de 0 R que frenan el ajuste con pocas muestras
W_MIN, W_MAX = 0.25, 2.0
GAIN = 2.0  # un bot que gana de media 0,25 R por voto llega a peso 1,5
R_CAP = 3.0  # una sola operación no da ni quita más de 3 R
FORCED = ("KILL", "LIMITE", "MUERTE")  # cierres forzosos: los decidió un límite de riesgo, no el mercado
SIGN = {"LONG": 1, "SHORT": -1}

# Ajustes que el operador puede cambiar desde la app, con su rango. Lo que no está aquí (stop máximo,
# límites de pérdida, dinero real) solo se cambia en .env, a propósito.
TUNABLE = {"RISK_PER_TRADE_PCT": (0.1, 2.0), "DECISION_THRESHOLD": (0.1, 0.9), "REWARD_RISK": (1.0, 5.0),
           "ATR_SL_MULT": (0.5, 4.0), "MAX_OPEN_POSITIONS": (1, 5), "COOLDOWN_S": (0, 7200),
           "MIN_AGENTS": (1, len(AGENT_LABELS))}
DEFAULTS = {k: getattr(config, k) for k in TUNABLE}  # los de .env


def params() -> dict:
    """Ajustes vigentes: los de .env más lo que el operador guardó en la app, recortado a su rango.
    Un valor que no sea un número se ignora: lo escribe otro proceso y aquí no se da por bueno."""
    saved, out = db.get_state("ctl:params", {}), {}
    for k, (lo, hi) in TUNABLE.items():
        v = saved.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            v = DEFAULTS[k]
        out[k] = type(DEFAULTS[k])(max(lo, min(hi, v)))
    return out


def apply_params() -> None:
    """El motor adopta lo guardado en la app. Lo que el operador no ha tocado se queda como esté."""
    saved = db.get_state("ctl:params", {})
    for k, v in params().items():
        if k in saved:
            setattr(config, k, v)


def reward(t: dict) -> dict[str, float]:
    """Recompensa de cada bot por una operación cerrada, en R (múltiplos de lo que se arriesgó).
    Quien votó la dirección que resultó rentable gana |R|; quien votó la contraria lo pierde. Un voto
    NEUTRAL no puntúa, y un cierre forzoso por un límite de riesgo ni premia ni castiga."""
    risked = abs(t["entry"] - t["sl"]) * t["qty"] if t.get("sl") else 0.0
    if not t.get("pnl") or not risked or t.get("exit_reason") in FORCED:
        return {}
    r = max(-R_CAP, min(R_CAP, t["pnl"] / risked))
    return {a: round(r * SIGN[t["side"]] * SIGN[v["direction"]], 3)
            for a, v in t["votes"].items() if v.get("direction") in SIGN}


def scoreboard(trades: list[dict]) -> dict:
    """Marcador de cada bot: puntos acumulados (en R), aciertos, fallos, racha y su evolución."""
    board: dict = {}
    for t in sorted(trades, key=lambda t: t["closed_ts"]):
        for agent, pts in reward(t).items():
            b = board.setdefault(agent, {"points": 0.0, "hits": 0, "misses": 0, "streak": 0, "best": 0.0,
                                         "last": 0.0, "history": []})
            b["points"] = round(b["points"] + pts, 3)
            b["hits"] += pts > 0
            b["misses"] += pts < 0
            b["streak"] = b["streak"] + 1 if pts > 0 else 0
            b["best"], b["last"] = max(b["best"], pts), pts
            b["history"].append((t["closed_ts"], b["points"]))
    return board


def metrics(trades: list[dict]) -> dict:
    pnls = [t["pnl"] for t in trades]
    if not pnls:
        return {"n": 0}
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    rets = [t["pnl"] / (t["entry"] * t["qty"]) for t in trades if t["entry"] and t["qty"]]
    sd = statistics.stdev(rets) if len(rets) > 1 else 0.0
    return {
        "n": len(pnls), "pnl": sum(pnls), "win_rate": len(wins) / len(pnls),
        "profit_factor": sum(wins) / -sum(losses) if losses else None,  # None = sin pérdidas
        # ponytail: Sharpe por operación (media/desviación del retorno sobre el
        # nocional), sin anualizar. Con pocas operaciones es ruido: úsalo para
        # comparar semanas entre sí, no como cifra absoluta.
        "sharpe": statistics.mean(rets) / sd if sd else None,
    }


def agent_stats(trades: list[dict]) -> dict:
    """{agente: {activo: [votos, aciertos, puntos]}}. Acierto = votó la dirección que resultó rentable.
    No cuentan los cierres forzosos por un límite de riesgo."""
    out: dict = {}
    for t in trades:
        if not t["pnl"] or t.get("exit_reason") in FORCED:
            continue
        right, pts = t["side"] if t["pnl"] > 0 else ("SHORT" if t["side"] == "LONG" else "LONG"), reward(t)
        for agent, v in t["votes"].items():
            if v.get("direction") in SIGN:
                s = out.setdefault(agent, {}).setdefault(t["symbol"], [0, 0, 0.0])
                s[0] += 1
                s[1] += v["direction"] == right
                s[2] += pts.get(agent, 0.0)
    return out


def new_weights(stats: dict, old: dict) -> dict:
    # ponytail: el peso sale de la recompensa media por voto, en R, suavizada (0 R -> 1,0). Techo:
    # solo se aprende de operaciones que se llegaron a abrir (sesgo de selección). Mejora:
    # registrar también las señales no operadas y puntuar lo que habría pasado.
    weights = {a: dict(s) for a, s in old.items()}
    for agent, by_symbol in stats.items():
        for symbol, (n, _, pts) in by_symbol.items():
            if n >= MIN_SAMPLES:
                weights.setdefault(agent, {})[symbol] = round(max(W_MIN, min(W_MAX, 1 + GAIN * pts / (n + PRIOR))), 3)
    return weights


def run(days: float = 30, dry_run: bool = False) -> dict:
    trades = trade_logger.closed_trades(time.time() - days * 86400)
    old = db.get_state("weights", {})
    stats = agent_stats(trades)
    weights = new_weights(stats, old)
    report = {
        "ts": time.time(), "days": days, "metrics": metrics(trades),
        "agents": {a: {s: {"votos": n, "precision": round(ok / n, 3), "puntos": round(pts, 2),
                           "peso_antes": old.get(a, {}).get(s, 1.0), "peso_nuevo": weights.get(a, {}).get(s, 1.0)}
                       for s, (n, ok, pts) in by.items()} for a, by in stats.items()},
    }
    if not dry_run:
        for a, by in report["agents"].items():  # lo que el jefe cambia queda en la bitácora
            for s, r in by.items():
                if abs(r["peso_nuevo"] - r["peso_antes"]) >= 0.05:
                    trade_logger.log_event(
                        "trust", a, f"El jefe {'sube' if r['peso_nuevo'] > r['peso_antes'] else 'baja'} su confianza de "
                        f"×{r['peso_antes']:.2f} a ×{r['peso_nuevo']:.2f} ({r['puntos']:+.1f} R en {r['votos']} votos)",
                        s, r["peso_nuevo"])
        db.set_state("weights", weights)
        db.set_state("optimizer:report", report)
        db.set_state("optimizer:last_run", report["ts"])
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    rep = run(float(args[args.index("--days") + 1]) if "--days" in args else 30, "--dry-run" in args)
    m = rep["metrics"]
    if not m["n"]:
        print(f"Sin operaciones cerradas en los últimos {rep['days']:g} días: los pesos no cambian.")
    else:
        fmt = lambda x: "n/d" if x is None else f"{x:.2f}"
        print(f"Operaciones: {m['n']} · PnL {m['pnl']:+.2f} · Win rate {m['win_rate']:.0%} · "
              f"Profit factor {fmt(m['profit_factor'])} · Sharpe/op {fmt(m['sharpe'])}")
        for agent, by in rep["agents"].items():
            for symbol, r in by.items():
                print(f"  {agent:<10} {symbol:<16} votos={r['votos']:<3} precisión={r['precision']:.0%} {r['puntos']:+.1f} R "
                      f"peso {r['peso_antes']:.2f} -> {r['peso_nuevo']:.2f}")
        print("(simulación: no se guardó nada)" if "--dry-run" in args else "Pesos guardados.")
