"""Panel de control (Streamlit). Lee el estado que publica main.py y escribe los controles.

    streamlit run dashboard.py
"""
import datetime as dt
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from agents import AGENT_LABELS
from utils import db, token_budget, trade_logger

st.set_page_config(page_title="Mesa de agentes", layout="wide")
COLOR = {"LONG": "green", "SHORT": "red", "NEUTRAL": "gray"}
ALERT_CONF = 70
BADGE = {"analizando": ":blue[● Analizando]", "alerta": ":orange[● Alerta]", "descanso": ":gray[● Descanso]",
         "error": ":red[● Error]", "muerto": ":red[💀 Muerto, renaciendo]"}


def ago(ts: float | None) -> str:
    if not ts:
        return "nunca"
    s = int(time.time() - ts)
    return f"hace {s}s" if s < 120 else f"hace {s // 60} min" if s < 7200 else f"hace {s // 3600} h"


def clock(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%M:%S") if ts else "—"


def signal(name: str, symbol: str) -> dict | None:
    """Última señal del agente, ya desempaquetada del JSON minificado."""
    payload = db.get_state(f"signal:{name}:{symbol}")
    return token_budget.unpack(payload).model_dump() if payload else None


# --- Controles (barra lateral) --------------------------------------------
with st.sidebar:
    st.header("Control")
    kill = db.get_state("ctl:kill", {"on": False})
    if kill.get("on"):
        st.error(f"BOT DETENIDO · {kill.get('reason') or 'kill switch manual'}")
        # Reanudar tras el límite de pérdida total reinicia la línea base:
        # aceptas un nuevo presupuesto de pérdida a partir del capital actual.
        if st.button("Reanudar operaciones", width="stretch",
                     help="Si el bot se detuvo por el límite de pérdida total, se reinicia la línea base."):
            db.set_state("ctl:kill", {"on": False})
            db.del_state(f"start_equity:{config.EXECUTION_MODE}")
            st.rerun()
    else:
        if st.button("🛑 KILL SWITCH", type="primary", width="stretch", help="Apagado de emergencia"):
            db.set_state("ctl:kill", {"on": True, "flatten": st.session_state.get("flatten", True),
                                      "reason": "kill switch manual"})
            st.rerun()
        st.checkbox("Cerrar posiciones abiertas al detener", value=True, key="flatten")

    st.divider()
    symbol = st.selectbox("Activo", config.SYMBOLS)
    mode = db.get_state("ctl:mode", config.START_MODE)
    new_mode = st.radio("Modo de operación", ["AUTO", "MANUAL"], index=0 if mode == "AUTO" else 1, horizontal=True,
                        help="AUTO ejecuta solo. MANUAL propone y espera tu aprobación.")
    if new_mode != mode:
        db.set_state("ctl:mode", new_mode)
        st.rerun()
    only_alive = st.toggle("Genealogía: solo versiones vivas", value=False)
    st.caption(f"{config.EXCHANGE_ID} · comisión {config.FEE_PCT}% + deslizamiento {config.SLIPPAGE_PCT}% por lado")


# --- Panel en vivo ----------------------------------------------------------
@st.fragment(run_every=5)
def live() -> None:
    # Indicador de modo: lo primero que se ve.
    if config.EXECUTION_MODE == "paper":
        st.success("**PAPER TRADING ACTIVO** · precios reales, órdenes simuladas con comisión y deslizamiento")
    elif config.EXECUTION_MODE == "sandbox":
        st.warning("**SANDBOX DEL EXCHANGE** · órdenes reales con fondos de prueba")
    else:
        st.error("**REAL TRADING** · estas órdenes mueven dinero real")
    hb = db.get_state("heartbeat")
    if not hb or time.time() - hb > 3 * config.BOSS_INTERVAL + 30:
        st.warning(f"El motor no responde (último ciclo: {ago(hb)}). Arráncalo con `python main.py`.")

    acct = db.get_state("account", {})
    boss = db.get_state(f"boss:{symbol}", {})
    weights = db.get_state("weights", {})
    opened = trade_logger.open_trades()
    closed = trade_logger.closed_trades(mode=config.EXECUTION_MODE)
    net = sum(t["pnl"] for t in closed)
    costs = sum(t["fees"] + t["slippage"] for t in closed)

    # --- Métricas financieras ---
    k = st.columns(5)
    k[0].metric(f"Capital ({config.QUOTE})", f"{acct.get('equity', config.CAPITAL):,.2f}",
                f"{acct.get('floating', 0):+,.2f} flotante", delta_color="off")
    k[1].metric("PnL neto", f"{net:+,.2f}", help="Después de comisiones y deslizamiento, sobre operaciones cerradas.")
    k[2].metric("Win rate global", f"{sum(t['pnl'] > 0 for t in closed) / len(closed):.0%}" if closed else "—",
                f"{len(closed)} operaciones", delta_color="off")
    k[3].metric("Costes pagados", f"{costs:,.2f}", help="Comisiones + deslizamiento acumulados.")
    k[4].metric("PnL del día", f"{acct.get('day_pnl', 0):+,.2f}", help=f"Límite diario: -{config.DAILY_LOSS_LIMIT_PCT}%")
    if acct.get("blocked"):
        st.error(f"Nuevas entradas bloqueadas: {acct['blocked']}")

    # --- Agente Jefe ---
    st.subheader("Agente Jefe")
    action = boss.get("action", "NEUTRAL")
    c = st.columns([1.2, 1, 3])
    c[0].markdown(f"**Decisión · {symbol}**\n\n## :{COLOR[action]}[{action}]")
    c[1].metric("Score", f"{boss.get('score', 0):+.2f}",
                help=f"De -1 (SHORT) a +1 (LONG). Opera a partir de ±{config.DECISION_THRESHOLD}.")
    c[2].caption(f"{boss.get('reason', 'sin datos todavía')}\n\n{boss.get('status', '')} · {ago(boss.get('ts'))}")
    plan = boss.get("plan")
    if plan and boss.get("status") == "esperando aprobación manual":
        with st.container(border=True):
            st.markdown(
                f"**Propuesta:** :{COLOR[plan['side']]}[{plan['side']}] {plan['qty']:.6g} {symbol} a ~{plan['entry']:,.6g} · "
                f"SL {plan['sl']:,.6g} · TP {plan['tp']:,.6g} · riesgo neto {plan['risk_usd']:.2f} {config.QUOTE} "
                f"(costes {plan['cost_est']:.2f}) · apalancamiento {plan['leverage']:.2f}x")
            if st.button("Aprobar y ejecutar", type="primary"):
                db.set_state(f"approve:{symbol}", time.time())
                st.toast("Aprobada: se ejecuta en el próximo ciclo si la señal sigue vigente.")

    # --- Tarjetas de agentes ---
    tree = trade_logger.genealogy()
    current = {r["agent"]: r for r in tree}  # la última fila de cada linaje es la versión vigente
    cols = st.columns(len(AGENT_LABELS))
    for col, (name, label) in zip(cols, AGENT_LABELS.items()):
        sig = signal(name, symbol)
        status = db.get_state(f"status:{name}:{symbol}", {})
        state = status.get("state", "descanso")
        if state == "descanso" and sig and (sig["veto"] or (sig["direction"] != "NEUTRAL" and sig["confidence"] >= ALERT_CONF)):
            state = "alerta"
        with col.container(border=True):
            gen = current.get(name, {}).get("name", "")
            st.markdown(f"**{label}**\n\n`{gen}` {BADGE[state]}")
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

    # --- Matriz genealógica y consola de mutaciones ---
    st.subheader("Matriz genealógica de agentes")
    if True:
        rows = []
        for r in tree:
            alive = r["status"] == "alive"
            if only_alive and not alive:
                continue
            sig = signal(r["agent"], symbol) if alive else None
            parent = next((p for p in tree if p["id"] == r["parent_id"]), None)
            rows.append({
                "Agente": AGENT_LABELS.get(r["agent"], r["agent"]), "Versión": r["name"],
                "Estado": "🟢 Vivo" if alive else "💀 Muerto-Sustituido",
                "Confianza": sig["confidence"] if sig else None,
                "Causa del último fallo": r["death_cause"] or (parent or {}).get("death_cause") or "—",
                "Reglas": len(r["rules"]), "Nació (UTC)": clock(r["born_ts"]), "Murió (UTC)": clock(r["died_ts"])})
        if rows:
            st.dataframe(pd.DataFrame(rows[::-1]), hide_index=True, width="stretch", height=280, column_config={
                "Confianza": st.column_config.ProgressColumn("Confianza", min_value=0, max_value=100, format="%d",
                                                             width="small"),
                "Causa del último fallo": st.column_config.TextColumn(width="large")})
        else:
            st.caption("El motor todavía no ha registrado ninguna generación.")
    st.subheader("Consola de mutaciones")
    if True:
        events = []
        for r in tree:
            if r["version"] > 1:
                lesson = r["rules"][-1]["why"] if r["rules"] else ""
                events.append((r["born_ts"], f"🟢 {clock(r['born_ts'])}  REBIRTH {r['name']}\n     regla nueva: {lesson}"))
            if r["status"] == "dead":
                events.append((r["died_ts"], f"💀 {clock(r['died_ts'])}  BOT_DEATH {r['name']} [{r['death_kind']}]\n"
                                             f"     causa: {r['death_cause']}\n"
                                             f"     post-mortem: {r['post_mortem'] or 'en curso…'}"))
        with st.container(height=260):
            if events:
                st.code("\n".join(text for _, text in sorted(events, key=lambda e: -e[0])[:40]), language=None)
            else:
                st.caption("Sin muertes todavía. Aquí aparecerán cada muerte, su post-mortem y su renacimiento.")

    # --- Auditoría de tokens y curva de capital ---
    left, right = st.columns([2, 3])
    tok = token_budget.today()
    with left:
        st.subheader("Auditoría de tokens")
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=tok["llm"], number={"valueformat": ","},
            title={"text": "Tokens de Claude hoy (UTC)", "font": {"size": 14}},
            gauge={"axis": {"range": [0, config.DAILY_TOKEN_BUDGET]}, "bar": {"color": "#1f77b4"},
                   "steps": [{"range": [0.8 * config.DAILY_TOKEN_BUDGET, config.DAILY_TOKEN_BUDGET], "color": "#f4c7c3"}]}))
        fig.update_layout(height=230, margin=dict(l=20, r=20, t=60, b=10))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        used = ", ".join(f"{k} {v:,}" for k, v in tok["by_kind"].items()) or "ninguna llamada"
        st.caption(f"Presupuesto diario {config.DAILY_TOKEN_BUDGET:,} · {tok['llm_calls']} llamadas ({used}). "
                   "Al agotarse, Claude se apaga hasta mañana y el sistema sigue con sus respaldos.")
        st.caption(f"Mensajes entre agentes (estimado): último ciclo {tok['last_cycle']:,} tokens · pico "
                   f"{tok['peak_cycle']:,} · hoy {tok['payload']:,} en {tok['cycles']:,} ciclos · tope por mensaje "
                   f"{config.PAYLOAD_TOKEN_LIMIT}.")
    with right:
        st.subheader("Curva de PnL neto")
        if closed:
            curve = pd.DataFrame({"t": pd.to_datetime([t["closed_ts"] for t in closed], unit="s"),
                                  "pnl": pd.Series([t["pnl"] for t in closed]).cumsum()})
            fig = go.Figure(go.Scatter(x=curve.t, y=curve.pnl, mode="lines+markers", line={"color": "#2ca02c"}))
            fig.add_hline(y=0, line_dash="dot", line_color="gray")
            fig.update_layout(height=250, margin=dict(l=20, r=20, t=10, b=10), yaxis_title=config.QUOTE)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        else:
            st.caption("Aparecerá con la primera operación cerrada.")

    # --- Operaciones ---
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
        st.subheader("Últimas cerradas")
        if closed:
            df = pd.DataFrame(closed[-15:][::-1])[["symbol", "side", "entry", "exit", "pnl", "fees", "slippage",
                                                   "exit_reason", "closed_ts"]]
            df["closed_ts"] = pd.to_datetime(df.closed_ts, unit="s").dt.strftime("%m-%d %H:%M")
            df.columns = ["Activo", "Lado", "Entrada", "Salida", "PnL neto", "Comisiones", "Desliz.", "Motivo",
                          "Cerrada (UTC)"]
            st.dataframe(df.style.format({"PnL neto": "{:+.2f}", "Comisiones": "{:.2f}", "Desliz.": "{:.2f}",
                                          "Entrada": "{:,.6g}", "Salida": "{:,.6g}"}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Ninguna todavía.")

    rep = db.get_state("optimizer:report")
    if rep and rep["metrics"].get("n"):
        m = rep["metrics"]
        st.subheader("Pesos por historial de aciertos")
        st.caption(f"Optimizador ejecutado {ago(rep['ts'])} sobre {rep['days']:g} días · profit factor "
                   + ("n/d" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}") + " · Sharpe por operación "
                   + ("n/d" if m["sharpe"] is None else f"{m['sharpe']:.2f}"))
        rows = [{"agente": AGENT_LABELS.get(a, a), "activo": s, **r} for a, by in rep["agents"].items() for s, r in by.items()]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


live()
