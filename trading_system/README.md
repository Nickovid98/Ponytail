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

Necesitas Python 3.11 o superior.

**En Windows, lo fácil:** doble clic en `Iniciar.bat`. La primera vez prepara el
entorno (unos minutos) y crea tu `.env`; después abre la app en el navegador. El
motor se enciende y se apaga desde la propia app, y al cerrar esa ventana se
apagan los dos.

**La cuenta de fondeo simulada:** doble clic en `Iniciar cuenta de fondeo.bat`.
Abre otra app (en `localhost:8502`) con 50.000 USD, cripto y acciones, en largo
y en corto, y las reglas de una cuenta de fondeo. Se explica en «Cuenta de
fondeo simulada: cripto y acciones».

A mano, en cualquier sistema:

```bash
python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate         # macOS / Linux
pip install -r requirements.txt

python selfcheck.py              # 1. comprueba toda la lógica, sin red ni claves
copy .env.example .env           # 2. Windows   (cp en macOS / Linux)
python main.py --once            # 3. un ciclo real con datos del exchange
streamlit run dashboard.py       # 4. la app; el motor se enciende desde ella
python main.py                   #    (o el motor suelto, sin app, en una terminal)
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
| SMC | `SMC_Scalper` | BOS / CHoCH, order blocks, FVG, barridos y pools de liquidez | 5m (acciones 1h) | 1 min |
| Wyckoff | `Wyckoff_Volume` | Fase de Wyckoff, POC, HVN y área de valor | 1h | 3 min |
| Tendencia | `Trend_Sentiment` | EMA 50/200, MACD y RSI en 4H y 1D, desequilibrio del libro | 4h, 1d | 3 min |
| Escenarios | `Scenario_Simulator` | 10.000 futuros ficticios por activo y vota solo por las 5 mejores apuestas | 5m | 3 min |

Cinco de los seis son cálculo puro: no gastan tokens y dan siempre el mismo
resultado con los mismos datos. Solo el agente macro consulta a Claude.

### El bot de escenarios (`agents/scenario_agent.py`)

Rehace el futuro de cada activo 10.000 veces (`SCENARIOS`) con sus propias velas
de 5 minutos, con el método de la skill `trader-timemachine`: cada tramo es una
vela pasada, escalada al precio de ahora. En acciones, una vela de la misma hora
de la sesión (±2 velas) de las sesiones completas de los últimos 60 días, hasta 5
minutos antes del cierre; con la bolsa cerrada no simula. En cripto, cualquiera
de las últimas ~3,5 jornadas, durante 6 horas (`SCENARIO_HOURS`). En cada
escenario prueba la operación que pondría el jefe, en largo y en corto (stop por
ATR, objetivo a 2 veces el riesgo, con comisiones y deslizamiento), y anota el
resultado en R: −1 en el stop (si una vela toca los dos niveles, cuenta el stop),
el objetivo neto de costes, o lo que valga al acabar el tiempo.

Después ordena las apuestas (el mejor lado de cada activo) por su resultado medio
y vota solo por las 5 mejores (`SCENARIO_TOP`) que salen positivas; si ninguna lo
es, no vota por nada. Su voto cuenta como el de cualquier otro bot: el jefe sigue
exigiendo acuerdo. La Sala de control enseña su tabla: puesto, lado, cuántas
veces llega antes al objetivo o al stop y el resultado medio.

No predice: los escenarios repiten cómo se movió el activo hace poco, y si el
mercado cambia de carácter se quedan viejos al momento. No está en el backtest:
10.000 simulaciones por activo en cada paso harían la simulación eterna.

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

**Recompensas y confianza.** Al cerrarse una operación, cada bot cobra o paga en
R (veces lo arriesgado): gana quien votó la dirección que resultó rentable y
paga quien votó la contraria; un voto neutral no puntúa y un cierre forzoso por
un límite de riesgo ni premia ni castiga. Una sola operación no mueve más de
3 R. Con la recompensa media por voto el jefe fija la confianza (el peso) de
cada bot en cada activo, entre ×0,25 y ×2, a partir de 5 votos. Lo recalcula él
solo tras cada cierre (`optimizer.py`; se puede apagar en la app) y deja cada
cambio en su diario.

## Alpaca: precios reales, órdenes simuladas

Con `EXCHANGE_ID=alpaca` el motor y el backtest leen las velas de cripto de
Alpaca. Solo simula: `PAPER_TRADING=False` se rechaza al arrancar, porque Alpaca
cripto es contado (sin cortos ni stops reduce-only) y esa ejecución no existe.

```env
EXCHANGE_ID=alpaca
SYMBOLS=BTC/USD,ETH/USD
QUOTE=USD
FEE_PCT=0.25                 # comisión real de Alpaca cripto por lado
EXCHANGE_API_KEY=            # opcional: clave de tu cuenta paper
EXCHANGE_SECRET=
```

Sin clave funciona, pero Alpaca limita mucho los accesos anónimos (unas pocas
peticiones por minuto): alcanza para `backtest.py`, no para el motor en
continuo. Con la clave de una cuenta paper el límite deja de ser un problema;
la clave solo se usa para leer datos. Los cortos que abra la simulación no se
podrían replicar en una cuenta de Alpaca.

## Cuenta de fondeo simulada: cripto y acciones

`Iniciar cuenta de fondeo.bat` arranca la app y el motor con estas reglas, que
mandan sobre `.env` solo en esa ventana (son las de las simulaciones de cuenta
fondeada de `ESTADO.md`):

| Regla | Valor |
|---|---|
| Capital de cada cuenta | 50.000 USD |
| Riesgo por operación | 0,4 % (200), neto de costes |
| Pérdida diaria | 1.000; al 75 % (750) se cierra todo y no se opera hasta el día siguiente (UTC) |
| Pérdida máxima | la cuenta se pierde al caer 2.000 bajo su mejor cierre diario; ese suelo deja de subir en 50.000 |
| Margen | no se abre nada cuyo stop, sumado al de lo abierto, pase de lo que queda hasta el kill switch o el suelo |
| Activos | BTC y ETH (Binance) y NVDA, META, GOOGL, TTWO, MU, MSFT y UBER, en largo y en corto |
| Posiciones a la vez | 3, apalancamiento máximo 2x por posición |

Todo es simulado (`PAPER_TRADING=True` forzado): precios reales y ninguna orden
sale del equipo. El lanzador fija también exchange, estrategia, marcos de tiempo,
costes y frecuencia, para que tu `.env` no los cambie. El historial va aparte, en
`trades_fondeo.db`, y el registro del motor en `data/motor_trades_fondeo.txt` y
`trading_fondeo.log`; las dos apps pueden estar abiertas a la vez.

**Acciones.** Un símbolo sin "/" es una acción. Sus velas y su precio salen de
Yahoo Finance (gratis y sin clave; las de 4 h se forman con las de 1 h) y los bots
solo ven velas ya cerradas, como en el backtest. Usan velas de 1 hora para SMC,
Wyckoff y el stop (`STOCK_TIMEFRAME`), como en las simulaciones de cuenta
fondeada: con velas de 5 minutos perdían bastante más. Solo se opera en la sesión
regular de Nueva York: se entra desde que cierra la primera vela de 1 hora (10:30)
hasta media hora antes del cierre, y todo se cierra 5 minutos antes del cierre
(16:00, o 13:00 los días de cierre temprano). Si el motor estaba apagado al
cierre, lo que quedó abierto se cierra en la apertura siguiente, a precio de
entonces. Los festivos de 2026 y 2027 vienen de la skill `trader-alerts`. Costes:
0,005 % de comisión y 0,02 % de deslizamiento por lado (`STOCK_FEE_PCT`,
`STOCK_SLIPPAGE_PCT`), acciones enteras redondeadas hacia abajo, y un stop que
salta con un hueco de precio se ejecuta donde abrió esa vela. Las acciones solo
se simulan: con `PAPER_TRADING=False` el programa no arranca.

**Relevo de cuenta.** La cuenta se pierde la primera vez que toca el suelo, aunque
el precio rebote después: se cierra todo (mientras quede algo sin cerrar no se
abre nada), la cuenta queda anotada y nace otra con 50.000 que hereda lo
aprendido y corrige una causa, con el mismo diagnóstico que el backtest
(`--lineage`): stop más ancho, más acuerdo exigido, veto al par activo/lado que
más perdió o menos riesgo. El acuerdo y el riesgo nuevos quedan en Ajustes,
donde se pueden deshacer. La Sala de control enseña la cuenta en curso, su
suelo, la pérdida del día frente al kill switch, si la bolsa está abierta y las
cuentas perdidas.

**Si fallan los datos.** Cada posición se vigila por separado: si Yahoo o el
exchange no responden para un activo, se usa su último precio conocido y los
límites, el suelo y los cierres siguen funcionando para todo lo demás. Tras un
apagado, los stops y objetivos se revisan desde la apertura de cada operación,
así que lo que saltó mientras el motor no miraba también cuenta. Tras un 429 de
Yahoo (demasiadas peticiones) se le deja de pedir dos minutos.

Lo que no modela: el coste de pedir prestadas las acciones para ir corto, la
regla del tick al alza, el preapertura ni las subastas, ni las paradas de
cotización (se opera con el último precio que dé Yahoo). Yahoo no tiene API
oficial: si cambia o bloquea, las acciones se quedan sin datos y no operan
(cripto sigue). La cuenta mezcla cripto y acciones y no tiene un backtest igual.

## Topstep: futuros con TopstepX (`utils/topstepx.py`)

Con `BROKER=topstepx` el mismo motor opera futuros de una cuenta de Topstep a
través de la API de ProjectX (`https://api.topstepx.com`). Los agentes, el jefe
y la evolución no cambian; cambian los datos, la ejecución y los límites.

**Se ejecuta solo en tu ordenador.** Topstep exige que toda la actividad salga
de un dispositivo propio, sin VPS, VPN ni acceso remoto. No lo despliegues en
AWS, Google Cloud, DigitalOcean ni similares, ni lo arranques desde una sesión
en la nube.

```env
BROKER=topstepx
SYMBOLS=MNQ                      # raíz del contrato; se resuelve al vencimiento activo
TOPSTEPX_USERNAME=tu_usuario
TOPSTEPX_API_KEY=tu_clave        # solo aquí, en .env; nunca en el código ni en el chat
TOPSTEPX_ACCOUNT_ID=             # obligatorio si tienes más de una cuenta operable
PROP_DAILY_LOSS_LIMIT_USD=1000   # el límite diario de TU cuenta, tal como lo muestra Topstep
PROP_ACCOUNT_FLOOR_USD=48000     # saldo en el que pierdes la cuenta (Maximum Loss Limit de hoy)
PROP_KILL_AT_PCT=75              # el cierre forzoso salta al 75 % del límite (techo: 80)
PROP_MAX_CONTRACTS=1
CAPITAL=50000                    # solo para la simulación
TOTAL_LOSS_LIMIT_USD=0
```

Los dos importes son los de tu cuenta: el programa no los adivina y no arranca
sin el primero. El suelo de la cuenta se mueve al cierre de cada día con
beneficio, así que hay que actualizarlo a mano.

| Regla | Cómo se cumple |
|---|---|
| Límite de pérdida diaria | En cada ciclo (30 s) se suma lo realizado y lo flotante desde las 17:00 de Chicago. Al llegar al 75 % del límite, o del margen que quede hasta el suelo de la cuenta si es menor, se cierran todas las posiciones, se cancelan todas las órdenes y el motor queda parado hasta el siguiente día operativo. El bloqueo se guarda en SQLite: reiniciar no lo levanta. |
| Reinicio a media sesión | El saldo de inicio del día se reconstruye con las ejecuciones de hoy, de modo que lo ya perdido sigue contando. |
| Stop siempre en el servidor | La entrada lleva stop y objetivo en la misma orden. Después se comprueba en la cuenta que existe la posición y un stop del lado correcto; si no, se cierra en el acto. |
| Frecuencia | Decisiones cada 30-60 s, 60 s mínimos entre órdenes, 15 min de espera por contrato tras un cierre, 10 operaciones al día como máximo, stops de más de 5 ticks y una petición a la API cada 0,35 s como mucho. Tres órdenes fallidas seguidas detienen el motor hasta mañana. |
| Horario | Sin entradas desde las 14:30 y todo cerrado a las 15:00, hora de Chicago (`PROP_LAST_ENTRY_CT`, `PROP_FLAT_BY_CT`). |
| Auditoría | Cada petición, respuesta HTTP, error de conexión, cambio de estado de una orden y activación del kill switch va a la consola y a `trading.log` (20 MB por archivo, 10 archivos). La clave y el token nunca se escriben. |

Orden recomendado: primero `PAPER_TRADING=True` (datos reales de TopstepX,
órdenes simuladas con contratos enteros, 1 tick de deslizamiento y comisión por
contrato). Para enviar órdenes a la cuenta: `PAPER_TRADING=False` y
`CONFIRM_LIVE=SI_ARRIESGO_DINERO_REAL`, con 1 micro y mirando `trading.log`.

Lo que no está comprobado contra el servicio real, porque este código solo se
ha probado con una API falsa local (`selfcheck.py`):

- Los nombres exactos de los campos de cada respuesta. Si alguno difiere, el
  motor falla al arrancar o al verificar la orden, no en silencio.
- El signo de los ticks en los brackets. Por defecto van con signo (stop de un
  largo en negativo). Si la plataforma rechaza la orden, prueba
  `PROP_BRACKET_SIGNED=false`; si el stop quedara del lado equivocado, la
  verificación cierra la posición.
- Si tienes brackets automáticos activados en la plataforma, desactívalos:
  se sumarían a los de la orden.

Simplificaciones: precio y estado de las posiciones se consultan por REST (hasta
5 s de retraso) en lugar de los hubs en tiempo real de `rtc.topstepx.com`; no
hay libro de órdenes, así que el agente de sentimiento trabaja sin él; y
`backtest.py` no descarga históricos de futuros.

## Backtest (`backtest.py`)

```bash
python backtest.py --days 30                  # descarga las velas, las guarda y simula
python backtest.py --days 30 --evolution      # con la selección natural activada
python backtest.py --days 60 --threshold 0.35 --rr 1.5 --sl-mult 2
```

Reproduce el motor vela a vela con las mismas piezas que en vivo (agentes,
decisión del jefe, stop, tamaño, comisiones y deslizamiento) y sin mirar nunca
una vela que aún no ha cerrado. Devuelve operaciones, win rate, PnL neto,
profit factor, caída máxima, costes y la precisión de cada agente. Úsalo para
comparar configuraciones entre sí, y con `--evolution` para ver si la selección
natural mejora o empeora el resultado.

No reproduce el agente macro (no hay titulares históricos), el libro de órdenes,
los pesos del optimizador ni los post-mortem con Claude.

### Cuenta fondeada por generaciones (`--lineage`)

`python backtest.py --days 365 --evolution --lineage 2000 [--flat]` simula una
cuenta de fondeo: el bot muere cuando su saldo cae 2.000 bajo su mejor cierre
diario (el suelo deja de subir al llegar al capital inicial, como en Topstep) y
nace un sucesor con capital nuevo. El sucesor hereda los pesos de los agentes,
calculados con el historial de todos los bots anteriores, más sus vetos y
ajustes, y corrige una causa de la muerte, por este orden: stop más ancho si los
costes superaban el 25 % de lo arriesgado, umbral más alto si acertaba antes de
costes, veto al par activo/lado que más perdió, umbral más alto y, por último,
menos riesgo por operación. `--flat` cierra todo al final de cada sesión y no
entra en la última media hora (acciones). El informe da el resultado por semana.

Cada bot solo aprende del pasado, así que el recorrido completo es una prueba
honesta de si el aprendizaje sirve. Deja de serlo si se cambian las reglas y se
repite hasta que el año salga bien: eso es memorizar el año.

### Ruptura del rango de apertura con validación fuera de muestra (`STRATEGY=orb`)

Con `STRATEGY=orb` opera un único agente, `agents/orb_agent.py`, con las reglas
de las skills `trader-entry` y `trader-exit`: el rango es el máximo y el mínimo
de los primeros minutos de la sesión, la entrada es la primera vela que CIERRA
fuera de él (una mecha no cuenta), solo vale la primera ruptura del día y el
stop va al punto medio del rango. Su genoma tiene seis genes que se entrenan:
minutos del rango, stop (punto medio, o `atr` para dejarlo al gestor de riesgo),
lados (solo largos o ambos), objetivo en R (10 equivale a mantener hasta el
cierre) y dos tomados del estudio de Zarattini, Barbon y Aziz (2024) sobre
"acciones en juego": `rvol`, que exige que el rango de apertura traiga un
volumen varias veces mayor que el de las sesiones anteriores (casi siempre por
una noticia), y `dir`, que solo opera en el sentido de la vela de apertura.
Cuando hay más señales que huecos entra primero la de mayor volumen relativo.

```bash
STRATEGY=orb EXCHANGE_ID=yahoo SYMBOLS=NVDA,META,MSFT SMC_TIMEFRAME=1h ATR_TIMEFRAME=1h \
CAPITAL=50000 RISK_PER_TRADE_PCT=0.4 TOTAL_LOSS_LIMIT_USD=0 PROP_DAILY_LOSS_LIMIT_USD=1000 \
python backtest.py --walk-forward 180,60 --lineage 2000 --flat
```

`--walk-forward ENTRENA,EXAMINA` parte la historia en exámenes consecutivos.
Para cada uno, la población entera de genomas opera los días de entrenamiento
anteriores con reglas de cuenta fondeada; los que pierden la cuenta quedan
eliminados, y de los supervivientes con ganancia se elige el mejor. Ese genoma,
congelado, opera el examen, del que el entrenamiento no ha visto ni una vela. Si
nadie sobrevive ganando, en ese examen no se opera. El informe compara lo que
los elegidos ganaron entrenando con lo que hicieron en el examen: esa
diferencia es lo memorizado.

`EXCHANGE_ID=yahoo` lee acciones de Yahoo Finance sin clave: unos 3 años en
velas de 1 hora, y solo los últimos 60 días en velas de 5 minutos. El motor en
vivo no tiene todavía una fuente de precios de acciones: esta estrategia solo
se puede simular.

## La app (`dashboard.py`)

Solo escucha en `localhost` (`.streamlit/config.toml`): tiene el kill switch y
el botón de aprobar órdenes y no pide contraseña.

- **Barra lateral.** Encender y apagar el motor (se apaga al terminar el ciclo
  en curso, nunca a mitad de una orden), KILL SWITCH de emergencia, modo AUTO o
  MANUAL y tu objetivo de rentabilidad anual, contra el que la app compara el
  resultado real.
- **Sala de control.** Modo activo, capital, resultado neto, ritmo anual (solo
  con dos semanas de historia o más), qué decide el jefe y por qué, la
  propuesta pendiente de aprobar, posiciones abiertas con su precio y su
  resultado flotante, y la bitácora de lo último que ha pasado.
- **Los bots.** Una tarjeta por bot: recompensas acumuladas, nivel, aciertos,
  fallos, racha, la confianza que le da el jefe y qué opina ahora. Debajo, cada
  bot a fondo: su curva de recompensas, cada voto con su resultado, las
  lecciones que heredó, sus relevos y un interruptor para mandarlo al banquillo
  (ni analiza ni vota). Se pueden mirar los bots del motor o los de la última
  simulación.
- **El Jefe.** La confianza en cada bot por activo, su diario (operaciones,
  cambios de confianza, límites, relevos y lo que tú cambies), "Optimizar
  ahora", el interruptor de auto-optimización y las generaciones de cada bot.
- **Laboratorio.** Simula una configuración sobre precios reales del pasado,
  cripto o acciones, con o sin selección natural y reglas de cuenta fondeada, y
  enseña el veredicto frente a tu objetivo, la curva, lo que hizo cada bot y el
  resultado por semana. "Usar estos ajustes en el motor" los copia al motor.
- **Ajustes.** Riesgo por operación, acuerdo exigido, objetivo, distancia del
  stop, posiciones a la vez, descanso y bots mínimos, cada uno con su rango: el
  motor recorta lo que se salga. El stop máximo, los límites de pérdida y el
  paso a dinero real solo se cambian en `.env`. También qué claves tienes
  conectadas y el consumo de Claude del día.

Con el motor apagado nadie vigila los stops de las posiciones simuladas; en
sandbox y en real los stops viven en el exchange.

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
- **No es alta frecuencia.** Opera cripto por CCXT y, solo en simulación,
  acciones de EE. UU. con precios de Yahoo (CCXT no conecta con Forex).
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
backtest.py              simulación sobre velas históricas
CLAUDE.md                guía de trabajo para sesiones de Claude Code
ESTADO.md                qué está hecho, qué se probó ya (con sus cifras) y qué falta, para retomar el trabajo
dashboard.py             la app (Streamlit): bots, jefe, laboratorio y ajustes
Iniciar.bat              lanzador para Windows: prepara el entorno y abre la app
Iniciar cuenta de fondeo.bat   la misma app con la cuenta de fondeo simulada (cripto y acciones)
optimizer.py             recompensas, confianza del jefe en cada bot y ajustes de la app
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
  trade_logger.py        operaciones, árbol genealógico y bitácora de actividad en SQLite
  token_budget.py        JSON minificado y auditoría de tokens
  cache_manager.py       caché en disco (diskcache)
  notifier.py            Telegram y Discord
  exchange.py            datos de mercado (CCXT), PaperBroker y CcxtBroker
  alpha_vantage.py       noticias: Alpha Vantage + RSS
  db.py                  conexión SQLite y estado compartido
.claude/skills/          37 skills de terceros para Claude Code (ver su README)
.claude/settings.json    impide que Claude Code lea o edite .env (hay otro igual en la raíz del repositorio)
```
