@echo off
REM HYP_check — Windows başlatıcı
REM Çift tıkla, tarayıcı otomatik açılır.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python bulunamadı. https://www.python.org adresinden indirin.
    pause
    exit /b 1
)

echo Bağımlılıklar kuruluyor...
python -m pip install -r requirements.txt -q

echo Uygulama başlatılıyor...
python -m streamlit run app.py --server.port 8502 --server.headless true
pause
