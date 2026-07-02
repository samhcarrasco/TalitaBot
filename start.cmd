@echo off
title Talita Job Bot
rem Always run from the folder this file lives in, wherever it was copied.
cd /d "%~dp0"
echo ============================================
echo   Starting Talita's job bot...
echo   A Chrome window will open in a moment.
echo ============================================
echo.
uv run python main.py
echo.
echo ============================================
echo   The bot has finished. You can close this window.
echo ============================================
pause
