@echo off
REM Launches the Discord moderation bot.
REM Place this file in the same folder as bot.py.

cd /d "%~dp0"

echo Starting Discord moderation bot...
echo.

python bot.py

echo.
echo Bot process stopped.
pause
