# Estado del proyecto

Actualizado el 16-11-2026. Quien cierre una tarea actualiza este archivo en el mismo commit.

## En una pantalla

- **Dónde está el código.** En la rama `claude/alpha-vantage-mcp-connection-xpwtx6`. La rama por defecto del
  repositorio (`bench/agentic-126`) no contiene `trading_system/`: una sesión nueva tiene que traer la primera
  (`git fetch origin <rama> && git merge --ff-only FETCH_HEAD`, probado en un clon nuevo el 15-11). No se ha
  pedido un pull request.
- **Qué funciona.** El motor (cinco bots, jefe, riesgo, selección natural), la app local, el simulador con cuenta
  fondeada y examen fuera de muestra, y el modo Topstep contra una API falsa. Desde el 16-11, además, la cuenta de
  fondeo simulada en vivo (`Iniciar cuenta de fondeo.bat`): 50.000, cripto y las siete acciones del usuario con
  precios de Yahoo, en largo y en corto, con relevo de cuenta. `selfcheck.py` pasa entero.
- **Qué dice la investigación.** Con precios reales y después de costes, nada de lo probado gana dinero en datos
  que no había visto. No hay ventaja demostrada. Los objetivos del usuario no se han alcanzado en ninguna
  simulación.
- **Recomendación dada al usuario.** No poner dinero ni pagar una evaluación de fondeo con este bot. Elegir entre
  parar o hacer una última prueba bien hecha ("acciones en juego" con datos de Alpaca) con el criterio de
  aprobado fijado de antemano. No ha contestado todavía.

## Lo que el usuario pidió y decidió

- **Objetivo.** 500 USD por semana o más sobre una cuenta de fondeo de 50.000 (son 26.000 al año, un 52 %), con
  el bot eliminado al perder 2.000 y relevado por otro que hereda lo aprendido. Después pidió "por lo menos entre
  40 % y 50 % rentable": no está claro si es rentabilidad anual o porcentaje de operaciones ganadoras.
- **Sus límites para dinero propio.** Capital 3.550, pérdida total 500, stop ≤ 5 %, riesgo 1 % (techo 2 %).
- **Sus acciones.** NVDA, META, GOOGL, TTWO, MU, MSFT, UBER.
- **Topstep.** Ahora mismo no tiene cuenta. El modo está hecho y sin probar contra el servicio real.
- **Claves.** Las de los archivos que subió al principio eran de prueba: se ignoran y no se publican. Las reales
  solo en su `.env`, nunca en GitHub ni en el chat.
- **App.** La quiere en su PC (Windows) para ver cada bot, darle recompensas, tener un jefe que los dirija y
  poder controlarlo y optimizarlo él, y que también se optimice solo. Está hecha.
- **Skills.** Las de `.claude/skills/` las entregó él para que se aprovechen; las reglas del bot de ruptura salen
  de `trader-entry` y `trader-exit`.
- **Cuenta de fondeo en vivo (16-11).** "este set de bots tambien debe operar con acciones, además de longs y
  short, todo esto como si estuviera en una cuenta de fondeo". Hecho como cuenta de fondeo simulada en vivo, con
  las mismas reglas que las simulaciones de abajo.
- **Bot de escenarios (18-11).** "quiero agregar un bot que haga la mayor cantidad de escenarios ficticios de la
  acción y elija los 5 mejores". Hecho como sexto bot: 10.000 escenarios por activo con el método de la skill
  `trader-timemachine` y vota solo por las 5 mejores apuestas (activo y lado) con resultado medio positivo. Se
  interpretó "los 5 mejores" como las 5 mejores apuestas para operar, no como los 5 escenarios más bonitos (eso no
  sirve para decidir); hay que confirmárselo. Corre en los nueve activos, no solo en las acciones.

Decisiones que se tomaron en su nombre en las simulaciones de cuenta fondeada, ya comunicadas: riesgo de 200 por operación
(0,4 %) en vez del 1 %, eliminación al caer 2.000 bajo el mejor cierre diario (el suelo deja de subir al llegar
al capital inicial), límite diario de 1.000 con cierre forzoso al 75 %, acciones en velas de 1 hora cerrando
todo al final de la sesión, y relevos corregidos con reglas fijas, sin Claude.

Decisiones tomadas en su nombre el 16-11 para la cuenta de fondeo en vivo, comunicadas al cerrar la tarea: cripto
(BTC y ETH de Binance) y sus siete acciones en la misma cuenta; 3 posiciones a la vez y apalancamiento máximo 2x
por posición; acciones con velas de 1 hora para SMC, Wyckoff y el stop, como en las simulaciones (con velas de 5
min perdían más), y cripto con las del motor; entradas en acciones desde que cierra la primera vela de 1 hora
(10:30) hasta media hora antes del cierre, todo cerrado 5 minutos antes del cierre de Nueva York, y lo que quede
abierto de un día anterior se cierra en la apertura siguiente; comisión de acciones 0,005 % y deslizamiento 0,02 %
por lado, acciones enteras, y el stop que salta con un hueco se ejecuta donde abrió la vela; el día operativo
(límite diario y mejor cierre) es el día UTC, como ya lo era en cripto; entra primero la señal más fuerte cuando no
hay hueco para todas; y no se abre nada cuyo stop, sumado al de lo abierto, pase de lo que queda hasta el kill
switch o el suelo (esto último no lo hace el backtest).

## Esperando respuesta del usuario

1. ¿Le abre la app con doble clic en `Iniciar.bat`? Claude lo ejecutó en su PC el 16-11 y funcionó (ver
   "Comprobado en Windows"); falta que él lo confirme con su propio doble clic.
2. ¿"40–50 %" es rentabilidad anual o porcentaje de operaciones ganadoras?
2b. ¿"Elija los 5 mejores" eran las 5 mejores apuestas para operar (así está hecho) y en todos los activos, o
   solo en las acciones?
3. ¿Parar, o hacer la prueba de "acciones en juego"? Criterio que se le propuso: dos años de exámenes fuera de
   muestra, resultado neto positivo, más de la mitad de los periodos en positivo y ninguna cuenta perdida; si no
   lo cumple, la idea se abandona sin más ajustes.
4. ¿Quiere fusionar la rama de trabajo en la rama por defecto?
5. Opcional, en su `.env`: una clave de Anthropic. Sin ella todo funciona con reglas fijas; con ella el bot Macro
   puntúa titulares y Claude diagnostica cada relevo (modelo en `CLAUDE_MODEL`, tope en `DAILY_TOKEN_BUDGET`).
6. ¿Sigue queriendo instalar en su PC los programas de los que dependen algunas skills (Strix, agent-reach, el
   plugin de claude-mem)? Lo pidió el 11-11 y desde la nube no se pudo; lo que necesita cada una está en
   `.claude/skills/README.md`.

## Pendientes, por orden

Los pendientes 1 y 5 solo se pueden comprobar en el ordenador del usuario; desde la nube fue imposible.

1. **La app en Windows.** Probada por Claude en su PC el 16-11 (abre, enciende el motor, se apaga al cerrar).
   Hecho cuando el usuario confirme que le abre con doble clic. Si falla, se arregla a partir del mensaje de
   error que copie.
1b. **La cuenta de fondeo simulada en vivo.** Dejarla correr. Con pocas semanas no dice nada a favor ni en contra
   (regla 5 de "Investigar estrategias"); lo honesto es compararla con lo ya simulado, que perdió dinero. Mezcla
   cripto y acciones en una cuenta y no tiene un backtest igual.
2. **Aclarar el objetivo** (respuesta 2) antes de tocar nada de estrategia.
3. **Prueba de "acciones en juego"**, solo si el usuario la elige y aporta claves de una cuenta paper de Alpaca.
   Falta un cargador de velas de acciones de Alpaca (5 minutos, dos años o más, universo amplio) y elegir cada
   día las de mayor volumen relativo; hoy Alpaca solo sirve cripto en `make_exchange()`. El criterio de la
   respuesta 3 se escribe aquí antes de lanzar nada. Las claves están solo en el PC del usuario: o la prueba se
   ejecuta allí, o él las guarda como secretos del entorno de nube.
4. ~~**Binance**~~: hecho el 16-11. `python main.py --once` termina un ciclo en el PC del usuario con datos
   reales de `binanceusdm` (desde la nube sigue respondiendo 451).
5. **TopstepX real**, cuando tenga cuenta: los pasos están en la sección "Topstep" del `README.md`. Sin verificar:
   los nombres de los campos de las respuestas, el signo de los ticks de stop y objetivo
   (`PROP_BRACKET_SIGNED=false` si la plataforma rechaza la orden) y la regla de los 5 ticks, que viene de una
   fuente de terceros. El suelo de la cuenta se actualiza a mano en `.env`. Precio y posiciones van por REST con
   hasta 5 s de retraso; no hay WebSocket.

No hacer sin que el usuario lo pida: enviar órdenes a Alpaca o a cualquier broker de acciones (las acciones solo se
simulan), añadir genes al bot de ruptura o repetir el año de simulación cambiando reglas hasta que salga bien. La
fuente de precios de acciones en vivo (Yahoo) la pidió el 16-11 y ya está.

## Ya probado: no repetir

Todo con precios reales y neto de comisiones y deslizamiento, entre el 11 y el 14 de noviembre de 2026.

**Motor de cuatro bots técnicos en cripto** (BTC y ETH de Alpaca, capital 3.550, 30 días)

| Prueba | Operaciones | Acierto | Neto |
|---|---|---|---|
| Costes por defecto (0,075 % + 0,02 % por lado) | 43 | 30 % | −513 |
| Comisión real de Alpaca cripto (0,25 % por lado) | 24 | 17 % | −529 |
| Con selección natural | 47 | 36 % | −509 |

En las tres saltó el límite de 500; sin él, −1.846. Comprar y mantener dio −9,7 % en BTC y −16,1 % en ETH.
Con 12 configuraciones (umbral, ancho del stop y objetivo), ajustadas en 30 días y validadas en los 30
siguientes, ninguna fue positiva en ninguno de los dos periodos. El bruto medio fue +96 y −57. La menos mala
(umbral 0,55, stop sobre velas de 1 hora, objetivo 3:1) perdió 238 y 160.

**Cuenta fondeada de 50.000 por generaciones** (`--lineage 2000`; riesgo 0,4 %, límite diario 1.000)

| Simulación | Operaciones | Neto | Bots eliminados | Media semanal | Semanas ≥ 500 |
|---|---|---|---|---|---|
| Acciones, 1 año, velas de 1 h, largos y cortos | 486 | −2.857 | 3 de 4 | −54 | 7 de 53 |
| Acciones, 1 año, solo compras | 184 | −2.644 | 1 de 2 | −50 | 3 de 53 |
| Acciones, 1 año, sin aprendizaje | 627 | −8.863 | 6 de 7 | −167 | 5 de 53 |
| Acciones, 50 días, velas de 5 min | 161 | −5.536 | 4 de 5 | −692 | 0 de 8 |
| Cripto, 1 año, largos y cortos | 223 | −10.722 | 6 de 7 | −202 | 2 de 53 |
| Cripto, 1 año, solo compras | 96 | −8.866 | 4 de 5 | −167 | 2 de 53 |
| Cripto, 1 año, sin aprendizaje | 2.871 | −223.656 | 113 de 114 | −4.220 | 0 de 53 |

Acciones: las siete del usuario. En acciones el bruto de todo el año fue +148. Otra simulación de un año con
esas acciones, lanzada el 14-11 desde el Laboratorio de la app, dio −3.637 (−7,3 %) con 453 operaciones, 46 %
ganadoras y 4 cuentas perdidas; los cuatro bots acabaron con recompensas negativas (−3,6, −5,0, −14,3 y −11,5 R).

**Ruptura del rango de apertura con examen fuera de muestra** (`STRATEGY=orb`, entrenar 180 días y examinar 60)

| Prueba | Entrenamiento | Examen | Exámenes en positivo | Cuentas perdidas |
|---|---|---|---|---|
| 7 acciones, velas de 1 h, 14 exámenes (28 meses) | +5.635 | −1.738 | 1 de 14 | 0 |
| Lo mismo con los genes de "acciones en juego", 31 acciones | +16.628 | −10.330 | 2 de 14 | 4 |

En la primera, 9 de 14 exámenes quedaron sin operar porque ningún genoma sobrevivió ganando, y hubo 2 semanas
de 126 con 500 o más. Sin entrenar (el genoma de la skill siempre) perdió 8.620. Entrenando 365 días y
examinando 90, ningún genoma sobrevivió ganando en ninguno de los 7 exámenes y no se operó. Con velas de 5
minutos Yahoo solo da 60 días: una pasada dio +1.140 con 37 operaciones y otra (entrenar 30 días, examinar 10)
−6 con 77. Con tan pocas operaciones no demuestran nada.

**"Acciones en juego" con reglas fijas** (Zarattini, Barbon y Aziz, 2024; 93 acciones, 50 días, velas de 5 min)

| Variante | Operaciones | Bruto | Neto | Cuentas perdidas |
|---|---|---|---|---|
| A: volumen relativo ≥ 1x, rango de 5 min, sentido de la vela, stop 10 % del ATR diario, hasta el cierre | 268 | +1.370 | −5.548 | 4 |
| D: como A con volumen relativo ≥ 2x | 139 | −94 | −3.685 | 3 |
| B: como A sin filtro de volumen | 318 | +201 | −8.034 | 5 |
| C: genoma de la skill (15 min, punto medio, 2R, solo largos) | 197 | −5.068 | −7.149 | 4 |

En A los largos perdieron 7.738 y los cortos ganaron 2.190 (el mercado bajaba), y la primera mitad ganó 2.674
frente a −8.222 de la segunda. Al verificar la receta de abajo el 15-11 con solo tres acciones (NVDA, TSLA,
COIN) salió +2.565 en 36 operaciones: es justo la muestra pequeña y elegida a mano de la que no hay que fiarse.

## Lo que enseñan esos resultados

- **Antes de costes el resultado está en torno a cero.** Las señales aciertan como una moneda. No es un problema
  de infraestructura ni de entrenar más.
- **Los costes deciden.** Con un stop ajustado la posición es grande: en A, 52.600 de media para arriesgar 180, y
  cada operación paga unos 26 (0,14 R) entre comisión y deslizamiento.
- **El entrenamiento siempre encuentra ganadoras y en el examen pierden.** Con más genes donde elegir, la
  diferencia creció: más opciones es más memorización.
- **Las generaciones reducen pérdidas porque enseñan a operar menos**, no porque creen ganancias: el último bot
  de cripto hizo 9 operaciones en tres meses y medio.
- **Fuera de aquí la evidencia apunta igual.** La mayoría de los day traders pierde (en Taiwán más de ocho de
  cada diez en un semestre típico; en Brasil el 97 % de los que persistieron). El estudio de acciones en juego
  declara un 41,6 % anual entre 2016 y 2023 según resúmenes; no se leyó completo ni se pudo reproducir, porque
  elige cada día las 20 más activas entre unas 7.000 acciones y aquí solo había 93 grandes durante 50 días.

## Qué está comprobado y qué no

Comprobado en Linux, en la nube:

- `selfcheck.py` entero. Se rompieron a propósito 12 reglas del modo Topstep y 15 de la app y las recompensas, y
  la batería detectó todas.
- En la app, con el navegador: encender y apagar el motor, que se apaga solo al cerrar la app, el kill switch
  (cerró una venta simulada de BTC abierta con precios reales), banquillo, ajustes, modo manual, "Optimizar
  ahora", la auto-optimización y una simulación de un año lanzada desde el Laboratorio.
- Los datos reales de Alpaca (cripto, sin clave) y de Yahoo (acciones).
- Las dos llamadas a Claude (bot Macro y diagnóstico de cada relevo), revisadas el 15-11 contra el SDK
  instalado (`anthropic` 2.8): salida estructurada con `messages.parse`, esfuerzo bajo, sin parámetros
  retirados, y una negativa o una respuesta cortada acaban en voto neutral o en la regla fija. Solo se han
  ejecutado contra un cliente falso.

Comprobado en Windows, en el PC del usuario (16-11, sesión de Claude Code de escritorio, Python 3.14):

- `selfcheck.py` entero.
- `Iniciar.bat` ejecutado con su ruta completa, como un doble clic: la app abre en `localhost:8501` y lee la
  base de datos que ya había, el motor se enciende desde la app con datos reales de Binance, y al cerrar el
  lanzador se apagan la app y el motor.
- `python main.py --once` contra `binanceusdm`: un ciclo completo con los cinco bots y la decisión del jefe.
- Arreglado al probar: `Iniciar.bat` esperaba con `timeout`, que fallaba si el `timeout` de Git iba antes en el
  PATH o sin consola; ahora espera con `ping`. `main.py --once` con la salida a un archivo se caía en Windows
  (cp1252 no tiene la "σ" del Historiador); ahora escribe en UTF-8. La prueba de caché de `selfcheck.py`
  fallaba 1 de cada 6 veces en Windows por un plazo de 50 ms; ahora usa 0,5 s.
- Sin probar con un doble clic del propio usuario.
- Cuenta de fondeo simulada (16-11, con la bolsa abierta): `Iniciar cuenta de fondeo.bat` abre la app en
  `localhost:8502`, el motor se enciende desde ella, Yahoo sirve velas de 1 min a 1 día de las siete acciones en
  menos de medio segundo por petición, los cinco bots dan señal en los nueve activos sin errores y el jefe operó
  acciones en corto y en largo con 200 de riesgo cada una. `selfcheck.py funded` cubre horario de Nueva York
  (festivos, cierre temprano, cambio de hora), costes y acciones enteras, velas de 1 h, datos de Yahoo sin red y
  sus fallos, orden por fuerza, cierre antes del cierre y en la apertura siguiente, fallo de precio aislado,
  suelo, margen, kill switch, cuenta perdida aunque rebote y relevo; se rompieron a propósito 28 de sus reglas y
  la comprobación detectó todas. La app se probó con `AppTest` en modo fondeo, sin errores.
- Un incidente real que llevó a dos arreglos: el motor murió con la sesión de Claude que lo había lanzado y tres
  acciones quedaron sin vigilar unas 3 horas. Al volver, el motor solo miraba los últimos 10 minutos de precios;
  ahora revisa desde la apertura de cada operación, y al reiniciarlo encontró un objetivo (GOOGL, +365) y un stop
  (MSFT, −200) tocados durante el apagón. Ahora se lanza como un doble clic, independiente de la sesión.
- Dos rondas de revisión con críticos independientes (diseño y código); lo que encontraron está arreglado o
  anotado como límite (`ponytail:` y "Lo que no modela" en el `README.md`). De la segunda ronda solo terminó el
  ángulo de riesgo (el límite de uso cortó los de datos y sesiones, regresiones y pruebas); sus 6 hallazgos están
  arreglados: el margen cuenta lo abierto desde su precio y con costes, la primera cuenta nace antes de leer el
  saldo, el hueco de una acción se ve también por el precio de ahora, los stops se revisan con velas más gruesas
  tras apagones de más de 16 h, el Laboratorio de la app de fondeo vuelve a simular sin reglas de fondeo, y con la
  bolsa cerrada ningún cierre de emergencia toca las acciones (se cierran en la apertura).
- Motor de la cuenta de fondeo en marcha del 16 al 18-11 con los arreglos urgentes: 23 operaciones cerradas,
  +693 netos, cuenta 1 viva con el suelo en 49.131; cerró las acciones a las 15:55 cada día y siguió con el
  último precio cuando Yahoo no respondió. Es poquísima historia: no dice nada de la estrategia.
- Bot de escenarios (18-11): `selfcheck.py scenarios` cubre el lado según la tendencia, nada sin ventaja tras
  costes, solo las 5 mejores y en orden, mismos escenarios con los mismos datos, la cuenta de cada escenario, la
  misma hora del día y solo sesiones completas en acciones, bolsa cerrada y el fallo de un activo; se rompieron
  10 de sus reglas a propósito y la comprobación detectó todas. Con datos reales tarda unos 3 s para los nueve
  activos; el 18-11 por la tarde, con la bolsa cerrada, ni BTC ni ETH tenían un lado positivo y no votó por nada.

Sin comprobar:

- Binance con claves, TopstepX real y cualquier ruta con claves: órdenes en un exchange, la API de Claude, Telegram,
  Discord y los RSS. La clave gratuita de Alpha Vantage no incluye noticias.

## Cómo se hicieron las pruebas

`data/` no está en git y se pierde con el contenedor: hay que volver a descargar. Yahoo se guarda por día en
`data/`; Alpaca sin clave es lento por su límite de peticiones. Las cifras cambian con la ventana de datos (Yahoo
solo sirve los últimos 730 días en velas de 1 hora) y con la versión del código. Desde el 16-11 SMC y Wyckoff
usan `STOCK_TIMEFRAME` (1 h por defecto) en las acciones: para repetir la simulación de acciones con velas de 5
minutos hay que añadir `STOCK_TIMEFRAME=5m`.

Cuenta fondeada con las siete acciones, desde `trading_system/`:

```bash
EXCHANGE_ID=yahoo SYMBOLS=NVDA,META,GOOGL,TTWO,MU,MSFT,UBER QUOTE=USD CAPITAL=50000 RISK_PER_TRADE_PCT=0.4 \
MAX_LEVERAGE=2 FEE_PCT=0.005 TOTAL_LOSS_LIMIT_USD=0 PROP_DAILY_LOSS_LIMIT_USD=1000 SMC_TIMEFRAME=1h \
ATR_TIMEFRAME=1h WYCKOFF_TIMEFRAME=1h venv/bin/python backtest.py --days 365 --evolution --lineage 2000 --flat
```

Para cripto: `EXCHANGE_ID=alpaca SYMBOLS=BTC/USD,ETH/USD MAX_LEVERAGE=3 FEE_PCT=0.075`, los marcos de tiempo por
defecto (velas de 5 minutos) y sin `--flat`; la variante de solo compras usó `MAX_LEVERAGE=1 FEE_PCT=0.25`.
"Solo compras" y "sin aprendizaje" no están en la línea de comandos: se llaman desde Python con
`lineage={"max_loss": 2000, "flat": True, "learn": False, "blocked": [(símbolo, "SHORT"), ...]}`.

El examen fuera de muestra de la ruptura está en el `README.md` ("Ruptura del rango de apertura"). Los genes
fijos de "acciones en juego" se simulan así, con `STRATEGY=orb EXCHANGE_ID=yahoo MAX_LEVERAGE=1.3
MAX_OPEN_POSITIONS=3 COOLDOWN_S=0 SMC_TIMEFRAME=5m ATR_TIMEFRAME=1d ATR_SL_MULT=0.1 PYTHONPATH=.`, las variables
de cuenta de arriba y `SYMBOLS` con el universo:

```python
import asyncio, backtest, config
from agents import orb_agent
data = {s: backtest.yahoo(s, config.ROOT / "data" / "universe") for s in config.SYMBOLS}
genes = orb_agent.DEFAULT | {"or_min": 5, "stop": "atr", "rvol": 1.0, "sides": "both", "dir": "candle", "rr": 10.0}
r = asyncio.run(backtest.run(data, 50, lineage={"max_loss": 2000, "flat": True, "learn": False}, genes=genes))
print(r["lineage"]["net"], len(r["lineage"]["generations"]) - 1, r["metrics"])
```

Universo de 93 acciones: AAPL MSFT NVDA AMZN GOOGL META TSLA AMD AVGO NFLX CRM ORCL ADBE INTC MU QCOM CSCO IBM
UBER ABNB SHOP PYPL COIN PLTR SNOW CRWD PANW NET DDOG ZS MDB SNAP PINS RBLX U DKNG HOOD SOFI AFRM UPST RIVN LCID
NIO F GM BA CAT DE GE JPM BAC C WFC GS MS V MA AXP DIS CMCSA T VZ WMT TGT COST HD LOW NKE SBUX MCD KO PEP PG JNJ
PFE MRNA LLY UNH ABBV MRK XOM CVX OXY SLB BABA PDD JD TTWO MARA RIOT SMCI ARM DELL.

## Historia: qué se pidió y con qué commit se entregó

Todo salió de una sola conversación de nube, entre septiembre y noviembre de 2026. Los textos entre comillas son
del usuario.

1. **Antes de noviembre, fuera de este repositorio.** Un resumen diario de bolsa por correo, que sigue
   funcionando en aquella conversación, y un bot anterior por webhook (Flask, con Alpaca y Binance) que pidió
   abrir como `.exe` con consola. Se le entregó como archivos sueltos; nada de eso está aquí.
2. **10-11, sistema multi-agente.** Cinco agentes que devuelven JSON estricto, un Agente Jefe que pondera según
   los aciertos de cada uno y aplica el riesgo (stop por ATR o estructura, límite diario, 1–2 % por operación),
   ejecución por CCXT en sandbox, panel en Streamlit con kill switch y modo automático o manual, caché, bucles
   asíncronos, registro de operaciones con el voto de cada agente y optimizador de pesos.
3. **10-11, "selección natural".** El agente que falla, o cuya recomendación acaba en stop o en pérdida neta,
   muere y nace su versión siguiente con una regla para no repetirlo. Genealogía en SQLite que se recarga al
   reiniciar, `PAPER_TRADING=True`, comisiones y deslizamiento descontados antes de juzgar al bot, mensajes entre
   agentes de menos de 150 tokens con auditoría, y avisos por Telegram o Discord.
4. **10 y 11-11, skills.** Copiar las de siete repositorios a `.claude/skills/` y aprovecharlas. Después, "hazlo
   ahora todo, ya instale claude code directamente en mi ordenador": instalar en su PC los programas de los que
   dependen, cosa que desde la nube no se pudo hacer.

   Los puntos 2 a 4 son los commits `1fd014f` a `45c37d8`.
5. **11-11, TopstepX** (`71b44b0`). Futuros de fondeo por la API de ProjectX: ejecución solo local, kill switch al
   75–80 % del límite diario, prevención de alta frecuencia y registro de todo en `trading.log`.
6. **11-11, Alpaca** (`795b170`). "ahora no tengo la cuenta, usa la cuenta de alpaca y la otra para empezar a
   probar que tal sirve el bot": datos de Alpaca para simular, sin envío de órdenes.
7. **12-11, un año de cuenta fondeada** (`5001f94`). 50.000, bot eliminado al perder 2.000 y relevado por otro que
   hereda lo aprendido y corrige el error, objetivo de 500 por semana; también "con acciones reales, como se haria
   normalmente en una cuenta de fondeo".
8. **12-11, ruptura del rango de apertura** (`d883dd1` y `8bd1d36`). Entrenar en un periodo y examinar en otro que
   no haya visto. Después, "que el bot estudie traders rentables de la ultima decada, estrategias, que pueda
   anticipar cosas debido a noticias": los genes de "acciones en juego".
9. **12-11, "Entonces que debería hacer".** La recomendación que abre este archivo.
10. **14-11, la plataforma** (`b8a46fe`). "una plataforma más amigable en la que se pueda ver cada uno [...] como
    se comporta, que se les de recompensas cada vez que hagan algo bueno, que aqui haya un bot encargado de los
    demás, que sea el jefe, y que yo pueda controlarlo, optimizarlo, y que solo tambien lo haga [...] la especie de
    app para abrirla en mi pc". Es la app de `dashboard.py`, que en Windows se abre con `Iniciar.bat`.
11. **15-11, traspaso** (`cde97e1` y `f0c7f09`). La guía de trabajo, este archivo, las pruebas parciales y el
    bloqueo de `.env`, para que otra sesión pueda continuar.
12. **15 y 16-11, en su PC (Claude Code de escritorio).** En paralelo, sin saber de la app de `b8a46fe`, otra
    sesión hizo una plataforma distinta ("haz entonces la plataforma en la que pueda ver los bots [...] que no
    tenga que abrir nada de codigo"). Se descartó en favor de esta y quedó solo en la rama local
    `respaldo/plataforma-local` (`f9ebbfb`, sin subir). Lo único que tenía y esta no: editor del calendario de
    eventos, descarga de operaciones en CSV, botón de `selfcheck` y registro del motor en pantalla. Después, las
    pruebas de Windows y Binance de "Comprobado en Windows".
13. **16-11, cuenta de fondeo en vivo con acciones.** "ahora ejecuta todo lo que debas, recuerda que este set de
    bots tambien debe operar con acciones, además de longs y short, todo esto como si estuviera en una cuenta de
    fondeo". Acciones en simulación con precios de Yahoo junto a cripto, horario de Nueva York, costes por activo,
    reglas y relevo de cuenta de fondeo en el motor en vivo, `Iniciar cuenta de fondeo.bat` y su panel en la app.
14. **18-11, bot de escenarios.** "quiero agregar un bot que haga la mayor cantidad de escenarios ficticios de la
    acción y elija los 5 mejores". El sexto bot, `agents/scenario_agent.py`, y su tabla en la Sala de control.
