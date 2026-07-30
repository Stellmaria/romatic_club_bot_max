@echo off
chcp 65001 >nul
set "PYTHON=C:\Users\aamch\PyCharmMiscProject\.venv\Scripts\python.exe"
set "TARGET=E:\python\main\refactored_project_phase6\bot\handlers\auctions.py"

"%PYTHON%" "%~dp0apply_addlot_preview_hotfix.py" "%TARGET%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo Исправление завершилось с ошибкой %EXIT_CODE%.
) else (
  echo Проверка строки сигнатуры:
  findstr /n /c:"custom_offer_terms: str | None = None" "%TARGET%"
)
echo.
pause
exit /b %EXIT_CODE%
