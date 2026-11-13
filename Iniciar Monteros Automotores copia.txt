@echo off
setlocal

rem Usa la carpeta donde se encuentra este .bat para que funcione desde cualquier ubicacion.
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "DB_FILE=%APP_DIR%\instance\database.db"

if not exist "%APP_DIR%\app.py" (
    echo No se encontro la aplicacion en:
    echo %APP_DIR%
    echo.
    echo Verifique que la carpeta del sistema siga en esa ubicacion.
    pause
    exit /b 1
)

if not exist "%DB_FILE%" (
    echo No se encontro la base de datos existente:
    echo %DB_FILE%
    echo.
    echo Se cancelo el inicio para evitar crear una base nueva vacia.
    pause
    exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo No se encontro Python.
        echo Instale Python 3 y vuelva a ejecutar este archivo.
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

start "Monteros Automotores - Servidor" cmd /k "cd /d ""%APP_DIR%"" && %PYTHON% app.py"

rem Espera a que el servidor responda antes de abrir el navegador.
set "URL=http://127.0.0.1:5000"
set "READY=0"
for /L %%I in (1,1,30) do (
    powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { (Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -TimeoutSec 2) | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
    if not errorlevel 1 (
        set "READY=1"
        goto navegador
    )
    timeout /t 1 /nobreak >nul
)

:navegador
if "%READY%"=="1" (
    start "" "%URL%"
) else (
    echo El servidor no respondio a tiempo en %URL%.
    echo Verifique manualmente la app si ya se inicio.
    start "" "%URL%"
)

endlocal
