#!/bin/bash
# HYP_check — Mac başlatıcı
# Çift tıkla, tarayıcı otomatik açılır.

cd "$(dirname "$0")"

# Python 3 kontrolü
if ! command -v python3 &>/dev/null; then
    echo "Python 3 bulunamadı. https://www.python.org adresinden indirin."
    read -p "Çıkmak için ENTER'a basın..."
    exit 1
fi

echo "Bağımlılıklar kuruluyor..."
python3 -m pip install -r requirements.txt -q

echo "Uygulama başlatılıyor..."
python3 -m streamlit run app.py --server.port 8502 --server.headless true
