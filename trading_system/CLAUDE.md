# trading_system: guía para Claude

Sistema de day trading multi-agente para cripto (CCXT) y futuros de Topstep (`BROKER=topstepx`). El detalle está en
`README.md`; esto es lo mínimo para trabajar sin releer el proyecto entero.

## Comandos

- Comprobar todo, sin red ni claves: `venv/bin/python selfcheck.py`. Debe pasar antes de cada commit.
- Entorno: `python -m venv venv && venv/bin/pip install -r requirements.txt`
- Un ciclo real: `python main.py --once` · motor: `python main.py` · panel: `streamlit run dashboard.py`
- Simulación histórica: `python backtest.py --days 30 [--evolution]`

## Reglas que no se rompen

- Nunca subir `.env`, `*.db`, `venv/` ni `data/`. Nunca poner claves en código, logs o commits.
- `PAPER_TRADING=True` es el valor por defecto. No cambiarlo ni tocar `CONFIRM_LIVE` salvo que el usuario lo pida.
- Riesgo y ejecución son deterministas: ningún LLM decide tamaños, stops ni órdenes.
- Límites del usuario: stop ≤ 5 %, riesgo 1 % por operación (techo 2 %), pérdida total 500 USDT sobre 3550.
- Todo PnL que se evalúe es neto de comisiones y deslizamiento.
- Cada cambio de lógica lleva su comprobación en `selfcheck.py`. Las simplificaciones se marcan con `ponytail:` y dicen su techo.
- No ejecutar código de repositorios externos sin revisarlo antes.
- Alpaca (`EXCHANGE_ID=alpaca`): solo datos y simulación; el motor no le envía órdenes.
- TopstepX: el motor solo corre en el ordenador del usuario. Nunca conectar a `api.topstepx.com` desde la nube
  ni desplegar en un VPS; aquí se prueba contra la API falsa de `selfcheck.py`. El kill switch (75 % del límite
  diario, techo 80 %) y la verificación del stop tras cada orden no se relajan.

## Mapa

- `agents/`: `Signal` (contrato), cinco agentes analíticos, `boss_agent.py` (decisión y ejecución), `evolution_manager.py` (muerte y renacimiento).
- `utils/`: `risk_manager` (stop, tamaño, costes), `trade_logger` (operaciones y genealogía en SQLite), `token_budget`, `cache_manager`, `notifier`, `exchange` (PaperBroker y CcxtBroker), `topstepx` (cliente REST, datos y brokers de futuros), `alpha_vantage`, `db`.
- `agents/orb_agent.py`: ruptura del rango de apertura (`STRATEGY=orb`); se entrena y examina con `backtest.py --walk-forward`.
- `main.py` orquesta; `optimizer.py` ajusta pesos; `backtest.py` simula; `dashboard.py` muestra y controla.

## Skills útiles aquí (`.claude/skills/`)

- Planificar un cambio grande: `/make-plan` y luego `/do`, o `/task-breakdown`.
- Coste y caché de la sesión: `/session-metrics`.
- Dudas sobre Claude Code: `/claude-docs-consultant`.
- Seguridad: la revisión integrada `/security-review`; las de Strix necesitan su CLI instalada.
- Acciones de EE. UU.: `/trader-*`. Operaciones del Congreso: `/congress-trading-monitor`.

## Sesiones

Una tarea, una sesión. Una conversación larga relee todo su historial en cada
paso; es mejor empezar una nueva y apoyarse en este archivo.
