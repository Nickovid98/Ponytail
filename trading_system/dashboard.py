"""Panel de control (Streamlit). Lee el estado que publica main.py y escribe los controles.

    streamlit run dashboard.py
"""
import time

import pandas as pd
import streamlit as st

import config
from agents import AGENT_LABELS
from utils import cache, trade_logger

st.set_page_config(page_title="Mesa de agentes", layout="wide")
COLOR = {"LONG": "green", "SHORT": "red", "NEUTRAL": "gray"}
ALERT_CONF = 70


def ago(ts: float | None) -> str:
    if not ts:
        return "nunca"
    s = int(time.time() - ts)
    return f"hace {s}s" if s < 120 else f"hace {s // 60} min" if s < 7200 else f"hace {s // 3600} h"


# --- Controles (barra lateral) --------------------------------------------
with st.sidebar:
    st.header("Control")
    symbol = st.selectbox("Activo", config.SYMBOLS)
    mode = cache.get_state("ctl:mode", config.START_MODE)
    new_mode = st.radio("Modo de operación", ["AUTO", "MANUAL"], index=0 if mode == "AUTO" else 1, horizontal=True,
                        help="AUTO ejecuta solo. MANUAL propone y espera tu aprobación.")
    if new_mode != mode:
        cache.set_state("ctl:mode", new_mode)
        st.rerun()

    st.divider()
    kill = cache.get_state("ctl:kill", {"on": False})
    if kill.get("on"):
        st.error(f"BOT DETENIDO · {kill.get('reason') or 'kill switch manual'}")
        # Reanudar tras el límite de pérdida total reinicia la línea base:
        # aceptas un nuevo presupuesto de pérdida a partir del capital actual.
        if st.button("Reanudar operaciones", width="stretch",
                     help="Si el bot se detuvo por el límite de pérdida total, se reinicia la línea base."):
            cache.set_state("ctl:kill", {"on": False})
            cache.del_state(f"start_equity:{config.EXECUTION_MODE}")
            st.rerun()
    else:
        flatten = st.checkbox("Cerrar posiciones abiertas al detener", value=True)
        if st.button("KILL SWITCH", type="primary", width="stretch"):
            cache.set_state("ctl:kill", {"on": True, "flatten": flatten, "reason": "kill switch manual"})
            st.rerun()
    st.caption(f"Modo de ejecución: **{config.EXECUTION_MODE.upper()}** · {config.EXCHANGE_ID}")


# --- Panel en vivo ----------------------------------------------------------
@st.fragment(run_every=5)
def live() -> None:
    hb = cache.get_state("heartbeat")
    if not hb or time.time() - hb > 3 * config.BOSS_INTERVAL + 30:
        st.warning(f"El motor no responde (último ciclo: {ago(hb)}). Arráncalo con `python main.py`.")

    acct = cache.get_state("account", {})
    boss = cache.get_state(f"boss:{symbol}", {})
    weights = cache.get_state("weights", {})
    opened = trade_logger.open_trades()

    st.subheader("Agente Jefe")
    action = boss.get("action", "NEUTRAL")
    c = st.columns([1.4, 1, 1, 1, 1])
    c[0].markdown(f"**Decisión · {symbol}**\n\n## :{COLOR[action]}[{action}]")
    c[1].metric("Score", f"{boss.get('score', 0):+.2f}", help="De -1 (SHORT) a +1 (LONG). Opera a partir de ±"
                f"{config.DECISION_THRESHOLD}.")
    c[2].metric(f"Capital ({config.QUOTE})", f"{acct.get('equity', 0):,.2f}")
    c[3].metric("PnL flotante", f"{acct.get('floating', 0):+,.2f}")
    c[4].metric("PnL del día", f"{acct.get('day_pnl', 0):+,.2f}", help=f"Límite diario: -{config.DAILY_LOSS_LIMIT_PCT}%")
    st.caption(f"{boss.get('reason', 'sin datos todavía')} · {boss.get('status', '')} · {ago(boss.get('ts'))}")
    if acct.get("blocked"):
        st.error(f"Nuevas entradas bloqueadas: {acct['blocked']}")

    plan = boss.get("plan")
    if plan and boss.get("status") == "esperando aprobación manual":
        with st.container(border=True):
            st.markdown(
                f"**Propuesta:** :{COLOR[plan['side']]}[{plan['side']}] {plan['qty']:.6g} {symbol} a ~{plan['entry']:,.6g} · "
                f"SL {plan['sl']:,.6g} · TP {plan['tp']:,.6g} · riesgo {plan['risk_usd']:.2f} {config.QUOTE} · "
                f"apalancamiento {plan['leverage']:.2f}x")
            if st.button("Aprobar y ejecutar", type="primary"):
                cache.set_state(f"approve:{symbol}", time.time())
                st.toast("Aprobada: se ejecuta en el próximo ciclo si la señal sigue vigente.")

    st.subheader("Agentes")
    cols = st.columns(len(AGENT_LABELS))
    for col, (name, label) in zip(cols, AGENT_LABELS.items()):
        sig = cache.get_state(f"signal:{name}:{symbol}")
        status = cache.get_state(f"status:{name}:{symbol}", {})
        state = status.get("state", "descanso")
        if state == "descanso" and sig and (sig["veto"] or (sig["direction"] != "NEUTRAL" and sig["confidence"] >= ALERT_CONF)):
            state = "alerta"
        badge = {"analizando": ":blue[● Analizando]", "alerta": ":orange[● Alerta]", "descanso": ":gray[● Descanso]",
                 "error": ":red[● Error]"}[state]
        with col.container(border=True):
            st.markdown(f"**{label}**\n\n{badge}")
            if not sig:
                st.caption(status.get("error") or "sin señal todavía")
                continue
            st.markdown(f"### :{COLOR[sig['direction']]}[{sig['direction']}]")
            st.progress(sig["confidence"] / 100, text=f"Confianza {sig['confidence']}/100")
            fmt = lambda x: "—" if x is None else f"{x:,.6g}"
            st.caption(f"Soporte {fmt(sig['support'])} · Resistencia {fmt(sig['resistance'])}")
            st.caption(sig["rationale"])
            st.caption(f"{ago(sig['ts'])} · peso {weights.get(name, {}).get(symbol, 1.0):.2f}"
                       + (f" · :red[{status['error']}]" if status.get("error") else ""))

    left, right = st.columns(2)
    with left:
        st.subheader(f"Operaciones abiertas ({len(opened)})")
        if opened:
            df = pd.DataFrame(opened)[["symbol", "side", "qty", "entry", "sl", "tp", "opened_ts"]]
            df["opened_ts"] = pd.to_datetime(df.opened_ts, unit="s").dt.strftime("%m-%d %H:%M")
            df.columns = ["Activo", "Lado", "Cantidad", "Entrada", "SL", "TP", "Abierta (UTC)"]
            st.dataframe(df.style.format({c: "{:,.6g}" for c in ("Cantidad", "Entrada", "SL", "TP")}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Ninguna.")
    with right:
        closed = trade_logger.closed_trades(time.time() - 7 * 86400, config.EXECUTION_MODE)[-15:][::-1]
        st.subheader("Últimas cerradas")
        if closed:
            df = pd.DataFrame(closed)[["symbol", "side", "entry", "exit", "pnl", "exit_reason", "closed_ts"]]
            df["closed_ts"] = pd.to_datetime(df.closed_ts, unit="s").dt.strftime("%m-%d %H:%M")
            df.columns = ["Activo", "Lado", "Entrada", "Salida", "PnL", "Motivo", "Cerrada (UTC)"]
            st.dataframe(df.style.format({"PnL": "{:+.2f}", "Entrada": "{:,.6g}", "Salida": "{:,.6g}"}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Ninguna en los últimos 7 días.")

    rep = cache.get_state("optimizer:report")
    if rep and rep["metrics"].get("n"):
        m = rep["metrics"]
        st.subheader("Auto-mejora")
        k = st.columns(4)
        k[0].metric("Operaciones", m["n"])
        k[1].metric("Win rate", f"{m['win_rate']:.0%}")
        k[2].metric("Profit factor", "n/d" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}")
        k[3].metric("Sharpe por operación", "n/d" if m["sharpe"] is None else f"{m['sharpe']:.2f}")
        rows = [{"agente": AGENT_LABELS.get(a, a), "activo": s, **r} for a, by in rep["agents"].items() for s, r in by.items()]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"Optimizador ejecutado {ago(rep['ts'])} sobre {rep['days']:g} días.")


live()
