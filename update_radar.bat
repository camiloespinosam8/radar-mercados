@echo off
REM Regenera el Radar de Mercados y lo publica en GitHub Pages.
REM Corre en dias habiles despues del cierre de Wall Street (Task Scheduler: Radar_Mercados).
REM Solo publica si algo cambio. Las alertas por Telegram las manda build_radar.py.
setlocal
set PY="C:\Users\Cami\AppData\Local\Programs\Python\Python312\python.exe"
cd /d "C:\Users\Cami\Desktop\CLAUDE\Sistema\Mercados"

echo ---- %date% %time% ---- >> update_radar.log
%PY% build_radar.py >> update_radar.log 2>&1
if errorlevel 1 (
  echo BUILD FALLO - no publico >> update_radar.log
  exit /b 1
)

git add index.html datos.json historico.json README.md build_radar.py
git diff --cached --quiet
if %errorlevel%==0 (
  echo sin cambios >> update_radar.log
  exit /b 0
)
git commit -m "auto: radar de mercados" >> update_radar.log 2>&1
git push >> update_radar.log 2>&1
echo PUBLICADO >> update_radar.log
exit /b 0
