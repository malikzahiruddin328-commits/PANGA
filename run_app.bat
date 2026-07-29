@echo off
cd /d "%~dp0"
venv\Scripts\streamlit.exe run src\ui\app.py
