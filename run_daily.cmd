@echo off
title LinkedIn Daily Easy Apply
cd /d "c:\Users\shcar\Documents\Projects\applyBot\LinkedIn-AI-Job-Applier-Ultimate"
echo [%date% %time%] Starting daily LinkedIn Easy Apply run...
".venv\Scripts\python.exe" main.py
echo [%date% %time%] Run finished with exit code %errorlevel%.
