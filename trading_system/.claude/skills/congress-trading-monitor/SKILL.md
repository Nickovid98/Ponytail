---
name: congress-trading-monitor
description: >-
  Consulta las operaciones bursátiles declaradas por congresistas, senadores y
  altos cargos del ejecutivo de EE. UU. (STOCK Act) en el dataset abierto de
  Kadoa. Úsala cuando el usuario pregunte qué compró o vendió un político,
  quién ha operado un ticker, cuáles son las últimas operaciones declaradas, o
  por declaraciones tardías.
---

# congress-trading-monitor

Lee el dataset abierto de [kadoa-org/congress-trading-monitor](https://github.com/kadoa-org/congress-trading-monitor)
(MIT): operaciones declaradas ante la Cámara, el Senado y la Oficina de Ética
Gubernamental (OGE). Son JSON estáticos; no hay API ni clave.

Esta skill solo consulta datos. No opera ni recomienda operar.

## Dónde están los datos

Base: `https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/`

| Archivo | Tamaño | Contenido |
| --- | --- | --- |
| `stats.json` | pequeño | Totales, rango de fechas (`dateRange`), retraso típico al declarar |
| `trades.json` | ~4 MB | Las 5 000 operaciones declaradas más recientes, de la más nueva a la más antigua por `filing_date` |
| `filers.json` | ~150 KB | Un registro por persona: `id`, `full_name`, `branch`, `chamber`, `party`, `state`, `office`, `trade_count`, `purchases`, `sales`, `late_filings`, `est_volume` |
| `tickers.json` | ~400 KB | Un registro por ticker: `ticker`, `trade_count`, `filer_count`, `purchases`, `sales`, `est_volume` |
| `filer/<id>.json` | mediana 17 KB (hasta 9 MB) | `{filer, trades}`: historial completo de una persona |
| `ticker/<TICKER>.json` | mediana 6 KB | `{ticker, price, history, lobbying, trades}`: todas las operaciones sobre un ticker |

Para una pregunta concreta, pide el archivo pequeño que la responde
(`filer/<id>.json` o `ticker/<TICKER>.json`) en vez de `trades.json`. Si hay que
cruzar muchos archivos, clona el repositorio (unos 160 MB) con
`git clone --depth 1` y consulta en local con `jq`.

## Campos de cada operación

`transaction_date`, `filing_date`, `days_to_file`, `is_late` (1 si superó los 45
días del STOCK Act), `ticker`, `asset_name`, `asset_type`, `transaction_type`
(`Purchase`, `Sale (Full)`, `Sale (Partial)`, `Exchange`), `owner` (titular,
cónyuge, conjunto o hijo: `Self`, `SP`/`Spouse`, `JT`/`Joint`, `DC`/`Child`),
`amount_range_low`, `amount_range_high`, `amount_range_label`, `filer_id`,
`filer_name`, `branch`, `chamber`, `party`, `state`, `office`, `doc_url` (el PDF
oficial), `ret_since` (rentabilidad en % desde la operación) y `excess_since`
(esa rentabilidad menos la de SPY).

## Cómo responder

1. **"¿Qué ha operado X?"** Busca a la persona en `filers.json` por `full_name`
   para obtener su `id` y lee `filer/<id>.json`.
2. **"¿Quién opera NVDA?"** Lee `ticker/NVDA.json` y agrupa `trades` por
   `filer_name` y `transaction_type`.
3. **"¿Qué se ha declarado últimamente?"** Lee `trades.json` y filtra por
   `filing_date`.

Ejemplos con el repositorio clonado:

```bash
cd congress-trading-monitor/public/data
jq -r '.[] | select(.full_name | test("pelosi"; "i")) | .id' filers.json
jq -r '.trades | sort_by(.transaction_date) | reverse | .[0:15][] |
  [.transaction_date, .filing_date, .transaction_type, .ticker, .amount_range_label] | @tsv' filer/<id>.json
jq -r '.trades | group_by(.filer_name) | map({n: .[0].filer_name, ops: length}) |
  sort_by(-.ops) | .[0:10][] | "\(.ops)\t\(.n)"' ticker/NVDA.json
```

## Lo que hay que decirle siempre al usuario

- **Fecha de los datos.** Cita el `dateRange.to` de `stats.json`: el dataset no
  se actualiza en tiempo real.
- **Retraso.** Entre la operación y su declaración pasan de mediana 30 días, y
  una de cada cuatro declaraciones llega tarde. Cuando se publica, el
  movimiento ya ocurrió.
- **Importes.** Son rangos (`$1,001 - $15,000`), no cifras exactas. Los
  volúmenes y rentabilidades derivados son estimaciones.
- **Cobertura.** El propio proyecto indica que faltan los PDF en papel del
  Senado anteriores a 2015 y la mayoría de los formularios de la OGE que solo
  se entregan bajo petición.
- Enlaza el `doc_url` de cada operación que cites, para que se pueda comprobar
  en la fuente oficial.
