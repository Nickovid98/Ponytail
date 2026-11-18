@echo off
setlocal
rem Cuenta de fondeo SIMULADA: 50.000 USD con cripto y acciones de EE. UU., en largo y en corto.
rem Precios reales y ninguna orden sale de este equipo. Estos valores mandan sobre .env solo en esta ventana.
rem Son las reglas de las simulaciones de cuenta fondeada (ESTADO.md): 200 de riesgo por operacion, perdida
rem diaria de 1.000 con kill switch al 75 por ciento, y la cuenta se pierde al caer 2.000 bajo su mejor cierre
rem diario; entonces nace otra que hereda lo aprendido. Acciones con velas de 1 hora, como en esas simulaciones;
rem cripto con las del motor (5 min para SMC, 15 min para el stop). Su historial va aparte, en trades_fondeo.db.
set "PAPER_TRADING=True"
set "BROKER=ccxt"
set "EXCHANGE_ID=binanceusdm"
set "STRATEGY=ensemble"
set "DB_PATH=%~dp0trades_fondeo.db"
set "LOG_FILE=%~dp0trading_fondeo.log"
set "CAPITAL=50000"
set "RISK_PER_TRADE_PCT=0.4"
set "TOTAL_LOSS_LIMIT_USD=0"
set "PROP_DAILY_LOSS_LIMIT_USD=1000"
set "PROP_KILL_AT_PCT=75"
set "PROP_ACCOUNT_FLOOR_USD=0"
set "PROP_MAX_LOSS_USD=2000"
set "SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,NVDA,META,GOOGL,TTWO,MU,MSFT,UBER"
set "QUOTE=USD"
set "MAX_LEVERAGE=2"
set "MAX_OPEN_POSITIONS=3"
set "MAX_TRADES_PER_DAY=0"
set "MIN_ORDER_GAP_S=0"
set "FEE_PCT=0.075"
set "SLIPPAGE_PCT=0.02"
set "STOCK_FEE_PCT=0.005"
set "STOCK_SLIPPAGE_PCT=0.02"
set "STOCK_TIMEFRAME=1h"
set "SMC_TIMEFRAME=5m"
set "ATR_TIMEFRAME=15m"
set "WYCKOFF_TIMEFRAME=1h"
set "DECISION_THRESHOLD=0.25"
set "MIN_AGENTS=3"
set "COOLDOWN_S=900"
set "APP_PORT=8502"
set "APP_TITLE=Mesa de bots - cuenta de fondeo"
call "%~dp0Iniciar.bat"
