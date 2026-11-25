@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Zainstaluj Python 3.11 lub nowszy: https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Tworze srodowisko i instaluje biblioteki...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Nie udalo sie utworzyc .venv
    pause
    exit /b 1
  )
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Instalacja bibliotek nie powiodla sie.
    pause
    exit /b 1
  )
)
.venv\Scripts\python.exe app.py
echo.
pause
