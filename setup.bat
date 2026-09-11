@echo off
REM One-time setup: downloads and installs Tesseract OCR, installs Python
REM dependencies, and creates your .env file. Run this once, then use
REM start.bat every time after.

cd /d "%~dp0"

echo ============================================
echo  Discord Mod Bot - Setup
echo ============================================
echo.

REM ---- 1. Check Python is available ----
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on your PATH.
    echo Install it from https://www.python.org/downloads/ ^(check "Add python.exe to PATH" during install^), then run this script again.
    pause
    exit /b 1
)

REM ---- 2. Install Python packages ----
echo Installing required Python packages...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed. See the messages above.
    pause
    exit /b 1
)
echo Done.
echo.

REM ---- 3. Download and install Tesseract OCR ----
set "TESS_DIR=%LOCALAPPDATA%\Programs\Tesseract-OCR"
set "TESS_EXE=%TESS_DIR%\tesseract.exe"
set "TESS_INSTALLER=%TEMP%\tesseract-installer.exe"
set "TESS_URL=https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe"

if exist "%TESS_EXE%" (
    echo Tesseract is already installed at %TESS_DIR%
    goto :tesseract_done
)

echo Downloading Tesseract OCR installer...
curl -L -o "%TESS_INSTALLER%" "%TESS_URL%"
if errorlevel 1 (
    echo ERROR: Download failed. Check your internet connection, or install
    echo Tesseract manually from https://github.com/UB-Mannheim/tesseract/wiki
    pause
    exit /b 1
)

echo Installing Tesseract OCR to %TESS_DIR% ...
"%TESS_INSTALLER%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%TESS_DIR%"

if not exist "%TESS_EXE%" (
    echo WARNING: Installer ran but tesseract.exe wasn't found at the expected
    echo location. You may need to install it manually - see README.md.
) else (
    echo Tesseract installed successfully.
)
del "%TESS_INSTALLER%" >nul 2>nul

:tesseract_done
echo.

REM ---- 4. Create .env if it doesn't exist ----
if not exist ".env" (
    echo Creating .env from template...
    copy .env.example .env >nul
)

REM ---- 5. Make sure TESSERACT_PATH is set in .env so the bot finds it ----
REM (done via a small Python script rather than batch text munging, since
REM  batch's >> append doesn't add a newline if the file doesn't already end
REM  with one - that previously corrupted the last line of .env)
set "PY_FIX=%TEMP%\fix_env.py"
> "%PY_FIX%" echo import os
>> "%PY_FIX%" echo tess = os.environ.get("TESS_EXE_FOR_PY", "")
>> "%PY_FIX%" echo lines = []
>> "%PY_FIX%" echo if os.path.exists(".env"):
>> "%PY_FIX%" echo     with open(".env", "r", encoding="utf-8") as f:
>> "%PY_FIX%" echo         lines = [l.rstrip("\r\n") for l in f if not l.startswith("TESSERACT_PATH=")]
>> "%PY_FIX%" echo lines.append("TESSERACT_PATH=" + tess)
>> "%PY_FIX%" echo with open(".env", "w", encoding="utf-8", newline="\n") as f:
>> "%PY_FIX%" echo     f.write("\n".join(lines) + "\n")
set "TESS_EXE_FOR_PY=%TESS_EXE%"
python "%PY_FIX%"
del "%PY_FIX%" >nul 2>nul

echo.
echo ============================================
echo  Setup complete!
echo ============================================
echo.
echo Next step: open .env in Notepad and fill in:
echo   - DISCORD_TOKEN
echo   - MOD_LOG_CHANNEL_ID
echo   - (optional) HUGGINGFACE_API_KEY plus MODERATION_BACKEND=huggingface
echo.
echo Then run start.bat to launch the bot.
echo.
pause
