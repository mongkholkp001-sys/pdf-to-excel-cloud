# คู่มือการนำระบบ PDF to Excel ขึ้น Google Cloud Run และ Cloud องค์กร
### (เวอร์ชัน Cloud Edition - พร้อม Docker Container และภาษาไทยในตัว 100%)

---

## 1. จุดเด่นของเวอร์ชัน Cloud Edition
* **ออนไลน์ 24 ชั่วโมง 365 วัน (Uptime 99.9%):** ไม่ต้องเปิดคอมพิวเตอร์ทิ้งไว้ ไฟดับ เน็ตหลุด ก็ไม่มีผลกระทบ
* **ลิงก์คงที่ถาวร (Permanent HTTPS URL):** ได้ URL ถาวร ไม่มีการเปลี่ยนชื่อสุ่มอีกต่อไป
* **ฟรีหรือประหยัดที่สุด:** Google Cloud Run มีโควต้า **ฟรีตลอดชีพ (Free Tier)** สำหรับการแปลงไฟล์ทั่วไปในองค์กร แทบจะไม่มีค่าใช้จ่ายเลย (0 บาท)
* **รองรับทั้งสองโหมด:** อ่านเอกสารแบบไม่รับรอง (`ผล ทร.pdf`) และแบบรับรอง (`ทะเบียนราษฎร.pdf`) พร้อม Tesseract OCR ภาษาไทยสมบูรณ์แบบ

---

## 2. วิธีนำขึ้น Google Cloud Run ด้วย E-mail ของคุณ (ง่ายที่สุด - ผ่านเว็บเบราว์เซอร์)

คุณสามารถใช้อีเมล Google (ทั้ง `@gmail.com` หรืออีเมลบริษัท Google Workspace) นำระบบขึ้น Cloud ได้ง่ายๆ ดังนี้ครับ:

### ขั้นตอนที่ 1: เข้าสู่ Google Cloud Console
1. เปิดเว็บเบราว์เซอร์ เข้าไปที่: [https://console.cloud.google.com](https://console.cloud.google.com)
2. ล็อกอินด้วยอีเมล Google ของคุณ
3. สร้างโปรเจกต์ใหม่ (หรือเลือกโปรเจกต์ที่มีอยู่) เช่น ตั้งชื่อว่า `pdf-to-excel-system`

### ขั้นตอนที่ 2: เปิด Cloud Shell (หน้าจอดำบนเว็บ ไม่ต้องลงโปรแกรมในคอม)
1. มองไปที่ **มุมขวาบนของหน้าจอเว็บ** จะเห็นไอคอนรูป **`>_`** (Activate Cloud Shell)
2. คลิกที่ไอคอนนั้น จะมีหน้าต่าง Terminal ปรากฏขึ้นมาที่ด้านล่างของหน้าจอ

### ขั้นตอนที่ 3: อัปโหลดชุดไฟล์ Cloud Edition
1. ที่หน้าต่าง Cloud Shell คลิกที่ปุ่มจุดสามจุด `⋮` (More) แล้วเลือก **Upload**
2. เลือกไฟล์ **`PDF_to_Excel_Cloud_Edition.zip`** (ที่อยู่บนหน้าจอ Desktop ของคุณ)
3. เมื่ออัปโหลดเสร็จ ให้พิมพ์คำสั่งนี้ใน Cloud Shell แล้วกด Enter:
   ```bash
   unzip -o PDF_to_Excel_Cloud_Edition.zip -d pdf-cloud
   cd pdf-cloud
   ```

### ขั้นตอนที่ 4: สั่ง Deploy ขึ้น Cloud ด้วยคำสั่งเดียว!
พิมพ์คำสั่งนี้ลงไปแล้วกด Enter:
```bash
gcloud run deploy pdf-excel-system \
  --source . \
  --region asia-southeast1 \
  --allow-unauthenticated
```
*(หมายเหตุ: `asia-southeast1` คือดาต้าเซ็นเตอร์สิงคโปร์ อยู่ใกล้ไทยที่สุดและเร็วที่สุด)*

* ระบบจะถามยืนยัน ให้พิมพ์ **`y`** แล้วกด Enter
* รอระบบสร้างระบบประมาณ 2 - 3 นาที
* เมื่อเสร็จสิ้น Google Cloud จะแสดง **URL ถาวร** ให้ทันที เช่น:
  👉 **`https://pdf-excel-system-xxxx-as.a.run.app`**
* สามารถนำ URL นี้ไปเปิดใช้งาน บันทึก Bookmark หรือส่งให้ลูกค้าใช้งานได้ตลอด 24 ชม. ทันทีครับ!

---

## 3. วิธีนำไปรันบน Server / Cloud เดิมของบริษัท (Docker Compose)

หากบริษัทของคุณมี Server Linux / Ubuntu หรือใช้ Docker อยู่แล้ว:
1. นำโฟลเดอร์ `PDF_to_Excel_Cloud_Edition` ไปวางบน Server
2. เปิด Terminal ในโฟลเดอร์นั้น แล้วสั่งรัน:
   ```bash
   docker compose up -d --build
   ```
3. ระบบจะสร้าง Container และเปิดทำงานที่พอร์ต `8080` ทันที เข้าใช้งานได้ที่:
   👉 **`http://IP-SERVER-ของบริษัท:8080`**

---

## 4. การผูกชื่อโดเมนเนมของบริษัท (Custom Domain)
หากต้องการให้เป็นชื่อเว็บของบริษัทตนเอง เช่น `https://pdf.yourcompany.com`:
1. ในหน้า **Google Cloud Run** คลิกเข้าไปที่ชื่อเซอร์วิส `pdf-excel-system`
2. คลิกแท็บ **Custom Domains** -> **Add Mapping**
3. ใส่ชื่อโดเมนของบริษัท แล้วนำค่า DNS (CNAME / TXT) ไปใส่ที่ผู้ให้บริการโดเมน
4. Google จะออกใบรับรองความปลอดภัย **SSL ฟรี (HTTPS)** ให้อัตโนมัติ

---
*จัดทำขึ้นโดยระบบ AI Pair Programming สำหรับการพัฒนาซอฟต์แวร์ระดับองค์กร*
