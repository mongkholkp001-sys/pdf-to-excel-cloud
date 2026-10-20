import os
import io
import json
import base64
import time
import requests
import fitz
from PIL import Image

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
CANDIDATE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
]
_ACTIVE_GEMINI_MODEL = None

SYSTEM_PROMPT = """คุณคือผู้เชี่ยวชาญด้านการอ่านและสกัดข้อมูลเอกสารราชการไทย โดยเฉพาะเอกสารทะเบียนราษฎร ท.ร. 14/1 (ทั้งแบบไม่รับรองและแบบรับรองที่มีตราประทับนายทะเบียน/ผู้ช่วยนายทะเบียน ลายเซ็น และรอยหมึก)

หน้าที่ของคุณ:
สกัดข้อมูลจากภาพเอกสาร ท.ร. 14/1 ที่แนบมานี้ ให้มีความถูกต้องแม่นยำ 100% และแปลงเป็น JSON ตามโครงสร้างที่กำหนดอย่างเคร่งครัด

กฎการสกัดข้อมูลที่สำคัญมาก:
1. "ชื่อมารดา" (mother_name) และ "ชื่อบิดา" (father_name):
   - ให้สกัดเฉพาะ "ชื่อตัวจริง" ของบุคคลเท่านั้น (เช่น "พรพิมล", "น้ำอ้อย", "รุ่งรัตน์", "ประยูร", "กาเหว่า", "จรัญ", "กิ่งแก้ว", "อดิศักดิ์")
   - ห้ามมีคำว่า "มารดาชื่อ", "บิดาชื่อ", "ชื่อ", "สัญชาติ", "ไทย" ปนมาในชื่อตัวเด็ดขาด
2. "เลขประจำตัวประชาชน" (cid, mother_cid, father_cid):
   - จัดรูปแบบเลข 13 หลัก โดยมีขีดคั่น เช่น "1-2104-00003-76-3" หรือหากไม่มีข้อมูลให้ระบุเป็น "-"
3. "รหัสประจำบ้าน" (hid):
   - จัดรูปแบบ เช่น "2003-006975-1" หรือ "6004-009952-1"
4. "ชื่อ-สกุล" (full_name, title, fname, lname):
   - title: คำนำหน้า (นาย, นางสาว, นาง, เด็กชาย, เด็กหญิง)
   - fname: ชื่อตัว (เช่น ธีรยุทธ)
   - lname: นามสกุล (เช่น สาโรจน์)
   - full_name: รวมคำนำหน้าและชื่อ-สกุล เช่น "นายธีรยุทธ สาโรจน์"
5. "ที่อยู่" (address components):
   - addr_no: บ้านเลขที่ (เช่น "63", "6/3", "244/3")
   - moo: หมู่ที่ เป็นตัวเลข (เช่น "2", "11", "8") หรือ "-"
   - soi: ซอย หรือ "-"
   - road: ถนน หรือ "-"
   - subdistrict: ตำบล หรือ แขวง
   - district: อำเภอ หรือ เขต
   - province: จังหวัด
   - address: ที่อยู่รวมทั้งหมด
6. "สถานะบุคคล" (person_status):
   - ให้เป็นสตริงว่าง "" เสมอ (ไม่ต้องสกัดข้อความตราประทับหรือสถานะบุคคลมา)
7. "วันที่และสำนักทะเบียน":
   - dob: วันเดือนปีเกิด เช่น "26 กันยายน 2527"
   - age: อายุ เช่น "41"
   - reg_office: สำนักทะเบียน เช่น "อำเภอหนองใหญ่"
   - move_date: วันที่แจ้ง/ย้ายเข้า เช่น "12 พฤษภาคม 2547"
   - issue_office: สำนักทะเบียนที่ออก เช่น "ท้องถิ่นเขตสายไหม"
   - print_date: วันที่พิมพ์ เช่น "1 กันยายน 2569"

คืนค่าผลลัพธ์เป็น JSON Object เท่านั้น ห้ามใส่ Markdown หรือข้อความอื่นนอกเหนือจาก JSON
"""

JSON_SCHEMA_EXAMPLE = {
    "no": 1,
    "cid": "",
    "hid": "",
    "title": "",
    "fname": "",
    "lname": "",
    "full_name": "",
    "gender": "ชาย",
    "nat": "ไทย",
    "dob": "",
    "age": "",
    "status": "ผู้อาศัย",
    "mother_name": "",
    "mother_cid": "-",
    "mother_nat": "ไทย",
    "father_name": "",
    "father_cid": "-",
    "father_nat": "ไทย",
    "address": "",
    "addr_no": "",
    "moo": "-",
    "soi": "-",
    "road": "-",
    "subdistrict": "",
    "district": "",
    "province": "",
    "reg_office": "",
    "move_date": "",
    "person_status": "",
    "issue_office": "",
    "print_date": ""
}


def _discover_available_models(api_key):
    """Query Google Gemini ListModels API to find supported generateContent models dynamically."""
    try:
        resp = requests.get(f"{GEMINI_BASE_URL}/models?key={api_key}", timeout=10)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            supported = []
            for m in models:
                methods = m.get("supportedGenerationMethods", [])
                name = m.get("name", "")
                if "generateContent" in methods and name.startswith("models/gemini"):
                    short_name = name.replace("models/", "")
                    if "tts" not in short_name and "image" not in short_name and "embedding" not in short_name:
                        supported.append(short_name)
            # Prioritize flash models
            flash_models = [m for m in supported if "flash" in m]
            other_models = [m for m in supported if "flash" not in m]
            return flash_models + other_models
    except Exception:
        pass
    return []


def _get_model_candidates(api_key):
    global _ACTIVE_GEMINI_MODEL
    candidates = []
    if _ACTIVE_GEMINI_MODEL:
        candidates.append(_ACTIVE_GEMINI_MODEL)
    for m in CANDIDATE_MODELS:
        if m not in candidates:
            candidates.append(m)
    return candidates


def _post_generate_content(api_key, payload, timeout=45):
    """Try candidate models in order; if 404 NOT_FOUND, auto-discover via ListModels."""
    global _ACTIVE_GEMINI_MODEL
    headers = {"Content-Type": "application/json"}
    candidates = _get_model_candidates(api_key)
    last_resp = None

    for model_name in candidates:
        url = f"{GEMINI_BASE_URL}/models/{model_name}:generateContent?key={api_key}"
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        last_resp = resp
        if resp.status_code == 200:
            _ACTIVE_GEMINI_MODEL = model_name
            return resp, model_name
        if resp.status_code == 404:
            continue
        # If 400/401/403/429 (e.g. invalid API key or quota), return immediately
        return resp, model_name

    # Fallback: dynamically list models from the API key's account
    discovered = _discover_available_models(api_key)
    for model_name in discovered:
        if model_name in candidates:
            continue
        url = f"{GEMINI_BASE_URL}/models/{model_name}:generateContent?key={api_key}"
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        last_resp = resp
        if resp.status_code == 200:
            _ACTIVE_GEMINI_MODEL = model_name
            return resp, model_name
        if resp.status_code != 404:
            return resp, model_name

    return last_resp, (candidates[0] if candidates else "gemini-2.5-flash")


def page_to_base64_image(page, dpi=200):
    """Render PyMuPDF page to high-quality JPEG/PNG base64 string."""
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("jpeg")
    return base64.b64encode(img_bytes).decode("utf-8")


def extract_page_with_gemini(page, page_num=1, api_key=None, max_retries=2):
    """
    Extracts Thai Civil Registration data from a PDF page using Gemini Flash Vision.
    Returns a standardized dictionary ready for Excel export.
    """
    if not api_key:
        raise ValueError("Gemini API key is required")

    img_b64 = page_to_base64_image(page, dpi=200)

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": img_b64
                        }
                    },
                    {
                        "text": SYSTEM_PROMPT + "\n\nตัวอย่าง JSON schema ที่ต้องตอบกลับ:\n" + json.dumps(JSON_SCHEMA_EXAMPLE, ensure_ascii=False)
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    for attempt in range(max_retries + 1):
        try:
            resp, used_model = _post_generate_content(api_key, payload, timeout=45)
            if resp is not None and resp.status_code == 200:
                res_data = resp.json()
                candidates = res_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        raw_json_str = parts[0].get("text", "{}")
                        record = json.loads(raw_json_str)

                        # Standardize fields
                        record["no"] = page_num
                        record["person_status"] = ""  # strictly empty

                        # Clean up mother / father names if any label leaked
                        if record.get("mother_name"):
                            m_name = record["mother_name"]
                            for bad in ["มารดาชื่อ", "ชื่อ", "สัญชาติ", "ไทย", "-", ":", "."]:
                                m_name = m_name.replace(bad, "")
                            record["mother_name"] = m_name.strip()

                        if record.get("father_name"):
                            f_name = record["father_name"]
                            for bad in ["บิดาชื่อ", "ชื่อ", "สัญชาติ", "ไทย", "-", ":", "."]:
                                f_name = f_name.replace(bad, "")
                            record["father_name"] = f_name.strip()

                        return record
            else:
                status_code = resp.status_code if resp is not None else "NoResponse"
                err_msg = resp.text if resp is not None else "No response"
                print(f"[Gemini API Error {status_code} ({used_model})] Attempt {attempt+1}: {err_msg}")
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"Gemini API error ({status_code}): {err_msg}")
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise e

    raise RuntimeError("Failed to extract data using Gemini AI")


def test_gemini_connection(api_key):
    """Verifies that the Gemini API Key is valid and functional."""
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Hello, please reply with 'OK' if you can read this."}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0
        }
    }
    try:
        resp, used_model = _post_generate_content(api_key, payload, timeout=15)
        if resp is not None and resp.status_code == 200:
            return True, f"เชื่อมต่อ Google Gemini API สำเร็จเรียบร้อยแล้ว! (โมเดล: {used_model})"
        else:
            code = resp.status_code if resp is not None else "N/A"
            text = resp.text if resp is not None else "ไม่สามารถติดต่อเซิร์ฟเวอร์ได้"
            return False, f"เชื่อมต่อไม่สำเร็จ (Code {code}): {text}"
    except Exception as e:
        return False, f"ข้อผิดพลาดการเชื่อมต่อ: {str(e)}"
