@echo off
title TG-LOGIN
cd /d %~dp0
echo.
echo ============================================================
echo  TELEGRAM LOGIN for tg-watcher session regeneration
echo ============================================================
echo.
echo Step 1: Enter your phone number in international format
echo         e.g.  +15551234567
echo Step 2: Telegram sends you a login code (in-app or SMS)
echo Step 3: Type the code in this window and press Enter
echo.
echo At the end you get a TG_SESSION_STRING between the
echo ---TG_SESSION_STRING_START--- and ---TG_SESSION_STRING_END---
echo markers. Copy that whole string and paste it back to Jcode.
echo.
echo ============================================================
echo.
".venv\Scripts\python.exe" _login_now.py
echo.
echo ============ DONE ============
echo Copy the whole TG_SESSION_STRING (the long line between the
echo START and END markers) and paste it back to the Jcode chat.
echo.
pause
