@echo off
REM Conversion DWG -> DXF en lot (Windows)
REM Glissez-deposez un dossier sur ce fichier, ou lancez :
REM    convertir_dwg.bat "C:\chemin\vers\dossier_dwg"

if "%~1"=="" (
    set /p DOSSIER="Chemin du dossier contenant les DWG : "
) else (
    set DOSSIER=%~1
)

python "%~dp0convertir_dwg.py" "%DOSSIER%"
pause
