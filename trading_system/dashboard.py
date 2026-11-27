"""La app: enseña cómo se porta cada bot, qué decide el jefe y deja controlarlo todo.

    streamlit run dashboard.py        (en Windows basta con abrir Iniciar.bat)

Lee el estado que publica main.py en SQLite y escribe ahí los controles. El motor
se enciende y se apaga desde la propia app.
"""
import datetime as dt
import html
import json
import os
import subprocess
import sys
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import optimizer
from agents import AGENT_LABELS
from utils import db, token_budget, trade_logger
from utils import public_data as pdx
from utils import risk_manager as risk

FUNDED = config.FUNDED  # abierta con "Iniciar cuenta de fondeo.bat"
KINDS = (["cripto"] * any(not risk.on_yahoo(s) for s in config.SYMBOLS) + ["acciones"] * bool(config.STOCKS)
         + ["divisas"] * bool(config.FOREX))  # lo que opera la cuenta
st.set_page_config(page_title="Cuenta de fondeo" if FUNDED else "Mesa de bots", page_icon="👾", layout="wide")
# Cada base de datos (tu cuenta, la de fondeo) con su registro del motor y su laboratorio: las dos apps pueden
# estar abiertas a la vez sin pisarse.
OWN = "" if config.DB_PATH.name == "trades_history.db" else f"_{config.DB_PATH.stem}"
LAB = config.ROOT / "data" / f"lab{OWN}"
MOTOR_LOG = f"motor{OWN}.txt"
COLOR = {"LONG": "green", "SHORT": "red", "NEUTRAL": "gray"}
ALERT_CONF = 70
BADGE = {"analizando": ":blue[● Analizando]", "alerta": ":orange[● Alerta]", "descanso": ":gray[● Descanso]",
         "error": ":red[● Error]", "muerto": ":red[💀 Renaciendo]", "banquillo": ":violet[🪑 Banquillo]"}
BOTS = {  # la cara de cada bot: icono, nombre corto y a qué se dedica
    "historian": ("📜", "Historiador", "Busca tendencias con buen ajuste y patrones por hora en velas de 1 h."),
    "macro": ("🌍", "Macro", "Lee titulares y el calendario económico. Puede vetar una operación antes de un evento."),
    "smc": ("🎯", "SMC", "Sigue la estructura del precio intradía: rupturas, order blocks, huecos y barridos."),
    "wyckoff": ("📊", "Wyckoff", "Estudia el volumen: dónde se negocia más y si se acumula o se distribuye."),
    "sentiment": ("🧭", "Tendencia", "Mide tendencia e impulso en 4 h y 1 día, y la presión del libro de órdenes."),
    "scenarios": ("🎲", "Escenarios", "Simula miles de futuros ficticios de cada activo con sus propias velas y vota "
                  "solo por las 5 apuestas con mejor resultado medio, después de costes."),
    "orb": ("🚪", "Ruptura de apertura", "Opera la primera ruptura del rango de apertura de la sesión."),
}
ICON = {"signal": "💬", "reward": "⭐", "trust": "🤝", "trade": "💼", "risk": "🛑", "death": "💀", "rebirth": "🟢",
        "bench": "🪑", "params": "🎛️"}
LEVELS = [(0, "Aprendiz"), (5, "Competente"), (15, "Experto"), (30, "Maestro")]
PARAMS = {  # ajustes que se pueden tocar desde aquí: etiqueta, paso y explicación
    "RISK_PER_TRADE_PCT": ("Riesgo por operación (%)", 0.1, "Parte del capital que se pierde si salta el stop. Tope: 2 %."),
    "DECISION_THRESHOLD": ("Acuerdo que exige el jefe", 0.05, "De 0 a 1. Más alto: menos operaciones y más convencidas."),
    "REWARD_RISK": ("Objetivo (veces el riesgo)", 0.5, "A cuántas veces la distancia del stop se pone el objetivo."),
    "ATR_SL_MULT": ("Distancia del stop (ATR)", 0.25, "Múltiplos del movimiento medio reciente. El stop nunca pasa del 5 %."),
    "MAX_OPEN_POSITIONS": ("Posiciones abiertas a la vez", 1, "Cuántas operaciones puede tener vivas el jefe."),
    "COOLDOWN_S": ("Descanso tras un cierre (s)", 60.0, "Espera antes de volver a entrar en el mismo activo."),
    "MIN_AGENTS": ("Bots mínimos con opinión", 1, "Con menos bots con señal vigente, el jefe no opera."),
}


def bot(name: str) -> tuple[str, str, str]:
    return BOTS.get(name, ("🧩", name, ""))


def ago(ts: float | None) -> str:
    if not ts:
        return "nunca"
    s = int(time.time() - ts)
    return f"hace {s} s" if s < 120 else f"hace {s // 60} min" if s < 7200 else f"hace {s // 3600} h"


def clock(ts: float | None, fmt: str = "%d-%m %H:%M") -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime(fmt) if ts else "—"


def signal(name: str, symbol: str) -> dict | None:
    """Última señal del bot, ya desempaquetada del JSON minificado."""
    payload = db.get_state(f"signal:{name}:{symbol}")
    return token_budget.unpack(payload).model_dump() if payload else None


def level(points: float) -> tuple[str, float, str]:
    """Nivel del bot, avance hacia el siguiente (0-1) y qué le falta."""
    if points < 0:
        return "En apuros", 0.0, f"le faltan {-points:.1f} R para volver a cero"
    i = max(k for k, (floor, _) in enumerate(LEVELS) if points >= floor)
    if i == len(LEVELS) - 1:
        return LEVELS[i][1], 1.0, "nivel máximo"
    lo, hi = LEVELS[i][0], LEVELS[i + 1][0]
    return LEVELS[i][1], (points - lo) / (hi - lo), f"{hi - points:.1f} R para {LEVELS[i + 1][1]}"


def performance(trades: list[dict], capital: float, days: float) -> dict:
    """Las cifras que importan. Con menos de dos semanas no se anualiza: sería inventar."""
    net = sum(t["pnl"] for t in trades)
    return {"n": len(trades), "net": net, "ret": net / capital if capital else 0.0,
            "annual": net / capital * 365 / days if capital and days >= 14 else None,
            "win": sum(t["pnl"] > 0 for t in trades) / len(trades) if trades else None,
            "costs": sum(t["fees"] + t["slippage"] for t in trades)}


def verdict(p: dict, target: float) -> str:
    if p["annual"] is None:
        return f"Objetivo {target:.0f} % anual: todavía no hay dos semanas de historia para medirlo."
    hit = p["annual"] * 100 >= target
    return (f"{'✅' if hit else '❌'} Ritmo anual {p['annual']:+.0%} frente a tu objetivo del {target:.0f} %. "
            + ("Por ahora lo cumple; lo pasado no garantiza lo que viene." if hit else "Por ahora no lo cumple."))


def line_chart(x, y, key: str, color: str = "#2ca02c", height: int = 240, title: str = "") -> None:
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines+markers" if len(x) < 30 else "lines", line={"color": color}))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=title)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=key)


# --- Procesos que la app arranca: el motor y las simulaciones ----------------
@st.cache_resource
def procs() -> dict:
    return {}  # viven lo que viva la app; al cerrarla, el motor ve cerrarse su tubería y se apaga en orden


def spawn(args: list[str], out: str, env: dict | None = None, **kw) -> subprocess.Popen:
    (config.ROOT / "data").mkdir(exist_ok=True)
    log = open(config.ROOT / "data" / out, "w", encoding="utf-8")
    return subprocess.Popen([sys.executable, "-u", *args], cwd=config.ROOT, stdout=log, stderr=subprocess.STDOUT,
                            env=os.environ | {"PYTHONUTF8": "1"} | (env or {}),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)


def tail(name: str, lines: int = 8) -> str:
    path = config.ROOT / "data" / name
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]) if path.exists() else ""


def engine_alive() -> bool:
    e = db.get_state("engine")
    return bool(e and time.time() - e["ts"] < 20)


@st.cache_data(show_spinner=False)
def _read_sim(mtime: float) -> dict:
    return json.loads((LAB / "sim.json").read_text(encoding="utf-8"))


def sim_result() -> dict | None:
    path = LAB / "sim.json"
    return _read_sim(path.stat().st_mtime) if path.exists() else None


# --- Barra lateral: motor, emergencia y modo ---------------------------------
@st.fragment(run_every=3)
def engine_switch() -> None:
    p = procs().get("engine")
    if engine_alive():
        st.success("Motor encendido")
        if st.button("Apagar motor", width="stretch", help="Termina el ciclo en curso y se apaga."):
            db.set_state("ctl:engine", "stop")
            st.toast("El motor se apaga al terminar el ciclo en curso.")
        if config.PAPER_TRADING and trade_logger.open_trades():
            st.caption("Hay posiciones simuladas abiertas: si apagas el motor, nadie vigila su stop.")
    else:
        starting = time.time() - st.session_state.get("starting", 0) < 15 and (p is None or p.poll() is None)
        st.info("Motor arrancando…" if starting else "Motor apagado")
        if st.button("▶ Encender motor", type="primary", width="stretch", disabled=starting):
            st.session_state["starting"] = time.time()
            procs()["engine"] = spawn(["main.py", "--child"], MOTOR_LOG, stdin=subprocess.PIPE)
            st.rerun(scope="fragment")
        if p is not None and p.poll() not in (None, 0):
            st.error("El motor no pudo arrancar:")
            st.code(tail(MOTOR_LOG, 6) or "sin mensaje", language=None)


with st.sidebar:
    st.title("👾 Mesa de bots")
    if FUNDED:
        st.caption(f"**Cuenta de fondeo simulada** · {config.CAPITAL:,.0f} {config.QUOTE}")
    engine_switch()
    st.divider()
    kill = db.get_state("ctl:kill", {"on": False})
    if kill.get("on"):
        st.error(f"OPERATIVA DETENIDA · {kill.get('reason') or 'kill switch manual'}")
        # Reanudar tras el límite de pérdida total reinicia la línea base:
        # aceptas un nuevo presupuesto de pérdida a partir del capital actual.
        if st.button("Reanudar operaciones", width="stretch",
                     help="Si se detuvo por el límite de pérdida total, se reinicia la línea base."):
            db.set_state("ctl:kill", {"on": False})
            db.del_state(f"start_equity:{config.EXECUTION_MODE}")
            st.rerun()
    else:
        if st.button("🛑 KILL SWITCH", width="stretch", help="Emergencia: bloquea entradas nuevas ahora mismo."):
            db.set_state("ctl:kill", {"on": True, "flatten": st.session_state.get("flatten", True),
                                      "reason": "kill switch manual"})
            st.rerun()
        st.checkbox("Cerrar posiciones abiertas al detener", value=True, key="flatten")
    st.divider()
    symbol = st.selectbox("Activo", config.SYMBOLS)
    mode = db.get_state("ctl:mode", config.START_MODE)
    new_mode = st.radio("El jefe opera", ["AUTO", "MANUAL"], index=0 if mode == "AUTO" else 1, horizontal=True,
                        help="AUTO ejecuta solo. MANUAL propone y espera tu aprobación.")
    if new_mode != mode:
        db.set_state("ctl:mode", new_mode)
        st.rerun()
    target = st.number_input("Tu objetivo de rentabilidad anual (%)", 5, 500, int(db.get_state("ui:target", 40)), 5)
    if target != db.get_state("ui:target", 40):
        db.set_state("ui:target", target)
    st.caption(f"{config.EXCHANGE_ID} · comisión {config.FEE_PCT} % + deslizamiento {config.SLIPPAGE_PCT} % por lado"
               + (f" · acciones (Yahoo): {config.STOCK_FEE_PCT} % + {config.STOCK_SLIPPAGE_PCT} %" if config.STOCKS else "")
               + (f" · divisas (Yahoo): {config.FX_FEE_PCT} % + {config.FX_SLIPPAGE_PCT} %" if config.FOREX else ""))


# --- Piezas compartidas por lo vivo y lo simulado -----------------------------
def feed(events: list[dict], height: int = 300) -> None:
    with st.container(height=height):
        if not events:
            st.caption("Todavía no ha pasado nada. Enciende el motor y aquí verás cada paso.")
        for e in events:
            who = "El Jefe" if e["who"] == "boss" else " ".join(bot(e["who"])[:2])
            st.markdown(f"{ICON.get(e['kind'], '•')} `{clock(e['ts'], '%d-%m %H:%M:%S')}` **{who}**"
                        f"{' · ' + e['symbol'] if e['symbol'] else ''} · {e['text']}")


def bot_card(name: str, board: dict, weights: dict, live: bool) -> None:
    icon, short, _ = bot(name)
    b = board.get(name, {"points": 0.0, "hits": 0, "misses": 0, "streak": 0, "last": 0.0})
    lvl, progress, missing = level(b["points"])
    with st.container(border=True):
        if live:
            sig, status = signal(name, symbol), db.get_state(f"status:{name}:{symbol}", {})
            state = "banquillo" if name in db.get_state("ctl:bench", []) else status.get("state", "descanso")
            if state == "descanso" and sig and (sig["veto"] or (sig["direction"] != "NEUTRAL" and sig["confidence"] >= ALERT_CONF)):
                state = "alerta"
        # Un <div> y no un título: los títulos de Streamlit llevan un ancla que descuadra las tarjetas.
        st.markdown(f"<div style='font-size:1.3rem;font-weight:600'>{icon} {html.escape(short)}</div>", unsafe_allow_html=True)
        if live:
            st.markdown(BADGE[state])
        st.metric("Recompensas", f"{b['points']:+.1f} R", f"{b['last']:+.2f} R la última" if b["hits"] + b["misses"] else None)
        st.progress(progress, text=f"**{lvl}** · {missing}")
        st.caption(f"✅ {b['hits']} aciertos · ❌ {b['misses']} fallos" + (f" · 🔥 racha de {b['streak']}" if b["streak"] >= 2 else ""))
        if live:
            st.caption(f"Confianza del jefe ×{weights.get(name, {}).get(symbol, 1.0):.2f}")
            if status.get("error"):  # el detalle técnico queda en data/motor.txt; aquí, lo que importa
                st.caption(f":red[Sin datos ahora ({status['error'].split(':')[0]}). Lo reintenta solo.]")
            if not sig:
                st.caption("Sin opinión todavía.")
                return
            st.markdown(f"**Ahora:** :{COLOR[sig['direction']]}[{sig['direction']}] · convicción {sig['confidence']}")
            st.caption(f"{sig['rationale']} ({ago(sig['ts'])})")


def bot_detail(name: str, trades: list[dict], board: dict, live: bool) -> None:
    icon, short, about = bot(name)
    st.subheader(f"{icon} {short}")
    st.caption(about)
    left, right = st.columns([3, 2])
    with left:
        history = board.get(name, {}).get("history", [])
        if history:
            line_chart(pd.to_datetime([h[0] for h in history], unit="s"), [h[1] for h in history], f"pts-{name}-{live}",
                       "#f5a623", title="Recompensas acumuladas (R)")
        else:
            st.caption("Aún no ha cobrado ni pagado ninguna recompensa: llegan al cerrarse una operación en la que votó.")
        rows = [{"Cerrada (UTC)": clock(t["closed_ts"]), "Activo": t["symbol"], "Operación": t["side"],
                 "Su voto": t["votes"][name]["direction"], "Convicción": t["votes"][name].get("confidence"),
                 "Resultado": t["pnl"], "Recompensa (R)": optimizer.reward(t)[name]}
                for t in reversed(trades) if name in optimizer.reward(t)][:40]
        if rows:
            st.dataframe(pd.DataFrame(rows).style.format({"Resultado": "{:+.2f}", "Recompensa (R)": "{:+.2f}"}),
                         hide_index=True, width="stretch", height=260)
    with right:
        if not live:
            st.caption("Datos de la última simulación. El banquillo y las lecciones son del motor en vivo.")
            return
        benched = set(db.get_state("ctl:bench", []))
        on = st.toggle("En el equipo (apágalo para mandarlo al banquillo)", value=name not in benched, key=f"bench-{name}")
        if on == (name in benched):
            db.set_state("ctl:bench", sorted(benched ^ {name}))
            trade_logger.log_event("bench", name, "Vuelve al equipo" if on else "Al banquillo por decisión tuya: ni analiza ni vota")
            st.rerun()
        if name == "macro" and not on:
            st.caption("Ojo: en el banquillo, Macro tampoco veta operaciones antes de un evento.")
        versions = [r for r in trade_logger.genealogy() if r["agent"] == name]
        if versions:
            cur = versions[-1]
            st.markdown(f"**Generación actual:** `{cur['name']}` · {len(versions) - 1} relevos")
            for r in cur["rules"][-5:]:
                st.caption(f"📌 Lección heredada: {r.get('why', '')}")
            deaths = [r for r in versions if r["status"] == "dead"][-3:]
            for r in reversed(deaths):
                st.caption(f"💀 {r['name']} · {clock(r['died_ts'])} · {r['death_cause']}")
        st.markdown("**Su actividad**")
        feed(trade_logger.events(30, who=name), height=220)


# --- 1. Sala de control ------------------------------------------------------
def scenarios_table() -> None:
    """Lo que ve el bot de escenarios: el mejor lado de cada activo, con sus probabilidades, y sus 5 elegidas."""
    rk = db.get_state("scenarios:ranking")
    st.subheader(f"🎲 Las {config.SCENARIO_TOP} mejores apuestas según {config.SCENARIOS:,} escenarios".replace(",", "."))
    if not rk:
        st.caption("Aparecerá cuando el motor haya hecho su primera ronda de escenarios.")
        return
    rows = [{"Puesto": r["rank"] or "—", "Activo": r["symbol"], "Lado": r["side"], "Objetivo primero": r["ptp"],
             "Stop primero": r["psl"], "Sin resolver": r["popen"], "Media (R)": r["er"], "Hasta": r["horizon"]}
            for r in rk["rows"] if "er" in r]
    if rows:
        st.dataframe(pd.DataFrame(rows).style.format({"Objetivo primero": "{:.0%}", "Stop primero": "{:.0%}",
                                                      "Sin resolver": "{:.0%}", "Media (R)": "{:+.2f}"}),
                     hide_index=True, width="stretch")
    quiet = [f"{r['symbol']}: {r['why']}" for r in rk["rows"] if "why" in r]
    st.caption((f"Elegidas: {', '.join(rk['top'])}. " if rk["top"] else "Ninguna apuesta tiene media positiva después "
                "de costes: el bot no vota por nada. ") + f"Calculado {ago(rk['ts'])}. Cada escenario rehace el futuro con "
               "velas pasadas del propio activo: repite cómo se movió hace poco, no predice."
               + (" · " + " · ".join(quiet[:3]) if quiet else ""))


def funded_panel(acct: dict) -> None:
    """La cuenta de fondeo simulada: cuánto le queda hasta el kill switch de hoy y hasta perderla."""
    total = acct.get("equity", config.CAPITAL) + acct.get("floating", 0.0)
    floor = acct.get("floor", config.CAPITAL - config.PROP_MAX_LOSS_USD)
    kill_at = config.PROP_DAILY_LOSS_LIMIT_USD * config.PROP_KILL_AT_PCT / 100
    lost_today = max(0.0, -acct.get("day_pnl", 0.0))
    history = db.get_state("fund:history", [])
    with st.container(border=True):
        c = st.columns(4)
        c[0].metric("Cuenta", f"n.º {acct.get('gen', 1)}", f"{len(history)} perdidas antes", delta_color="off", delta_arrow="off")
        c[1].metric("Saldo de esta cuenta", f"{total:,.2f}", f"{total - config.CAPITAL:+,.2f}",
                    help="Con lo flotante de las posiciones abiertas. Cada cuenta nueva empieza en el capital inicial.")
        c[2].metric("Suelo", f"{floor:,.2f}", f"le quedan {total - floor:,.2f}", delta_color="off", delta_arrow="off",
                    help=f"Si el saldo llega aquí la cuenta se pierde y nace otra. Sube con el mejor cierre diario "
                         f"(queda {config.PROP_MAX_LOSS_USD:,.0f} por debajo) y deja de subir en {config.CAPITAL:,.0f}.")
        c[3].metric("Pérdida de hoy", f"{lost_today:,.2f}", f"kill switch a {kill_at:,.0f}", delta_color="off", delta_arrow="off",
                    help=f"Al {config.PROP_KILL_AT_PCT:g} % del límite diario de {config.PROP_DAILY_LOSS_LIMIT_USD:,.0f} "
                         "se cierra todo y no se opera hasta el día siguiente (UTC).")
        if kill_at:
            st.progress(min(1.0, lost_today / kill_at), text=f"Hoy: {lost_today:,.0f} de {kill_at:,.0f} hasta el kill switch")
        st.progress(max(0.0, min(1.0, 1 - (total - floor) / config.PROP_MAX_LOSS_USD)),
                    text=f"Margen hasta perder la cuenta: {total - floor:,.0f}")
        if config.STOCKS:
            entries, _ = risk.nyse_session()
            state = "abierta, el jefe puede entrar" if entries else "abierta, ahora sin entradas nuevas" \
                if risk.nyse_open_since() else "cerrada"
            st.caption(f"Bolsa de Nueva York {state}. Acciones con velas de {config.STOCK_TIMEFRAME}: no se entra en los "
                       f"primeros {config.STOCK_OPEN_WAIT_MIN:g} min ni en los últimos {config.STOCK_LAST_ENTRY_MIN:g}, y se "
                       f"cierran {config.STOCK_FLAT_MIN:g} min antes del cierre (16:00, o 13:00 los días de cierre temprano). "
                       "Cripto opera las 24 h.")
        if config.FOREX:
            entries, _ = risk.fx_session()
            state = "abierto, el jefe puede entrar" if entries else "abierto, ahora sin entradas nuevas" \
                if risk.fx_open_since() else "cerrado (fin de semana)"
            st.caption(f"Mercado de divisas {state}. {', '.join(config.FOREX)} con velas de {config.STOCK_TIMEFRAME}: "
                       f"abre el domingo a las 17:00 de Nueva York (se entra desde {config.FX_OPEN_WAIT_MIN:g} min después, "
                       f"cuando Yahoo ya da precios), no se entra en los últimos {config.FX_LAST_ENTRY_MIN:g} min del viernes "
                       f"y todo se cierra {config.FX_FLAT_MIN:g} min antes de su cierre, a las 17:00.")
        fund = db.get_state("fund", {})
        if fund.get("gen", 1) > 1:
            st.caption(f"Lo que corrige esta cuenta: {fund.get('lesson')}")
        if history:
            with st.expander(f"Cuentas perdidas ({len(history)})"):
                st.dataframe(pd.DataFrame([{"Cuenta": h["gen"], "Abierta": clock(h["born"]), "Perdida": clock(h["end"]),
                                            "Operaciones": h["n"], "Resultado": h["net"], "Corregía": h["lesson"]}
                                           for h in history[::-1]]).style.format({"Resultado": "{:+,.2f}"}),
                             hide_index=True, width="stretch")


@st.fragment(run_every=5)
def control_room() -> None:
    if FUNDED:
        st.success(f"**CUENTA DE FONDEO SIMULADA** · {config.CAPITAL:,.0f} {config.QUOTE}"
                   + (" · " + ", ".join(KINDS[:-1]) + " y " + KINDS[-1] if len(KINDS) > 1 else "")
                   + ", en largo y en corto · precios reales, "
                   "ninguna orden sale de este equipo")
    elif config.EXECUTION_MODE == "paper":
        st.success("**SIMULACIÓN CON PRECIOS REALES** · ninguna orden sale de este equipo")
    elif config.EXECUTION_MODE == "sandbox":
        st.warning("**SANDBOX DEL EXCHANGE** · órdenes reales con fondos de prueba")
    else:
        st.error("**DINERO REAL** · estas órdenes mueven dinero real")
    hb, failed = db.get_state("heartbeat"), db.get_state("boss:error")
    if engine_alive() and failed:
        st.warning(f"El jefe no pudo completar su último ciclo ({ago(failed['ts'])}): {failed['error']}. Lo reintenta solo "
                   "cada pocos segundos y mientras tanto no opera ni vigila los stops simulados. Si se repite, el detalle "
                   f"está en data/{MOTOR_LOG}.")
    elif engine_alive() and hb and time.time() - hb > 3 * config.BOSS_INTERVAL + 30:
        st.warning(f"El motor está encendido pero el jefe lleva sin completar un ciclo desde {ago(hb)}. Mira data/{MOTOR_LOG}.")

    acct, boss = db.get_state("account", {}), db.get_state(f"boss:{symbol}", {})
    opened, closed = trade_logger.open_trades(), trade_logger.closed_trades(mode=config.EXECUTION_MODE)
    first = min([t["opened_ts"] for t in closed + opened], default=time.time())
    p = performance(closed, config.CAPITAL, (time.time() - first) / 86400)

    k = st.columns(6)
    k[0].metric(f"Capital ({config.QUOTE})", f"{acct.get('equity', config.CAPITAL):,.2f}",
                f"{acct.get('floating', 0):+,.2f} flotante", delta_color="off", delta_arrow="off")
    k[1].metric("Resultado neto", f"{p['net']:+,.2f}", f"{p['ret']:+.1%} del capital",
                help="Después de comisiones y deslizamiento, sobre operaciones cerradas.")
    k[2].metric("Ritmo anual", "—" if p["annual"] is None else f"{p['annual']:+.0%}", f"objetivo {target:.0f} %", delta_color="off", delta_arrow="off")
    k[3].metric("Operaciones ganadoras", "—" if p["win"] is None else f"{p['win']:.0%}", f"{p['n']} cerradas", delta_color="off", delta_arrow="off",
                help="Acertar muchas no es ganar dinero: cuenta cuánto se gana en las buenas y se pierde en las malas.")
    k[4].metric("Hoy", f"{acct.get('day_pnl', 0):+,.2f}")
    k[5].metric("Costes pagados", f"{p['costs']:,.2f}", help="Comisiones y deslizamiento acumulados.")
    st.caption(verdict(p, target) + (" El resultado neto suma todas las cuentas; el de la actual, en el panel." if FUNDED else ""))
    if acct.get("blocked"):
        st.error(f"Entradas nuevas bloqueadas: {acct['blocked']}")
    if FUNDED:
        funded_panel(acct)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("👔 El Jefe decide")
        action = boss.get("action", "NEUTRAL")
        c = st.columns([1.6, 1, 3])
        c[0].markdown(f"**{symbol}**\n\n### :{COLOR[action]}[{action}]")
        c[1].metric("Acuerdo", f"{boss.get('score', 0):+.2f}",
                    help=f"De -1 (vender) a +1 (comprar). Opera a partir de ±{optimizer.params()['DECISION_THRESHOLD']}.")
        c[2].caption(f"{boss.get('reason', 'sin datos todavía')}\n\n{boss.get('status', '')} · {ago(boss.get('ts'))}")
        plan = boss.get("plan")
        if plan and boss.get("status") == "esperando aprobación manual":
            with st.container(border=True):
                st.markdown(
                    f"**Propone:** :{COLOR[plan['side']]}[{plan['side']}] {plan['qty']:.6g} {symbol} a ~{plan['entry']:,.6g} · "
                    f"stop {plan['sl']:,.6g} · objetivo {plan['tp']:,.6g} · riesgo {plan['risk_usd']:.2f} {config.QUOTE} "
                    f"(costes {plan['cost_est']:.2f}) · apalancamiento {plan['leverage']:.2f}x")
                if st.button("Aprobar y ejecutar", type="primary"):
                    db.set_state(f"approve:{symbol}", time.time())
                    st.toast("Aprobada: se ejecuta en el próximo ciclo si la señal sigue vigente.")
        if "scenarios" in AGENT_LABELS:
            scenarios_table()
        st.subheader("Resultado acumulado")
        if closed:
            # Empieza en cero al abrirse la primera operación: con un solo cierre ya se ve una línea.
            line_chart(pd.to_datetime([first] + [t["closed_ts"] for t in closed], unit="s"),
                       [0.0] + pd.Series([t["pnl"] for t in closed]).cumsum().tolist(), "curva-vivo", title=config.QUOTE)
        else:
            st.caption("Aparecerá con la primera operación cerrada.")
    with right:
        st.subheader("Lo último que ha pasado")
        feed(trade_logger.events(40), height=470)

    left, right = st.columns(2)
    with left:
        st.subheader(f"Operaciones abiertas ({len(opened)})")
        if opened:
            marks = acct.get("marks", {})
            df = pd.DataFrame([{"Activo": t["symbol"], "Lado": t["side"], "Entrada": t["entry"],
                                "Ahora": marks.get(str(t["id"]), [None])[0], "Stop": t["sl"], "Objetivo": t["tp"],
                                "Flotante": marks.get(str(t["id"]), [None, None])[1], "Abierta (UTC)": clock(t["opened_ts"])}
                               for t in opened])
            st.dataframe(df.style.format({c: "{:,.6g}" for c in ("Entrada", "Ahora", "Stop", "Objetivo")} | {"Flotante": "{:+,.2f}"},
                                         na_rep="—"), hide_index=True, width="stretch")
        else:
            st.caption("Ninguna.")
    with right:
        st.subheader("Últimas cerradas")
        if closed:
            df = pd.DataFrame(closed[-15:][::-1])[["symbol", "side", "entry", "exit", "pnl", "exit_reason", "closed_ts"]]
            df["closed_ts"] = df.closed_ts.map(clock)
            df.columns = ["Activo", "Lado", "Entrada", "Salida", "Resultado neto", "Motivo", "Cerrada (UTC)"]
            st.dataframe(df.style.format({"Resultado neto": "{:+.2f}", "Entrada": "{:,.6g}", "Salida": "{:,.6g}"}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Ninguna todavía.")


# --- 2. Los bots -------------------------------------------------------------
@st.fragment(run_every=5)
def bots_room() -> None:
    sim = sim_result()
    source = st.radio("Qué bots miro", ["Los del motor en vivo", "Los de la última simulación"], horizontal=True,
                      disabled=sim is None, help="La simulación sale del Laboratorio.")
    live = source.startswith("Los del motor") or sim is None
    trades = trade_logger.closed_trades(mode=config.EXECUTION_MODE) if live else sim["trades"]
    board, weights = optimizer.scoreboard(trades), db.get_state("weights", {})
    names = list(AGENT_LABELS) if live else sorted(board) or list(AGENT_LABELS)
    st.caption("Cada bot cobra en R (veces lo arriesgado) cuando vota la dirección que acaba ganando, y paga cuando vota "
               "la que pierde. Con esas recompensas el jefe decide cuánto caso le hace a cada uno.")
    for col, name in zip(st.columns(len(names)), names):
        with col:
            bot_card(name, board, weights, live)
    st.divider()
    pick = st.selectbox("Ver un bot a fondo", names, format_func=lambda n: " ".join(bot(n)[:2]))
    bot_detail(pick, trades, board, live)


# --- 3. El Jefe --------------------------------------------------------------
@st.fragment(run_every=5)
def boss_room() -> None:
    st.markdown(
        "**Qué hace el jefe.** Escucha a todos los bots, pesa cada voto según la confianza que se han ganado y solo opera "
        "si el acuerdo supera el umbral. Calcula tamaño, stop y objetivo con reglas fijas (ningún modelo de lenguaje decide "
        "dinero), vigila los límites de pérdida, reparte las recompensas al cerrar y reajusta él solo la confianza.")
    c = st.columns([1.2, 1, 2])
    auto = c[0].toggle("Se optimiza solo tras cada operación", value=db.get_state("ctl:auto_opt", True))
    if auto != db.get_state("ctl:auto_opt", True):
        db.set_state("ctl:auto_opt", auto)
        trade_logger.log_event("params", "boss", "Auto-optimización " + ("encendida" if auto else "apagada") + " por ti")
    if c[1].button("Optimizar ahora", help="Recalcula la confianza en cada bot con las recompensas de los últimos 30 días."):
        rep = optimizer.run()
        st.toast(f"Hecho: {rep['metrics'].get('n', 0)} operaciones revisadas.")
    rep = db.get_state("optimizer:report")
    c[2].caption(f"Última optimización {ago(rep['ts'])}." if rep else "Todavía no se ha optimizado: hacen falta operaciones cerradas.")

    left, right = st.columns(2)
    with left:
        st.subheader("Confianza en cada bot")
        weights = db.get_state("weights", {})
        board = optimizer.scoreboard(trade_logger.closed_trades(mode=config.EXECUTION_MODE))
        bench = set(db.get_state("ctl:bench", []))
        rows = [{"Bot": " ".join(bot(a)[:2]) + (" 🪑" if a in bench else ""),
                 "Recompensas (R)": board.get(a, {}).get("points", 0.0),
                 **{s: weights.get(a, {}).get(s, 1.0) for s in config.SYMBOLS}} for a in AGENT_LABELS]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "Recompensas (R)": st.column_config.NumberColumn(format="%+.1f"),
            **{s: st.column_config.ProgressColumn(s, min_value=0.0, max_value=optimizer.W_MAX, format="×%.2f")
               for s in config.SYMBOLS}})
        st.caption(f"×1.00 es un voto normal. Va de ×{optimizer.W_MIN} a ×{optimizer.W_MAX} y solo se mueve cuando el bot "
                   f"lleva {optimizer.MIN_SAMPLES} votos o más en ese activo.")
    with right:
        st.subheader("Diario del jefe")
        feed(trade_logger.events(60, kinds=("trade", "trust", "risk", "bench", "params", "death", "rebirth")), height=300)

    st.subheader("Qué decide ahora")
    now = [(s, db.get_state(f"boss:{s}", {})) for s in config.SYMBOLS]
    st.dataframe(pd.DataFrame([{"Activo": s, "Decisión": b.get("action", "—"), "Acuerdo": f"{b.get('score', 0):+.2f}",
                                "Por qué": b.get("reason", "sin datos todavía"), "Estado": b.get("status", "—")}
                               for s, b in now]), hide_index=True, width="stretch")

    st.subheader("Relevos: generaciones de cada bot")
    tree = trade_logger.genealogy()
    rows = [{"Bot": " ".join(bot(r["agent"])[:2]), "Versión": r["name"],
             "Estado": "🟢 Vivo" if r["status"] == "alive" else "💀 Sustituido",
             "Por qué cayó": r["death_cause"] or "—", "Qué aprendió el sucesor": r["post_mortem"] or "—",
             "Lecciones": len(r["rules"]), "Nació (UTC)": clock(r["born_ts"]), "Cayó (UTC)": clock(r["died_ts"])}
            for r in reversed(tree)]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=260,
                     column_config={"Por qué cayó": st.column_config.TextColumn(width="large")})
    else:
        st.caption("El motor todavía no ha registrado ninguna generación.")


# --- 4. Laboratorio ----------------------------------------------------------
def show_sim(sim: dict) -> None:
    setup, m, lin = sim["setup"], sim["metrics"], sim.get("lineage")
    st.markdown(f"**Simulación de {sim['days']:g} días** · {', '.join(setup['symbols'])} · {setup['exchange']} · "
                f"{'ruptura de apertura' if setup['strategy'] == 'orb' else 'los bots en equipo'} · capital {setup['capital']:,.0f}")
    if not m.get("n"):
        st.warning("No hubo ninguna operación: con estos ajustes el jefe nunca vio acuerdo suficiente.")
        return
    net = lin["net"] if lin else m["pnl"]
    p = performance(sim["trades"], setup["capital"], sim["days"]) | {"net": net, "ret": net / setup["capital"]}
    p["annual"] = p["ret"] * 365 / sim["days"] if sim["days"] >= 14 else None
    (st.success if net > 0 else st.error)(
        f"**{'Ganó' if net > 0 else 'Perdió'} {abs(net):,.2f} ({p['ret']:+.1%}) después de costes.** " + verdict(p, target))
    k = st.columns(6)
    k[0].metric("Operaciones", m["n"])
    k[1].metric("Ganadoras", f"{m['win_rate']:.0%}")
    k[2].metric("Ganado / perdido", "—" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}",
                help="Por cada unidad perdida, cuántas ganó. Por debajo de 1 pierde dinero.")
    k[3].metric("Peor caída", f"{sim['max_drawdown_pct']:.1f} %")
    k[4].metric("Costes pagados", f"{sim['costs']:,.0f}")
    k[5].metric("Cuentas perdidas", sum(g["dead"] for g in lin["generations"]) if lin else "—",
                help="Con reglas de cuenta fondeada: bots eliminados por perder el máximo permitido.")
    line_chart(pd.to_datetime([c[0] for c in sim["curve"]], unit="s"), [c[1] for c in sim["curve"]], "curva-sim",
               "#1f77b4", 260, "Capital")
    board = optimizer.scoreboard(sim["trades"])
    a, b = st.columns(2)
    with a:
        st.markdown("**Qué hizo cada bot**")
        st.dataframe(pd.DataFrame([{"Bot": " ".join(bot(n)[:2]), "Recompensas (R)": v["points"], "Aciertos": v["hits"],
                                    "Fallos": v["misses"], "Mejor (R)": v["best"]} for n, v in board.items()]),
                     hide_index=True, width="stretch", column_config={
                         "Recompensas (R)": st.column_config.NumberColumn(format="%+.1f"),
                         "Mejor (R)": st.column_config.NumberColumn(format="%+.2f")})
    with b:
        if lin:
            st.markdown("**Resultado por semana**")
            fig = go.Figure(go.Bar(y=lin["weekly"], marker_color=["#2ca02c" if w > 0 else "#d62728" for w in lin["weekly"]]))
            fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="semanas-sim")
    if lin and len(lin["generations"]) > 1:
        st.markdown("**Relevos de bots durante la simulación**")
        st.dataframe(pd.DataFrame([{"Bot": g["gen"], "Desde": clock(g["born"], "%d-%m-%y"), "Hasta": clock(g["end"], "%d-%m-%y"),
                                    "Operaciones": g["n"], "Neto": round(g["net"]), "Final": "eliminado" if g["dead"] else "vivo",
                                    "Nació con la lección": g["lesson"]} for g in lin["generations"]]),
                     hide_index=True, width="stretch")
    with st.expander("Informe completo"):
        st.code(sim.get("report", ""), language=None)
    if st.button("Usar estos ajustes en el motor", help="Copia riesgo, umbral, objetivo y stop de esta simulación a los ajustes del motor."):
        db.set_state("ctl:params", setup["params"])
        trade_logger.log_event("params", "boss", "Ajustes tomados de una simulación del laboratorio")
        st.toast("Hecho: el motor los aplica en su próximo ciclo.")


def lab_status() -> None:
    p, running = procs().get("sim"), False
    if p is not None and p.poll() is None:
        running = True
        st.info("Simulando… puedes cambiar de pestaña; el resultado aparecerá aquí.")
        st.code(tail(f"lab{OWN}/sim.txt", 6) or "descargando velas…", language=None)
        if st.button("Cancelar simulación"):
            p.terminate()
    elif p is not None and procs().get("sim_seen") is not p:
        procs()["sim_seen"] = p
        st.rerun()  # terminó: se repinta todo una vez y se deja de consultar
    if p is not None and not running and p.returncode != 0:
        st.error("La simulación no terminó bien:")
        st.code(tail(f"lab{OWN}/sim.txt", 12), language=None)
    elif not running and procs().get("sim_text"):
        st.code(tail(f"lab{OWN}/sim.txt", 60), language=None)  # validación fuera de muestra: el informe es texto
    elif not running and sim_result():
        show_sim(sim_result())


def lab() -> None:
    st.markdown("Prueba una configuración sobre precios reales del pasado **antes** de dejar que el motor la use. "
                "Lo que aquí pierde, en vivo no va a ganar; lo que aquí gana, puede no repetirse.")
    par = optimizer.params()
    with st.form("lab"):
        c = st.columns(3)
        market = c[0].radio("Mercado", ["Cripto (tu exchange)", "Acciones de EE. UU. (Yahoo)", "Divisas (Yahoo)"])
        symbols = c[0].text_input("Activos, separados por comas", help="Vacío: los de tu configuración para cripto, "
                                  "NVDA, META, GOOGL, TTWO, MU, MSFT, UBER para acciones o EUR/USD, GBP/USD, JPY/USD "
                                  "para divisas (el yen se escribe JPY/USD).")
        days = c[0].number_input("Días a simular", 7, 730, 30, help="Acciones: hasta 730 (velas de 1 h). Cripto: velas de 5 min.")
        strategy = c[1].radio("Quién opera", ["Los bots en equipo", "Ruptura del rango de apertura (solo acciones)"])
        evolution = c[1].toggle("Con selección natural", value=True,
                                help="Un bot muere cuando falla y nace un sucesor con la lección aprendida.")
        funded = c[1].toggle("Reglas de cuenta fondeada", value=FUNDED,
                             help="El equipo queda eliminado al perder el máximo y otro empieza con capital nuevo.")
        max_loss = c[1].number_input("Pérdida máxima de la cuenta fondeada", 100, 100000, 2000, 100)
        walk = c[1].toggle("Entrenar y examinar", value=False, help="Solo con la ruptura de apertura: elige la mejor "
                           "configuración en un periodo y la examina en el siguiente, que no ha visto. Tarda varios minutos.")
        capital = c[2].number_input("Capital", 500.0, 1e7, float(config.CAPITAL), 500.0)
        risk_pct = c[2].slider("Riesgo por operación (%)", 0.1, 2.0, float(par["RISK_PER_TRADE_PCT"]), 0.1)
        threshold = c[2].slider("Acuerdo que exige el jefe", 0.1, 0.9, float(par["DECISION_THRESHOLD"]), 0.05)
        rr = c[2].slider("Objetivo (veces el riesgo)", 1.0, 5.0, float(par["REWARD_RISK"]), 0.5)
        go_ = st.form_submit_button("🧪 Simular", type="primary")
    if go_:
        stocks, fx, orb = market.startswith("Acciones"), market.startswith("Divisas"), strategy.startswith("Ruptura")
        if orb and not stocks:
            st.error("La ruptura del rango de apertura necesita un mercado con sesiones: elige acciones.")
        else:
            env = {"PAPER_TRADING": "true", "BROKER": "ccxt", "CAPITAL": str(capital), "RISK_PER_TRADE_PCT": str(risk_pct),
                   "DECISION_THRESHOLD": str(threshold), "REWARD_RISK": str(rr), "STRATEGY": "orb" if orb else "ensemble"}
            if symbols.strip() or stocks or fx:
                env["SYMBOLS"] = symbols.strip().upper() or ("EUR/USD,GBP/USD,JPY/USD" if fx else "NVDA,META,GOOGL,TTWO,MU,MSFT,UBER")
            else:  # cripto: solo los pares de cripto de la configuración (la de fondeo también lleva acciones)
                env["SYMBOLS"] = ",".join(s for s in config.SYMBOLS if "/" in s and not risk.is_fx(s)) \
                    or "BTC/USDT:USDT,ETH/USDT:USDT"
            if stocks or fx:  # las simulaciones llevan un solo coste: el de las acciones o el de las divisas
                env |= {"EXCHANGE_ID": "yahoo", "QUOTE": "USD", "SMC_TIMEFRAME": "1h", "ATR_TIMEFRAME": "1h",
                        "WYCKOFF_TIMEFRAME": "1h", "MIN_AGENTS": "1" if orb else "3",
                        "FEE_PCT": str(config.FX_FEE_PCT) if fx else "0.005",
                        "SLIPPAGE_PCT": str(config.FX_SLIPPAGE_PCT) if fx else "0.02"}
            # Sin reglas de fondeo no hay límite diario en dólares, aunque la app sea la de la cuenta de fondeo:
            # la simulación hereda el entorno de la app y, si no, arrastraría su kill switch.
            env |= {"PROP_DAILY_LOSS_LIMIT_USD": "0", "PROP_MAX_LOSS_USD": "0"}  # el backtest lleva su propio --lineage
            if funded or walk:
                env |= {"TOTAL_LOSS_LIMIT_USD": "0", "PROP_DAILY_LOSS_LIMIT_USD": str(max_loss / 2)}
            args = ["backtest.py", "--days", str(days)] + (["--evolution"] if evolution and not orb else [])
            args += (["--lineage", str(max_loss)] + (["--flat"] if stocks or fx else [])) if funded or walk else []
            args += ["--walk-forward", "180,60"] if walk and orb else ["--save", str(LAB / "sim.json")]
            LAB.mkdir(parents=True, exist_ok=True)
            (LAB / "sim.json").unlink(missing_ok=True)
            procs().update(sim=spawn(args, f"lab{OWN}/sim.txt", env), sim_text=walk and orb)
            st.rerun()
    running = procs().get("sim") is not None and procs()["sim"].poll() is None
    st.fragment(run_every=2 if running else None)(lab_status)()


# --- 5. Políticos y grandes inversores ------------------------------------------
LAB_WHAT = {"H8": "copiar a los políticos", "H9": "copiar a los grandes inversores"}


def watch_verdict() -> None:
    """Lo que dijo el laboratorio de la tercera ronda, si ya se pasó."""
    for name, label in (("holdout3.json", "Periodo reservado"), ("exam3.json", "Examen"), ("train3.json", "Entrenamiento")):
        f = pdx.DIR / "lab" / name
        if not f.exists():
            continue
        data = json.loads(f.read_text())
        if "choice" in data:
            parts = [f"{LAB_WHAT.get(k, k)}: {'pasa al examen' if v else 'descartado'}" for k, v in data["choice"].items()]
        else:
            parts = [f"{LAB_WHAT.get(k, k)}: {'aprueba' if r.get('pass') else 'suspende'}" for k, r in data["result"].items()]
        st.info(f"**{label} del laboratorio (tercera ronda):** " + " · ".join(parts) + " (detalle en ESTADO.md)")
        return


def watch_congress() -> None:
    st.subheader("🏛️ Políticos: lo último que declararon")
    path = pdx.DIR / "congress" / "latest.parquet"
    c = st.columns([1, 3])
    if c[0].button("Actualizar declaraciones", help="Descarga las 5.000 últimas del archivo abierto de Kadoa (unos 4 MB)."):
        with st.spinner("Descargando…"):
            try:
                pdx.congress_latest(days=0)
            except (ConnectionError, FileNotFoundError, ValueError) as e:
                st.warning(f"No se pudo descargar: {e}")
    if not path.exists():
        st.info("Pulsa «Actualizar declaraciones» para descargarlas.")
        return
    df = pd.read_parquet(path)
    c[1].caption(f"Copia del {clock(path.stat().st_mtime, '%d-%m-%Y %H:%M')} · {len(df):,} operaciones declaradas del "
                 f"{df.filed.min():%d-%m-%Y} al {df.filed.max():%d-%m-%Y}. Fuente: kadoa-org/congress-trading-monitor, "
                 "sacada de las páginas de la Cámara, el Senado y la Oficina de Ética del Gobierno.")
    f = st.columns(3)
    mine = f[0].toggle("Solo mis acciones", value=False, disabled=not config.STOCKS)
    buys = f[1].toggle("Solo compras", value=False)
    days = f[2].slider("Declaradas en los últimos días", 7, 180, 60)
    view = df[df.filed >= df.filed.max() - pd.Timedelta(days=days)]
    if mine:
        view = view[view.ticker.isin(config.STOCKS)]
    if buys:
        view = view[view.kind == "Purchase"]
    view = view.sort_values("filed", ascending=False)
    kinds = {"Purchase": "Compra", "Sale (Full)": "Venta total", "Sale (Partial)": "Venta parcial", "Exchange": "Canje"}
    st.dataframe(pd.DataFrame({
        "Declarada": view.filed.dt.strftime("%d-%m-%Y"), "Operación": view.traded.dt.strftime("%d-%m-%Y"),
        "Quién": view.name, "Partido": view.party,
        "Dónde": view.chamber.map({"house": "Cámara", "senate": "Senado"}).fillna("Gobierno"),
        "Acción": view.ticker, "Qué": view.kind.map(kinds).fillna(view.kind),
        "Importe (USD)": [f"{lo:,.0f}–{hi:,.0f}" if lo == lo and hi == hi else "" for lo, hi in zip(view.usd_low, view.usd_high)],
        "Días en declararla": (view.filed - view.traded).dt.days}), hide_index=True, width="stretch", height=320)
    top = view[(view.kind == "Purchase") & (view.ticker != "")].groupby("ticker").name.nunique() \
        .sort_values(ascending=False).head(10)  # sin las declaraciones que no traen ticker
    if len(top):
        st.caption("Las más compradas en ese periodo, por número de políticos distintos: "
                   + ", ".join(f"{t} ({n})" for t, n in top.items()))


def watch_funds() -> None:
    st.subheader("💼 Grandes inversores: su último 13F")
    c = st.columns([2, 1, 3])
    who = c[0].selectbox("Gestor", list(pdx.MANAGERS))
    fresh = c[1].button("Actualizar 13F", help="Pregunta a la SEC si este gestor ha presentado un 13F nuevo.")
    if not fresh and not all((pdx.DIR / "13f" / str(cik) / "filings.parquet").exists() for cik in pdx.MANAGERS[who]):
        c[2].info("Pulsa «Actualizar 13F» para descargar sus 13F de la SEC (la primera vez, un minuto).")
        return  # al abrir la app no se pide nada a la red
    try:
        with st.spinner("Leyendo sus 13F…"):
            h = pd.concat([pdx.holdings13f(cik, days=0 if fresh else 3650) for cik in pdx.MANAGERS[who]],
                          ignore_index=True)
    except (ConnectionError, FileNotFoundError, ValueError, KeyError) as e:
        st.warning(f"No se pudo leer de la SEC: {e}")
        return
    h = h[~h.amendment.astype(bool)].dropna(subset=["period"])
    if h.empty:
        st.info("Sin 13F en XML de este gestor.")
        return
    periods = sorted(h.period.unique())
    last = h[h.period == periods[-1]]
    prev = h[h.period == periods[-2]] if len(periods) > 1 else h.iloc[:0]
    tickers = pdx.cusips(days=3650) if (pdx.DIR / "cusips.json").exists() else {}
    cur = last.groupby("cusip").agg(issuer=("issuer", "first"), value=("value", "sum"), shares=("shares", "sum"))
    cur = cur.sort_values("value", ascending=False)
    before = prev.groupby("cusip").shares.sum()
    change = ["nueva" if k not in before.index else f"{s / before[k] - 1:+.0%} en acciones" if before[k] else ""
              for k, s in zip(cur.index, cur.shares)]
    c[2].caption(f"Trimestre que acaba el {pd.Timestamp(periods[-1]):%d-%m-%Y}, público desde el "
                 f"{last.accepted.max().tz_convert(pdx.NY):%d-%m-%Y}: {len(cur)} posiciones y {cur.value.sum() / 1e9:,.2f} "
                 "mil millones de USD. Un split cambia el número de acciones sin que el gestor compre.")
    st.dataframe(pd.DataFrame({"Empresa": cur.issuer, "Ticker": [tickers.get(k, "") for k in cur.index],
                               "Valor (millones USD)": cur.value / 1e6, "% de la cartera": cur.value / cur.value.sum(),
                               "Frente al trimestre anterior": change}).head(15).style.format(
        {"Valor (millones USD)": "{:,.1f}", "% de la cartera": "{:.1%}"}), hide_index=True, width="stretch")


def watch_room() -> None:
    st.markdown("Lo que **declaran** los congresistas y senadores de EE. UU. (ley STOCK Act) y lo que tienen los grandes "
                "inversores según sus **13F** en la SEC. Llega tarde: una declaración, de mediana 31 días después de la "
                "operación; un 13F, hasta 45 días después del trimestre. Verlo no da ventaja por sí solo: el laboratorio "
                "comprueba si copiarlos gana.")
    watch_verdict()
    watch_congress()
    watch_funds()


# --- 6. Ajustes --------------------------------------------------------------
def settings() -> None:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Cómo opera el jefe")
        par, new = optimizer.params(), {}
        with st.form("ajustes"):
            for key, (label, step, hint) in PARAMS.items():
                lo, hi = optimizer.TUNABLE[key]
                kind = type(optimizer.DEFAULTS[key])
                new[key] = st.slider(label, kind(lo), kind(hi), par[key], kind(step), help=hint)
            a, b = st.columns(2)
            save = a.form_submit_button("Guardar", type="primary", width="stretch")
            reset = b.form_submit_button("Volver a los de .env", width="stretch")
        if save or reset:
            new = dict(optimizer.DEFAULTS) if reset else new
            changed = ", ".join(f"{PARAMS[k][0]} {par[k]:g} → {v:g}" for k, v in new.items() if v != par[k])
            db.set_state("ctl:params", new)
            if changed:
                trade_logger.log_event("params", "boss", f"Ajustes cambiados por ti: {changed}")
            st.toast("Guardado: el motor lo aplica en su próximo ciclo." if changed else "No había nada que cambiar.")
            st.rerun()
        if FUNDED:
            st.caption(f"Reglas de la cuenta de fondeo, fijas en «Iniciar cuenta de fondeo.bat»: stop máximo {config.MAX_SL_PCT:g} %, "
                       f"pérdida diaria {config.PROP_DAILY_LOSS_LIMIT_USD:,.0f} con kill switch al {config.PROP_KILL_AT_PCT:g} %, "
                       f"la cuenta se pierde {config.PROP_MAX_LOSS_USD:,.0f} bajo su mejor cierre diario, y siempre simulada.")
        else:
            st.caption(f"Solo se cambian en el archivo .env, a propósito: stop máximo {config.MAX_SL_PCT:g} %, límite de pérdida "
                       f"diaria {config.DAILY_LOSS_LIMIT_PCT:g} %, límite de pérdida total {config.TOTAL_LOSS_LIMIT_USD:g} {config.QUOTE} "
                       f"y el paso a dinero real (ahora: {config.EXECUTION_MODE}).")
    with right:
        st.subheader("Qué tienes conectado")
        for ok, what, why in (
                (True, "Precios del mercado", f"{config.EXCHANGE_ID}, sin clave en simulación"),
                (config.USE_CLAUDE, "Clave de Anthropic", "el bot Macro puntúa titulares y los relevos los diagnostica Claude"),
                (bool(config.EXCHANGE_API_KEY), "Claves del exchange", "solo para órdenes reales o de sandbox"),
                (bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID) or bool(config.DISCORD_WEBHOOK_URL),
                 "Avisos a Telegram o Discord", "aperturas, cierres y relevos en el móvil"),
                (bool(config.ALPHAVANTAGE_API_KEY), "Clave de Alpha Vantage", "titulares para el bot Macro")):
            st.markdown(f"{'✅' if ok else '⬜'} **{what}** · {why}")
        st.caption("Las claves van en el archivo .env de esta carpeta, nunca en la app ni en un chat.")
        tok = token_budget.today()
        st.subheader("Consumo de Claude hoy")
        st.progress(min(1.0, tok["llm"] / config.DAILY_TOKEN_BUDGET),
                    text=f"{tok['llm']:,} de {config.DAILY_TOKEN_BUDGET:,} tokens · {tok['llm_calls']} llamadas")
        st.caption("Al agotarse el presupuesto diario, Claude se apaga hasta mañana y el sistema sigue con sus reglas fijas.")


tabs = st.tabs(["🏠 Sala de control", "👾 Los bots", "👔 El Jefe", "🧪 Laboratorio", "🏛️ Políticos y fondos",
                "⚙️ Ajustes"])
for tab, page in zip(tabs, (control_room, bots_room, boss_room, lab, watch_room, settings)):
    with tab:
        page()
