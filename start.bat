@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo   HYP_check - Hyperion vs BW Comparison Tool
echo ================================================
echo.

REM -- Python'u bul (py launcher > python > python3) --
set PYTHON=
where py >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" (
    where python >nul 2>&1 && set PYTHON=python
)
if "%PYTHON%"=="" (
    where python3 >nul 2>&1 && set PYTHON=python3
)

REM -- Store stub kontrolu: gercekten calisip calismadigi test edilir --
if not "%PYTHON%"=="" (
    %PYTHON% -c "import sys; sys.exit(0)" >nul 2>&1
    if errorlevel 1 set PYTHON=
)

if "%PYTHON%"=="" (
    echo [HATA] Python bulunamadi!
    echo.
    echo Lutfen Python 3 kurun: https://www.python.org/downloads/
    echo Kurulum sirasinda "Add Python to PATH" secenegini isaretleyin.
    echo.
    pause
    exit /b 1
)

echo Python bulundu: %PYTHON%
echo.
echo Bagimliliklar kuruluyor...
%PYTHON% -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo.
    echo [HATA] Bagimliliklar kurulamadi.
    echo Lutfen internete bagli oldugunuzdan emin olun.
    pause
    exit /b 1
)

echo.
echo Uygulama baslatiliyor...
echo Tarayici otomatik acilacak: http://localhost:8502
echo.
echo Kapatmak icin bu pencereyi kapatin.
echo.
%PYTHON% -m streamlit run app.py --server.port 8502 --server.headless true
pause
