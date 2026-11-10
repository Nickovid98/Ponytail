# Mesa de trading multi-agente

Sistema intradía para cripto (futuros perpetuos vía CCXT): cinco agentes analizan
cada activo, un Agente Jefe pondera sus votos, aplica los filtros de riesgo y
ejecuta. Un dashboard en Streamlit lo muestra y lo controla, y un optimizador
semanal reajusta el peso de cada agente según cómo le fue.

```
 5 agentes  ──señales JSON──▶  Agente Jefe  ──orden + SL/TP──▶  Broker (paper | sandbox | live)
 (asyncio, cada uno                │  ▲                              │
  a su ritmo)                      │  └── pesos ◀── optimizer.py ◀── registro de operaciones
                                   ▼
                              dashboard.py  (estado, kill switch, AUTO/MANUAL)
```

Todo el estado vive en un único SQLite (`data/trading.db`): caché, señales,
controles y operaciones. El motor y el dashboard son dos procesos que solo se
comunican por ahí.

## Puesta en marcha

Necesitas Python 3.10 o superior.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

python selfcheck.py             # 1. comprueba la lógica, sin red ni claves
copy .env.example .env          # 2. Windows   (cp en macOS / Linux)
python main.py --once           # 3. un ciclo real con datos del exchange
python main.py                  # 4. el motor (déjalo corriendo)
streamlit run dashboard.py      # 5. en otra terminal: el panel
```

El modo por defecto es `paper`: usa precios reales, simula las órdenes y **no
necesita ninguna clave**. Empieza ahí.

## Claves (`.env`)

| Variable | Para qué | Obligatoria |
|---|---|---|
| `EXCHANGE_API_KEY` / `EXCHANGE_API_SECRET` | Órdenes en `sandbox` y `live` | Solo fuera de `paper` |
| `ALPHAVANTAGE_API_KEY` | Noticias con puntuación de sentimiento | No |
| `ANTHROPIC_API_KEY` | Que Claude evalúe los titulares | No |

`.env` está en `.gitignore`. No lo subas a ningún repositorio.

### De paper a dinero real

1. **`paper`** — sin claves. Déjalo correr días y mira el registro.
2. **`sandbox`** — órdenes reales contra el entorno de pruebas. En Binance el
   testnet clásico de futuros ya no existe; su reemplazo es *Demo Trading*, y
   las claves se crean en <https://demo.binance.com/en/my/settings/api-management>.
   Con `SANDBOX_KIND=auto` el sistema ya lo elige. En otros exchanges (Bybit,
   OKX) `auto` usa su testnet.
3. **`live`** — además de las claves reales exige
   `CONFIRM_LIVE=SI_ARRIESGO_DINERO_REAL`. Sin esa línea el programa no arranca.

La cuenta de futuros debe estar en modo *one-way* (no *hedge*).

## Los agentes

Todos devuelven la misma estructura (`agents/__init__.py: Signal`): dirección,
confianza 0-100, soporte, resistencia y una línea de justificación.

| Agente | Qué calcula | Velas | Cada | ¿LLM? |
|---|---|---|---|---|
| 1 · Historiador | Regresión lineal sobre log-precio, R², desviación en σ, estacionalidad por hora | 1h | 3 min | No |
| 2 · Macro | Titulares (Alpha Vantage + RSS) y calendario de eventos | — | 15 min | Claude, opcional |
| 3 · SMC | BOS / CHoCH, order block, barridos y pools de liquidez | 5m | 1 min | No |
| 4 · Wyckoff | Fase (acumulación, distribución, markup, markdown), POC, HVN, área de valor | 1h | 3 min | No |
| 5 · Tendencia | EMA 50/200, MACD y RSI en 4H y 1D, desequilibrio del libro | 4h, 1d | 3 min | No |

Cuatro de los cinco son cálculo puro: no gastan tokens y dan siempre el mismo
resultado con los mismos datos. Los intervalos y timeframes se cambian en `.env`
(`SMC_INTERVAL`, `TECH_INTERVAL`, `MACRO_INTERVAL`, `SMC_TIMEFRAME`, `WYCKOFF_TIMEFRAME`).

### Uso de Claude y de tokens

Claude solo interviene en el agente macro, y solo cuando **cambian los
titulares**: una única llamada cubre todos los activos, pide salida estructurada
(`messages.parse` con un modelo Pydantic), usa esfuerzo `low` y el veredicto se
cachea 6 horas. El modelo es `claude-opus-5-5`; para gastar menos pon
`CLAUDE_MODEL=claude-haiku-5-5`. Sin `ANTHROPIC_API_KEY` el agente usa la media
de las puntuaciones de Alpha Vantage, y sin ninguna de las dos claves vota
NEUTRAL.

Los titulares son texto de terceros. El prompt los trata como datos, y aunque
alguno consiguiera manipular la respuesta, lo máximo que mueve es un voto de
cinco con confianza acotada: el riesgo y la ejecución nunca pasan por el modelo.
Si Claude se niega a responder o falla, el voto queda NEUTRAL.

### Alpha Vantage

La clave gratuita da 25 peticiones al día y rechaza las ráfagas (más de una por
segundo). El cliente espacia las llamadas, reparte un presupuesto diario
(`AV_DAILY_BUDGET`, 20 por defecto) entre los activos y cachea también las
respuestas vacías. Ojo: en algunas claves gratuitas `NEWS_SENTIMENT` responde
"premium endpoint"; en ese caso el agente sigue con los RSS (`NEWS_RSS_URLS`).

### Calendario económico

Alpha Vantage no ofrece calendario económico, así que no hay ninguno conectado
en vivo. Lo que hay es `macro_events.csv`: apuntas ahí los eventos de alto
impacto y el agente macro **veta nuevas entradas** 30 minutos antes y después
(`BLACKOUT_MIN`). Para automatizarlo, sustituye `upcoming_event()` en
`agents/macro_agent.py` por un proveedor que sí lo tenga.

## El Agente Jefe y el riesgo

`score = Σ peso · dirección · confianza / Σ peso`, en [-1, 1]. Los votos NEUTRAL
cuentan en el denominador, así que hace falta acuerdo amplio. Opera si
`|score| ≥ 0.25` y hay al menos 3 agentes con señal vigente (una señal caduca a
las 3 veces el intervalo de su agente).

Filtros, todos en `utils/risk.py` y `agents/boss_agent.py`:

- **Stop y objetivo.** Stop en el nivel técnico más cercano que defienden los
  agentes a favor (si queda entre 0,5 y 3 ATR), y si no a 1,5 × ATR(15m).
  Nunca a más del 5 % de la entrada. Objetivo a 2 veces el riesgo.
- **Tamaño.** `capital × 1 % / distancia al stop`. El riesgo por operación tiene
  un techo fijo del 2 % en el código, y el nocional no supera 3× el capital.
- **Límite diario.** Si el capital (con el PnL flotante) cae un 3 % respecto al
  inicio del día UTC, se cierran las posiciones y no se abre nada hasta mañana.
- **Límite total.** Con 500 USDT de pérdida acumulada salta el kill switch solo.
  Reanudar desde el dashboard reinicia esa cuenta: es aceptar otros 500.
- **Otros.** Una posición por activo, máximo 2 abiertas, 15 minutos de espera
  tras cada cierre antes de reentrar al mismo activo.
- **Protección en el exchange.** En `sandbox`/`live` el SL y el TP quedan como
  órdenes `reduceOnly` en el exchange, así que protegen aunque el programa se
  caiga. Si no se pueden colocar, la posición se cierra en el acto.

Los valores (3550 de capital, 5 % de stop, 500 de pérdida máxima) vienen de lo
que ya usabas en tu bot anterior. Se cambian en `.env`.

## Dashboard

- Una tarjeta por agente: estado (Analizando, Alerta, Descanso, Error), última
  recomendación, confianza, niveles, justificación y peso actual. "Alerta" es
  confianza ≥ 70 o un veto.
- Panel del jefe: decisión, score, capital, PnL flotante y del día, operaciones.
- **KILL SWITCH**: bloquea nuevas entradas en el siguiente ciclo (30 s como
  mucho) y, si dejas marcada la casilla, cierra lo abierto a mercado.
- **AUTO / MANUAL**: en manual el jefe publica la propuesta completa y espera a
  que pulses "Aprobar y ejecutar". La aprobación vale 2 minutos y se ejecuta la
  decisión vigente en ese momento, no una antigua.

## Auto-mejora

Cada operación se guarda con el voto exacto de cada agente al entrar
(`utils/trade_logger.py`). `optimizer.py` calcula win rate, profit factor y
Sharpe, y para cada agente y activo mide cuántas veces votó la dirección que
resultó rentable. Con al menos 5 votos, su peso pasa a `2 × precisión
suavizada`, entre 0,25 y 2,0. `main.py` lo ejecuta una vez por semana; a mano:

```bash
python optimizer.py --dry-run     # ver el informe sin guardar
python optimizer.py --days 30     # recalcular y guardar
```

## Límites que conviene conocer

- **No es alta frecuencia.** Decide cada 30-60 segundos sobre velas de 1 minuto
  en adelante. HFT de verdad exige colocation y otro lenguaje.
- **Solo cripto.** CCXT no conecta con brokers de Forex.
- **No hay ventaja demostrada.** SMC y Wyckoff están implementados como reglas
  mecánicas simplificadas; cada simplificación está marcada en el código con
  `ponytail:` y dice cuál es su techo. Que el código funcione no significa que
  la estrategia gane dinero: eso solo lo dice un periodo largo en paper.
- **El paper es optimista.** Sale al precio exacto del stop (sin huecos ni
  deslizamiento) y revisa cada 30 s con velas de 1m.
- **El optimizador aprende despacio.** Con pocas operaciones la precisión es
  ruido; por eso el ajuste está suavizado. Además solo ve las operaciones que se
  abrieron, no las señales descartadas.
- **Probado sin red.** `selfcheck.py` cubre riesgo, decisión, ciclo completo de
  una operación, límites, kill switch, modo manual, el broker CCXT (contra un
  exchange simulado) y el optimizador. Lo que **no** se ha ejecutado contra un
  servicio real: órdenes en un exchange, la API de Claude, Alpha Vantage y los
  RSS. Antes de `live`, pasa por `sandbox` y comprueba en la web del exchange
  que cada entrada crea sus dos órdenes de salida.

## Archivos

```
main.py               orquestador asyncio (--once para un solo ciclo)
dashboard.py          panel Streamlit
optimizer.py          métricas y reajuste de pesos
selfcheck.py          comprobación de extremo a extremo
config.py             toda la configuración (.env)
macro_events.csv      calendario manual de eventos
agents/
  __init__.py         Signal: el contrato JSON de los agentes
  boss_agent.py       decisión, límites y ejecución
  historian_agent.py  macro_agent.py  smc_agent.py  wyckoff_agent.py  sentiment_agent.py
utils/
  exchange.py         datos de mercado (CCXT), PaperBroker y CcxtBroker
  alpha_vantage.py    noticias: Alpha Vantage + RSS
  cache.py            caché con caducidad y estado compartido (SQLite)
  risk.py             ATR, stop, tamaño de posición, límites
  trade_logger.py     registro de operaciones
```
