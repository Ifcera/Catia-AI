@echo off
rem CATIA Asistanı'nı konsol penceresi olmadan başlatır.
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m catia_aibot.ui
