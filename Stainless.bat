@echo off
setlocal
title Stainless launcher

rem --- where the Python runtime lives (no admin, no PATH or registry changes) ---
rem     shared:  one Python + one package cache for every tool set up this way
rem     private: a self-contained folder only Stainless uses
set "SHARED_ROOT=C:\ProgramData\PyApps"
set "PRIVATE_ROOT=C:\Users\Public\Stainless"
set "PROJECT=%~dp0"
set "PYTHONPATH=%~dp0src"
set "ICON=%~dp0src\stainless\assets\stainless.ico"

rem --- pick a root. No question is asked: shared unless you ask for private. ---
rem       Stainless.bat private   (or 2)  -> a folder only Stainless uses
rem       set STAINLESS_RUNTIME=...    -> some other location of your own
rem     After the first run the folder on disk is the memory, so a plain
rem     double-click (and the Desktop shortcut) re-use whatever is there.
set "ROOT="
if defined STAINLESS_RUNTIME set "ROOT=%STAINLESS_RUNTIME%"
if not defined ROOT if /I "%~1"=="private" set "ROOT=%PRIVATE_ROOT%"
if not defined ROOT if "%~1"=="2" set "ROOT=%PRIVATE_ROOT%"
if not defined ROOT if exist "%PRIVATE_ROOT%\uv.exe" set "ROOT=%PRIVATE_ROOT%"
if not defined ROOT if exist "%SHARED_ROOT%\uv.exe"  set "ROOT=%SHARED_ROOT%"
if not defined ROOT set "ROOT=%SHARED_ROOT%"

set "UV=%ROOT%\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\python"
set "UV_CACHE_DIR=%ROOT%\cache"
set "UV_PROJECT_ENVIRONMENT=%ROOT%\envs\stainless"
rem the cache must sit on the same drive as the venv, or uv copies instead of
rem hardlinking and the sharing buys nothing
set "UV_NO_MODIFY_PATH=1"

rem --- one-time: fetch the uv binary ---
if not exist "%UV%" (
    echo First run: downloading uv into %ROOT% ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "New-Item -ItemType Directory -Force -Path '%ROOT%' | Out-Null;" ^
        "Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%ROOT%\uv.zip';" ^
        "Expand-Archive -Force '%ROOT%\uv.zip' '%ROOT%';" ^
        "Remove-Item '%ROOT%\uv.zip'"
    if not exist "%UV%" (
        echo Failed to download uv into %ROOT%.
        echo Check your internet connection, or whether that folder is writable.
        pause
        exit /b 1
    )
)

rem --- install Python + dependencies ---
rem     skipped while uv.lock is the one the environment was built from: even a no-op
rem     sync costs seconds on a cold start (antivirus scanning uv and the cache)
fc /b "%PROJECT%uv.lock" "%UV_PROJECT_ENVIRONMENT%\.stainless.lock" >nul 2>&1
if errorlevel 1 (
    echo Preparing environment in %ROOT% ...
    "%UV%" sync --project "%PROJECT%." --python 3.13
    if errorlevel 1 (
        echo Environment setup failed.
        pause
        exit /b 1
    )
    copy /y "%PROJECT%uv.lock" "%UV_PROJECT_ENVIRONMENT%\.stainless.lock" >nul
)

rem --- one-time: put a Stainless shortcut (with the app icon) on the Desktop ---
rem     resolve Desktop via .NET so a OneDrive-redirected / localized folder works;
rem     best-effort - a shortcut failure must never block launch.
rem     The stamp lives in the venv, not the root, because two apps can share
rem     one root and each still needs its own shortcut.
rem     The stamp is what keeps this off the fast path: starting PowerShell only
rem     to be told the shortcut is already there costs a second of every launch.
if not exist "%UV_PROJECT_ENVIRONMENT%\.shortcut" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "try {" ^
            "$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Stainless.lnk';" ^
            "if (-not (Test-Path $lnk)) {" ^
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
                "$s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0';" ^
                "$s.IconLocation='%ICON%'; $s.WindowStyle=7;" ^
                "$s.Description='Stainless - stained area in whole-slide scans (DAB, SA-beta-gal)'; $s.Save() }" ^
        "} catch {}"
    echo done> "%UV_PROJECT_ENVIRONMENT%\.shortcut"
)

rem --- launch the GUI with pythonw (no console window) and exit ---
start "" "%UV_PROJECT_ENVIRONMENT%\Scripts\pythonw.exe" -m stainless
exit /b 0
