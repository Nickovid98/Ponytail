# Skills de terceros

Claude Code detecta estas skills al trabajar dentro de `trading_system/` y cada
una se invoca como `/<nombre-de-carpeta>`. Todas son copias sin modificar de su
repositorio de origen, salvo `congress-trading-monitor`, que se escribió aquí.

Ninguna forma parte del motor de trading (`main.py`): son herramientas que
Claude usa contigo en una conversación.

| Origen | Commit | Licencia | Skills |
| --- | --- | --- | --- |
| [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | `3d0f4ef` | MIT | las 11 `trader-*` |
| [mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis) | `095e35e` | CC0 | `free-llm-apis` |
| [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) | `c70ae6d` | MIT | `agent-reach` |
| [centminmod/my-claude-code-setup](https://github.com/centminmod/my-claude-code-setup) | `c0e7592` | MIT | `ai-image-creator`, `audit-session-metrics`, `claude-docs-consultant`, `consult-codex`, `consult-zai`, `session-metrics`, `task-breakdown` |
| [usestrix/strix](https://github.com/usestrix/strix) | `685bbc8` | Apache-2.0 | `api-security-testing`, `application-security-testing`, `ci-security-scanning-with-strix`, `find-security-vulnerabilities-in-code`, `fix-security-vulnerabilities-with-strix`, `managed-pentesting-with-strix`, `owasp-top-10-testing`, `penetration-testing-with-strix`, `web-app-penetration-testing` |
| [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | `eee6ef2` | Apache-2.0 | `babysit`, `design-is`, `do`, `learn-codebase`, `make-plan`, `pathfinder`, `what-the`, `wowerpoint` |
| [kadoa-org/congress-trading-monitor](https://github.com/kadoa-org/congress-trading-monitor) | `49f1d36` | MIT (datos) | `congress-trading-monitor` (escrita aquí) |

Los textos de licencia están en esta carpeta: `LICENSE-<origen>` y
`NOTICE-claude-mem`.

## Lo que cada una necesita para funcionar

Copiar la carpeta instala las instrucciones, no los programas de los que
dependen. Nada de lo siguiente está instalado:

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

Para actualizar una skill, vuelve a copiar su carpeta desde el origen.
