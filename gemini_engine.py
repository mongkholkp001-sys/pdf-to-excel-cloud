import os
import io
import json
import base64
import time
import requests
import fitz
from PIL import Image

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

_ACTIVE_MODELS = {}

def get_supported_models(api_key):
    """Query Google Gemini ModelService to get available models for this key."""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            supported = [
                m["name"].replace("models/", "")
                for m in models
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            if supported:
                return supported
    except Exception as e:
        print(f"[Gemini AI] ListModels exception: {e}")
    
    return [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.1-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash-latest"
    ]

def rank_models(models_list):
    """Ranks models with latest, fastest Gemini 3.5 / 3.1 Flash models first."""
    priorities = [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash",
        "gemini-3-flash",
        "gemini-3.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash-002",
        "gemini-1.5-flash",
        "gemini-3.5-pro",
        "gemini-3.1-pro",
        "gemini-3-pro",
        "gemini-2.5-pro",
        "gemini-2.0-pro",
        "gemini-1.5-pro"
    ]
    def score(name):
        name_lower = name.lower()
        for idx, p in enumerate(priorities):
            if p == name_lower:
                return idx
            if p in name_lower:
                return idx + 0.1
        if "3.5" in name_lower:
            return len(priorities)
        if "3." in name_lower:
            return len(priorities) + 1
        if "flash" in name_lower:
            return len(priorities) + 2
        return len(priorities) + 3
        
    return sorted(models_list, key=score)

def clean_and_parse_json(raw_text):
    """Safely extracts and parses JSON even if wrapped in markdown code blocks."""
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    return json.loads(text)

def page_to_base64_image(page, dpi=200):
    """Render PyMuPDF page to high-quality JPEG base64 string."""
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("jpeg")
    return base64.b64encode(img_bytes).decode("utf-8")

def test_gemini_connection(api_key):
    """Verifies that the Gemini API Key is valid and functional, testing across all supported models."""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        resp = requests.get(url, timeout=12)
        if resp.status_code != 200:
            err_detail = "API Key ไม่ถูกต้อง หรือไม่มีสิทธิ์เข้าถึง"
            try:
                err_json = resp.json()
                if "error" in err_json and "message" in err_json["error"]:
                    err_detail = err_json["error"]["message"]
            except Exception:
                pass
            return False, f"เชื่อมต่อไม่สำเร็จ (Code {resp.status_code}): {err_detail}"

        data = resp.json()
        models = data.get("models", [])
        supported = [
            m["name"].replace("models/", "")
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

        if not supported:
            return False, "API Key ถูกต้อง แต่ไม่พบโมเดลที่รองรับ generateContent ในโปรเจกต์นี้"

        ranked = rank_models(supported)
        last_error = ""

        # Test through the ranked models until one succeeds!
        for model_name in ranked:
            post_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": "สวัสดี ตอบสั้นๆ ว่า OK"}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.0
                }
            }
            try:
                test_resp = requests.post(post_url, headers={"Content-Type": "application/json"}, json=payload, timeout=10)
                if test_resp.status_code == 200:
                    _ACTIVE_MODELS[api_key] = model_name
                    return True, f"เชื่อมต่อสำเร็จ 100%! ระบบตรวจพบและเชื่อมต่อกับโมเดล '{model_name}' เรียบร้อยแล้ว"
                else:
                    try:
                        err_json = test_resp.json()
                        last_error = err_json.get("error", {}).get("message", test_resp.text)
                    except Exception:
                        last_error = f"Code {test_resp.status_code}: {test_resp.text}"
            except Exception as e:
                last_error = str(e)

        return False, f"เชื่อมต่อไม่สำเร็จ ({last_error})"
    except Exception as e:
        return False, f"ข้อผิดพลาดการเชื่อมต่อ: {str(e)}"

def extract_page_with_gemini(page, page_num=1, api_key=None, max_retries=2):
    """
    Extracts Thai Civil Registration data from a PDF page using Google Gemini Vision.
    Returns a standardized dictionary ready for Excel export.
    """
    if not api_key:
        raise ValueError("Gemini API key is required")

    supported = get_supported_models(api_key)
    ranked = rank_models(supported) if supported else ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
    active_model = _ACTIVE_MODELS.get(api_key, ranked[0])
    candidate_models = [active_model] + [m for m in ranked if m != active_model]

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

    headers = {
        "Content-Type": "application/json"
    }

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=45)
                if resp.status_code == 200:
                    res_data = resp.json()
                    candidates = res_data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            raw_json_str = parts[0].get("text", "{}")
                            record = clean_and_parse_json(raw_json_str)

                            # Cache working model for subsequent pages
                            _ACTIVE_MODELS[api_key] = model_name

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
                elif resp.status_code == 404:
                    print(f"[Gemini AI] Model {model_name} returned 404, switching to next model...")
                    break
                else:
                    err_msg = resp.text
                    print(f"[Gemini API Error {resp.status_code}] Attempt {attempt+1}: {err_msg}")
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                break

    raise RuntimeError("Failed to extract data using Gemini AI across available models")
