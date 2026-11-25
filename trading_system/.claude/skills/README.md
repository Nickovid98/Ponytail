# Skills de terceros

Claude Code detecta estas skills al trabajar dentro de `trading_system/` y cada
una se invoca como `/<nombre-de-carpeta>`. Todas son copias sin modificar de su
repositorio de origen, salvo `congress-trading-monitor`, que se escribió aquí.

Ninguna forma parte del motor de trading (`main.py`): son herramientas que
Claude usa contigo en una conversación.

| Origen | Commit | Licencia | Skills |
| --- | --- | --- | --- |
| [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | `3d0f4ef` | MIT | las 11 `trader-*` |
| [mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis) | `7266021` | CC0 | `free-llm-apis` |
| [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) | `c70ae6d` | MIT | `agent-reach` (ver abajo por qué no se actualizó) |
| [centminmod/my-claude-code-setup](https://github.com/centminmod/my-claude-code-setup) | `3262389` | MIT | `ai-image-creator`, `audit-session-metrics`, `claude-docs-consultant`, `consult-codex`, `consult-zai`, `session-metrics`, `task-breakdown` |
| [usestrix/strix](https://github.com/usestrix/strix) | `935f823` | Apache-2.0 | `api-security-testing`, `application-security-testing`, `ci-security-scanning-with-strix`, `find-security-vulnerabilities-in-code`, `fix-security-vulnerabilities-with-strix`, `managed-pentesting-with-strix`, `owasp-top-10-testing`, `penetration-testing-with-strix`, `web-app-penetration-testing` |
| [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | `4dc8e48` | Apache-2.0 | `babysit`, `design-is`, `do`, `learn-codebase`, `make-plan`, `pathfinder`, `what-the`, `wowerpoint` |
| [kadoa-org/congress-trading-monitor](https://github.com/kadoa-org/congress-trading-monitor) | `49f1d36` | MIT (datos) | `congress-trading-monitor` (escrita aquí) |
| [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) | `4080042` | MIT | `backtest-expert`, `residual-edge-analyzer`, `crypto-regime-analyzer`, `market-breadth-analyzer` (24-11) |
| [anthropics/financial-services-plugins](https://github.com/anthropics/financial-services-plugins) | `3e32c22` | Apache-2.0 | `catalyst-calendar`, `earnings-preview` (24-11, del plugin `equity-research`) |

Los textos de licencia están en esta carpeta: `LICENSE-<origen>` y
`NOTICE-claude-mem`.

El 24-11 se revisó cada origen contra su versión del día. Cambiaron cuatro skills, que se copiaron de nuevo
tal cual:
- `free-llm-apis`: lista de proveedores al 15-11.
- `session-metrics`: precios nuevos de modelos, v1.95.
- `managed-pentesting-with-strix`: un párrafo reescrito.
- `design-is`: dos títulos suavizados.

Las demás eran idénticas. Antes de copiar, revisores independientes leyeron entero el código de las seis skills
nuevas y el de los cambios. No encontraron accesos a la red fuera de los indicados abajo, ni uso de claves,
telemetría, órdenes automáticas o instrucciones escondidas. Ese código no se ha ejecutado en tu PC: el control de
seguridad de Claude Code no deja ejecutar programas recién bajados sin que tú lo apruebes, así que la primera vez
que uses una de estas skills te pedirá permiso.

## Lo que cada una necesita para funcionar

Copiar la carpeta instala las instrucciones, no los programas de los que
dependen. Salvo donde se dice que ya está, nada de lo siguiente está instalado:

- **`trader-*`**: Python 3.8+, sin dependencias. Operan acciones de EE. UU. con
  datos públicos; `trader-broker` prepara órdenes para Alpaca o Interactive
  Brokers, en paper por defecto y solo tras tu "sí" para cada orden.
- **`free-llm-apis`**: nada. Es una guía para conseguir claves gratuitas de
  proveedores de LLM.
- **`agent-reach`**: el programa `agent-reach`, instalado con `uv` desde GitHub:
  `uv tool install https://github.com/Panniantong/Agent-Reach/releases/latest/download/agent-reach.zip`
  y después `agent-reach setup`. **No uses `pip install agent-reach`**: el paquete
  de ese nombre en PyPI no es este proyecto, según avisa su propio autor. Lee
  contenido de X, Reddit, YouTube y otras plataformas; algunas piden que
  inicies sesión tú. `SKILL.md` está en chino y `SKILL_en.md` en inglés.
- **`ai-image-creator`**: `uv` y una clave de OpenRouter.
- **`consult-codex`** y **`consult-zai`**: la CLI de Codex y una cuenta de
  z.ai, respectivamente.
- **`session-metrics`** y **`audit-session-metrics`**: Python. Analizan los
  registros de tus sesiones de Claude Code. El modo de comparación de modelos
  de `session-metrics` lanza otra sesión de Claude con
  `--permission-mode bypassPermissions`: no lo uses sin leer antes
  `references/model-compare.md`.
- **Las 9 de Strix**: la CLI `strix` (su instalador es
  `curl -sSL https://strix.ai/install | bash`, o `pipx install strix-agent`),
  Docker y una clave de LLM. Son pruebas de intrusión: úsalas solo contra
  sistemas tuyos o para los que tengas autorización por escrito.
- **`wowerpoint`**: una cuenta de NotebookLM.
- **`backtest-expert`**: Python, sin dependencias ni red. Puntúa un backtest a partir de sus cifras (operaciones,
  acierto, ganancia y pérdida media, caída máxima, años, parámetros) y lo bloquea si tiene pocas operaciones,
  esperanza negativa, menos de 5 años o costes sin probar. Dale solo cifras del examen fuera de muestra. Su
  "DEPLOY" no autoriza dinero real: aquí manda el protocolo de `ESTADO.md`.
- **`residual-edge-analyzer`**: Python, sin dependencias ni red. Separa la rentabilidad de una estrategia en lo
  que explica una referencia elegida antes (el mercado, comprar y mantener BTC) y lo que queda como ventaja
  propia. Necesita una serie de rentabilidades con fecha: al menos 60 observaciones.
- **`crypto-regime-analyzer`**: Python y `requests`, que ya está instalado. Lee datos públicos sin clave de
  `api.coingecko.com` y de `fapi.binance.com` (este último responde 451 desde la nube). La primera vez de cada
  día tarda de 2 a 4 minutos por el límite gratuito de CoinGecko. Sus zonas son heurísticas sin validar: no se
  conecta a los bots sin un examen registrado antes.
- **`market-breadth-analyzer`**: Python, `requests` y `tzdata`, ya instalados. Lee el CSV público del autor en
  `tradermonty.github.io`, con datos diarios desde 2016 del porcentaje del S&P 500 sobre su media de 200
  sesiones.
- **`catalyst-calendar`** y **`earnings-preview`**: solo instrucciones, sin programas. Usan la búsqueda web de
  Claude. Las referencias de pago que mencionan (Bloomberg, FactSet) no hacen falta; para fechas oficiales,
  `utils/public_data.py` ya da la Reserva Federal, la BLS y los 8-K de la SEC.
- **Dónde dejan sus archivos las cuatro de tradermonty.** Por defecto escriben en `reports/` y
  `.crypto_regime_cache/` dentro de la carpeta desde la que se lanzan. Las dos están en `.gitignore`, pero es
  mejor pasarles `--output-dir data/reports` (y `--cache-dir data/cache` a la de cripto). Sus `SKILL.md` dicen
  `skills/...`: aquí la ruta es `.claude/skills/...` y se ejecutan con `venv/Scripts/python`.

## Lo que no se copió, y por qué

- **La memoria de claude-mem.** No es una skill sino un plugin: funciona con
  seis hooks que se ejecutan en cada sesión y un servicio en segundo plano que
  guarda y resume lo que Claude hace. Solo se copiaron las 8 skills del plugin
  que funcionan sin ese servicio; las otras 22 fallarían. Para tener la memoria
  hay que instalar el plugin:

  ```
  /plugin marketplace add thedotmack/claude-mem
  /plugin install claude-mem
  ```

  Antes de hacerlo en este proyecto, ten en cuenta que registra las entradas y
  salidas de las herramientas: si alguna vez Claude lee tu `.env`, las claves
  del exchange quedarían en su base de datos local. Además, el instalador
  alternativo (`npx claude-mem install`) pide iniciar sesión y ofrece un
  "observer" con 30 días de prueba que exige un método de pago, y la telemetría
  anónima viene activada (`npx claude-mem telemetry disable` la apaga).
- **El resto de my-claude-code-setup**: plantillas de `CLAUDE.md`, comandos,
  subagentes, hooks y `settings.json`. No son skills, y los hooks y ajustes
  cambiarían el comportamiento de Claude Code en este repositorio.
- **La aplicación web y los 158 MB de datos de congress-trading-monitor.** El
  repositorio no trae ninguna skill; la que hay aquí consulta sus datos
  públicos directamente.
- **Las pruebas y datos de ejemplo de day-trader-skill** (1 MB).
- **La actualización de `agent-reach` (24-11, `8d9ecc1`).** Ahora manda a Claude a usar primero un servidor MCP
  "agent-reach" que no aparece en el repositorio (el propio proyecto quitó su servidor MCP en la versión 1.6.0),
  y en ese caso deja de avisar de que usa la skill. No aporta nada aquí, porque ni el programa ni ese MCP están
  instalados, y la copia anterior es más transparente.
- **Las 4 skills nuevas de [Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill)**
  (`trader-copycat`, `trader-rebalance`, `trader-screener` y `trader-tax`). Son para invertir a largo plazo con
  una cartera real: copiar las carteras declaradas de otros inversores, rebalancear y llevar los impuestos de
  EE. UU. Aquí las acciones solo se simulan y los bots operan a corto plazo. Sus otras 7 skills son la versión
  antigua, "de inversión", de las `trader-*` de day trading, y reemplazarlas rompería el flujo de day trading.
- **Las otras 70 skills de tradermonty/claude-trading-skills.** La mayoría necesitan claves de pago (Financial
  Modeling Prep, FINVIZ Elite) o duplican las `trader-*`, y algunas leen finviz.com haciéndose pasar por un
  navegador. Cada skill instalada carga su descripción en todas las conversaciones, así que solo se copiaron
  las cuatro más útiles para este proyecto.
- **El resto de anthropics/financial-services-plugins.** Son entregables de banca de inversión (modelos DCF y
  LBO, presentaciones, conciliaciones) o necesitan conectores de pago (S&P Global, LSEG, FactSet).

Para actualizar una skill, vuelve a copiar su carpeta desde el origen.
