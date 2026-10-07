@echo off
setlocal
cd /d "%~dp0"
echo Starting Quant Backtest Lab...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\start_quant_lab.py"
) else (
  python "scripts\start_quant_lab.py"
)
endlocal
