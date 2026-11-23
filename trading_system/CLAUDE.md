# trading_system: guía de trabajo para Claude

Mesa de trading multi-agente con app local: seis bots analizan, un jefe determinista decide y ejecuta, y
`dashboard.py` lo enseña y lo controla. Opera cripto por CCXT y futuros de Topstep (`BROKER=topstepx`); las
acciones de EE. UU. solo en simulación, con precios de Yahoo (un símbolo sin "/" es una acción). La cuenta de
fondeo simulada (cripto y acciones, 50.000) se abre con `Iniciar cuenta de fondeo.bat`, que fija sus reglas y su
propia base de datos. El usuario no es programador y escribe en español: respóndele en español llano.

**Lee `ESTADO.md` antes de proponer o repetir nada.** Dice qué funciona, qué se probó ya (con sus cifras) y qué
falta, por orden. Actualízalo en el mismo commit que cierre cada tarea. `README.md` es el manual del usuario:
consulta solo la sección que toque (`grep -n '^#' README.md`).

## Reglas que no se rompen

- **Claves.** Nunca en código, logs, commits ni en el chat, y no las pidas por el chat. Viven en el `.env` del
  ordenador del usuario. No leas ni edites `.env` (lo bloquea `.claude/settings.json`, no lo rodees): las variables
  están en `.env.example`, y para probar otra configuración se ponen delante del comando, que manda sobre `.env`.
- **Fuera de git:** `.env`, `*.db`, `*.log`, `venv/` y `data/`.
- **Dinero real.** `PAPER_TRADING=True` es el valor por defecto. No lo cambies, ni `CONFIRM_LIVE`, ni añadas
  envío de órdenes a un broker nuevo (Alpaca hoy solo da datos) salvo que el usuario lo pida expresamente.
- **Riesgo determinista.** Ningún LLM decide tamaños, stops ni órdenes. Límites del usuario: stop ≤ 5 %, riesgo
  1 % por operación (techo 2 %), pérdida total 500 sobre 3.550.
- **TopstepX solo en el ordenador del usuario.** Nunca conectes a `api.topstepx.com` desde la nube ni propongas
  un VPS: aquí se prueba contra la API falsa de `selfcheck.py`. El kill switch (75 % del límite diario, techo
  80 %) y la verificación de stop y objetivo tras cada orden no se relajan.
- **Bloqueos que no se rodean.** Binance responde 451 desde la nube (restricción de ubicación): para datos usa
  Alpaca o Yahoo. No ejecutes código de repositorios externos sin revisarlo.
- **Resultados sin maquillar.** Todo PnL es neto de comisiones y deslizamiento. No prometas rentabilidad ni
  presentes como evidencia un resultado obtenido en los mismos datos con que se ajustó.
- **Git.** No abras un pull request si el usuario no lo pide. El contenedor de nube es efímero: commit y push al
  terminar cada paso.

## Comandos (dentro de `trading_system/`)

| Para | Comando |
|---|---|
| Crear el entorno en la nube (segundos) | `uv venv venv && uv pip install --python venv/bin/python -r requirements.txt` |
| Crearlo sin `uv` (minutos) | `python -m venv venv && venv/bin/pip install -r requirements.txt` |
| Comprobar todo (1 min, sin red ni claves) | `venv/bin/python selfcheck.py` → debe acabar en `selfcheck OK` |
| Comprobar una parte (3 s) | `venv/bin/python selfcheck.py optimizer app` |
| App | `venv/bin/python -m streamlit run dashboard.py` (en Windows, `Iniciar.bat`) |
| Motor sin app | `venv/bin/python main.py` (`--once` hace un solo ciclo) |
| Simular | `venv/bin/python backtest.py --days 30 [--evolution] [--lineage 2000 --flat] [--save f.json]` |

`selfcheck OK` es obligatorio antes de cada commit; la pasada parcial sirve para iterar y no lo sustituye.
Comprobaciones: `cache risk agents boss_decision lifecycle evolution tokens_and_alerts ccxt_broker macro optimizer
app orb backtest alpaca topstepx funded scenarios public`. Solo `backtest` es lenta (50 s).

## Mapa (salta con `grep -n "def nombre"`, no leas archivos enteros)

| Archivo | Qué hay | Puntos de entrada |
|---|---|---|
| `config.py` | toda la configuración (entorno y `.env`) y las negativas de seguridad al arrancar | `BROKER`, `STRATEGY`, `PROP_*`, `FUNDED`, `STOCK_*`, `STOCKS` |
| `main.py` | orquestador asyncio; `--child` es el motor que lanza la app | `run_agent`, `boss_loop`, `alive_loop`, `_main` |
| `agents/__init__.py` | `Signal`, el contrato de los bots, y `AGENT_LABELS` | |
| `agents/{smc,wyckoff,sentiment,historian,macro}_agent.py` | cinco de los seis bots; solo `macro` llama a Claude | `run`, `analyze` |
| `agents/scenario_agent.py` | el sexto: 10.000 escenarios por activo y vota por las 5 mejores apuestas (no va en el backtest) | `rank`, `simulate`, `outcomes` |
| `agents/orb_agent.py` | ruptura del rango de apertura (`STRATEGY=orb`) | `DEFAULT`, `GRID`, `genomes`, `analyze` |
| `agents/boss_agent.py` | decisión ponderada, límites, kill switch, ejecución y relevo de la cuenta de fondeo | `decide`, `tick`, `_maybe_trade`, `_fund`, `_floor`, `_relevo`, `_flatten` |
| `agents/evolution_manager.py` | muerte y renacimiento de bots; post-mortem con Claude | `on_trade_closed`, `_kill`, `_rebirth` |
| `optimizer.py` | recompensas en R, confianza del jefe en cada bot, ajustes de la app | `reward`, `new_weights`, `TUNABLE`, `apply_params`, `run` |
| `utils/risk_manager.py` | stop, tamaño, costes por activo, límites diarios, sesión de Topstep y de Nueva York | `build_plan`, `costs`, `is_stock`, `nyse_session`, `nyse_open_since`, `check_limits` |
| `utils/exchange.py` | datos (CCXT, Alpaca; acciones por Yahoo), `PaperBroker`, `CcxtBroker` | `make_exchange`, `yahoo_chart`, `Market.ohlcv` |
| `utils/topstepx.py` | cliente REST de ProjectX, datos y brokers de futuros | `TopstepClient`, `TopstepBroker.open`, `flatten_all` |
| `utils/trade_logger.py` | operaciones, genealogía y bitácora (`events`) en SQLite | `log_close`, `log_event`, `events` |
| `utils/db.py` | SQLite y el estado compartido `kv` | `get_state`, `set_state`, `del_state` |
| `utils/token_budget.py` | tope diario de tokens y auditoría de cada llamada a Claude | `llm_allowed`, `record_llm` |
| `backtest.py` | simulación vela a vela, cuenta fondeada por generaciones, examen fuera de muestra | `run`, `successor`, `fold`, `walk_forward`, `yahoo`, `export` |
| `scenario_exam.py` | examen fuera de muestra del bot de escenarios (cripto 2 años por CCXT, acciones 60 días por Yahoo) | `crypto_bets`, `stock_bets`, `chosen`, `report` |
| `utils/public_data.py` | datos públicos sin clave: SEC (resultados, formularios 4, tamaño), State Street, Reserva Federal, BLS, Yahoo | `universe`, `filings`, `earnings`, `insider`, `floats`, `daily`, `hourly`, `calendar` |
| `event_lab.py` | laboratorio de H1-H4 con datos públicos (protocolo en `ESTADO.md`): `download`, `train`, `exam`, `holdout` | `Panel`, `clean`, `pit_groups`, `h1`-`h4`, `stats`, `h4_stats` |
| `dashboard.py` | la app (Streamlit): una función por pestaña | `control_room`, `bots_room`, `boss_room`, `lab`, `settings` |
| `selfcheck.py` | la batería: una función `check_*` por área | |

Motor y app solo se hablan por la tabla `kv`. Órdenes de la app: `ctl:kill`, `ctl:mode`, `ctl:params`,
`ctl:bench`, `ctl:auto_opt`, `ctl:engine` ("stop"). Estado del motor: `engine` (latido `{pid, ts}`), `account`,
`fund` y `fund:history` (cuenta de fondeo simulada),
`scenarios:ranking`, `weights`, `boss:error`, `boss:{símbolo}`, `signal:{bot}:{símbolo}`, `status:{bot}:{símbolo}`, `approve:{símbolo}`.

## Cómo comprobar cada tipo de cambio

- **Lógica** (riesgo, jefe, bots, optimizador, brokers): añade o ajusta el `assert` en su `check_*`, pasa esa
  parcial y después la batería entera. Una comprobación solo vale si falla al romper la regla a propósito:
  rómpela una vez, mira que falla y deshaz el cambio.
- **App:** humo con `streamlit.testing.v1.AppTest.from_file(<ruta absoluta>)`; lo visual, con Playwright, que
  viene instalado en la nube (campos numéricos: `getByRole('spinbutton', {name})`). Para arrancarla en la nube:
  `EXCHANGE_ID=alpaca SYMBOLS=BTC/USD,ETH/USD QUOTE=USD FEE_PCT=0.25 BOSS_INTERVAL=60 SMC_INTERVAL=180
  TECH_INTERVAL=600` delante del comando (sin clave, Alpaca corta con 429 si se le pide más deprisa).
- **Estrategia:** el protocolo de abajo.
- **Lo que no se puede probar en la nube** (`Iniciar.bat` en Windows, Binance, TopstepX real, cuentas con claves): dilo
  tal cual en el informe, no lo des por bueno.

## Investigar estrategias sin engañarse

1. Mira en `ESTADO.md` si ya se probó. Lo probado no se repite con otros parámetros hasta que salga bien.
2. Escribe en `ESTADO.md` la hipótesis y el criterio de aprobado **antes** de mirar ningún resultado.
3. Ajusta en un periodo y examina en otro que no haya visto (`backtest.py --walk-forward ENTRENA,EXAMINA`). El
   único número que cuenta es el del examen, neto de costes.
4. Informa de todas las variantes probadas, no solo de la mejor. No reajustes sobre el examen: si falla, la
   hipótesis queda descartada.
5. Menos de 100 operaciones o menos de un año de examen no demuestran nada, a favor ni en contra: dilo así.
6. Anota el resultado en `ESTADO.md`, gane o pierda.

## Entorno de nube

- Efímero: `venv/`, `data/` y los archivos temporales se pierden con el contenedor. Lo que valga va al
  repositorio; las cifras de un experimento, a `ESTADO.md`.
- Datos sin clave: Yahoo para acciones (`EXCHANGE_ID=yahoo`: 730 días en velas de 1 h, 5 años en diarias, solo 60
  días en 5 min) y Alpaca para cripto (`EXCHANGE_ID=alpaca`, lento por el límite de peticiones).
- La salida a internet pasa por un proxy con su propia CA: `SSL_CERT_FILE` ya viene puesto y `make_exchange()` lo
  respeta con `include_OS_certificates`; no lo quites.
- Una simulación de un año tarda minutos: lánzala en segundo plano con la salida a un archivo y lee solo el final.

## Forma de trabajar

- Una tarea, una sesión: una conversación larga relee todo su historial en cada paso y agota el límite de uso.
- Trabaja en línea. Los subagentes (`/do`, `/make-plan`) y `/learn-codebase` rehacen o leen todo el contexto:
  úsalos solo si el usuario lo pide. El mapa de arriba sustituye a leer el proyecto.
- Regla del repositorio (`AGENTS.md`, en la raíz): el mínimo código que funcione, sin abstracciones ni
  dependencias que nadie pidió; las simplificaciones se marcan con `ponytail:` y dicen su techo.
- Informa con lo que hiciste, lo que comprobaste y lo que no pudiste comprobar.
- Skills de `.claude/skills/` que sirven aquí: `trader-entry` y `trader-exit` (de ahí salen las reglas de
  `orb_agent.py`), `session-metrics` (coste de la sesión) y `claude-docs-consultant`. Las de Strix necesitan su CLI.
