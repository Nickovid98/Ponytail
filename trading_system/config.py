"""Configuración central. Todo sale de variables de entorno o del archivo .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")  # no pisa variables que ya estén en el entorno

_FALSE = ("false", "0", "no", "off")


def _s(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _f(key: str, default: float) -> float:
    return float(_s(key) or default)  # una variable vacía (PROP_...= en la plantilla) vale lo que no ponerla


def _on(key: str) -> bool:
    """True salvo que la variable diga explícitamente que no. Un valor mal
    escrito (PAPER_TRADING=Flase) deja la opción segura activada."""
    return _s(key, "true").lower() not in _FALSE


# --- Ejecución -------------------------------------------------------------
PAPER_TRADING = _on("PAPER_TRADING")  # True: precios reales, órdenes simuladas, sin claves
EXCHANGE_SANDBOX = _on("EXCHANGE_SANDBOX")  # con PAPER_TRADING=False: testnet/demo del exchange
# paper | sandbox | live. "live" es dinero real y exige CONFIRM_LIVE (ver abajo).
EXECUTION_MODE = "paper" if PAPER_TRADING else "sandbox" if EXCHANGE_SANDBOX else "live"
BROKER = _s("BROKER", "ccxt").lower()  # ccxt (cripto) | topstepx (futuros en Topstep, API de ProjectX)
if BROKER == "topstepx" and not PAPER_TRADING:
    EXECUTION_MODE = "live"  # una cuenta de evaluación se pierde como una real: exige CONFIRM_LIVE
EXCHANGE_ID = _s("EXCHANGE_ID", "binanceusdm")
SANDBOX_KIND = _s("SANDBOX_KIND", "auto").lower()  # auto | testnet | demo
EXCHANGE_API_KEY = _s("EXCHANGE_API_KEY")
EXCHANGE_SECRET = _s("EXCHANGE_SECRET")
EXCHANGE_PASSWORD = _s("EXCHANGE_PASSWORD")
SYMBOLS = [s.strip() for s in _s("SYMBOLS", "BTC/USDT:USDT,ETH/USDT:USDT").split(",") if s.strip()]
QUOTE = _s("QUOTE", "USDT")
START_MODE = _s("START_MODE", "AUTO").upper()  # AUTO | MANUAL (se cambia desde el dashboard)

# --- Riesgo y costes -------------------------------------------------------
CAPITAL = _f("CAPITAL", 3550)  # capital inicial del paper trading
# Techo duro del 2 % por operación: ni el .env puede subirlo.
RISK_PER_TRADE_PCT = min(_f("RISK_PER_TRADE_PCT", 1.0), 2.0)
MAX_SL_PCT = _f("MAX_SL_PCT", 5.0)  # distancia máxima del stop respecto a la entrada
ATR_TIMEFRAME = _s("ATR_TIMEFRAME", "15m")
ATR_SL_MULT = _f("ATR_SL_MULT", 1.5)
REWARD_RISK = _f("REWARD_RISK", 2.0)
MAX_LEVERAGE = _f("MAX_LEVERAGE", 3)
MAX_OPEN_POSITIONS = int(_f("MAX_OPEN_POSITIONS", 2))
MIN_NOTIONAL = _f("MIN_NOTIONAL", 10)
DAILY_LOSS_LIMIT_PCT = _f("DAILY_LOSS_LIMIT_PCT", 3.0)
TOTAL_LOSS_LIMIT_USD = _f("TOTAL_LOSS_LIMIT_USD", 0 if BROKER == "topstepx" else 500)  # 0 = sin límite total
COOLDOWN_S = _f("COOLDOWN_S", 900)  # espera tras un cierre antes de reentrar al mismo activo
FEE_PCT = _f("FEE_PCT", 0.075)  # comisión por lado, en % del nocional
SLIPPAGE_PCT = _f("SLIPPAGE_PCT", 0.02)  # deslizamiento proyectado por orden a mercado, en %

# --- Topstep / prop firm (solo con BROKER=topstepx) -------------------------
TOPSTEPX_API_URL = _s("TOPSTEPX_API_URL", "https://api.topstepx.com")
TOPSTEPX_USERNAME = _s("TOPSTEPX_USERNAME")
TOPSTEPX_API_KEY = _s("TOPSTEPX_API_KEY")
TOPSTEPX_ACCOUNT_ID = _s("TOPSTEPX_ACCOUNT_ID")
PROP_DAILY_LOSS_LIMIT_USD = _f("PROP_DAILY_LOSS_LIMIT_USD", 0)  # el de TU cuenta; obligatorio
PROP_ACCOUNT_FLOOR_USD = _f("PROP_ACCOUNT_FLOOR_USD", 0)  # saldo en el que se pierde la cuenta (Max Loss Limit)
PROP_KILL_AT_PCT = min(_f("PROP_KILL_AT_PCT", 75), 80.0)  # el kill switch salta al 75 % del límite; techo 80
PROP_MAX_CONTRACTS = int(_f("PROP_MAX_CONTRACTS", 1))
PROP_COMMISSION = _f("PROP_COMMISSION_PER_CONTRACT", 1.5)  # estimación por contrato y lado, en USD
PROP_SLIPPAGE_TICKS = _f("PROP_SLIPPAGE_TICKS", 1)
PROP_LAST_ENTRY_CT = _s("PROP_LAST_ENTRY_CT", "14:30")  # hora de Chicago: sin entradas nuevas desde aquí
PROP_FLAT_BY_CT = _s("PROP_FLAT_BY_CT", "15:00")  # hora de Chicago: todo cerrado desde aquí
PROP_BRACKET_SIGNED = _s("PROP_BRACKET_SIGNED", "true").lower() not in _FALSE  # ticks de los brackets con signo
PROP_VERIFY_WAIT_S = _f("PROP_VERIFY_WAIT_S", 1.0)
MAX_TRADES_PER_DAY = int(_f("MAX_TRADES_PER_DAY", 10 if BROKER == "topstepx" else 0))  # 0 = sin tope
MIN_ORDER_GAP_S = _f("MIN_ORDER_GAP_S", 60 if BROKER == "topstepx" else 0)  # espera entre entradas

# --- Cuenta de fondeo simulada (lanzador "Iniciar cuenta de fondeo.bat") ----
# La cuenta se pierde al caer PROP_MAX_LOSS_USD bajo su mejor cierre diario (el suelo deja de subir al
# llegar a CAPITAL, como en el backtest) y nace otra que hereda lo aprendido. 0 = desactivado.
PROP_MAX_LOSS_USD = _f("PROP_MAX_LOSS_USD", 0)
FUNDED = PROP_MAX_LOSS_USD > 0

# --- Acciones de EE. UU. (precios de Yahoo; solo simulación) ----------------
# Un símbolo sin "/" (NVDA, MSFT...) es una acción. Opera en la sesión regular de Nueva York con velas de 1 h
# para SMC, Wyckoff y el stop, como en las simulaciones de cuenta fondeada (con 5 min perdían más).
STOCK_TIMEFRAME = _s("STOCK_TIMEFRAME", "1h")
STOCK_FEE_PCT = _f("STOCK_FEE_PCT", 0.005)  # comisión por lado, en % del nocional
STOCK_SLIPPAGE_PCT = _f("STOCK_SLIPPAGE_PCT", 0.02)
STOCK_OPEN_WAIT_MIN = _f("STOCK_OPEN_WAIT_MIN", 60)  # sin entradas hasta que cierra la primera vela de 1 h del día
STOCK_LAST_ENTRY_MIN = _f("STOCK_LAST_ENTRY_MIN", 30)  # sin entradas en la última media hora
STOCK_FLAT_MIN = _f("STOCK_FLAT_MIN", 5)  # todo cerrado 5 minutos antes del cierre: nada pasa la noche abierto
STOCKS = [s for s in SYMBOLS if BROKER == "ccxt" and "/" not in s]

# --- Divisas contra el dólar (precios de Yahoo; solo simulación) -------------
# EUR/USD, GBP/USD, AUD/USD, NZD/USD y, al revés que en la calle, JPY/USD, CAD/USD y CHF/USD (como los futuros
# 6J, 6C y 6S de Chicago): así el resultado sale siempre en dólares. El mercado abre el domingo a las 17:00 de
# Nueva York y cierra el viernes a las 17:00. Velas de 1 h para SMC, Wyckoff y el stop, como las acciones, y nada
# pasa el fin de semana abierto. En Topstep el euro es el futuro M6E (o 6E) y se pone en SYMBOLS tal cual.
FX_CODES = ("EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF")
FX_FEE_PCT = _f("FX_FEE_PCT", 0.0035)  # comisión por lado, en % del nocional (3,5 USD por lote de 100.000)
FX_SLIPPAGE_PCT = _f("FX_SLIPPAGE_PCT", 0.005)  # medio diferencial más deslizamiento, por lado (~0,5 pips)
# Yahoo da el primer precio a las 00:00 de Londres del lunes: las 19:00 del domingo en Nueva York, o las 20:00 en
# las semanas en que EE. UU. ya cambió de hora y Reino Unido no. Hasta entonces el último precio es el del viernes.
FX_OPEN_WAIT_MIN = _f("FX_OPEN_WAIT_MIN", 180)
FX_LAST_ENTRY_MIN = _f("FX_LAST_ENTRY_MIN", 60)  # sin entradas en la última hora antes del cierre del viernes
FX_FLAT_MIN = _f("FX_FLAT_MIN", 5)  # todo cerrado 5 minutos antes del cierre del viernes
FOREX = [s for s in SYMBOLS if BROKER == "ccxt" and s[:3] in FX_CODES and s[3:] == "/USD"]

# --- Decisión --------------------------------------------------------------
STRATEGY = _s("STRATEGY", "ensemble").lower()  # ensemble: los cinco agentes · orb: ruptura del rango de apertura
if STRATEGY not in ("ensemble", "orb"):
    raise SystemExit(f"STRATEGY={STRATEGY!r} no existe: usa ensemble u orb")
DECISION_THRESHOLD = _f("DECISION_THRESHOLD", 0.25)  # sobre un score en [-1, 1]
MIN_AGENTS = int(_f("MIN_AGENTS", 1 if STRATEGY == "orb" else 3))  # agentes con señal vigente para poder operar
APPROVAL_TTL_S = _f("APPROVAL_TTL_S", 120)  # validez de una aprobación manual
# Bot de escenarios: cuántos futuros ficticios simula de cada activo, cuántas apuestas elige y, en cripto
# (sin sesiones), cuántas horas dura cada escenario.
SCENARIOS = int(_f("SCENARIOS", 10_000))
SCENARIO_TOP = int(_f("SCENARIO_TOP", 5))
SCENARIO_HOURS = _f("SCENARIO_HOURS", 6)

# --- Evolución y tokens ----------------------------------------------------
MAX_RULES = int(_f("MAX_RULES", 8))  # lecciones que conserva un agente; las más viejas se olvidan
DAILY_TOKEN_BUDGET = int(_f("DAILY_TOKEN_BUDGET", 200_000))  # tokens de Claude por día UTC
PAYLOAD_TOKEN_LIMIT = int(_f("PAYLOAD_TOKEN_LIMIT", 150))  # tope por mensaje entre agentes

# --- Intervalos (segundos) -------------------------------------------------
BOSS_INTERVAL = _f("BOSS_INTERVAL", 30)
SMC_INTERVAL = _f("SMC_INTERVAL", 60)
TECH_INTERVAL = _f("TECH_INTERVAL", 180)
MACRO_INTERVAL = _f("MACRO_INTERVAL", 900)
SMC_TIMEFRAME = _s("SMC_TIMEFRAME", "5m")
WYCKOFF_TIMEFRAME = _s("WYCKOFF_TIMEFRAME", "1h")

# --- Noticias / macro / Claude ---------------------------------------------
ALPHAVANTAGE_API_KEY = _s("ALPHAVANTAGE_API_KEY")
AV_DAILY_BUDGET = int(_f("AV_DAILY_BUDGET", 20))  # la clave gratuita permite 25/día
NEWS_RSS_URLS = [u.strip() for u in _s(
    "NEWS_RSS_URLS",
    "https://cointelegraph.com/rss,https://www.coindesk.com/arc/outboundfeeds/rss/",
).split(",") if u.strip()]
MACRO_EVENTS_FILE = Path(_s("MACRO_EVENTS_FILE", str(ROOT / "macro_events.csv")))
BLACKOUT_MIN = _f("BLACKOUT_MIN", 30)
CLAUDE_MODEL = _s("CLAUDE_MODEL", "claude-opus-5-5")
USE_CLAUDE = bool(_s("ANTHROPIC_API_KEY") or _s("ANTHROPIC_AUTH_TOKEN"))

# --- Notificaciones --------------------------------------------------------
TELEGRAM_BOT_TOKEN = _s("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _s("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK_URL = _s("DISCORD_WEBHOOK_URL")

# --- Almacenamiento --------------------------------------------------------
DB_PATH = Path(_s("DB_PATH", str(ROOT / "trades_history.db")))
CACHE_DIR = Path(_s("CACHE_DIR", str(ROOT / "data" / "cache")))
PUBLIC_DATA_DIR = Path(_s("PUBLIC_DATA_DIR", str(ROOT / "data" / "public")))  # SEC, Reserva Federal, BLS y precios
LOG_FILE = Path(_s("LOG_FILE", str(ROOT / "trading.log")))

if BROKER == "topstepx":
    if PROP_DAILY_LOSS_LIMIT_USD <= 0:
        raise SystemExit("BROKER=topstepx exige PROP_DAILY_LOSS_LIMIT_USD: el límite de pérdida diaria de tu cuenta,\n"
                         "tal como aparece en Topstep. No se asume ningún valor.")
    # En futuros los costes son por contrato y los aplica el broker; el tamaño lo acota PROP_MAX_CONTRACTS.
    FEE_PCT = SLIPPAGE_PCT = MIN_NOTIONAL = 0.0
    MAX_LEVERAGE = 1e9

if EXCHANGE_ID == "alpaca" and BROKER == "ccxt" and not PAPER_TRADING:
    raise SystemExit("Con Alpaca el motor solo simula (PAPER_TRADING=True): lee sus precios reales pero no envía\n"
                     "órdenes. Alpaca cripto es contado, sin cortos ni stops reduce-only, y esa ejecución no existe.")

if STOCKS and not PAPER_TRADING:
    raise SystemExit(f"Las acciones ({', '.join(STOCKS)}) solo se simulan (PAPER_TRADING=True): el motor lee sus precios\n"
                     "en Yahoo y no hay ningún broker de acciones conectado que pueda recibir órdenes.")
if FOREX and not PAPER_TRADING:
    raise SystemExit(f"Las divisas ({', '.join(FOREX)}) solo se simulan (PAPER_TRADING=True): el motor lee sus precios\n"
                     "en Yahoo y no hay ningún broker de divisas conectado. En Topstep el euro es el futuro M6E o 6E.")
_yahoo_names = [s for s in SYMBOLS if BROKER == "ccxt" and s.upper().endswith("=X")]
if _yahoo_names:
    raise SystemExit(f"{', '.join(_yahoo_names)} es el nombre de Yahoo: escribe el par con barra, como "
                     f"{_yahoo_names[0][:3].upper()}/USD (el yen, JPY/USD).")
_usd_first = [s for s in SYMBOLS if BROKER == "ccxt" and s[:4] == "USD/" and s[4:] in FX_CODES]
if _usd_first:
    raise SystemExit(f"Escribe {', '.join(s[4:] + '/USD' for s in _usd_first)} en vez de {', '.join(_usd_first)}: "
                     "el par va contra el dólar,\ncomo el futuro de Chicago, para que el resultado salga en dólares.")

if PROP_MAX_LOSS_USD < 0:
    raise SystemExit("PROP_MAX_LOSS_USD es lo que se puede perder bajo el mejor cierre diario: un importe positivo (o 0).")
if FUNDED:
    if BROKER != "ccxt" or not PAPER_TRADING:
        raise SystemExit("PROP_MAX_LOSS_USD simula una cuenta de fondeo: solo con PAPER_TRADING=True y BROKER=ccxt.\n"
                         "En una cuenta real de Topstep el suelo se fija con PROP_ACCOUNT_FLOOR_USD.")
    if PROP_DAILY_LOSS_LIMIT_USD <= 0:
        raise SystemExit("La cuenta de fondeo simulada necesita su límite diario en PROP_DAILY_LOSS_LIMIT_USD.")
    TOTAL_LOSS_LIMIT_USD = 0.0  # la pérdida máxima de la cuenta sustituye al límite total de tu dinero

if EXECUTION_MODE == "live" and _s("CONFIRM_LIVE") != "SI_ARRIESGO_DINERO_REAL":
    raise SystemExit(
        "Esta configuración envía órdenes reales (dinero real o una cuenta de evaluación). Para confirmarlo,\n"
        "añade al .env:  CONFIRM_LIVE=SI_ARRIESGO_DINERO_REAL"
    )
