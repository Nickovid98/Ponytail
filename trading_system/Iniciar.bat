@echo off
chcp 65001 >nul
if not defined APP_TITLE set "APP_TITLE=Mesa de bots"
if not defined APP_PORT set "APP_PORT=8501"
title %APP_TITLE%
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo No encuentro Python en este equipo.
  echo Instala Python 3.11 o superior desde https://www.python.org/downloads/
  echo y marca la casilla "Add python.exe to PATH" durante la instalacion.
  pause
  exit /b 1
)

if not exist venv\Scripts\python.exe (
  echo Preparando el entorno. Solo ocurre la primera vez y tarda unos minutos...
  %PY% -m venv venv
  if errorlevel 1 goto :fallo
  venv\Scripts\python -m pip install --quiet --upgrade pip
  venv\Scripts\python -m pip install --quiet -r requirements.txt
  if errorlevel 1 goto :fallo
)
if not exist .env copy .env.example .env >nul

echo.
echo Abriendo la app en el navegador: http://localhost:%APP_PORT%
echo Deja esta ventana abierta. Al cerrarla se apagan la app y el motor.
echo.
start "" /b cmd /c "ping -n 6 127.0.0.1 >nul & start http://localhost:%APP_PORT%"
venv\Scripts\python -m streamlit run dashboard.py --server.headless true --server.port %APP_PORT%
pause
exit /b 0

:fallo
echo.
echo No se pudo preparar el entorno. Copia el mensaje de arriba y revisa tu conexion a internet.
pause
exit /b 1
