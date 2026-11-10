# Skills de terceros: The Day Trader skill

Las once carpetas `trader-*` son una copia sin modificar de
[Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(commit `3d0f4ef`, v2.0.0, licencia MIT: ver `LICENSE-day-trader-skill`),
tomada del espejo `prashantranghar-sys/https-github.com-Jakeschincariol-day-trader-skill`,
que es idéntico byte a byte al original.

Claude Code las detecta al trabajar dentro de `trading_system/` y se invocan
como `/trader-scanner`, `/trader-news`, `/trader-entry`, `/trader-exit`,
`/trader-timemachine`, `/trader-budget`, `/trader-risk`, `/trader-broker`,
`/trader-alerts`, `/trader-analyst` y `/trader-pelosi`.

Son independientes del motor de este proyecto:

- Operan **acciones de EE. UU.** con datos públicos (Yahoo Finance, SEC EDGAR,
  Nasdaq Trader), no los futuros de cripto que opera `main.py`.
- Son herramientas que Claude usa contigo en una conversación; no corren solas
  ni forman parte del bucle de agentes.
- Guardan su estado en una carpeta `trader/`, no en `trades_history.db`.
- `trader-broker` puede preparar órdenes para Alpaca o Interactive Brokers,
  siempre en paper por defecto y solo tras tu "sí" explícito para cada orden.

No se copiaron las pruebas ni sus datos de ejemplo (1 MB): están en el
repositorio original. Para actualizar, vuelve a copiar las carpetas `trader-*`.
