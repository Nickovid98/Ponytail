"""Auto-mejora: mide el rendimiento y reajusta el peso de cada agente por activo.

Uso:
    python optimizer.py              # evalúa los últimos 30 días y guarda los pesos
    python optimizer.py --days 7     # otra ventana
    python optimizer.py --dry-run    # solo muestra el informe

main.py lo ejecuta solo una vez por semana.
"""
import statistics
import sys
import time

from utils import db, trade_logger

MIN_SAMPLES = 5  # con menos votos de un agente en un activo no se toca su peso
PRIOR = 10  # votos "fantasma" al 50 % que frenan el ajuste con pocas muestras
W_MIN, W_MAX = 0.25, 2.0


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
    """{agente: {activo: [votos, aciertos]}}. Acierto = votó la dirección que resultó rentable."""
    out: dict = {}
    for t in trades:
        if not t["pnl"]:
            continue
        right = t["side"] if t["pnl"] > 0 else ("SHORT" if t["side"] == "LONG" else "LONG")
        for agent, v in t["votes"].items():
            if v.get("direction") in ("LONG", "SHORT"):
                s = out.setdefault(agent, {}).setdefault(t["symbol"], [0, 0])
                s[0] += 1
                s[1] += v["direction"] == right
    return out


def new_weights(stats: dict, old: dict) -> dict:
    # ponytail: el peso sale de la precisión suavizada (50 % -> 1,0). Techo:
    # solo se aprende de operaciones que se llegaron a abrir (sesgo de
    # selección) y la precisión ignora el tamaño de la ganancia. Mejora:
    # registrar también las señales no operadas y ponderar por PnL.
    weights = {a: dict(s) for a, s in old.items()}
    for agent, by_symbol in stats.items():
        for symbol, (n, ok) in by_symbol.items():
            if n >= MIN_SAMPLES:
                acc = (ok + PRIOR / 2) / (n + PRIOR)
                weights.setdefault(agent, {})[symbol] = round(max(W_MIN, min(W_MAX, 2 * acc)), 3)
    return weights


def run(days: float = 30, dry_run: bool = False) -> dict:
    trades = trade_logger.closed_trades(time.time() - days * 86400)
    old = db.get_state("weights", {})
    stats = agent_stats(trades)
    weights = new_weights(stats, old)
    report = {
        "ts": time.time(), "days": days, "metrics": metrics(trades),
        "agents": {a: {s: {"votos": n, "precision": round(ok / n, 3),
                           "peso_antes": old.get(a, {}).get(s, 1.0), "peso_nuevo": weights.get(a, {}).get(s, 1.0)}
                       for s, (n, ok) in by.items()} for a, by in stats.items()},
    }
    if not dry_run:
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
                print(f"  {agent:<10} {symbol:<16} votos={r['votos']:<3} precisión={r['precision']:.0%} "
                      f"peso {r['peso_antes']:.2f} -> {r['peso_nuevo']:.2f}")
        print("(simulación: no se guardó nada)" if "--dry-run" in args else "Pesos guardados.")
