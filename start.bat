@echo off
title Rental Tracking App
echo ======================================================================
echo        RENTAL TRACKING APP - SHOP RENT BILLING SYSTEM
echo ======================================================================
echo.

:: 1. Find or Install Python
set PYTHON_CMD=python
python --version >nul 2>&1
if %errorlevel% equ 0 goto check_venv

set PYTHON_CMD=py
py --version >nul 2>&1
if %errorlevel% equ 0 goto check_venv

:: Search local user installs
for /d %%d in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%d\python.exe" (
        set "PYTHON_CMD=%%d\python.exe"
        goto check_venv
    )
)

:: Search program files
for /d %%d in ("%ProgramFiles%\Python*") do (
    if exist "%%d\python.exe" (
        set "PYTHON_CMD=%%d\python.exe"
        goto check_venv
    )
)

for /d %%d in ("%ProgramFiles(x86)%\Python*") do (
    if exist "%%d\python.exe" (
        set "PYTHON_CMD=%%d\python.exe"
        goto check_venv
    )
)

:: If not found, attempt install
echo [SETUP] Python is not detected on this system.
echo [SETUP] Attempting automatic installation of Python...
echo.

:: Try winget first
winget --version >nul 2>&1
if %errorlevel% neq 0 goto fallback_download

echo [SETUP] Installing Python using Windows Package Manager (winget)...
winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements
if %errorlevel% neq 0 (
    echo [WARNING] winget installation failed. Trying fallback installer...
    goto fallback_download
)
goto post_install_check

:fallback_download
echo [SETUP] Downloading Python installer using PowerShell...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe' -OutFile 'python_setup.exe'"
if not exist python_setup.exe (
    echo [ERROR] Failed to download Python installer.
    goto install_failed
)

echo [SETUP] Running Python silent installation. Please wait...
python_setup.exe /quiet PrependPath=1 InstallAllUsers=0 Include_test=0
:wait_install
tasklist | findstr /i "python_setup.exe" >nul
if %errorlevel% equ 0 (
    ping 127.0.0.1 -n 3 > nul
    goto wait_install
)
del python_setup.exe

:post_install_check
:: Search for python again
for /d %%d in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%d\python.exe" (
        set "PYTHON_CMD=%%d\python.exe"
        goto check_venv
    )
)
for /d %%d in ("%ProgramFiles%\Python*") do (
    if exist "%%d\python.exe" (
        set "PYTHON_CMD=%%d\python.exe"
        goto check_venv
    )
)
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto check_venv
)

:install_failed
echo [ERROR] Python installation completed but could not be located.
echo Please install Python 3.9+ manually from https://www.python.org/downloads/ and make sure to check 'Add Python to PATH'.
pause
exit /b 1

:check_venv
echo [SETUP] Python found: %PYTHON_CMD%
set REBUILD_VENV=0
set INSTALL_DEPS=0

:: Check if virtual environment directory exists
if not exist .venv (
    set REBUILD_VENV=1
    goto setup_venv
)

:: If .venv exists, verify if it is functional
echo [SETUP] Verifying existing virtual environment...
.venv\Scripts\python.exe -c "import sys" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Virtual environment is broken or has invalid paths. Recreating...
    set REBUILD_VENV=1
    goto setup_venv
)

:: If .venv works, check if all required libraries are installed
.venv\Scripts\python.exe -c "import flask, flask_sqlalchemy, openpyxl, waitress, dateutil" >nul 2>&1
if %errorlevel% neq 0 (
    echo [SETUP] Missing required libraries in virtual environment.
    set INSTALL_DEPS=1
) else (
    echo [SETUP] Virtual environment is healthy and all dependencies are satisfied.
)
goto activate_env

:setup_venv
if %REBUILD_VENV% equ 1 (
    echo [SETUP] Creating Python virtual environment .venv...
    if exist .venv rmdir /s /q .venv
    %PYTHON_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    set INSTALL_DEPS=1
)

:activate_env
echo [SETUP] Activating virtual environment...
call .venv\Scripts\activate

if %INSTALL_DEPS% equ 1 (
    echo [SETUP] Installing required python libraries from requirements.txt...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install python libraries. Check your internet connection.
        pause
        exit /b 1
    )
)

:run_tests
:: 5. Run test suite
echo [TESTS] Verifying database integrity and billing ledger formulas...
python test_billing.py
if %errorlevel% neq 0 (
    echo [WARNING] Test suite failed! The application logic or schema might be broken.
    echo Press any key to attempt server launch anyway, or close this window.
    pause
)

:: 6. Start application server in background, then open browser
echo [SERVER] Starting production waitress server on local LAN...
echo [SERVER] Please wait while the server initializes...
echo.

:: Start the server in the background (new window)
start "Rental Tracking App - Billing Server" .venv\Scripts\python.exe app.py

:: Wait for server to be ready by polling port 5000
echo [SERVER] Waiting for server to bind to port 5000...
:wait_loop
netstat -ano | findstr /c:":5000 " > nul
if %errorlevel% neq 0 (
    ping 127.0.0.1 -n 2 > nul
    goto wait_loop
)

:: 7. Now open browser (server should be ready)
echo [BROWSER] Opening browser...
start "" http://localhost:5000

echo.
echo [INFO] Server is running. Close the "Rental Tracking App - Billing Server" window to stop.
echo [INFO] This setup window will close automatically in 5 seconds.
ping 127.0.0.1 -n 6 > nul
exit
