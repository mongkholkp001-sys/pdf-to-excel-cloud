#!/usr/bin/env bash
# ==============================================================================
# สคริปต์ติดตั้งระบบแปลงไฟล์ PDF เป็น Excel (ท.ร.14/1 + ข้อมูลที่ดิน 25 คอลัมน์)
# สำหรับเซิร์ฟเวอร์ ReadyIDC (S2Sytem - 119.10.137.52, 2 Cores, 4GB RAM, 40GB Disk)
# ทำงานแยกพอร์ตอิสระ (Port 5055) และจำกัด CPU/RAM เพื่อไม่ให้กระทบระบบเดิมบน S2Sytem
# ==============================================================================
set -e

APP_DIR="/opt/pdf_to_excel_system"
PORT="5055"

echo "========================================================"
echo " กำลังติดตั้งระบบ PDF to Excel บนเซิร์ฟเวอร์ S2Sytem (Port $PORT)"
echo "========================================================"

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-tha tesseract-ocr-eng git

if [ ! -d "$APP_DIR" ]; then
    sudo git clone https://github.com/mongkholkp001-sys/pdf-to-excel-cloud.git "$APP_DIR"
else
    cd "$APP_DIR"
    sudo git pull origin master
fi

cd "$APP_DIR"
sudo mkdir -p uploads outputs tessdata

if [ ! -d "$APP_DIR/venv" ]; then
    sudo python3 -m venv "$APP_DIR/venv"
fi

sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r requirements.txt gunicorn

# สร้าง Systemd Service ที่จำกัด CPU Priority (Nice=10) และ OCR_WORKERS=2
# เพื่อไม่ให้แย่งทรัพยากรกับระบบหลักบน S2Sytem และเก็บฐานข้อมูลผู้ใช้งานถาวรบนดิสก์
sudo tee /etc/systemd/system/pdf-to-excel.service > /dev/null <<EOF
[Unit]
Description=PDF to Excel Civil & Land Conversion System (Port $PORT)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment="PORT=$PORT"
Environment="OCR_WORKERS=2"
Environment="PYTHONUNBUFFERED=1"
Nice=10
CPUQuota=140%
MemoryMax=1536M
ExecStart=$APP_DIR/venv/bin/gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 600
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pdf-to-excel
sudo systemctl restart pdf-to-excel

echo "========================================================"
echo " [สำเร็จ] ระบบพร้อมใช้งานอย่างถาวรแล้วที่:"
echo "   http://119.10.137.52:$PORT"
echo "   - ข้อมูลผู้ใช้งาน (Users) และประวัติการแปลงไฟล์ถูกเก็บถาวรใน $APP_DIR/system.db"
echo "   - จำกัดการใช้ CPU/RAM อัตโนมัติ ไม่กระทบการทำงานของ S2Sytem"
echo "========================================================"
