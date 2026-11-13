@echo off
setlocal

rem Instala las dependencias en un entorno local, sin permisos de administrador.
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "VENV_PYTHON=%APP_DIR%\.venv\Scripts\python.exe"

if not exist "%APP_DIR%\app.py" (
    echo No se encontro app.py en:
    echo %APP_DIR%
    echo.
    echo Extraiga primero el ZIP completo y ejecute este instalador desde la carpeta del sistema.
    pause
    exit /b 1
)

if not exist "%APP_DIR%\requirements.txt" (
    echo No se encontro requirements.txt. No se puede instalar el sistema.
    pause
    exit /b 1
)

if not exist "%APP_DIR%\instance\database.db" (
    echo No se encontro la base de datos:
    echo %APP_DIR%\instance\database.db
    echo.
    echo Se cancelo la instalacion para evitar iniciar el sistema con una base vacia.
    pause
    exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo No se encontro Python 3.
        echo Instale Python 3 para Windows y marque "Add Python to PATH".
        echo Luego vuelva a ejecutar este instalador.
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

if not exist "%VENV_PYTHON%" (
    echo Creando entorno local de Python...
    %PYTHON% -m venv "%APP_DIR%\.venv"
    if errorlevel 1 (
        echo No se pudo crear el entorno de Python.
        pause
        exit /b 1
    )
)

echo Instalando dependencias. Esto puede tardar unos minutos...
"%VENV_PYTHON%" -m pip install -r "%APP_DIR%\requirements.txt"
if errorlevel 1 (
    echo Hubo un error al instalar las dependencias.
    echo Compruebe su conexion a Internet e intente ejecutar el instalador otra vez.
    pause
    exit /b 1
)

echo.
echo Instalacion completada.
echo Para iniciar el sistema y ngrok, ejecute:
echo Iniciar Monteros Automotores.bat
echo.
pause
endlocal
