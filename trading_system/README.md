# Mesa de trading multi-agente con selección natural

Sistema intradía para cripto (futuros perpetuos vía CCXT). Cinco agentes analizan
cada activo, un Agente Jefe pondera sus votos, aplica los filtros de riesgo y
ejecuta. Los agentes que fallan o recomiendan una operación perdedora mueren y
renacen como una versión nueva con una lección añadida. Todo queda en SQLite.

```
 5 agentes ──JSON minificado──▶ Agente Jefe ──orden + SL/TP──▶ Broker (paper | sandbox | real)
 (asyncio, cada uno                │   ▲                             │
  a su ritmo)                      │   └─ pesos ◀─ optimizer.py ◀────┤ PnL NETO de comisiones
      ▲                            ▼                                 │ y deslizamiento
      └── reglas ◀── evolution_manager.py ◀──── 💀 muerte / 🟢 vN+1 ◀┘
                                   │
            dashboard.py  ·  Telegram / Discord  ·  trades_history.db
```

## Puesta en marcha

Necesitas Python 3.10 o superior.

```bash
python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate         # macOS / Linux
pip install -r requirements.txt

python selfcheck.py              # 1. comprueba toda la lógica, sin red ni claves
copy .env.example .env           # 2. Windows   (cp en macOS / Linux)
python main.py --once            # 3. un ciclo real con datos del exchange
python main.py                   # 4. el motor (déjalo corriendo)
streamlit run dashboard.py       # 5. en otra terminal: el panel
```

Por defecto `PAPER_TRADING=True`: precios reales, órdenes simuladas y **ninguna
clave necesaria**. Empieza ahí.

## Configuración (`.env`)

| Variable | Para qué | Obligatoria |
|---|---|---|
| `PAPER_TRADING` | `True` simula; `False` envía órdenes reales | — (por defecto `True`) |
| `EXCHANGE_API_KEY` / `EXCHANGE_SECRET` | Órdenes fuera del paper | Solo con `PAPER_TRADING=False` |
| `ANTHROPIC_API_KEY` | Que Claude evalúe titulares y haga los post-mortem | No |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alertas por Telegram | No |
| `DISCORD_WEBHOOK_URL` | Alertas por Discord | No |
| `ALPHAVANTAGE_API_KEY` | Noticias con puntuación de sentimiento | No |

`.env` está en `.gitignore`. No lo subas a ningún repositorio.

### De paper a dinero real

1. **`PAPER_TRADING=True`** — sin claves. Déjalo correr días y mira el registro.
2. **`PAPER_TRADING=False`** con `EXCHANGE_SANDBOX=True` (por defecto) — órdenes
   reales contra el entorno de pruebas. En Binance el testnet clásico de futuros
   ya no existe; su reemplazo es *Demo Trading*, y las claves se crean en
   <https://demo.binance.com/en/my/settings/api-management>.
3. **`EXCHANGE_SANDBOX=False`** — dinero real. Además exige
   `CONFIRM_LIVE=SI_ARRIESGO_DINERO_REAL`; sin esa línea el programa no arranca.

Solo `false`, `0`, `no` u `off` desactivan el paper: una errata como
`PAPER_TRADING=Flase` deja la simulación activa. La cuenta de futuros debe estar
en modo *one-way* (no *hedge*).

## Los agentes

Todos devuelven la misma estructura (`agents/__init__.py: Signal`): dirección,
confianza 0-100, soporte, resistencia, una línea de justificación y las medidas
numéricas (`features`) en las que se basó.

| Agente | Linaje | Qué calcula | Velas | Cada |
|---|---|---|---|---|
| Historiador | `Market_Historian` | Regresión sobre log-precio, R², desviación en σ, estacionalidad horaria | 1h | 3 min |
| Macro | `Macro_Watcher` | Titulares (Alpha Vantage + RSS) y calendario de eventos | — | 15 min |
| SMC | `SMC_Scalper` | BOS / CHoCH, order blocks, FVG, barridos y pools de liquidez | 5m | 1 min |
| Wyckoff | `Wyckoff_Volume` | Fase de Wyckoff, POC, HVN y área de valor | 1h | 3 min |
| Tendencia | `Trend_Sentiment` | EMA 50/200, MACD y RSI en 4H y 1D, desequilibrio del libro | 4h, 1d | 3 min |

Cuatro de los cinco son cálculo puro: no gastan tokens y dan siempre el mismo
resultado con los mismos datos. Solo el agente macro consulta a Claude.

## Selección natural (`agents/evolution_manager.py`)

**Cuándo muere un agente (💀):**

- Su código lanza un error. Los fallos de red o del exchange no cuentan: no son
  culpa del agente y matarlo no los arregla.
- Votó a favor de una operación que salió por stop o con **PnL neto** negativo.
  El PnL que se evalúa ya tiene restadas comisiones y deslizamiento, así que una
  operación que toca el objetivo pero no cubre costes también mata.

No muere quien votó en contra o se abstuvo, ni nadie cuando la operación la
cierra el kill switch o un límite de riesgo (la cerraste tú, no el mercado).

**Qué pasa después (🟢 versión N+1):**

1. *Post-mortem.* Con `ANTHROPIC_API_KEY`, Claude recibe las instrucciones del
   agente, la señal que emitió con sus medidas y el resultado, y devuelve la
   causa raíz, una lección en una línea y, si procede, una regla medible. Sin
   clave (o sin presupuesto de tokens) se usa una mutación fija: exigir más
   confianza para repetir ese tipo de señal.
2. *Mutación.* La versión nueva hereda las instrucciones más la lección. Una
   regla medible tiene la forma "si `rsi4 > 75` en LONG, restar 40 de confianza"
   y se aplica a cada señal futura del agente. Solo se acepta si usa una medida
   real de la señal y si habría penalizado la operación que mató al agente; la
   penalización va de 10 a 60 puntos.
3. *Renacimiento.* Ocurre en segundo plano; mientras tanto el agente no vota.

**Qué muta y qué no.** Mutan las instrucciones (el *system prompt*, que el
agente macro envía tal cual a Claude) y las reglas sobre las medidas. El código
Python de los agentes no se reescribe solo: sería irreproducible e inseguro en
un sistema que mueve dinero. Un error de código queda registrado como lección,
pero arreglarlo sigue siendo trabajo de una persona; el mismo error no vuelve a
matar a las versiones siguientes, para no generar versiones sin fin.

**Persistencia.** Cada versión, con sus instrucciones, reglas, causa de muerte y
post-mortem, se guarda en `trades_history.db` (tabla `agent_versions`). Al
arrancar, cada agente retoma su versión más alta: nunca se vuelve a `v1`.

Cada agente conserva sus últimas 8 lecciones (`MAX_RULES`); las más antiguas se
olvidan.

## Costes: comisiones y deslizamiento (`utils/risk_manager.py`)

- Comisión del 0,075 % por lado (`FEE_PCT`) y deslizamiento del 0,02 % por orden
  a mercado (`SLIPPAGE_PCT`), siempre en contra.
- En paper, la entrada y el stop se ejecutan peor que el precio visto; el
  objetivo es una orden límite y no desliza.
- El tamaño de la posición ya descuenta los costes: si salta el stop, la pérdida
  **neta** es el 1 % del capital.
- El registro guarda por separado PnL neto, comisiones y deslizamiento. El
  optimizador, las alertas y el gestor de evolución solo ven el neto.

## Tokens (`utils/token_budget.py`)

- **Mensajes entre agentes.** Cada señal viaja al jefe como JSON minificado con
  claves de una o dos letras. Nunca supera 150 tokens estimados: si hace falta
  se recorta la justificación.
- **Gasto real.** Se anota el consumo de cada respuesta de Claude. Al llegar a
  `DAILY_TOKEN_BUDGET` (200 000 por día UTC), Claude se apaga hasta mañana y el
  sistema sigue con sus respaldos.
- **Cuándo se llama a Claude.** El agente macro, solo si cambian los titulares o
  su prompt (una llamada cubre todos los activos y se cachea 6 horas), y un
  post-mortem por cada muerte. Modelo `claude-opus-5-5` con esfuerzo `low`; para
  gastar menos, `CLAUDE_MODEL=claude-haiku-5-5`.

Los titulares son texto de terceros. Los prompts los tratan como datos, y lo
máximo que puede mover una respuesta manipulada es un voto de cinco con
confianza acotada o una regla que solo resta confianza: el riesgo y la ejecución
nunca pasan por el modelo.

## Caché (`utils/cache_manager.py`)

`diskcache` en `data/cache/`. Las velas se reutilizan entre 1 y 3 minutos según
el timeframe. Las noticias duran más por necesidad: la clave gratuita de Alpha
Vantage da 25 peticiones al día, así que se reparten a lo largo de 24 horas. El
último precio se refresca cada 5 segundos, porque de él dependen los stops.

## Alertas (`utils/notifier.py`)

Con Telegram o Discord configurados, llega un mensaje cuando se abre o se cierra
una posición (con su PnL neto) y en cada `BOT_DEATH` y `REBIRTH`. Los envíos van
en segundo plano: si el servicio falla, el motor no se entera.

Para Telegram: crea un bot con @BotFather, copia el token y obtén tu `chat_id`
escribiéndole al bot y abriendo `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## El Agente Jefe y el riesgo

`score = Σ peso · dirección · confianza / Σ peso`, en [-1, 1]. Los votos NEUTRAL
cuentan en el denominador, así que hace falta acuerdo amplio. Opera si
`|score| ≥ 0.25` y hay al menos 3 agentes con señal vigente.

- **Stop y objetivo.** Stop en el nivel técnico más cercano que defienden los
  agentes a favor (si queda entre 0,5 y 3 ATR), y si no a 1,5 × ATR(15m). Nunca
  a más del 5 % de la entrada. Objetivo a 2 veces la distancia del stop.
- **Tamaño.** 1 % del capital en riesgo neto; techo fijo del 2 % en el código;
  nocional máximo de 3× el capital.
- **Límite diario.** Con una caída del 3 % en el día UTC se cierran las
  posiciones y no se abre nada hasta mañana.
- **Límite total.** Con 500 USDT de pérdida acumulada salta el kill switch solo.
- **Otros.** Una posición por activo, máximo 2 abiertas, 15 minutos de espera
  tras cada cierre.
- **Protección en el exchange.** Fuera del paper, el SL y el TP quedan como
  órdenes `reduceOnly`. Si no se pueden colocar, la posición se cierra.

Los pesos de cada agente salen de su historial de aciertos (`optimizer.py`, una
vez por semana, o `python optimizer.py --dry-run`).

## Dashboard

- **Modo activo** en la cabecera: `PAPER TRADING ACTIVO`, `SANDBOX DEL EXCHANGE`
  o `REAL TRADING`.
- **Métricas**: capital, PnL neto, win rate global, costes pagados, PnL del día.
- **Tarjetas de agentes** con su generación, estado, señal y justificación.
- **Matriz genealógica**: cada versión de cada agente, 🟢 Vivo o 💀
  Muerto-Sustituido, confianza actual y causa del último fallo.
- **Consola de mutaciones**: muertes, post-mortem y renacimientos.
- **Auditoría de tokens**: medidor del presupuesto diario y tokens por ciclo.
- **KILL SWITCH**: bloquea nuevas entradas en el siguiente ciclo (30 s como
  mucho) y, si dejas marcada la casilla, cierra lo abierto a mercado.
- **AUTO / MANUAL**: en manual el jefe propone y espera "Aprobar y ejecutar".

## Límites que conviene conocer

- **Morir por cada pérdida no es aprender.** Toda estrategia rentable pierde
  muchas operaciones, y una sola pérdida casi no dice nada sobre si la señal era
  mala. Con esta regla los agentes acumulan penalizaciones y tienden a volverse
  más callados, no necesariamente mejores. Las penalizaciones acotadas y el
  olvido de lecciones viejas limitan ese efecto, pero no lo eliminan. Vigila en
  la matriz genealógica si un linaje supera muchas versiones sin mejorar.
- **No hay ventaja demostrada.** SMC y Wyckoff son reglas mecánicas
  simplificadas; cada simplificación está marcada con `ponytail:` y dice cuál es
  su techo. Que el código funcione no significa que la estrategia gane dinero.
- **El deslizamiento es fijo.** No depende del tamaño de la orden ni de la
  liquidez; en mercados rápidos el real será peor.
- **No es alta frecuencia** y **solo opera cripto** (CCXT no conecta con Forex).
- **No hay calendario económico en vivo.** `macro_events.csv` se mantiene a
  mano; el agente macro veta entradas 30 minutos antes y después de cada evento.
- **Probado sin red.** `selfcheck.py` cubre riesgo y costes, decisión, ciclo
  completo de una operación, límites, kill switch, modo manual, evolución
  (muerte, mutación, persistencia tras reinicio), tokens, alertas, el broker
  CCXT contra un exchange simulado y el optimizador. **No** se ha ejecutado
  contra un servicio real: órdenes en un exchange, la API de Claude, Telegram,
  Discord, Alpha Vantage ni los RSS.

## Archivos

```
main.py                  orquestador asyncio (--once para un solo ciclo)
dashboard.py             panel Streamlit
optimizer.py             métricas y pesos por historial de aciertos
selfcheck.py             comprobación de extremo a extremo
config.py                toda la configuración (.env)
trades_history.db        SQLite: operaciones, genealogía, estado (se crea solo)
agents/
  __init__.py            Signal: el contrato de los agentes
  boss_agent.py          decisión, límites y ejecución
  evolution_manager.py   muerte, post-mortem y renacimiento
  historian_agent.py  macro_agent.py  smc_agent.py  wyckoff_agent.py  sentiment_agent.py
utils/
  risk_manager.py        ATR, stop, tamaño, comisiones y deslizamiento
  trade_logger.py        operaciones y árbol genealógico en SQLite
  token_budget.py        JSON minificado y auditoría de tokens
  cache_manager.py       caché en disco (diskcache)
  notifier.py            Telegram y Discord
  exchange.py            datos de mercado (CCXT), PaperBroker y CcxtBroker
  alpha_vantage.py       noticias: Alpha Vantage + RSS
  db.py                  conexión SQLite y estado compartido
.claude/skills/          The Day Trader skill (terceros, acciones de EE. UU.)
```
