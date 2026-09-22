# PDF to Excel System - Cloud Edition

ระบบแปลงไฟล์ PDF ทะเบียนราษฎร (ท.ร. 14/1) เป็น Excel อัตโนมัติ (Enterprise Edition with Google Gemini Vision & Tesseract OCR)

## ฟังก์ชันเด่น
- แปลงไฟล์ PDF ท.ร. 14/1 ทั้งแบบรับรองและไม่รับรองเป็นตาราง Excel ละเอียด
- รองรับ Enterprise AI (Google Gemini Multimodal Vision API)
- ระบบสำรองในตัว (Tesseract OCR ภาษาไทย)
- ระบบจัดการสิทธิ์ผู้ใช้, สถิติ, ประวัติการแปลงไฟล์ และรายงาน

## การ Deploy บน Render.com
1. เชื่อมต่อ Repository นี้เข้ากับ [Render.com](https://render.com)
2. สร้าง **New Web Service**
3. Render จะตรวจจับ `Dockerfile` ให้อัตโนมัติ
4. กด **Deploy Web Service** ได้ทันที
