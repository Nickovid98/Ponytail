"""Datos públicos oficiales, gratis y sin clave, con caché en data/public/ (fuera de git).

- SEC (EDGAR): ticker → CIK, todas las presentaciones de cada empresa (tipo, fecha y hora de aceptación y puntos
  del 8-K: el 2.02 es el comunicado de resultados) y los conjuntos trimestrales de los formularios 3, 4 y 5 (las
  compras y ventas que declaran sus consejeros y directivos).
- State Street (SSGA): las acciones de sus fondos del S&P 500 (SPY), 400 (MDY) y 600 (SPSM), con fecha de hoy.
- Reserva Federal: reuniones del FOMC. Oficina de Estadísticas Laborales (BLS): IPC y empleo.
- Yahoo: precios diarios desde 2009, ajustados por splits y dividendos, y velas de 1 hora de los últimos 730 días.
- Políticos: lo que declaran congresistas y senadores (STOCK Act), del archivo abierto de Kadoa, sacado de las páginas
  de la Cámara y el Senado. Grandes inversores: los 13F de la SEC de 26 gestores conocidos (Buffett, Ackman, Burry...).
  Para traducir el CUSIP de cada acción a su ticker: los fondos de State Street y las entregas fallidas de la SEC.
"""
import datetime as dt
import io
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
import requests

import config

DIR = config.PUBLIC_DATA_DIR
# ponytail: la SEC pide un User-Agent con un contacto. Va uno genérico, sin datos del usuario. Techo: si algún día lo
# bloquea (403), hay que poner otro contacto aquí.
SEC_UA = "TradingSystemResearch research@example.com"
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
NY = "America/New_York"
_http = requests.Session()
_last: dict[str, float] = {}
PACE = {"sec": 0.12, "yahoo": 0.35, "web": 0.0}  # la SEC admite 10 peticiones por segundo; Yahoo corta si se abusa


def fetch(url: str, kind: str = "web", tries: int = 5) -> bytes:
    """GET con pausa entre peticiones del mismo servidor y reintentos ante 429/5xx o la red caída."""
    for n in range(tries):
        wait = PACE[kind] - (time.monotonic() - _last.get(kind, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last[kind] = time.monotonic()
        try:
            r = _http.get(url, headers={"User-Agent": SEC_UA if kind == "sec" else WEB_UA}, timeout=60)
        except requests.RequestException:
            time.sleep(5 * (n + 1))
            continue
        if r.status_code == 200:
            return r.content
        if r.status_code == 404:
            raise FileNotFoundError(url)
        time.sleep(60 if r.status_code == 429 else 5 * (n + 1))
    raise ConnectionError(f"sin respuesta tras {tries} intentos: {url}")


def _fresh(path, days: float) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < days * 86400


def _save(df: pd.DataFrame, path) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


# --- Lista de acciones -----------------------------------------------------------------------------------------

FUNDS = {"500": "spy", "400": "mdy", "600": "spsm"}
SSGA = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{}.xlsx"


def _xlsx_rows(data: bytes) -> list[dict[str, str]]:
    """Filas de la primera hoja de un .xlsx como {columna: texto}, sin depender de openpyxl."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(ns + "t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si")]
    rows = []
    for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(ns + "row"):
        cells = {}
        for c in row.iter(ns + "c"):
            v, inline = c.find(ns + "v"), c.find(ns + "is")
            text = (shared[int(v.text)] if c.get("t") == "s" and v is not None else v.text if v is not None
                    else "".join(t.text or "" for t in inline.iter(ns + "t")) if inline is not None else "")
            cells[re.sub(r"\d", "", c.get("r", ""))] = (text or "").strip()
        rows.append(cells)
    return rows


def universe(days: float = 30) -> pd.DataFrame:
    """Acciones del S&P 1500 de hoy: ticker (formato de Yahoo y la SEC: BRK-B), grupo (500, 400 o 600) y nombre."""
    path = DIR / "universe.parquet"
    if _fresh(path, days):
        return pd.read_parquet(path)
    out = []
    for group, fund in FUNDS.items():
        rows = _xlsx_rows(fetch(SSGA.format(fund)))
        head = next(i for i, r in enumerate(rows) if "Ticker" in r.values())
        col = {name: letter for letter, name in rows[head].items()}
        for r in rows[head + 1:]:
            ticker = r.get(col["Ticker"], "")
            if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", ticker) and "CASH" not in r.get(col["Name"], "").upper():
                out.append({"ticker": ticker.replace(".", "-"), "group": group, "name": r.get(col["Name"], "")})
    df = pd.DataFrame(out).drop_duplicates("ticker")
    return _save(df.reset_index(drop=True), path)


def ciks(days: float = 30) -> dict[str, int]:
    """Ticker → CIK de la SEC."""
    path = DIR / "sec_tickers.json"
    if not _fresh(path, days):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch("https://www.sec.gov/files/company_tickers.json", "sec"))
    return {v["ticker"].upper(): int(v["cik_str"]) for v in json.loads(path.read_text()).values()}


# --- Presentaciones en la SEC -------------------------------------------------------------------------------------

SUB_COLS = ["accessionNumber", "filingDate", "acceptanceDateTime", "form", "items"]


def filings(cik: int, since: str = "2009-01-01", days: float = 1) -> pd.DataFrame:
    """Todas las presentaciones de una empresa desde `since`: número, fecha, aceptación (UTC), tipo y puntos."""
    path = DIR / "sec" / f"{cik}.parquet"
    if _fresh(path, days):
        return pd.read_parquet(path)
    j = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", "sec"))
    parts = [j["filings"]["recent"]]
    for f in j["filings"].get("files", []):
        if f.get("filingTo", "9999") >= since:
            parts.append(json.loads(fetch("https://data.sec.gov/submissions/" + f["name"], "sec")))
    df = pd.concat([pd.DataFrame({c: p.get(c, []) for c in SUB_COLS}) for p in parts], ignore_index=True)
    df = df[df.filingDate >= since].copy()
    df["accepted"] = pd.to_datetime(df.acceptanceDateTime, utc=True, errors="coerce")  # la SEC la da en UTC
    return _save(df.drop(columns="acceptanceDateTime").reset_index(drop=True), path)


def earnings(cik: int, days: float = 1) -> pd.Series:
    """Hora (UTC) de cada comunicado de resultados: 8-K con el punto 2.02. Si hay dos en menos de 20 días, el
    primero (los demás suelen ser correcciones o avances)."""
    f = filings(cik, days=days)
    f = f[(f.form == "8-K") & f["items"].fillna("").str.contains(r"(?:^|,)\s*2\.02")].dropna(subset=["accepted"])
    out, last = [], None
    for t in sorted(f.accepted):
        if last is None or t - last > pd.Timedelta(days=20):
            out.append(t)
        last = t
    return pd.Series(out, dtype="datetime64[ns, UTC]")


INSIDER = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{}q{}_form345.zip"


def insider(year: int, quarter: int) -> pd.DataFrame:
    """Compras (P) y ventas (S) en bolsa declaradas en formularios 4 de un trimestre: empresa (CIK), fecha de
    presentación, quién (CIK), si es consejero o directivo, código e importe en USD. El zip se lee en memoria y
    solo se guarda este resumen."""
    path = DIR / "insider" / f"{year}q{quarter}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    z = zipfile.ZipFile(io.BytesIO(fetch(INSIDER.format(year, quarter), "sec")))

    def tsv(name, cols):
        return pd.read_csv(z.open(name), sep="\t", usecols=cols, dtype=str, quoting=3, encoding_errors="replace")

    sub = tsv("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"])
    own = tsv("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"])
    tr = tsv("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_ACQUIRED_DISP_CD", "TRANS_SHARES",
                                    "TRANS_PRICEPERSHARE"])
    tr = tr[((tr.TRANS_CODE == "P") & (tr.TRANS_ACQUIRED_DISP_CD == "A"))
            | ((tr.TRANS_CODE == "S") & (tr.TRANS_ACQUIRED_DISP_CD == "D"))].copy()
    tr["value"] = pd.to_numeric(tr.TRANS_SHARES, errors="coerce") * pd.to_numeric(tr.TRANS_PRICEPERSHARE, errors="coerce")
    tr = tr.groupby(["ACCESSION_NUMBER", "TRANS_CODE"], as_index=False).value.sum()
    rel = own.assign(insider=own.RPTOWNER_RELATIONSHIP.fillna("").str.contains("Director|Officer"))
    rel = rel.groupby("ACCESSION_NUMBER").agg(owner=("RPTOWNERCIK", "first"), insider=("insider", "any")).reset_index()
    sub = sub[sub.DOCUMENT_TYPE == "4"]
    df = tr.merge(sub, on="ACCESSION_NUMBER").merge(rel, on="ACCESSION_NUMBER")
    df = pd.DataFrame({"issuer": pd.to_numeric(df.ISSUERCIK, errors="coerce"),
                       "filed": pd.to_datetime(df.FILING_DATE, format="%d-%b-%Y", errors="coerce"),
                       "owner": pd.to_numeric(df.owner, errors="coerce"), "insider": df.insider,
                       "code": df.TRANS_CODE, "value": df.value}).dropna(subset=["issuer", "filed", "owner"])
    return _save(df.astype({"issuer": "int64", "owner": "int64"}).reset_index(drop=True), path)


FRAMES = "https://data.sec.gov/api/xbrl/frames/dei/EntityPublicFloat/USD/CY{}Q{}I.json"


def floats(first: int = 2008, days: float = 30) -> pd.DataFrame:
    """Valor en bolsa en manos del público de TODAS las empresas que presentan en la SEC (también las que luego
    quebraron o dejaron de cotizar), con la fecha a la que se refiere: cik, end, val (USD). Sale de la portada de
    cada informe anual; la SEC lo agrupa por trimestre natural."""
    path = DIR / "floats.parquet"
    if _fresh(path, days):
        return pd.read_parquet(path)
    rows, today = [], dt.date.today()
    for year in range(first, today.year + 1):
        for q in range(1, 5):
            if dt.date(year, 3 * q, 1) > today:
                break
            try:
                rows += json.loads(fetch(FRAMES.format(year, q), "sec"))["data"]
            except FileNotFoundError:
                continue  # trimestre sin datos todavía
    df = pd.DataFrame(rows)[["cik", "end", "val"]]
    df["end"] = pd.to_datetime(df.end)
    return _save(df.astype({"cik": "int64", "val": "float64"}), path)


# --- Precios ---------------------------------------------------------------------------------------------------

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval={}&{}"
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def price_file(kind: str, ticker: str):
    """Archivo de la caché de precios. Windows no deja abrir archivos con nombres reservados (CON es una acción)."""
    return DIR / kind / f"{ticker}{'_' if ticker.upper() in RESERVED else ''}.parquet"


def _chart(ticker: str, interval: str, span: str) -> dict:
    r = json.loads(fetch(YAHOO.format(ticker, interval, span), "yahoo"))["chart"]["result"][0]
    return r


def daily(ticker: str, start: str = "2008-06-01", days: float = 1) -> pd.DataFrame:
    """Velas diarias desde `start`, por fecha de Nueva York: apertura y cierre (ajustados por splits) y cierre
    ajustado también por dividendos (`adj`, el que sirve para medir rentabilidades)."""
    path = price_file("daily", ticker)
    if _fresh(path, days):
        return pd.read_parquet(path)
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    r = _chart(ticker, "1d", f"period1={p1}&period2={int(time.time())}&events=div%2Csplit")
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "close": q["close"], "adj": r["indicators"]["adjclose"][0]["adjclose"]},
                      index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert(NY).normalize().tz_localize(None))
    df = df[~df.index.duplicated(keep="last")].dropna()
    return _save(df.astype("float64"), path)


def hourly(ticker: str, days: float = 1) -> pd.DataFrame:
    """Velas de 1 hora de la sesión regular de los últimos 730 días (la primera abre a las 9:30 de Nueva York)."""
    path = price_file("hourly", ticker)
    if _fresh(path, days):
        return pd.read_parquet(path)
    r = _chart(ticker, "1h", "range=730d")
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")},
                      index=pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert(NY)).dropna()
    df = df[~df.index.duplicated(keep="last")]
    return _save(df.astype("float64"), path)


# --- Lo que compran los políticos y los grandes inversores -----------------------------------------------------

# Operaciones que congresistas y senadores declaran por la ley STOCK Act, recopiladas de las páginas oficiales de la
# Cámara y el Senado por kadoa-org/congress-trading-monitor (licencia MIT, se renueva cada día). El laboratorio usa
# una versión fija (la del 25-11-2026) para que sus cifras se puedan repetir; la app lee la del día.
# ponytail: el archivo no trae a los senadores que dejaron el cargo antes de 2025 ni a los congresistas que
# declaraban en papel, y antes de 2019 tiene menos de la mitad de los informes de la Cámara. Techo: lo que se mida
# con él vale para los que siguen en el cargo (ESTADO.md, "Tercera ronda").
KADOA = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/{}/public/data/"
KADOA_PIN = "1d6be68f79ea6a1482e41f186ee44717e3500de6"
CONGRESS_COLS = ["filer", "name", "branch", "chamber", "party", "state", "traded", "filed", "ticker", "asset",
                 "asset_type", "kind", "owner", "usd_low", "usd_high", "doc"]


def _congress_rows(filer: dict, trades: list[dict]) -> list[dict]:
    return [{"filer": filer.get("id"), "name": filer.get("full_name") or t.get("filer_name"),
             "branch": filer.get("branch") or t.get("branch"), "chamber": filer.get("chamber") or t.get("chamber"),
             "party": filer.get("party") or t.get("party"), "state": filer.get("state") or t.get("state"),
             "traded": t.get("transaction_date"), "filed": t.get("filing_date"),
             "ticker": (t.get("ticker") or "").strip().upper(), "asset": t.get("asset_name") or "",
             "asset_type": t.get("asset_type") or "", "kind": t.get("transaction_type") or "",
             "owner": t.get("owner") or "", "usd_low": t.get("amount_range_low"),
             "usd_high": t.get("amount_range_high"), "doc": t.get("doc_url") or ""} for t in trades]


def _congress_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=CONGRESS_COLS)
    for c in ("traded", "filed"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("usd_low", "usd_high"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["filed"]).reset_index(drop=True)


def congress(rev: str = KADOA_PIN) -> pd.DataFrame:
    """Todas las operaciones declaradas por los 373 miembros del Congreso de esa versión del archivo: quién (cámara,
    partido y estado), fecha de la operación y de la declaración (la que la hace pública), ticker, activo, tipo
    (Purchase, Sale (Full), Sale (Partial) o Exchange), titular (él, cónyuge, conjunta o hijos) e importe (un rango).
    Una versión fija no cambia: se guarda para siempre."""
    path = DIR / "congress" / f"{rev[:12]}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    base, rows = KADOA.format(rev), []
    for f in json.loads(fetch(base + "filers.json")):
        if f.get("branch") == "congress":
            rows += _congress_rows(f, json.loads(fetch(base + f"filer/{f['id']}.json"))["trades"])
    return _save(_congress_frame(rows), path)


def congress_latest(days: float = 1) -> pd.DataFrame:
    """Las 5.000 últimas operaciones declaradas, del Congreso y de los altos cargos del Gobierno, en la versión del día."""
    path = DIR / "congress" / "latest.parquet"
    if _fresh(path, days):
        return pd.read_parquet(path)
    base = KADOA.format("main")
    filers = {f["id"]: f for f in json.loads(fetch(base + "filers.json"))}
    rows = [r for t in json.loads(fetch(base + "trades.json"))
            for r in _congress_rows(filers.get(t.get("filer_id")) or {"id": t.get("filer_id")}, [t])]
    return _save(_congress_frame(rows), path)


# Los 13F: cada trimestre, quien gestiona más de 100 millones en acciones de EE. UU. declara en la SEC lo que tenía
# al cierre del trimestre, como muy tarde 45 días después. Gestores conocidos y las CIK con las que presentan,
# también sus predecesoras y sucesoras, comprobadas en EDGAR el 26-11-2026 (ESTADO.md, "Tercera ronda").
MANAGERS = {
    "Berkshire Hathaway (Warren Buffett)": (1067983,),
    "Pershing Square (Bill Ackman)": (1336528, 2026053),
    "Scion (Michael Burry)": (1649339,),
    "Appaloosa (David Tepper)": (1006438, 1656456),
    "Baupost (Seth Klarman)": (1061768,),
    "Greenlight (David Einhorn)": (1079114, 1489933),
    "Third Point (Dan Loeb)": (1040273,),
    "Carl Icahn": (921669,),
    "Duquesne (Stanley Druckenmiller)": (1536411,),
    "Soros Fund Management (George Soros)": (1029160,),
    "Tiger Global (Chase Coleman)": (1167483,),
    "Lone Pine (Stephen Mandel)": (1061165,),
    "Viking Global (Andreas Halvorsen)": (1103804,),
    "Coatue (Philippe Laffont)": (1135730,),
    "Elliott (Paul Singer)": (1048445, 1791786),
    "Akre Capital (Chuck Akre)": (1112520,),
    "Himalaya (Li Lu)": (1709323,),
    "ValueAct (Jeff Ubben)": (1418814,),
    "Trian (Nelson Peltz)": (1345471,),
    "Glenview (Larry Robbins)": (1138995,),
    "Maverick (Lee Ainslie)": (934639,),
    "Fairholme (Bruce Berkowitz)": (1056831,),
    "Gotham (Joel Greenblatt)": (1510387,),
    "Oaktree (Howard Marks)": (949509,),
    "Bridgewater (Ray Dalio)": (1350694,),
    "Renaissance Technologies (Jim Simons)": (1037389,),
}
F13_FROM = "2013-05-20"  # desde ese día los 13F son XML; antes, texto libre
F13_COLS = ["cusip", "issuer", "title", "shares", "value"]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_rows(data: bytes, row: str) -> list[dict[str, str]]:
    """Cada elemento `row` de un XML como {etiqueta: texto} de todo lo que lleva dentro, sin espacios de nombres."""
    return [{_local(x.tag): (x.text or "").strip() for x in el.iter()}
            for el in ET.fromstring(data).iter() if _local(el.tag) == row]


def _number(text: str) -> float:
    return float(re.sub(r"[^\d.]", "", text or "") or 0)


def filing13f(cik: int, accession: str, accepted: pd.Timestamp, form: str) -> pd.DataFrame:
    """Acciones de un 13F (una fila por CUSIP): acciones y valor en USD. Fuera las opciones (putCall) y los bonos
    (PRN). Hasta el 03-01-2023 el valor venía en miles de USD. De las enmiendas solo cuentan las que añaden
    posiciones ("NEW HOLDINGS", las que el gestor pidió mantener en secreto); una corrección ("RESTATEMENT") llega
    tarde y no se usa: lo público en su momento era el original."""
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
    names = [i["name"] for i in json.loads(fetch(base + "index.json", "sec"))["directory"]["item"]
             if i["name"].lower().endswith(".xml")]
    primary = next((n for n in names if n.lower() == "primary_doc.xml"), None)
    table = next((n for n in names if n != primary), None)
    empty = pd.DataFrame(columns=F13_COLS)
    if form != "13F-HR":
        kind = next((r.get("amendmentType", "") for r in _xml_rows(fetch(base + primary, "sec"), "amendmentInfo")), "") \
            if primary else ""
        if "NEW HOLDINGS" not in kind.upper():
            return empty
    if table is None:
        return empty
    scale = 1.0 if accepted >= pd.Timestamp("2023-01-03", tz="UTC") else 1000.0
    rows = [{"cusip": r.get("cusip", "").upper(), "issuer": r.get("nameOfIssuer", ""), "title": r.get("titleOfClass", ""),
             "shares": _number(r.get("sshPrnamt")), "value": _number(r.get("value")) * scale}
            for r in _xml_rows(fetch(base + table, "sec"), "infoTable")
            if not r.get("putCall") and r.get("sshPrnamtType", "SH").upper() == "SH"]
    if not rows:
        return empty
    return pd.DataFrame(rows).groupby("cusip", as_index=False).agg(
        issuer=("issuer", "first"), title=("title", "first"), shares=("shares", "sum"), value=("value", "sum"))


def holdings13f(cik: int, days: float = 7) -> pd.DataFrame:
    """Las posiciones de cada 13F-HR de un gestor desde que son XML, y las de sus enmiendas que añaden posiciones:
    número de presentación, aceptación (UTC: desde ese momento es público), periodo, si es enmienda, CUSIP, emisor,
    clase, acciones y valor en USD. Cada 13F se guarda para siempre (no cambia); la lista se renueva cada `days` días."""
    folder = DIR / "13f" / str(cik)
    listing = folder / "filings.parquet"
    if _fresh(listing, days):
        subs = pd.read_parquet(listing)
    else:
        j = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", "sec"))
        parts = [j["filings"]["recent"]] + [json.loads(fetch("https://data.sec.gov/submissions/" + f["name"], "sec"))
                                            for f in j["filings"].get("files", []) if f.get("filingTo", "9999") >= F13_FROM]
        cols = ["accessionNumber", "acceptanceDateTime", "reportDate", "form"]
        subs = pd.concat([pd.DataFrame({c: p.get(c, []) for c in cols}) for p in parts], ignore_index=True)
        subs = subs[subs.form.isin(["13F-HR", "13F-HR/A"])].copy()
        subs["accepted"] = pd.to_datetime(subs.acceptanceDateTime, utc=True, errors="coerce")
        subs = subs[subs.accepted >= pd.Timestamp(F13_FROM, tz="UTC")].drop(columns="acceptanceDateTime")
        subs = _save(subs.drop_duplicates("accessionNumber").reset_index(drop=True), listing)
    out = []
    for acc, accepted, period, form in zip(subs.accessionNumber, subs.accepted, subs.reportDate, subs.form):
        path = folder / f"{acc}.parquet"
        df = pd.read_parquet(path) if path.exists() else _save(filing13f(cik, acc, accepted, form), path)
        out.append(df.assign(accession=acc, accepted=accepted, period=pd.to_datetime(period, errors="coerce"),
                             amendment=form != "13F-HR"))
    cols = F13_COLS + ["accession", "accepted", "period", "amendment"]
    return pd.concat(out, ignore_index=True)[cols] if out else pd.DataFrame(columns=cols)


def cusips(days: float = 30) -> dict[str, str]:
    """CUSIP → ticker (formato de Yahoo) de las acciones de hoy del S&P 1500, según los fondos de State Street."""
    path = DIR / "cusips.json"
    if not _fresh(path, days):
        out = {}
        for fund in FUNDS.values():
            rows = _xlsx_rows(fetch(SSGA.format(fund)))
            head = next(i for i, r in enumerate(rows) if "Ticker" in r.values())
            col = {name: letter for letter, name in rows[head].items()}
            for r in rows[head + 1:]:
                ticker, cusip = r.get(col["Ticker"], ""), r.get(col.get("Identifier", "?"), "").upper()
                if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", ticker) and re.fullmatch(r"[0-9A-Z]{9}", cusip):
                    out[cusip] = ticker.replace(".", "-")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return json.loads(path.read_text())


FTD_PAGE = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"


def ftd_links(days: float = 30) -> dict[str, str]:
    """AAAAMMa / AAAAMMb (primera o segunda quincena) → dirección del archivo de entregas fallidas de la SEC."""
    path = DIR / "ftd" / "links.json"
    if not _fresh(path, days):
        html = fetch(FTD_PAGE, "sec").decode("utf-8", "replace")
        found = {m.group(2): "https://www.sec.gov" + m.group(1)
                 for m in re.finditer(r'href="(/files/[^"]*cnsfails(\d{6}[ab])\.zip)"', html)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(found))
    return json.loads(path.read_text())


def ftd_symbols(month: str) -> pd.DataFrame:
    """Parejas CUSIP-símbolo de la primera quincena de `month` (AAAAMM) en las entregas fallidas de la SEC: dan el
    símbolo que tenía cada CUSIP en esa fecha, también el de empresas que después cambiaron de nombre o de ticker."""
    path = DIR / "ftd" / f"{month}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    url = ftd_links().get(month + "a")
    if url is None:
        raise FileNotFoundError(month)
    z = zipfile.ZipFile(io.BytesIO(fetch(url, "sec")))
    df = pd.read_csv(z.open(z.namelist()[0]), sep="|", dtype=str, quoting=3, encoding_errors="replace",
                     on_bad_lines="skip")
    df = df.rename(columns=lambda c: c.strip().upper())[["CUSIP", "SYMBOL"]].dropna()
    df = df[df.CUSIP.str.fullmatch(r"[0-9A-Z]{9}") & df.SYMBOL.str.fullmatch(r"[A-Z0-9.\-]{1,8}")]
    return _save(df.drop_duplicates().rename(columns=str.lower).reset_index(drop=True), path)


def states(cik_list: list[int], days: float = 365) -> dict[int, str]:
    """Estado de EE. UU. (o país) de la sede de cada empresa, según la dirección de negocio de su ficha en la SEC."""
    path = DIR / "states.json"
    known = json.loads(path.read_text()) if _fresh(path, days) else {}
    missing = [c for c in cik_list if str(c) not in known]
    for i, cik in enumerate(missing):
        try:
            j = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", "sec"))
            known[str(cik)] = ((j.get("addresses") or {}).get("business") or {}).get("stateOrCountry") or ""
        except FileNotFoundError:
            known[str(cik)] = ""
        if i % 100 == 99 or i == len(missing) - 1:  # cada 100: un corte de red no hace repetir todo
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(known))
    return {int(k): v for k, v in known.items()}


# --- Calendario oficial -----------------------------------------------------------------------------------------

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                           "September", "October", "November", "December"])}
# ponytail: la BLS bloquea las descargas automáticas (403), así que sus fechas se copiaron a mano de bls.gov el 23-11-2026
# (calendarios del IPC y de "Employment Situation"; todas a las 8:30 de Nueva York). Techo: acaban en diciembre de
# 2027; antes de esa fecha hay que copiar las del año siguiente.
BLS = {
    "IPC de EE. UU. (BLS)": ["2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12",
                             "2027-06-10", "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10",
                             "2027-12-10"],
    "Empleo de EE. UU. (BLS)": ["2026-12-04", "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07",
                                "2027-06-04", "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05",
                                "2027-12-03"],
}


def fomc_dates(html: str) -> list[dt.date]:
    """Último día de cada reunión programada del FOMC (el comunicado sale a las 14:00 de Nueva York)."""
    out = []
    for year, block in re.findall(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", html, re.S):
        for month, days in re.findall(r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?"
                                      r"fomc-meeting__date[^>]*>([^<]+)<", block, re.S):
            m = re.fullmatch(r"\s*(\d{1,2})-(\d{1,2})\*?\s*", days)
            last = month.split("/")[-1].strip()
            if m and last in MONTHS:
                out.append(dt.date(int(year), MONTHS[last], int(m.group(2))))
    return sorted(set(out))


def calendar(days: float = 7) -> list[tuple[dt.datetime, str]]:
    """(hora UTC, nombre) de los anuncios de alto impacto: FOMC (de la web de la Reserva Federal, se renueva cada
    semana y si no responde se usa la última copia) e IPC y empleo de la BLS."""
    path = DIR / "fomc.json"
    if not _fresh(path, days):
        try:
            found = fomc_dates(fetch(FOMC_URL, tries=1).decode("utf-8", "replace"))
            if found:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps([d.isoformat() for d in found]))
        except (ConnectionError, FileNotFoundError):
            pass
    fomc = [dt.date.fromisoformat(d) for d in json.loads(path.read_text())] if path.exists() else []
    events = [(pd.Timestamp(f"{d} 14:00", tz=NY), "Decisión de tipos de la Reserva Federal (FOMC)") for d in fomc]
    events += [(pd.Timestamp(f"{d} 08:30", tz=NY), name) for name, dates in BLS.items() for d in dates]
    return sorted((t.tz_convert("UTC").to_pydatetime(), name) for t, name in events)
