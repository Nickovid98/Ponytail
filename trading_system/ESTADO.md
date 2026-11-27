# Estado del proyecto

Actualizado el 27-11-2026. Quien cierre una tarea actualiza este archivo en el mismo commit.

## En una pantalla

- **Dónde está el código.** En la rama `claude/alpha-vantage-mcp-connection-xpwtx6`. La rama por defecto del
  repositorio (`bench/agentic-126`) no contiene `trading_system/`: una sesión nueva tiene que traer la primera
  (`git fetch origin <rama> && git merge --ff-only FETCH_HEAD`, probado en un clon nuevo el 15-11). No se ha
  pedido un pull request.
- **Qué funciona.** El motor (cinco bots, jefe, riesgo, selección natural), la app local, el simulador con cuenta
  fondeada y examen fuera de muestra, y el modo Topstep contra una API falsa. Desde el 16-11, además, la cuenta de
  fondeo simulada en vivo (`Iniciar cuenta de fondeo.bat`): 50.000, cripto y las siete acciones del usuario con
  precios de Yahoo, en largo y en corto, con relevo de cuenta. Desde el 27-11 opera también EUR/USD, GBP/USD y
  JPY/USD, y la app enseña lo que declaran los políticos y los 13F de 26 grandes inversores (pestaña "Políticos y
  fondos"). `selfcheck.py` pasa entero.
- **Qué dice la investigación.** Con precios reales y después de costes, nada de lo probado gana dinero en datos
  que no había visto. No hay ventaja demostrada. Los objetivos del usuario no se han alcanzado en ninguna
  simulación. Tampoco con datos oficiales de la SEC (resultados, compras de directivos y tamaño de cada empresa en
  cada fecha): el 23 y el 24-11 se examinaron 7 ideas publicadas en dos rondas y ninguna pasó, y el bot de
  escenarios suspendió el 19-11. El 27-11 suspendieron también copiar a los políticos y a los grandes inversores
  (tercera ronda), los bots en divisas (pierden ya antes de costes) y un relevo que elige la corrección con pruebas
  (perdió más en cripto; se quitó).
- **Activado el 23-11 (simulación).** El bot Macro veta entradas 30 minutos antes y después de cada anuncio de la
  Reserva Federal y de cada IPC y dato de empleo de la BLS. Se encendió el motor de la cuenta de fondeo.
- **Recomendación dada al usuario.** No poner dinero ni pagar una evaluación de fondeo con este bot. Elegir entre
  parar o hacer una última prueba bien hecha ("acciones en juego" con datos de Alpaca) con el criterio de
  aprobado fijado de antemano. No ha contestado todavía.

## Lo que el usuario pidió y decidió

- **Objetivo.** 500 USD por semana o más sobre una cuenta de fondeo de 50.000 (son 26.000 al año, un 52 %), con
  el bot eliminado al perder 2.000 y relevado por otro que hereda lo aprendido. Después pidió "por lo menos entre
  40 % y 50 % rentable": no está claro si es rentabilidad anual o porcentaje de operaciones ganadoras.
  El 25-11 lo precisó: "la idea es poder retirar diariamente dinero, pagar la cuenta y todo sus requerimentos y
  retirar dinero por lo menos una semana, intentar retirar minimo entre 500 a 1000 dolares semanales, no importa que
  la cuenta muera en una semana". Es decir: pagar una evaluación, pasarla y cobrar 500–1.000 en una o dos semanas.
  Las cifras de ese plan están en "Cobrar rápido en una cuenta de fondeo".
  El 26-11 pidió que, al perder la cuenta, nazcan bots nuevos que aprendan de los errores de los anteriores y
  optimicen el sistema; poder ver lo que invierten los políticos de EE. UU., millonarios y fondos, y saber si
  copiarlos es buena idea; y operar también el euro contra el dólar y otras divisas. Lo hecho y sus cifras, en
  "Relevo con pruebas, divisas y copiar a políticos y fondos".
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
7. ¿Mantener EUR/USD, GBP/USD y JPY/USD en la cuenta de fondeo simulada? Las pidió el 26-11 y en la prueba del 27-11
   pierden ya antes de costes. Siguen activas hasta que diga otra cosa.

## Examen del bot de escenarios (19-11, escrito antes de ver ningún resultado)

El usuario pidió "prepara el paso 3": examinar el bot 6 con al menos un año y 100 operaciones antes de fiarse.

- **Hipótesis.** Las apuestas que el bot 6 elige (el mejor lado de cada activo, solo si su media en los 10.000
  escenarios es positiva, las 5 mejores) ganan dinero de verdad después de costes.
- **Por qué no hace falta entrenar.** El bot no tiene nada que ajustar: cada decisión usa solo velas anteriores a
  ese momento, así que todo el periodo es examen. No se cambia ningún parámetro del bot (R:R 2, stop 1,5 ATR,
  horizonte 6 h en cripto y hasta 5 min antes del cierre en acciones). La única diferencia es que se usan 2.000
  escenarios en vez de 10.000, para que tarde minutos y no horas; la media cambia unas centésimas de R.
- **Cómo.** `scenario_exam.py`. Cripto: BTC y ETH de `binanceusdm` en velas de 5 min, dos años, una decisión cada
  6 h (00, 06, 12 y 18 UTC). La operación que da el resultado se cierra con las mismas reglas sobre las velas que
  vinieron después. Acciones: las siete del usuario, una decisión al día a las 10:30 de Nueva York, con los 60
  días de velas de 5 min que da Yahoo. Eso no llega a un año y no cuenta para aprobar.
- **Criterio de aprobado (cripto, dos años).** Las cuatro a la vez:
  1. al menos 100 operaciones;
  2. resultado medio por operación positivo, neto de costes;
  3. más de la mitad de los trimestres en positivo;
  4. ninguna caída de 10 R o más desde el máximo, que con 200 por R son los 2.000 que hacen perder la cuenta
     de fondeo.

  Si falla una sola, el bot queda descartado como fuente de ventaja y no se reajusta.
- **Resultado (19-11): SUSPENDE.** Solo cumple la regla 1. El bot queda descartado como fuente de ventaja. Sigue en la
  app como un bot más, sin cambios, y no se reajusta.

  | | Operaciones | Acierto | Media por operación | Total | Peor caída | Trimestres en positivo |
  |---|---|---|---|---|---|---|
  | Cripto, 2 años (BTC y ETH, `binanceusdm`) | 916 | 36 % | −0,17 R (el bot esperaba +0,14) | −156 R = −31.119 | 158 R | 0 de 9 |
  | Acciones, 60 días (Yahoo, no cuenta) | 193 | 47 % | +0,02 R (esperaba +0,08) | +3,4 R = +687 | 8,4 R | 1 de 2 |

  - En cripto, el lado contrario de las mismas apuestas también pierde (−0,20 R): los escenarios no aciertan el
    lado, y los costes se lo comen todo.
  - La media de todas las apuestas posibles fue −0,30 R, así que elegir las 5 mejores mejora algo frente a operar
    todo, pero sigue perdiendo.
  - Las acciones, con 39 días y 193 operaciones, no demuestran nada (regla 5).
  - Comprobación nueva en `selfcheck.py scenarios`, punto 4: si se tocan las velas futuras, lo que el bot
    esperaba no cambia. Se rompió dos veces a propósito (ver la vela de la decisión, y un ATR con la vela sin
    cerrar) y falló las dos.
  - Tres revisores independientes buscaron trampas en el script y no encontraron ninguna confirmada.

## Estrategias con datos públicos oficiales (23-11, escrito antes de ver ningún resultado)

El usuario pidió: "activa todo, haz pruebas hasta obtener éxito, agrega las funciones que sean necesarias, usa
datos que están de acceso público de los gobiernos y las empresas en sus propias páginas".

- **Qué es éxito y qué no.** "Hasta obtener éxito" no puede ser retocar una idea hasta que el pasado salga bonito:
  eso es memorizar y en el futuro pierde (ver "Lo que enseñan esos resultados"). Éxito es una hipótesis, escrita
  aquí antes de mirar, que gana neto de costes en años que no se usaron para elegirla y lo confirma después en
  un periodo reservado que solo se mira una vez. Si ninguna lo consigue, se dice. Todo sigue en simulación: nada
  de esto activa dinero real.
- **Datos, todos públicos y sin clave.**
  - SEC (EDGAR):
    - fecha y hora de cada comunicado de resultados, que es el 8-K con el punto 2.02;
    - compras de los propios directivos con su dinero, del formulario 4 en los conjuntos trimestrales de la SEC.
  - State Street (SSGA): la lista de acciones del S&P 500, 400 y 600.
  - Yahoo: precios diarios desde 2010, ajustados por splits y dividendos, y velas de 1 hora de los últimos 730
    días.
- **Sesgo conocido.** La lista de acciones es la de hoy, así que faltan las empresas que quebraron o salieron
  del índice. Eso infla los resultados de comprar. Para no engañarse, cada operación se mide contra la media de
  su mismo grupo (500, 400 o 600) en los mismos días. Ese grupo tiene el mismo sesgo y se cancela en gran parte.
- **Costes por lado:** comisión 0,005 % más deslizamiento de 0,02 % (S&P 500), 0,05 % (400) o 0,10 % (600).
  La cobertura con el índice cuesta 0,01 % por lado. No se cobra el préstamo de acciones en cortos (es pequeño
  a estos plazos; queda anotado).
- **Periodos (H1 a H3, diarias):**
  - entrenamiento 2010–2017, que solo sirve para elegir la variante;
  - examen 2018–2022;
  - reservado del 01-01-2023 a hoy, que se mira una sola vez y solo si pasa el examen.
- **Hipótesis.** Todas se basan en estudios publicados, no salen de buscar en los datos. En ninguna se entra
  antes del cierre del día siguiente a que el dato sea público.
  - **H1, compras de directivos.** Sostén: Lakonishok y Lee 2001; Cohen, Malloy y Pomorski 2012.
    - Se compra la acción cuando sus consejeros o directivos la compran en bolsa (código P). La operación se mide
      contra su grupo.
    - Variantes: cualquier compra o al menos 2 directivos distintos en 30 días; cualquier importe o al menos
      100.000 USD en 30 días; mantener 5, 21 o 63 sesiones. Son 12 variantes.
  - **H2, deriva tras los resultados.** Sostén: Chan, Jegadeesh y Lakonishok 1996; Brandt y otros 2008.
    - La reacción es la diferencia frente a su grupo desde el cierre anterior al comunicado hasta el cierre del
      día siguiente.
    - Se compra si esa reacción está en el 10 % (o 20 %) más alto de las del último año, y se vende en corto si
      está en el más bajo.
    - Se mantiene 5, 21 o 63 sesiones. Son 6 variantes.
  - **H3, prima de los días de resultados.** Sostén: Frazzini y Lamont 2007; Barber y otros 2013.
    - Fecha esperada = la del mismo trimestre del año anterior + 364 días. No se usa la fecha real, que todavía
      no se conoce.
    - Se compra 3, 5 o 10 sesiones antes y se vende al cierre del día siguiente a la fecha esperada. Son 3
      variantes.
  - **H4, hueco de resultados en el día (velas de 1 h, últimos 730 días; la única que encaja con la cuenta de
    fondeo, que cierra todo cada día).**
    - Condiciones: el día del primer precio tras el comunicado, la acción abre al menos un 3 % por encima (o
      por debajo) del cierre anterior y la primera hora cierra en ese mismo sentido.
    - Entrada a las 10:30 en ese sentido. Stop al otro extremo de la primera hora y objetivo a 2R. Si la vela
      toca los dos, cuenta el stop. Si no toca ninguno, se sale al cierre.
    - 200 de riesgo, como mucho 3 al día (los huecos más grandes) y las reglas de la cuenta de fondeo.
    - Sin variantes: todo el periodo es examen.
- **Cómo se elige y qué aprueba.**
  - **Elección (H1 a H3).** En el entrenamiento se elige la variante con más t-estadístico. Se calcula sobre la
    rentabilidad mensual de la cartera de todas las posiciones abiertas, neta de costes, y la variante necesita
    al menos 100 operaciones. Si la mejor no llega a t = 2, la hipótesis se descarta sin examen.
  - **Examen (H1 a H3).** Exige las tres cosas: al menos 100 operaciones, t ≥ 2,5 y al menos 3 de los 5 años en
    positivo.
  - **Periodo reservado (H1 a H3).** Exige al menos 100 operaciones, resultado positivo y t ≥ 1,65.
  - **H4.** Exige al menos 100 operaciones y t ≥ 2,5 sobre el resultado diario. Además, cada uno de sus dos años
    tiene que salir positivo por separado y no puede perder la cuenta de fondeo simulada.
  - **Por qué 2,5 y no 2.** Son 4 hipótesis a la vez: con 2 saldría una ganadora por pura suerte demasiadas
    veces (corrección de Bonferroni).
  - **Si todas fallan.** Cualquier ronda nueva se escribe aquí antes, con un umbral más alto que cuente las
    anteriores (t ≥ 2,8 si se llega a 8 hipótesis).
- **Enmienda del 23-11, antes de ver ningún resultado.**
  - **Qué la motiva.** Una revisión independiente del laboratorio (4 revisores y verificadores que intentaron
    refutar cada hallazgo) confirmó 6 fallos. La medida cambia así; las hipótesis, las variantes y los
    criterios siguen igual.
  - **1. Tamaño en cada fecha.** La frase de arriba ("se cancela en gran parte") era falsa para H1: con la lista
    de hoy entran empresas que entonces eran pequeñas y luego crecieron. En una simulación sin ninguna
    información, eso dio un t falso de +1,2 a +3,5. Ahora:
    - Cada mes se ordenan todas las empresas por su valor en bolsa en manos del público (`EntityPublicFloat`,
      SEC). Se usa el dato de cada empresa de los últimos 15 meses, incluidas las que después quebraron.
    - Puestos 1–500 = grupo 500, 501–900 = 400, 901–1500 = 600.
    - Una acción solo se opera si estaba en uno de esos grupos al entrar. Se mide contra la media de las
      acciones que estaban en ese mismo grupo ese día.
    - Salir del grupo durante la operación no la anula, porque eso quitaría a las perdedoras. Las empresas sin
      dato de tamaño quedan fuera, lo que pasa sobre todo con las pequeñas en 2010.
    - Queda el sesgo de las empresas que eran grandes y ya no cotizan (en la simulación, de +0,1 a +1 % al año).
  - **2. Filtro de datos.** Ya no se borra cualquier día de más del 75 %: eso también borraba días reales como
    la subida de GME en 2021. Solo se borran los errores demostrables:
    - días en que el precio ajustado y el cierre se separan más de 10 puntos (ajuste roto de Yahoo en escisiones);
    - precios por debajo de 1 USD;
    - subidas o bajadas de más del 75 % que se deshacen en 3 sesiones; se borran la ida y la vuelta.
  - **3. H2, comunicados tras el cierre.** La reacción va del cierre anterior al comunicado al cierre del día
    siguiente, como estaba escrito. Antes se medía un día de más.
  - **4. Comunicados de antes de junio de 2009.** No hay precio anterior, así que se descartan. Además, los
    precios empiezan ahora en junio de 2008.
  - **5. H4 y la cuenta de fondeo, vigiladas en cada vela de 1 hora.** La cuenta se pierde si el saldo, contando
    lo flotante, toca el suelo. A 750 de pérdida en el día se cierra todo y no se abre nada más. El coste
    entra dentro de los 200 de riesgo, como en el motor.
  - **6. Windows.** El archivo de CON no se podía leer (nombre reservado).
- **Qué se activa pase lo que pase (en simulación).**
  - El motor de la cuenta de fondeo.
  - El calendario del bot Macro: no se abren operaciones nuevas cerca de anuncios de la Reserva Federal y de los
    datos de la oficina de estadísticas laborales (IPC, empleo).
  - Lo que apruebe, convertido en bot.
- **Resultado (23-11): las cuatro suspenden.** Código en el commit dbff517, guardado antes de ver ningún resultado.
  Panel: 1.502 acciones, de 2008-06-02 a 2026-11-20. Tenían grupo por tamaño 442 al empezar el entrenamiento y
  948 al empezar el examen; el resto no tenía dato de la SEC o era más pequeño.
  - **H1 a H3: descartadas en el entrenamiento, sin examen.** Ninguna variante llegó a t = 2 ni con los datos con
    los que se elige. Todas las variantes, en 2010–2017, netas de costes y frente a su grupo:

    | Variante | Ops | Media por op | t | Al año | Años + |
    |---|---|---|---|---|---|
    | H1 1 directivo, cualquier importe, 5 sesiones | 5.390 | −0,11 % | −2,32 | −7,4 % | 2 de 8 |
    | H1 1 directivo, cualquier importe, 21 | 4.404 | −0,35 % | −2,28 | −3,2 % | 2 de 8 |
    | H1 1 directivo, cualquier importe, 63 | 3.488 | −0,36 % | −0,48 | −0,6 % | 4 de 8 |
    | H1 1 directivo, ≥ 100.000, 5 | 2.916 | −0,06 % | −1,00 | −4,2 % | 3 de 8 |
    | H1 1 directivo, ≥ 100.000, 21 | 2.389 | −0,15 % | −0,77 | −1,5 % | 4 de 8 |
    | H1 1 directivo, ≥ 100.000, 63 | 2.043 | +0,15 % | +0,09 | +0,1 % | 3 de 8 |
    | H1 2 directivos, cualquier importe, 5 | 1.702 | −0,14 % | −0,60 | −3,4 % | 3 de 8 |
    | H1 2 directivos, cualquier importe, 21 | 1.250 | −0,63 % | −1,53 | −4,3 % | 2 de 8 |
    | H1 2 directivos, cualquier importe, 63 | 1.028 | −0,42 % | −0,86 | −1,9 % | 4 de 8 |
    | H1 2 directivos, ≥ 100.000, 5 | 1.174 | −0,14 % | +0,10 | +0,6 % | 5 de 8 |
    | H1 2 directivos, ≥ 100.000, 21 | 887 | −0,50 % | −0,65 | −2,9 % | 2 de 8 |
    | H1 2 directivos, ≥ 100.000, 63 | 796 | −0,21 % | −0,63 | −1,7 % | 3 de 8 |
    | H2 10 % extremo, 5 sesiones | 5.135 | −0,25 % | −1,37 | −5,9 % | 4 de 8 |
    | H2 10 %, 21 | 5.114 | −0,03 % | −0,21 | −0,4 % | 4 de 8 |
    | H2 10 %, 63 | 4.195 | +0,00 % | −0,02 | −0,0 % | 2 de 8 |
    | H2 20 %, 5 | 10.234 | −0,14 % | −1,69 | −5,3 % | 4 de 8 |
    | H2 20 %, 21 | 10.150 | −0,02 % | −0,58 | −0,8 % | 3 de 8 |
    | H2 20 %, 63 | 7.778 | +0,04 % | +0,01 | +0,0 % | 3 de 8 |
    | H3 3 sesiones antes | 25.175 | −0,05 % | −0,71 | −3,1 % | 3 de 8 |
    | H3 5 sesiones antes | 25.158 | −0,05 % | +0,43 | +1,5 % | 4 de 8 |
    | H3 10 sesiones antes | 25.140 | −0,01 % | +1,03 | +1,8 % | 3 de 8 |

  - **H4: suspende el examen.**
    - Periodo: del 05-01-2024 al 20-11-2026, con 1.225 operaciones en 536 días.
    - Media −0,002 R, acierto 49 %, t = −0,06. Total −330: la primera mitad +1.908 y la segunda −2.238.
    - Perdió la cuenta de fondeo 3 veces. Semana media −2; semanas con 500 o más: 18 de 146.
  - **Lo que enseña.** Con datos oficiales pasa lo mismo que con los precios solos: antes de costes, en torno a
    cero; después, nada. Encaja con lo publicado:
    - las compras de directivos explican algo sobre todo en empresas muy pequeñas, fuera del S&P 1500;
    - la deriva tras resultados desapareció en las grandes desde mediados de los 2000 (Martineau, 2021).
  - **Siguiente ronda, solo si el usuario la pide.** Se escribe aquí antes de mirar, con t ≥ 2,8 porque ya van 4
    hipótesis. El periodo reservado (2023–hoy) sigue sin mirar.

## Segunda ronda con datos públicos (23-11, escrito antes de ver ningún resultado)

El usuario pidió "Sí, otra ronda". Son tres ideas nuevas, no las de la primera ronda con otros números. Las tres
apuestan a la baja frente a su grupo, porque la literatura las respalda sobre todo en ese sentido.

- **Igual que en la primera ronda:**
  - mismos datos (SEC, tamaño en cada fecha, grupos 500/400/600, limpieza de precios) y mismos costes;
  - mismos periodos: entrenamiento 2010–2017, examen 2018–2022 y reservado de 2023 a hoy, que sigue sin mirar;
  - misma elección: gana la variante con más t en el entrenamiento, si tiene al menos 100 operaciones; con t
    menor que 2, la idea se descarta sin examen.
- **Coste añadido:** las ventas en corto pagan un 3 % anual por el préstamo de las acciones, cobrado por cada
  sesión abierta. Es caro a propósito, para acciones con malas noticias.
- **Hipótesis:**
  - **H5, resultados que se retrasan.** Sostén: Bagnoli, Kross y Watts 2002; Johnson y So 2018.
    - Fecha esperada = la del mismo trimestre del año anterior + 364 días, como en H3.
    - Si k sesiones después no ha salido el comunicado de ese trimestre (ninguno desde 45 días antes de la fecha
      esperada), se vende en corto al cierre de esa sesión.
    - Se cierra al cierre del día siguiente a la primera reacción al comunicado, o a las M sesiones si no llega.
    - Variantes: k = 2 o 5; M = 10 o 30. Son 4.
  - **H6, avisos de mala noticia en la SEC.** Sostén: Alford, Jones y Zmijewski 1994; Palmrose, Richardson y
    Scholz 2004.
    - Eventos: formularios NT 10-K y NT 10-Q (avisan de que las cuentas no llegarán a tiempo) y 8-K con el
      punto 4.02 (las cuentas anteriores ya no son fiables).
    - Corto al cierre de la primera sesión en que el precio puede reaccionar.
    - Se mantiene 5, 21 o 63 sesiones. Son 3 variantes.
  - **H7, ventas en racimo de directivos.** Sostén: Jeng, Metrick y Zeckhauser 2003; Cohen, Malloy y Pomorski
    2012.
    - Ventas en bolsa (código S) de consejeros o directivos: al menos 2 o 3 personas distintas en 30 días y al
      menos 1 millón de USD en total.
    - Corto al cierre de la sesión siguiente a la presentación.
    - Se mantiene 21 o 63 sesiones. Son 4 variantes.
- **Aprobado:**
  - **Examen:** al menos 100 operaciones, t ≥ 2,8 y al menos 3 de los 5 años en positivo.
  - **Periodo reservado:** al menos 100 operaciones, media positiva y t ≥ 1,65.
  - **Por qué 2,8.** Con las 4 de la primera ronda son 7 hipótesis: la corrección de Bonferroni pide 2,69 y se
    usa 2,8.
- **Si alguna aprueba.** Son operaciones de días o semanas, a la baja. No caben en la cuenta de fondeo actual,
  que cierra todo cada día, así que irían como cartera aparte en simulación, y se decide con el usuario.
- **Enmienda del 24-11 a H5, antes de ver ningún resultado.**
  - **El fallo.** La revisión independiente encontró que H5 solo reconocía el comunicado por la etiqueta 2.02
    del 8-K. Algunas empresas lo publican solo con la 9.01 (GE 2017, PSA 2017, LNC 2018) y otras meten avances o
    actualizaciones con la 2.02. En la simulación con fechas, sin precios:
    - del 5 al 16 % de las señales eran falsos retrasos (la empresa sí había publicado);
    - esas señales ocupaban hasta el 46 % de los días con posición.
  - **El cambio.**
    - El trimestre cuenta como publicado con el primero de dos avisos: un 8-K 2.02 desde 45 días antes de la fecha
      esperada, o su 10-Q o 10-K desde 15 días antes. El del trimestre anterior sale antes.
    - Se cierra al día siguiente de ese primer aviso.
    - Un 2.02 con otro a menos de 50 días no sirve de referencia para el año siguiente: es un avance o una
      actualización, no un trimestre.
  - **Lo que queda.** Con el cambio, las señales dudosas bajan al 4–21 % de los días con posición, según la
    variante. La hipótesis, las variantes, los periodos, los costes y el umbral no cambian.
- **Resultado (24-11): las tres descartadas en el entrenamiento, sin examen.** Código en el commit b3b25cf, guardado
  antes de ver ningún resultado. Todas las variantes, en 2010–2017, netas de costes y del préstamo:

  | Variante | Ops | Media por op | t | Al año | Años + |
  |---|---|---|---|---|---|
  | H5 retraso de 2 sesiones, máximo 10 | 5.018 | −0,16 % | −0,85 | −8,3 % | 4 de 8 |
  | H5 retraso de 2, máximo 30 | 5.018 | −0,16 % | −0,66 | −6,1 % | 3 de 8 |
  | H5 retraso de 5, máximo 10 | 1.144 | −0,53 % | −1,37 | −14,9 % | 3 de 8 |
  | H5 retraso de 5, máximo 30 | 1.144 | −0,52 % | −0,97 | −9,6 % | 3 de 8 |
  | H6 5 sesiones | 109 | −0,05 % | +0,69 | +2,6 % | 5 de 8 |
  | H6 21 sesiones | 100 | −0,40 % | +0,76 | +4,3 % | 4 de 8 |
  | H6 63 sesiones | 94 | −3,01 % | −0,80 | −6,3 % | 3 de 8 |
  | H7 2 directivos, 21 sesiones | 13.823 | −0,42 % | −3,20 | −4,0 % | 2 de 8 |
  | H7 2 directivos, 63 sesiones | 8.641 | −0,82 % | −2,14 | −2,1 % | 2 de 8 |
  | H7 3 directivos, 21 sesiones | 10.556 | −0,44 % | −2,18 | −3,4 % | 3 de 8 |
  | H7 3 directivos, 63 sesiones | 7.026 | −0,92 % | −3,08 | −2,8 % | 2 de 8 |

  - **H7 no sirve al revés.** Su pérdida es casi toda el coste: unos 0,4 puntos por operación entre comisiones,
    deslizamiento y préstamo. Comprar tras esas ventas tampoco ganaría, y probarlo ahora sería ajustarse a lo
    que se acaba de ver.
  - **H6 con muy pocos casos.** En las empresas del tamaño del S&P 1500 hay unos 100 avisos en 8 años: no
    demuestran nada, ni a favor ni en contra.
  - **Balance de las dos rondas.** 7 ideas publicadas, 33 variantes y ninguna con ventaja después de costes. El
    periodo reservado (de 2023 a hoy) sigue sin mirar.

## Cobrar rápido en una cuenta de fondeo (25-11)

Pregunta del usuario: ¿se puede pagar una cuenta de fondeo, pasarla y cobrar 500–1.000 en una o dos semanas, aunque
la cuenta muera después? Las reglas se verificaron el 25-11-2026 en las páginas oficiales de cada empresa (centros de
ayuda y condiciones de topstep.com, Lucid, tradeify.co, myfundedfutures.com, takeprofittrader.com,
tradethepool.com, ftmo.com, hyrotrader.com y Kraken Prop), con un segundo revisor por empresa. Pueden cambiar.

- **Quién deja usar el bot del usuario:**
  - **Topstep:** sí, en la evaluación y en la cuenta financiada simulada, solo por su API (14,50 USD/mes con
    código, 29 sin él), sin servidores remotos, VPN ni alta frecuencia. En la cuenta real (Live), no.
  - **Lucid, Tradeify y MyFundedFutures:** también permiten bots (Tradeify pide un vídeo del usuario activándolo),
    pero usan Tradovate o Rithmic y el sistema solo tiene conector para TopstepX.
  - **Las prohíben:** Take Profit Trader, Trade The Pool (acciones) y Kraken Prop (cripto).
  - **FTMO** las permite, pero no paga hasta 14 días después de estar financiado. **HyroTrader** (cripto) las
    permite si la estrategia es propia.
  - **Colombia** no está en ninguna lista de países excluidos.
- **Topstep 50K, la única que el sistema puede operar hoy** (con el conector sin probar contra el servicio real):
  - **Precio:** 49 USD al mes más 149 de activación, o 95 al mes sin activación, más la API.
  - **Evaluación:** objetivo +3.000, con el mejor día como mucho el 55 % del beneficio, y 2.000 de pérdida máxima
    sobre el mejor cierre, vigilada también dentro del día.
  - **Cobros en la cuenta financiada:**
    - el más rápido, cada 3 días de operación, con el mejor día como mucho el 40 % del beneficio;
    - hasta el 50 % del saldo, con tope de 3.000, y el 90 % para el trader;
    - transferencia internacional de 30 USD, con 1–3 días hábiles de aprobación y 5–10 de transferencia;
    - tras el primer cobro, el suelo de la cuenta pasa a 0.
  - **Cobro diario:** no lo tiene en la cuenta financiada normal. "Topstep Labs Daily Payouts+" (119 USD, cobros
    de 250 a 1.500 al día por encima de un colchón de 2.000) se anunció en noviembre, pero su venta no está
    confirmada.
- **Cuántos cobran de verdad.** Cifras publicadas por las propias empresas o por un proveedor del sector, sin
  auditar:
  - **Topstep 2025:** aprobó el 16,8 % de las evaluaciones, y el 33,3 % de quienes llegaron a financiada cobró
    algo en todo el año; eso es alrededor del 17 % de las personas, con compras repetidas.
  - **Tradeify y MyFundedFutures:** 28–39 % de los financiados cobra algo.
  - **Entre 10 empresas** (FPFX): 14 % aprueba y 7 % de los clientes cobra alguna vez. Bloomberg habla de un 4 %.
  - **Fuera de las empresas:** el 97 % de quienes hicieron day trading de futuros en Brasil durante más de 300
    días perdió dinero (Chague y otros).
- **Simulación** (`prop_plan.py`):
  - **Método.** Cada intento sorteó, día a día, resultados reales de los bots. Salen de los 262 días de la
    simulación de un año con las acciones del usuario (`backtest.py --lineage 2000 --flat`, guardada en
    `data/lab/bt_stocks_365d.json`): 496 operaciones, −1.706 netos, 7 de 53 semanas con 500 o más, media diaria
    −6,5 y desviación 197.
  - **Optimista en dos cosas.** El suelo solo se mira al cierre del día y no se cuentan las comisiones de futuros.
  - **Resultado** (20.000 intentos por casilla; "riesgo x5" son unos 1.000 USD por operación):

  | Empresa (precio) | Cobra ≥ 500 en 2 semanas, riesgo x1 | Ídem, riesgo x5 | Cobra ≥ 500 en 4 semanas, riesgo x5 | Neto medio por intento, 4 semanas, x5 |
  |---|---|---|---|---|
  | Topstep 50K estándar (212) | 0 % | 0,2 % | 2,0 % | −172 |
  | Topstep 50K sin activación (110) | 0 % | 0,2 % | 2,1 % | −69 |
  | Topstep Labs cobro diario (134, venta sin confirmar) | 0 % | 1,4 % | 7,5 % | +36 |
  | Lucid 50K diario (94; sin conector) | 0 % | 0,4 % | 3,5 % | −18 |
  | Tradeify Select Daily (174; sin conector) | 0 % | 0,4 % | 3,3 % | −106 |
  | MyFundedFutures Rapid (209; sin conector) | 0 % | 0,8 % | 4,2 % | −109 |

- **Lo que dice.**
  - **Con el riesgo actual** (200 por operación), los bots no pasan una evaluación en semanas: hacen falta meses
    incluso sin pérdida media.
  - **Arriesgando 5 veces más**, cobrar 500 o más en dos semanas ocurre en menos de 2 intentos de cada 100. En
    cuatro semanas, entre 2 y 7,5 de cada 100. Casi siempre se pierde lo pagado.
  - **Topstep Labs:** es la única con media positiva (+36), dentro del margen de lo optimista de la simulación, y
    no está confirmado que se pueda comprar.
  - **El dinero llega tarde:** aunque salga bien, en Colombia llega 1–2 semanas después de pedirlo.
  - **En la práctica es un billete de lotería con esperanza negativa.** Encaja con el 4–17 % de clientes que
    cobran alguna vez.
- **Noticias:** no hace falta nada para lo que hay. El bot Macro no puntúa titulares porque no hay clave de
  Anthropic, y las fuentes gratuitas que mejor encajan (Alpaca News, con cuenta gratuita, y los RSS de la Reserva
  Federal y de las suspensiones de Nasdaq) no han demostrado mejorar nada.
- **Herramientas de GitHub revisadas, no añadidas:**
  - `LuxAlgo/prop-firm-sim` (MIT, 120 estrellas) hace lo mismo que `prop_plan.py`; su autor vende cupones de esas
    empresas.
  - `TexasCoding/project-x-py` (MIT) y la documentación oficial de ProjectX sirven para revisar `utils/topstepx.py`
    antes de usarlo de verdad; queda como pendiente si el usuario elige Topstep.

## Relevo con pruebas, divisas y copiar a políticos y fondos (26-11, escrito antes de ver ningún resultado)

Petición del usuario: "los bots van en perdidas muy grandes de ser necesario al llegar al limite de perdida de la
cuenta de fondeo, que se inicien unos nuevos con todo el aprendizaje nuevo, que aprendan de los errores de los
anteriores y que optimicen el sistema, deben poder acceder a lo que invierten los politicos de estados unidos y
algunos millonarios o fondos, y ver si es buena idea o no, deben operar tambien con el euro vs dolar y todo eso".

Son tres pruebas. Las tres se escriben aquí antes de mirar nada, y el código queda guardado en un commit antes de
lanzarlas.

### A. Relevo que aprende de todas las cuentas

- **Lo que ya había.** Al perder la cuenta nace otra que hereda:
  - los pesos de cada bot, calculados con todo el historial;
  - los vetos (un activo en un sentido que no se opera);
  - una corrección de UNA causa, con reglas fijas y por este orden: stop más ancho si los costes pasaban del 25 %
    de lo arriesgado; umbral +0,10 si acertaba antes de costes; vetar el par activo/lado que más perdió; umbral
    +0,05; o arriesgar un 25 % menos.
- **Lo nuevo.**
  - Los costes siguen yendo primero.
  - Después se buscan pruebas en dos periodos: la cuenta perdida y todas las anteriores. En la primera muerte, las
    dos mitades de la cuenta.
  - Candidatas: vetar cada par activo/lado, o subir el umbral 0,05, 0,10 o 0,15 (hasta 0,75).
  - Una candidata vale solo si en LOS DOS periodos habría quitado al menos 5 operaciones que, juntas, perdían. Gana
    la que más habría ahorrado en el peor de los dos.
  - Si ninguna vale, se usan las reglas fijas de antes. Así una mala racha de un solo periodo no se toma por una
    lección.
  - El motor en vivo usa la misma función (`backtest.successor`).
- **Cómo se compara.** Las mismas simulaciones de cuenta fondeada que la tabla de "Ya probado", con el relevo
  nuevo y con el de reglas fijas (`lineage={"learn": "rules"}`), sobre los mismos datos:
  - acciones (las siete del usuario), un año;
  - cripto (BTC y ETH de Binance, desde el PC), un año;
  - divisas (prueba B), todo lo que da Yahoo.
  - Las dos formas solo usan lo ya ocurrido en cada muerte: es una comparación limpia de dos procedimientos, sin
    ajustar nada después.
- **Criterio.** El relevo nuevo se queda si en las tres simulaciones pierde menos (o gana más) que el de reglas
  fijas, sin perder más cuentas. Si no, se quita el código y se dice. Ninguna de las dos formas crea ventaja: la
  tabla de "Ya probado" muestra que aprender reduce pérdidas porque enseña a operar menos.
- **Enmienda del 26-11, antes de ver ningún resultado.** Una revisión independiente (3 revisores, cada uno
  comprobando lo que encontraba) halló 28 fallos en las tres pruebas. En esta, cambia:
  - **Las pruebas solo cuentan lo que la configuración de ahora todavía operaría.** Lo ya vetado o por debajo del
    umbral actual no prueba nada. Sin esto, desde la segunda muerte subir el umbral se apoyaba en un solo periodo.
  - **Lo que se quita tiene que perder de verdad en cada periodo**, con media negativa y t ≥ 2. Con datos de puro
    azar, la versión anterior encontraba una "lección" en el 78 % de las primeras muertes.
  - **Cada forma se lanza con su propio comando y el mismo día UTC** (`backtest.py ... --rules` para la de reglas
    fijas). Así no se mezclan la evolución de los bots ni los datos, que se guardan por día.
  - **Empate:** un mercado en el que las dos formas operan igual (sin cuentas perdidas, o con las mismas lecciones)
    no cuenta y deciden los demás. Si ninguno tiene diferencias, no hay prueba y se quedan las reglas fijas.
  - **Con el resultado se dan, de cada forma, las operaciones, el bruto y el neto por operación.** Si gana solo por
    operar menos, se dice así.
  - **Datos ya vistos:** el año de acciones y el de cripto ya se vieron con el relevo de reglas fijas (11 a 14-11).
    Solo las divisas son datos nuevos.
  - **El motor en vivo sigue con las reglas fijas** hasta que esta comparación decida.
- **Resultado (27-11): el relevo con pruebas suspende y se quitó su código.** Código probado en el commit 914598f,
  guardado antes de ver ningún resultado. Simulaciones de cuenta fondeada (50.000, 200 de riesgo, pérdida máxima
  2.000), cada forma con su propio comando y el mismo día UTC:

  | Mercado | Forma | Ops | Neto | Bruto | Neto por op | Acierto | Cuentas perdidas | Semana media |
  |---|---|---|---|---|---|---|---|---|
  | Acciones (7), 1 año | las dos, iguales | 495 | −2.168 | +1.035 | −4,4 | 48 % | 3 | −41 |
  | Divisas (3), 2,6 años | las dos, iguales | 237 | −6.118 | −2.846 | −25,8 | 32 % | 4 | −45 |
  | Cripto (BTC, ETH), 1 año | con pruebas | 218 | −12.188 | −5.108 | −55,9 | 28 % | 6 | −230 |
  | Cripto (BTC, ETH), 1 año | reglas fijas | 147 | −9.267 | −4.309 | −63,0 | 27 % | 4 | −175 |

  - **Acciones y divisas: empate.** En ninguna muerte encontró una lección que se sostuviera en los dos periodos, y
    usó las mismas reglas fijas. Según lo escrito antes, un empate no cuenta.
  - **Cripto, el único mercado con diferencias: pierde 2.921 más y 2 cuentas más.** Su única lección con pruebas
    (umbral a 0,35 en la tercera cuenta: "se habría ahorrado 1.430 en 13 operaciones de esta cuenta y 1.635 en 18
    de las anteriores") llevó a operar más, no menos.
  - **Qué se hace.** Se quitó el código del relevo con pruebas y se queda el de reglas fijas, como estaba antes.
  - **Lo que enseña.** Lo que perdió en dos periodos tampoco anunciaba el tercero. Es lo mismo que en todo lo
    anterior: no hay patrón que aprender, solo menos operaciones que perder.

### B. Los bots en divisas

- **Qué se activa.**
  - EUR/USD, GBP/USD y JPY/USD (el yen al revés, como el futuro 6J de Chicago, para que el resultado salga en
    dólares). Simulados con precios de Yahoo, sin volumen ni libro de órdenes.
  - Horario: de domingo 17:00 a viernes 17:00 de Nueva York. Se entra desde 2 horas después de abrir (antes Yahoo
    no da precios) hasta una hora antes del cierre del viernes. Todo se cierra 5 minutos antes: nada pasa el fin de
    semana abierto.
  - Velas de 1 hora para SMC, Wyckoff y el stop, como las acciones. Wyckoff no vota (sin volumen no puede leer
    nada).
  - Costes por lado: comisión del 0,0035 % (3,5 USD por lote de 100.000, como IC o Pepperstone) y 0,005 % de medio
    diferencial y deslizamiento (unos 0,5 pips en el euro).
  - Se activa en la cuenta de fondeo simulada pase lo que pase en la prueba, porque el usuario lo pidió; si
    suspende, se dice.
  - En Topstep el euro sería el futuro M6E o 6E (permitidos allí, 1 o 4,22 USD por ida y vuelta). Basta ponerlo en
    `SYMBOLS`, pero no se ha probado contra el servicio real.
- **La prueba.** Los bots tal cual, sin ajustar nada, en la cuenta fondeada simulada:
  - 50.000 y 200 de riesgo por operación;
  - límite diario de 1.000;
  - cuenta perdida a 2.000 del mejor cierre, con relevo;
  - velas de 1 hora de Yahoo (unos 2,8 años, desde febrero de 2024);
  - `--evolution --lineage 2000 --flat`, como la de acciones.
- **Aprueba si:** al menos 100 operaciones, resultado neto positivo, las dos mitades en positivo y ninguna cuenta
  perdida. Todo el periodo es examen, porque no se elige nada.
- **Enmienda del 26-11, antes de ver ningún resultado** (la misma revisión independiente):
  - **El periodo.** La revisión creía que Yahoo solo da 2 años de velas de 1 hora. Pidiéndolas como las pide
    `backtest.py` (`range=730d`, 730 días de mercado) llegan desde el 12-02-2024: unos 2,8 años. Se simulan los
    últimos 950 días, para que los bots tengan 2 meses de calentamiento.
  - **El riesgo.** Con el tope de apalancamiento de 2 veces, cada operación en divisas arriesga unos 50–100, no 200:
    el stop a 1,5 veces el ATR de 1 hora queda a un 0,09 % y el tope deja 100.000 de nocional.
  - **Wyckoff** vota neutral en divisas y cuenta en el denominador del jefe: no se abstiene del todo.
  - **Arreglado antes de lanzar la prueba:**
    - el yen se habría operado con el precio redondeado de la cabecera de Yahoo (hasta un 0,8 % de error, varias
      veces el stop); ahora se usa el último cierre;
    - se entra desde 3 horas después de la apertura del domingo, porque Yahoo no da precios hasta las 19:00 (o las
      20:00) de Nueva York;
    - lo que quedara abierto del fin de semana se cierra cuando ya hay precios de la semana nueva, no con el último
      del viernes;
    - en el backtest no se entra en la última hora del viernes, todo se cierra antes del fin de semana con o sin
      reglas de fondeo, y un stop que salta con un hueco se ejecuta donde abre la vela, como en vivo. Esto último
      vale también para las acciones y cambia un poco sus simulaciones;
    - el bot Macro pide a Alpha Vantage las noticias de las divisas como divisas (`FOREX:EUR`), no como cripto;
    - `EURUSD=X` en `SYMBOLS` se rechaza (pasaba por una acción);
    - la lección "stop más ancho" de un relevo ensancha también, en vivo, el stop de las acciones y las divisas,
      como ya hacía el backtest.
- **Resultado (27-11): suspende.** Código en el commit 914598f, guardado antes de ver ningún resultado.
  - **Cifras.** Del 22-04-2024 al 26-11-2026 hubo 237 operaciones: neto −6.118, bruto −2.846 (pierde ya antes de
    costes), 32 % de acierto y 4 cuentas perdidas.
  - **Por mitades y semanas.** Las dos mitades en negativo (−3.541 y −2.578). Semana media −45; semanas con 500 o
    más, 3 de 136.
  - **Qué se hace.** Las divisas siguen en la cuenta de fondeo simulada porque el usuario las pidió, sabiendo que
    aquí pierden. Quitarlas es borrar EUR/USD, GBP/USD y JPY/USD de `SYMBOLS` en `Iniciar cuenta de fondeo.bat`.

### C. Tercera ronda del laboratorio: copiar a los políticos (H8) y a los grandes inversores (H9)

- **Igual que en las rondas anteriores:**
  - el S&P 1500 con el tamaño de cada empresa en cada fecha, la limpieza de precios y los costes;
  - solo compras;
  - entrada al cierre de la sesión siguiente al día en que el dato es público;
  - una posición por acción a la vez;
  - periodos: entrenamiento 2010–2017, examen 2018–2022 y reservado de 2023 a hoy, que se mira una sola vez.
- **Datos, todos públicos y sin clave.**
  - **Políticos:** las declaraciones de compra y venta de congresistas y senadores (ley STOCK Act). Vienen de
    `kadoa-org/congress-trading-monitor` (MIT), sacadas de las páginas de la Cámara y el Senado, con una versión fija
    (commit `1d6be68`, del 25-11-2026).
    - Cuenta la fecha de la declaración, no la de la operación: hasta entonces nadie la ve. La mediana del retraso
      es de 31 días.
    - Hay datos desde 2014, así que el entrenamiento real es 2014–2017.
  - **Grandes inversores:** los 13F de la SEC, en XML desde el 20-05-2013, de 26 gestores conocidos. Sus CIK se
    comprobaron en EDGAR el 26-11-2026 y están en `utils/public_data.py` (`MANAGERS`).
    - Buffett (Berkshire), Ackman, Burry, Tepper, Klarman, Einhorn, Loeb, Icahn, Druckenmiller, Soros, Tiger Global,
      Lone Pine, Viking, Coatue, Elliott, Akre, Li Lu, ValueAct, Trian, Glenview, Maverick, Fairholme, Greenblatt,
      Oaktree, Bridgewater y Renaissance.
    - Cuenta el momento de aceptación de la SEC.
    - Las posiciones que un gestor mantuvo en secreto y salieron después en una enmienda cuentan desde la enmienda.
    - Las correcciones posteriores no se usan.
  - **Para traducir cada CUSIP a su ticker:** primero el de hoy, de los fondos de State Street. Si no está, el
    símbolo que tenía en esa fecha según las entregas fallidas de la SEC (un archivo por trimestre), que también
    sigue los cambios de ticker (FB → META).
  - **La sede de cada empresa (H8, variante de su estado):** la dirección de su ficha en la SEC.
  - **El SPY** (S&P 500 ponderado por tamaño), de Yahoo.
- **H8, copiar a los políticos.** Compras de acciones (sin opciones, bonos ni fondos). Si una operación sale en
  varias declaraciones, cuenta la primera. Variantes:
  - **todas las compras**, mantener 63 sesiones. Lo publicado espera entre −2 % y +1 % al año: Belmont y otros 2022,
    Chen y Sacerdote 2026, y los fondos NANC y GOP, que copian al Congreso desde 2023 y van como el S&P 500;
  - **las de los 12 cargos de dirección** mientras los tienen (presidente de la Cámara, líderes y "whips" de las dos
    cámaras y presidentes de los grupos), mantener 250 sesiones. Wei y Zhou 2025 dan +10,6 % al año desde la
    declaración, con muy pocos casos; Chen y Sacerdote, −4,4 %;
  - **las de empresas con sede en el estado del político**, mantener 250 sesiones. Eggers y Hainmueller 2014 dan
    0–3 % al año, medido con carteras y no desde la declaración.
- **H9, copiar a los grandes inversores.** Variantes:
  - **Berkshire:** acciones nuevas o que crecen al menos un 10 % (descontando los splits) frente a lo que se sabía
    del trimestre anterior; mantener 252 sesiones. Martin y Puthenpurackal 2008 dan 6–11 % al año en 1976–2006,
    antes de publicarse; Frazzini, Kabiller y Pedersen 2018 lo explican por sus factores;
  - **consenso:** el día siguiente al plazo de 45 días, las acciones que al menos 3 gestores tienen entre sus 10
    mayores posiciones; mantener 63 sesiones. Solo cuentan los gestores con 10 a 200 posiciones, como en la regla
    de Goldman Sachs. Su fondo GVIP da −0,9 % al año frente al SPY desde 2016;
  - **apuestas nuevas:** una acción entra entre las 10 mayores de un gestor sin estar en su 13F anterior; mantener
    63 sesiones. Lo publicado espera entre −3 % y +2 % al año (el fondo GURU, −2,9 %).
- **Elección:** en cada hipótesis, la variante con más t en el entrenamiento, con al menos 100 operaciones. Si no
  llega a t = 2, la hipótesis se descarta sin examen.
- **Aprueba el examen si cumple todo:**
  - al menos 100 operaciones;
  - t ≥ 3,0 frente a su grupo: son 9 hipótesis con las dos rondas anteriores, y Bonferroni pide 2,77;
  - al menos 3 de los 5 años en positivo;
  - también gana al SPY, con t ≥ 1,65 (copiar a estos inversores carga en las gigantes, y entre 2023 y 2026 el SPY
    sacó más de 5 puntos al año a la media de sus acciones);
  - sin las 3 acciones que más aportan, la media por operación sigue en positivo.
- **Confirma en el reservado si:** al menos 100 operaciones, media positiva, t ≥ 1,65 y por encima del SPY.
- **Sesgos conocidos, que se dirán con el resultado:**
  - la lista de acciones es la de hoy;
  - en el Senado solo están los que siguen en el cargo;
  - la Cámara tiene menos de la mitad de los informes antes de 2019 y le faltan los que se declaraban en papel;
  - el 73–80 % de las compras son de menos de 15.000 USD;
  - más de la mitad son del cónyuge o de cuentas conjuntas;
  - muchas de las operaciones famosas de Pelosi son opciones, y aquí no se copian;
  - un 13F llega hasta 45 días tarde y no muestra lo que se mantiene en secreto;
  - las empresas que ya no cotizan quedan fuera, y la sede es la de hoy.
- **Qué se activa pase lo que pase:** en la app, las últimas declaraciones de los políticos y las últimas
  posiciones de los gestores, para mirarlas. Un bot que vote con esto, solo si una hipótesis aprueba el examen y
  confirma en el reservado.
- **Enmienda del 26-11, antes de ver ningún resultado** (la misma revisión independiente). Cambia la medida; las
  hipótesis, las variantes, los periodos y los criterios siguen igual:
  - **Apuestas nuevas.** El código pedía gestores de 10 a 200 posiciones, como el consenso, y el texto de arriba no.
    Se quita del código. Ahora también cuentan Ackman, Burry, Li Lu o Peltz, que tienen menos de 10.
  - **El plazo del 13F** también salta los festivos federales (el día de los presidentes, en febrero), no solo los
    fines de semana.
  - **Consenso.** Si una acción sigue en el consenso en la revisión siguiente y su posición sigue abierta, se alarga
    en vez de cerrarse. Antes, con 63 sesiones y revisiones cada 60–65, se saltaba un trimestre de cada dos.
  - **Compras de políticos.**
    - Fuera también las opciones, los bonos y las preferentes que llevan el código de acción ([ST]).
    - Fuera las filas cuyo texto trae otro ticker: a veces se tomaba del nombre de la cuenta.
    - Una operación redeclarada cuenta una vez, aunque cambie el titular o el importe.
  - **Tickers que hoy son de otra empresa.** 12, comprobados a mano: WTW era Weight Watchers, BBT era BB&T, COHR era
    Coherent... Antes de su fecha de cambio no se traducen por el símbolo. La lista está en `event_lab.py`
    (`REUSED`); puede haber más casos, del orden del 1 % de las operaciones.
  - **Berkshire y apuestas nuevas.** Una acción no es nueva si su emisor ya estaba con otro CUSIP (cambio de CUSIP u
    otra clase de acciones).
  - **Datos completos.** Sin el SPY o sin las entregas fallidas, la ronda no se lanza. Antes, la prueba contra el SPY
    habría sido contra cero.
  - **Recuento de operaciones, sin mirar ningún resultado** (entrenamiento / examen / reservado):

    | Variante | Operaciones |
    |---|---|
    | H8 todas las compras | 824 / 2.791 / 3.203 |
    | H8 los 12 cargos de dirección | 7 / 14 / 343 |
    | H8 empresas de su estado | 44 / 160 / 157 |
    | H9 Berkshire | 21 / 29 / 17 |
    | H9 consenso | 52 / 48 / 38 |
    | H9 apuestas nuevas | 128 / 161 / 160 |

    Con el mínimo de 100 en el entrenamiento, solo pueden elegirse "todas las compras" (H8) y "apuestas nuevas"
    (H9). Las otras cuatro se informan, pero con tan pocas operaciones no demuestran nada, ni a favor ni en contra.
    Los cargos de dirección tienen 343 en el reservado porque Lisa McClain, presidenta de la conferencia republicana
    desde 2025, compró mucho.
- **Resultado (27-11): las dos descartadas en el entrenamiento, sin examen.** Código en el commit 914598f, guardado
  antes de ver ningún resultado. Todas las variantes, en el entrenamiento (H8: 2014–2017; H9: mediados de 2013 a
  2017), netas de costes y frente a su grupo de tamaño:

  | Variante | Ops | Media por op | t | Al año | Años + | Contra el SPY | Sin las 3 que más aportan |
  |---|---|---|---|---|---|---|---|
  | H8 todas las compras, 63 sesiones | 824 | −0,07 % | +0,52 | +0,6 % | 3 de 8 | t +0,90 | −0,29 % por op |
  | H8 cargos de dirección, 250 | 7 | −6,57 % | +0,02 | +0,1 % | 2 de 8 | t +0,08 | −21,0 % |
  | H8 empresas de su estado, 250 | 44 | +2,53 % | +2,09 | +3,3 % | 3 de 8 | t +1,99 | −0,68 % |
  | H9 Berkshire, 252 | 21 | +0,78 % | −0,01 | −0,0 % | 1 de 8 | t +0,04 | −5,06 % |
  | H9 consenso de 3 gestores, 63 | 52 | +4,33 % | +1,68 | +4,6 % | 4 de 8 | t +1,98 | +2,45 % |
  | H9 apuestas nuevas, 63 | 128 | −0,47 % | −0,40 | −1,1 % | 2 de 8 | t −0,26 | −1,60 % |

  - **H8:** la única elegible (todas las compras) se queda en t = +0,52. Copiar lo que declaran los políticos, en
    conjunto, va como su grupo: lo mismo que dicen Belmont y otros (2022), Chen y Sacerdote (2026) y los fondos NANC
    y GOP.
  - **H9:** la única elegible (apuestas nuevas) pierde un 1,1 % al año frente a su grupo, como el fondo GURU.
  - **Lo que no demuestra nada:**
    - "empresas de su estado" (+3,3 % al año) tiene 44 operaciones y sin sus 3 mejores acciones pierde;
    - "consenso" (+4,6 % al año, t = 1,68) tiene 52. El fondo de Goldman Sachs que sigue esa regla (GVIP) va 0,9
      puntos al año por debajo del S&P 500 desde 2016.
    - Pasarlas ahora al examen sería elegir mirando el resultado, que es justo lo que el protocolo prohíbe.
  - **El periodo reservado (2023 a hoy) sigue sin mirar.**
  - **Balance de las tres rondas:** 9 ideas publicadas, 39 variantes y ninguna con ventaja demostrada después de
    costes.

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
  - El 23 y el 24-11 volvió a apagarse dos veces. Claude había abierto `Iniciar cuenta de fondeo.bat` con
    `Start-Process cmd`, y la ventana seguía colgando de la sesión de Claude.
  - Desde el 24-11 Claude la abre con `explorer.exe "…\Iniciar cuenta de fondeo.bat"`. La cadena queda
    explorer → cmd → app → motor, la misma de un doble clic, y ya no depende de la sesión.
  - Sigue haciendo falta dejar abierta esa ventana negra y el ordenador encendido.
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
15. **19-11, examen del bot de escenarios.** "prepara el paso 3". `scenario_exam.py`: dos años de cripto fuera de
    muestra. Suspende: −0,17 R por operación en 916 operaciones, 0 de 9 trimestres en positivo.
16. **23-11, datos públicos oficiales.** "activa todo, haz pruebas hasta obtener exito, agrega las funciones que sean
    necesarias, usa datos que estan de acceso publico de los gobiernos y las empresas en sus propias paginas".
    `utils/public_data.py` (SEC, State Street, Reserva Federal, BLS, Yahoo), calendario oficial en el bot Macro y
    `event_lab.py` con H1-H4. Las cuatro suspenden. Motor de la cuenta de fondeo encendido desde la app.
17. **24-11, segunda ronda.** "Sí, otra ronda". H5 (resultados que se retrasan), H6 (avisos de mala noticia en la
    SEC) y H7 (ventas en racimo de directivos), registradas antes y revisadas por críticos independientes (una
    enmienda a H5). Las tres, descartadas en el entrenamiento.
18. **24-11, motor y skills.** "vuelve a activarlo, si encuentras nuevas skills en github agrégalas y si encuentras
    más información también".
    - App y motor de la cuenta de fondeo encendidos de nuevo, esta vez con `explorer.exe` para que no dependan de
      la sesión de Claude.
    - Seis skills nuevas, revisadas antes por críticos independientes: `backtest-expert`, `residual-edge-analyzer`,
      `crypto-regime-analyzer` y `market-breadth-analyzer` (de tradermonty/claude-trading-skills), y
      `catalyst-calendar` y `earnings-preview` (del plugin oficial de Anthropic para finanzas).
    - Cuatro actualizadas: `free-llm-apis`, `session-metrics`, `managed-pentesting-with-strix` y `design-is`.
    - Lo que no se instaló y por qué, en `.claude/skills/README.md`. Sus programas no se han ejecutado en el PC: el
      control de seguridad de Claude Code no lo dejó sin la aprobación del usuario.
19. **25-11, cobrar rápido en una cuenta de fondeo.** "la idea es poder retirar diariamente dinero [...] minimo entre
    500 a 1000 dolares semanales, no importa que la cuenta muera [...] si necesitas algo para conectarte a noticias o
    algo dimelo, si vez en redes sociales o github cosas para agregar, agregalas".
    - Reglas reales de nueve empresas verificadas en sus páginas oficiales y tasas de cobro publicadas.
    - `prop_plan.py`, que simula el plan con los días reales de los bots (con `selfcheck.py prop`).
    - Resultado: menos de 2 de cada 100 intentos cobran 500 o más en dos semanas, incluso arriesgando 5 veces más.

20. **26 y 27-11, relevo que aprende, políticos y fondos, y divisas.** "los bots van en perdidas muy grandes de ser
    necesario al llegar al limite de perdida de la cuenta de fondeo, que se inicien unos nuevos con todo el
    aprendizaje nuevo [...] deben poder acceder a lo que invierten los politicos de estados unidos y algunos
    millonarios o fondos, y ver si es buena idea o no, deben operar tambien con el euro vs dolar y todo eso".
    - Divisas contra el dólar en el motor, el backtest y la cuenta de fondeo simulada (`selfcheck.py fx`).
    - Lo que declaran los políticos y los 13F de 26 gestores, en `utils/public_data.py` y en la pestaña "Políticos y
      fondos" de la app.
    - Tercera ronda del laboratorio y un relevo con pruebas, preinscritos (commit 914598f) y revisados antes por
      críticos independientes (28 fallos arreglados).
    - Resultado: copiar a políticos y fondos, descartado en el entrenamiento; los bots en divisas pierden; el relevo
      con pruebas perdió más en cripto y se quitó.
    - El disco C: del PC estaba lleno; con permiso del usuario se borró `C:\Autodesk\WI` (37 GB de instaladores).
