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

# --- Decisión --------------------------------------------------------------
DECISION_THRESHOLD = _f("DECISION_THRESHOLD", 0.25)  # sobre un score en [-1, 1]
MIN_AGENTS = int(_f("MIN_AGENTS", 3))  # agentes con señal vigente para poder operar
APPROVAL_TTL_S = _f("APPROVAL_TTL_S", 120)  # validez de una aprobación manual

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
LOG_FILE = Path(_s("LOG_FILE", str(ROOT / "trading.log")))

if BROKER == "topstepx":
    if PROP_DAILY_LOSS_LIMIT_USD <= 0:
        raise SystemExit("BROKER=topstepx exige PROP_DAILY_LOSS_LIMIT_USD: el límite de pérdida diaria de tu cuenta,\n"
                         "tal como aparece en Topstep. No se asume ningún valor.")
    # En futuros los costes son por contrato y los aplica el broker; el tamaño lo acota PROP_MAX_CONTRACTS.
    FEE_PCT = SLIPPAGE_PCT = MIN_NOTIONAL = 0.0
    MAX_LEVERAGE = 1e9

if EXECUTION_MODE == "live" and _s("CONFIRM_LIVE") != "SI_ARRIESGO_DINERO_REAL":
    raise SystemExit(
        "Esta configuración envía órdenes reales (dinero real o una cuenta de evaluación). Para confirmarlo,\n"
        "añade al .env:  CONFIRM_LIVE=SI_ARRIESGO_DINERO_REAL"
    )
