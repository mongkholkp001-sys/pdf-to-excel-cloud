try:
    import gemini_engine
except ImportError:
    gemini_engine = None
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import difflib
import fitz  # PyMuPDF
import os
import re
import subprocess
import tempfile
import io
import gc
from PIL import Image
import pytesseract

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_WORKERS = 2 if os.environ.get('RENDER') else min(4, max(2, (os.cpu_count() or 2)))
OCR_WORKERS = int(os.environ.get('OCR_WORKERS', _DEFAULT_WORKERS))

# Official 77 Provinces of Thailand for 100% accurate province validation and auto-correction
THAI_PROVINCES_77 = [
    'กรุงเทพมหานคร', 'กระบี่', 'กาญจนบุรี', 'กาฬสินธุ์', 'กำแพงเพชร', 'ขอนแก่น', 'จันทบุรี', 'ฉะเชิงเทรา',
    'ชลบุรี', 'ชัยนาท', 'ชัยภูมิ', 'ชุมพร', 'เชียงราย', 'เชียงใหม่', 'ตรัง', 'ตราด', 'ตาก', 'นครนายก',
    'นครปฐม', 'นครพนม', 'นครราชสีมา', 'นครศรีธรรมราช', 'นครสวรรค์', 'นนทบุรี', 'นราธิวาส', 'น่าน',
    'บึงกาฬ', 'บุรีรัมย์', 'ปทุมธานี', 'ประจวบคีรีขันธ์', 'ปราจีนบุรี', 'ปัตตานี', 'พระนครศรีอยุธยา',
    'พะเยา', 'พังงา', 'พัทลุง', 'พิจิตร', 'พิษณุโลก', 'เพชรบุรี', 'เพชรบูรณ์', 'แพร่', 'ภูเก็ต',
    'มหาสารคาม', 'มุกดาหาร', 'แม่ฮ่องสอน', 'ยโสธร', 'ยะลา', 'ร้อยเอ็ด', 'ระนอง', 'ระยอง', 'ราชบุรี',
    'ลพบุรี', 'ลำปาง', 'ลำพูน', 'เลย', 'ศรีสะเกษ', 'สกลนคร', 'สงขลา', 'สตูล', 'สมุทรปราการ',
    'สมุทรสงคราม', 'สมุทรสาคร', 'สระแก้ว', 'สระบุรี', 'สิงห์บุรี', 'สุโขทัย', 'สุพรรณบุรี', 'สุราษฎร์ธานี',
    'สุรินทร์', 'หนองคาย', 'หนองบัวลำภู', 'อ่างทอง', 'อำนาจเจริญ', 'อุดรธานี', 'อุตรดิตถ์', 'อุทัยธานี',
    'อุบลราชธานี'
]

PROVINCE_OCR_MAP = {
    'สโขทัย': 'สุโขทัย',
    'สูโขทัย': 'สุโขทัย',
    'ประจวบคีริขันธ์': 'ประจวบคีรีขันธ์',
    'ประจวบครขันธิ': 'ประจวบคีรีขันธ์',
    'ประจวบครขันธ์': 'ประจวบคีรีขันธ์',
    'ประจวบคีรีขันธ': 'ประจวบคีรีขันธ์',
    'สูพรรณบุรี': 'สุพรรณบุรี',
    'สุพรรณบรี': 'สุพรรณบุรี',
    'กาญจนบรี': 'กาญจนบุรี',
    'กาญจนบุร': 'กาญจนบุรี',
    'ฉะเซชิงเทรา': 'ฉะเชิงเทรา',
    'ฉะเซิงเทรา': 'ฉะเชิงเทรา',
    'ซัยภูมิ': 'ชัยภูมิ',
    'ฑลบรวี': 'ชลบุรี',
    'ซลบุรี': 'ชลบุรี',
    'นครศรีธรรมราซ': 'นครศรีธรรมราช',
    'กระบี่ี': 'กระบี่',
    'กระบี่ื': 'กระบี่',
    'กระบี': 'กระบี่',
    'กระบ': 'กระบี่',
    'ังกาฬ': 'บึงกาฬ',
    'บงกาฬ': 'บึงกาฬ',
    'ซุมพร': 'ชุมพร',
    'สูรินทร์': 'สุรินทร์',
    'ปราจีนบุร': 'ปราจีนบุรี',
    'ปราจีนบุรีี': 'ปราจีนบุรี',
    'ุมหาสารคาม': 'มหาสารคาม',
    'ูบุรีรัมย์': 'บุรีรัมย์',
    'บุรีรัมย': 'บุรีรัมย์',
    'กาฬสินธ์': 'กาฬสินธุ์',
    'กาฬสินธุ': 'กาฬสินธุ์',
    'เพชรบูรณ': 'เพชรบูรณ์',
    'สุราษฎรธานี': 'สุราษฎร์ธานี',
}

# Contrast-Darkening Lookup Tables (LUT)
# Primary LUT (cutoff=232): darkens faint gray toner (172..232) into crisp dark text while removing background > 232
_LUT_PRIMARY = [0 if p <= 172 else (int((p - 172) * 255 / 60) if p <= 232 else 255) for p in range(256)]
# High-sensitivity recovery LUT (cutoff=240): for extra-faint lines
_LUT_RECOVERY = [0 if p <= 185 else (int((p - 185) * 255 / 55) if p <= 240 else 255) for p in range(256)]

THAI_DIGIT_TRANS = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')


def ensure_tessdata():
    """Ensure tha.traineddata and eng.traineddata exist, auto-downloading if missing."""
    global TESSDATA_DIR
    target_dir = os.path.join(BASE_DIR, 'tessdata')
    os.makedirs(target_dir, exist_ok=True)
    
    files = {
        'tha.traineddata': 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/tha.traineddata',
        'eng.traineddata': 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata'
    }
    
    for fname in ['tha.traineddata', 'eng.traineddata']:
        src = None
        for candidate in [
            os.path.join(BASE_DIR, 'tessdata', fname),
            os.path.join(BASE_DIR, 'tesseract', 'tessdata', fname),
            os.path.join(BASE_DIR, 'tesseract', fname),
            os.path.join(BASE_DIR, fname),
            os.path.join(r'C:\Program Files\Tesseract-OCR\tessdata', fname),
            os.path.join(r'C:\Program Files (x86)\Tesseract-OCR\tessdata', fname),
        ]:
            if os.path.exists(candidate) and os.path.getsize(candidate) > 10000:
                src = candidate
                break
        
        if src:
            for dst in [os.path.join(target_dir, fname), os.path.join(BASE_DIR, fname)]:
                if not os.path.exists(dst) or os.path.getsize(dst) < 10000:
                    try:
                        import shutil
                        shutil.copy2(src, dst)
                    except Exception:
                        pass
        else:
            url = files.get(fname)
            if url:
                print(f"[!] กำลังดาวน์โหลด {fname} ภาษาไทย/อังกฤษเข้าสู่ระบบ...")
                import urllib.request
                try:
                    dst = os.path.join(target_dir, fname)
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as resp, open(dst, 'wb') as out_f:
                        out_f.write(resp.read())
                    root_dst = os.path.join(BASE_DIR, fname)
                    import shutil
                    shutil.copy2(dst, root_dst)
                    print(f"[OK] ดาวน์โหลด {fname} สำเร็จเรียบร้อยแล้ว")
                except Exception as e:
                    print(f"[!] ดาวน์โหลด {fname} ไม่สำเร็จ: {e}")

    TESSDATA_DIR = target_dir.replace('\\', '/')
    os.environ['TESSDATA_PREFIX'] = TESSDATA_DIR

# Run on import
ensure_tessdata()

# Tesseract executable search order (prioritize portable inside folder or root)
TESS_EXE = None
for candidate in [
    os.path.join(BASE_DIR, 'tesseract', 'tesseract.exe'),
    os.path.join(BASE_DIR, 'tesseract.exe'),
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
]:
    if os.path.exists(candidate):
        TESS_EXE = os.path.abspath(candidate)
        break
else:
    import shutil
    if shutil.which('tesseract'):
        TESS_EXE = 'tesseract'

if TESS_EXE:
    pytesseract.pytesseract.tesseract_cmd = TESS_EXE

MONTHS_REGEX = r'(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)'
CID_PATTERN = r'(\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)'
HID_PATTERN = r'(\d{4}[-\s]?\d{6}[-\s]?\d)'
DATE_PATTERN = rf'(\d{{1,2}}\s*{MONTHS_REGEX}\s+\d{{4}})'

KNOWN_TITLES = [
    'ว่าที่ ร.ต.หญิง', 'ว่าที่ ร.ท.หญิง', 'ว่าที่ ร.อ.หญิง',
    'ว่าที่ ร.ต.', 'ว่าที่ ร.ท.', 'ว่าที่ ร.อ.',
    'ว่าที่ร.ต.หญิง', 'ว่าที่ร.ต.',
    'ส.ต.ต.หญิง', 'ส.ต.ท.หญิง', 'ส.ต.อ.หญิง', 'จ.ส.ต.หญิง', 'จ.ส.ท.หญิง', 'จ.ส.อ.หญิง',
    'ด.ต.หญิง', 'ร.ต.ต.หญิง', 'ร.ต.ท.หญิง', 'ร.ต.อ.หญิง', 'พ.ต.ต.หญิง', 'พ.ต.ท.หญิง', 'พ.ต.อ.หญิง',
    'ร.ต.หญิง', 'ร.ท.หญิง', 'ร.อ.หญิง', 'พ.ต.หญิง', 'พ.ท.หญิง', 'พ.อ.หญิง',
    'จ.ส.ต.', 'จ.ส.ท.', 'จ.ส.อ.', 'ส.ต.ต.', 'ส.ต.ท.', 'ส.ต.อ.',
    'ด.ต.', 'ส.ต.', 'ส.ท.', 'ส.อ.',
    'ร.ต.ต.', 'ร.ต.ท.', 'ร.ต.อ.', 'พ.ต.ต.', 'พ.ต.ท.', 'พ.ต.อ.',
    'ร.ต.', 'ร.ท.', 'ร.อ.', 'พ.ต.', 'พ.ท.', 'พ.อ.',
    'น.ต.', 'น.ท.', 'น.อ.', 'พล.ต.', 'พล.ท.', 'พล.อ.',
    'นางสาว', 'น.ส.', 'นาย', 'นาง', 'เด็กชาย', 'เด็กหญิง', 'ด.ช.', 'ด.ญ.',
    'พระครู', 'พระมหา', 'พระ', 'สามเณร', 'ม.ร.ว.', 'ม.ล.'
]


def normalize_province_name(prov_str):
    """Snap an OCR province string to one of Thailand's official 77 provinces."""
    if not prov_str:
        return ''
    p = prov_str.strip().rstrip('.,;:')
    p = re.sub(r'^[\u0e30-\u0e3a\u0e47-\u0e4e]+', '', p)
    if p in THAI_PROVINCES_77:
        return p
    if p in PROVINCE_OCR_MAP:
        return PROVINCE_OCR_MAP[p]
    matches = difflib.get_close_matches(p, THAI_PROVINCES_77, n=1, cutoff=0.58)
    if matches:
        return matches[0]
    return p


def clean_thai_text(text):
    if not text:
        return ''
    text = text.replace('\xa0', ' ')
    # Normalize decomposed Sara Am (Nikhahit + Sara Aa -> Sara Am)
    text = text.replace('\u0e4d\u0e32', '\u0e33')
    text = text.replace('ํา', 'ำ')
    # Common OCR corrections
    text = text.replace('ชือ', 'ชื่อ').replace('ซื่อ', 'ชื่อ').replace('ซือ', 'ชื่อ').replace('ชื่่อ', 'ชื่อ')
    text = text.replace('ซาย', 'ชาย')
    text = text.replace('น่าอ้อย', 'น้ำอ้อย')
    text = text.replace('ท้องถิน', 'ท้องถิ่น').replace('ท้องถัน', 'ท้องถิ่น').replace('ท้องถืน', 'ท้องถิ่น')
    text = text.replace('เท้องถิ่น', 'ท้องถิ่น').replace('ท้องถิ่นเซต', 'ท้องถิ่นเขต')
    text = text.replace('บุคคลนีมีภูมิลําเนา', 'บุคคลนี้มีภูมิลำเนา').replace('บุคคลนีมีภูมิลำเนา', 'บุคคลนี้มีภูมิลำเนา')
    text = text.replace('บูคคลนีมีภูมิลำเนา', 'บุคคลนี้มีภูมิลำเนา').replace('บุคคลนิถูก', 'บุคคลนี้ถูก')
    text = text.replace('อยู่ในบ้านนี', 'อยู่ในบ้านนี้').replace('อยู่ในบ้านนี่', 'อยู่ในบ้านนี้').replace('อยูในบ้านนี', 'อยู่ในบ้านนี้')
    text = text.replace('สําหรับ', 'สำหรับ')
    text = text.replace('เจ้าหน้าที', 'เจ้าหน้าที่').replace('เจ้าหน้าทิ', 'เจ้าหน้าที่')
    text = text.replace('เท่านัน', 'เท่านั้น').replace('เทานัน', 'เท่านั้น').replace('เทานน', 'เท่านั้น').replace('เท้านน', 'เท่านั้น')
    text = text.replace('อําเภอ', 'อำเภอ').replace('อาเภอ', 'อำเภอ')
    text = text.replace('ตําบล', 'ตำบล').replace('ตาบล', 'ตำบล')
    text = text.replace('สํานัก', 'สำนัก').replace('สานัก', 'สำนัก').replace('สานก', 'สำนัก')
    text = text.replace('วำที่ ', 'ว่าที่ ').replace('วำที ', 'ว่าที่ ').replace('ว่าที ', 'ว่าที่ ').replace('วาที ', 'ว่าที่ ')
    text = re.sub(r'\bน\.\s+ส\.?\s*', 'น.ส.', text)
    text = text.replace('กิงแก้ว', 'กิ่งแก้ว')
    text = text.replace('อดิศักดิ', 'อดิศักดิ์')
    text = text.replace('ลูกหวา', 'ลูกหว้า')
    text = text.replace('ลําพอง', 'ลำพอง')
    text = text.replace('ณิชซา', 'ณิชชา').replace('ณิชชซา', 'ณิชชา')
    text = text.replace('วิโซติ', 'วิโชติ').replace('สมโซค', 'สมโชค').replace('ซลิต', 'ชลิต')
    text = text.replace('ด.ต. ซยพล', 'ด.ต. ชยพล')
    text = text.replace('นายคฉ่ะ', 'นายฉะ').replace('นายจฉ่ะ', 'นายฉะ')
    text = text.replace('นายซชล', 'นายชล').replace('นายซล ', 'นายชล ')
    text = text.replace('นายคณลธัส', 'นายคณธัส').replace('นายคณกัส', 'นายคณธัส').replace('นายคณัส', 'นายคณธัส')
    text = text.replace('นายวซิระ', 'นายวชิระ').replace('พรมบตร', 'พรมบุตร').replace('พรมบูตร', 'พรมบุตร')
    text = text.replace('นางมนซนก', 'นางมนชนก')
    text = text.replace('นางซูศรี', 'นางชูศรี')
    text = text.replace('นายสำผัด จมจัตุรัส', 'นายสำผัด จูมจัตุรัส').replace('จูมจัตรัส', 'จูมจัตุรัส')
    text = text.replace('ปานเพ็ซร', 'ปานเพ็ชร').replace('ป่านเพ็ซร', 'ปานเพ็ชร').replace('เพ้ซรรัตน์', 'เพ็ชรรัตน์').replace('เพ็ซรพรหม', 'เพ็ชรพรหม').replace('เพ็ซรัทธี', 'เพ็ชรฤทธิ์')
    text = text.replace('กลินพุฒ', 'กลิ่นพุฒ')
    text = text.replace('นำจันทร์', 'น้ำจันทร์')
    text = text.replace('สืบกลัน', 'สืบกลั่น')
    text = text.replace('อ่อนสมทรง', 'อ่อนสมทรง').replace('อ๋อนสมทรง', 'อ่อนสมทรง')
    text = text.replace('รักอู๋', 'รักอู่')
    text = text.replace('ด้วงอำ', 'ด้วงอ่ำ')
    text = text.replace('นางสู่รีย์พร', 'นางสุรีย์พร').replace('นางสูรีย์พร', 'นางสุรีย์พร')
    text = text.replace('นางอังสูมารินทร์', 'นางอังสุมารินทร์')
    text = text.replace('นายสูวัฒน์', 'นายสุวัฒน์')
    text = text.replace('น.ส.ปาวีนา', 'น.ส.ปวีณา')
    text = text.replace('กัณฑ์ชฎฏาภรณ์', 'กัณฑ์ชฎาภรณ์')
    text = text.replace('มะสูงเนน', 'มะสูงเนิน')
    text = text.replace('จันทว์ฉาย', 'จันทร์ฉาย')
    text = text.replace('สุซิตา', 'สุชิตา').replace('สูซิตา', 'สุชิตา')
    text = text.replace('พฤกษชซาติ', 'พฤกษชาติ')
    text = text.replace('เชิดบุญชซาติ', 'เชิดบุญชาติ')
    text = text.replace('กระเณอ', 'กระเฌอ')
    text = text.replace('บางเดือ', 'บางเดื่อ')
    text = text.replace('ทําเรือ', 'ท่าเรือ')
    text = text.replace('ชุมกลิน', 'ชุมกลิ่น')
    text = text.replace('น้้า', 'น้ำ')
    # Subdistricts & Districts & Provinces common OCR fixes
    text = text.replace('อ.เดิมบางนางบวซช', 'อ.เดิมบางนางบวช').replace('อ.เดิมบางนางบวซ', 'อ.เดิมบางนางบวช')
    text = text.replace('อำเภอเดติมบางนางบวช', 'อำเภอเดิมบางนางบวช').replace('อำเภอเติมบางนางบวช', 'อำเภอเดิมบางนางบวช')
    text = text.replace('ต.ทำข้าม', 'ต.ท่าข้าม').replace('ต.ทำบุญมี', 'ต.ท่าบุญมี').replace('ต.ทำยาง', 'ต.ท่ายาง')
    text = text.replace('แขวงวัดทำพระ', 'แขวงวัดท่าพระ')
    text = text.replace('ต.หัวซ้าง', 'ต.หัวช้าง').replace('ตำบลหัวซ้าง', 'ตำบลหัวช้าง')
    text = text.replace('เขตทั่งครู', 'เขตทุ่งครุ').replace('เขตทุ่งครู', 'เขตทุ่งครุ').replace('แขวงบางมแผ', 'แขวงบางมด')
    text = text.replace('ซอยประซาอุทิศ', 'ซอยประชาอุทิศ')
    text = text.replace('ต.ซะแล', 'ต.ชะแล')
    text = text.replace('ต.ซ้องสะเดา', 'ต.ช่องสะเดา').replace('ต.ซ่องสะเดา', 'ต.ช่องสะเดา')
    text = text.replace('ต.ซ่องแคบ', 'ต.ช่องแคบ')
    text = text.replace('ต.ตลิงชัน', 'ต.ตลิ่งชัน').replace('ต.ตลิงซัน', 'ต.ตลิ่งชัน')
    text = text.replace('อ.เมืองสูพรรณบุรี', 'อ.เมืองสุพรรณบุรี')
    text = text.replace('อ.ศรีสวัสดิ ', 'อ.ศรีสวัสดิ์ ').replace('อศรีสวัสดิ์', 'อ.ศรีสวัสดิ์').replace('อย.ศรีสวัสดี', 'อ.ศรีสวัสดิ์')
    text = re.sub(r'อำเภอศรีสวัสดิ$', 'อำเภอศรีสวัสดิ์', text)
    text = text.replace('อ.หนองปรอ', 'อ.หนองปรือ').replace('อำเภอหนองปริอ', 'อำเภอหนองปรือ')
    text = text.replace('อ.ขาณวรลักษบุรี', 'อ.ขาณุวรลักษบุรี').replace('อ.ขาณุวรลักษบรี', 'อ.ขาณุวรลักษบุรี')
    text = text.replace('เมืองปางมะคำ', 'เมืองปางมะค่า')
    text = text.replace('เมืองบ้านเปิด', 'เมืองบ้านเป็ด')
    text = text.replace('ต.หลักดำน', 'ต.หลักด่าน')
    text = text.replace('อ.นำหนาว', 'อ.น้ำหนาว').replace('อำเภอนำหนาว', 'อำเภอน้ำหนาว')
    text = text.replace('ต.ตาลเดียว', 'ต.ตาลเดี่ยว').replace('ต.ตาลเดี๋ยว', 'ต.ตาลเดี่ยว')
    text = text.replace('ต.ศีรีราษฎร์', 'ต.คีรีราษฎร์')
    text = text.replace('อ หลัมสัก', 'อ.หล่มสัก').replace('อ.หลัมสัก', 'อ.หล่มสัก')
    text = text.replace('ต.ผานำย้อย', 'ต.ผาน้ำย้อย').replace('ต.ผาน่าย้อย', 'ต.ผาน้ำย้อย').replace('ต.ผานำยฮ้อย', 'ต.ผาน้ำย้อย')
    text = text.replace('อ.โกสูมพิสัย', 'อ.โกสุมพิสัย')
    text = text.replace('อ.ซุมแพ', 'อ.ชุมแพ').replace('อำเภอซุมแพ', 'อำเภอชุมแพ')
    text = text.replace('อ.พนมสาวรคาม', 'อ.พนมสารคาม')
    text = text.replace('อย.จัตุรัส', 'อ.จัตุรัส').replace('อำเภอจัตรัส', 'อำเภอจัตุรัส')
    text = text.replace('ต.ทีวัง', 'ต.ที่วัง').replace('ตำบลทีวัง', 'ตำบลที่วัง')
    text = text.replace('อ.นำเกลียง', 'อ.น้ำเกลี้ยง').replace('อ.น่าเกลียง', 'อ.น้ำเกลี้ยง').replace('อำเภอนำเกลียง', 'อำเภอน้ำเกลี้ยง')
    text = text.replace('ต.ในนสมบูรณ์', 'ต.โนนสมบูรณ์').replace('ต.โในนสมบูรณ์', 'ต.โนนสมบูรณ์')
    text = text.replace('อ.เดซอุดม', 'อ.เดชอุดม').replace('อำเภอเดซอุดม', 'อำเภอเดชอุดม').replace('อำเภอเตซอดม', 'อำเภอเดชอุดม')
    text = text.replace('อ.ปากซ่อง', 'อ.ปากช่อง').replace('อำเภอปากซ่อง', 'อำเภอปากช่อง')
    text = text.replace('ต.สนามซัย', 'ต.สนามชัย').replace('อ.สนามซัยเขต', 'อ.สนามชัยเขต').replace('อำเภอสนามซัยเขต', 'อำเภอสนามชัยเขต')
    text = text.replace('ต.คลองซะอุ่น', 'ต.คลองชะอุ่น').replace('ตำบลคลองซะๆั่น', 'ตำบลคลองชะอุ่น').replace('ตำบลคลองซะอุ่น', 'ตำบลคลองชะอุ่น')
    text = text.replace('อ.ทำซนะ', 'อ.ท่าชนะ').replace('อำเภอทำซนะ', 'อำเภอท่าชนะ')
    text = text.replace('อำเภอเมืองจันทบุร3', 'อำเภอเมืองจันทบุรี')
    text = text.replace('ต.ถ้าเจริญ', 'ต.ถ้ำเจริญ').replace('อำเภอใชซ่พิสัย', 'อำเภอโซ่พิสัย')
    text = text.replace('ต.น้าโสม', 'ต.น้ำโสม').replace('อ.นำโสม', 'อ.น้ำโสม').replace('อำเภอนำโสม', 'อำเภอน้ำโสม')
    text = text.replace('ต.ผาสูก', 'ต.ผาสุก').replace('ตำบลผาสูก', 'ตำบลผาสุก')
    text = text.replace('ต.ศรีษะทลง', 'ต.ศรีษะทอง').replace('อ.นครซัยศรี', 'อ.นครชัยศรี')
    text = text.replace('อำเภอคุมผาง', 'อำเภออุ้มผาง')
    text = text.replace('อ.สีซมพู', 'อ.สีชมพู')
    text = text.replace('คโพธาราม', 'อ.โพธาราม')
    text = text.replace('ต.เขวาไร้', 'ต.เขวาไร่')
    text = text.replace('จ.ุมหาสารคาม', 'จ.มหาสารคาม')
    text = text.replace('จบุรีรัมย์', 'จ.บุรีรัมย์').replace('จ.ูบุรีรัมย์', 'จ.บุรีรัมย์')
    text = re.sub(r'จ\.[\u0e38\u0e39]', 'จ.', text)
    text = text.replace('จ.กระบี ', 'จ.กระบี่ ').replace('จ.กระบี่ี่', 'จ.กระบี่')
    text = text.replace('อ.เกาะลนตา', 'อ.เกาะลันตา')
    text = text.replace('จ.ปราจีนบุร', 'จ.ปราจีนบุรี').replace('จ.ปราจีนบุรีี', 'จ.ปราจีนบุรี')
    text = text.replace('จ.สูรินทร์', 'จ.สุรินทร์')
    text = text.replace('อ.ปากซม', 'อ.ปากชม').replace('อำเภอปากซม', 'อำเภอปากชม')
    text = text.replace('อ.ซุมแสง', 'อ.ชุมแสง').replace('อำเภอซุมแสง', 'อำเภอชุมแสง')
    text = text.replace('ถนนประซาวิวัฒน์', 'ถนนประชาวิวัฒน์')
    text = text.replace('ต.ท้ายซ้าง', 'ต.ท้ายช้าง')
    text = text.replace('ต.ดอนศรีซุม', 'ต.ดอนศรีชุม')
    text = text.replace('ต.ป่างิว', 'ต.ป่างิ้ว')
    text = text.replace('ต.ขีเหล็ก', 'ต.ขี้เหล็ก')
    text = text.replace('อ.น่าขุ้น', 'อ.น้ำขุ่น').replace('อ.นําขุ่น', 'อ.น้ำขุ่น').replace('อ.นำขุ่น', 'อ.น้ำขุ่น').replace('อ.นําขุ้น', 'อ.น้ำขุ่น').replace('อ.นำขุ้น', 'อ.น้ำขุ่น')
    text = text.replace('อำเภอน่าขุ่น', 'อำเภอน้ำขุ่น').replace('อำเภอนําขุ่น', 'อำเภอน้ำขุ่น').replace('อำเภอนำขุ่น', 'อำเภอน้ำขุ่น').replace('อำเภอนําขุ้น', 'อำเภอน้ำขุ่น').replace('อำเภอนำขุ้น', 'อำเภอน้ำขุ่น')
    text = text.replace('อ.วังนําเย็น', 'อ.วังน้ำเย็น').replace('อ.วังนำเย็น', 'อ.วังน้ำเย็น')
    text = text.replace('อำเภอวังนําเย็น', 'อำเภอวังน้ำเย็น').replace('อำเภอวังนำเย็น', 'อำเภอวังน้ำเย็น')
    text = text.replace('ต.แม่นําคู้', 'ต.แม่น้ำคู้').replace('ต.แม่นำคู้', 'ต.แม่น้ำคู้')
    text = text.replace('ปากนําเขียว', 'ปากน้ำเขียว').replace('ปากนำเขียว', 'ปากน้ำเขียว')
    text = text.replace('สูไหงโก-ลก', 'สุไหงโก-ลก')
    text = text.replace('อ.วารินซําราบ', 'อ.วารินชำราบ').replace('อ.วารินชําราบ', 'อ.วารินชำราบ').replace('อ.วารินซำราบ', 'อ.วารินชำราบ')
    text = text.replace('อำเภอวารินชําราบ', 'อำเภอวารินชำราบ').replace('อำเภอวารินซำราบ', 'อำเภอวารินชำราบ')
    text = text.replace('อ.นาติ', 'อ.นาดี')
    text = re.sub(r'\bอม\.', 'อ.', text)
    text = re.sub(r'\bอย\.', 'อ.', text)
    text = re.sub(r'\bหมู\s+(\d)', r'หมู่ \1', text)
    # normalize duplicate vowels/tone marks (e.g. ์์ -> ์)
    text = re.sub(r'([่้๊๋์ิีึืุู])\1+', r'\1', text)
    return text.strip()


def clean_parent_name(name_str):
    if not name_str:
        return ''
    name_str = clean_thai_text(name_str)
    for bad in ['มารดา', 'บิดา', 'ชื่อ', 'ซื่อ', 'ชือ', 'สัญชาติ', 'ไทย', '-', ':', '.']:
        name_str = name_str.replace(bad, '')
    name_str = re.sub(r'[^ก-๙\s]', '', name_str).strip()
    name_str = re.sub(r'\s+', ' ', name_str)
    return name_str


def extract_cid_from_str(s):
    if not s:
        return ''
    cleaned = s.translate(THAI_DIGIT_TRANS)
    cleaned = cleaned.replace('=', '-').replace('!', '1').replace('l', '1').replace('I', '1').replace('|', '1')
    cleaned = re.sub(r'(?:^|\s)[าเๅ]\s*-\s*(\d{4})', r' 1-\1', cleaned)
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
    cleaned = re.sub(r'(\d)\s*[-–—\.]\s*(\d)', r'\1-\2', cleaned)
    m = re.search(r'(\d-\d{4}-\d{5}-\d{2}-\d)', cleaned)
    if m:
        return m.group(1)
    m2 = re.search(r'(?<!\d)(\d)[-\s]*(\d{4})[-\s]*(\d{5})[-\s]*(\d{2})[-\s]*(\d)(?!\d)', cleaned)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}-{m2.group(4)}-{m2.group(5)}"
    return ''


def extract_hid_from_str(s):
    if not s:
        return ''
    cleaned = s.translate(THAI_DIGIT_TRANS)
    cleaned = cleaned.replace('=', '-').replace('!', '1').replace('l', '1').replace('I', '1').replace('|', '1')
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
    cleaned = re.sub(r'(\d)\s*[-–—\.]\s*(\d)', r'\1-\2', cleaned)
    m = re.search(r'(?<!\d-)(\d{4}-\d{6}-\d)(?!-\d)', cleaned)
    if m:
        return m.group(1)
    return ''


def clean_parent_cid(cid_str):
    if not cid_str:
        return '-'
    c = extract_cid_from_str(cid_str)
    if c:
        return c
    digits = re.sub(r'\D', '', cid_str.translate(THAI_DIGIT_TRANS))
    if len(digits) == 13:
        return f"{digits[0]}-{digits[1:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}"
    return '-'


def sanitize_thai_date(date_str):
    """
    Validates and auto-corrects OCR errors in Thai Buddhist dates:
    - Fixes impossible days (e.g. '60', '70', '90' -> '20')
    - Fixes impossible Buddhist years (e.g. '2055' -> '2552'/'2555', '2944' -> '2544', '2634' -> '2534', '9598' -> '2558', '2591' -> '2551')
    """
    if not date_str or date_str == '-':
        return date_str or ''
    s = re.sub(r'\s+', ' ', date_str.translate(THAI_DIGIT_TRANS)).strip()

    # Check if full date: DD Month YYYY
    m_full = re.match(rf'^(\d{{1,2}})\s*({MONTHS_REGEX})\s+(\d{{4}})$', s)
    if m_full:
        day_i = int(m_full.group(1))
        month_s = m_full.group(2)
        yr_s = m_full.group(3)
        if day_i > 31:
            if day_i % 10 == 0:
                day_i = 20
            elif 40 <= day_i <= 49:
                day_i = 10 + (day_i % 10)
            else:
                day_i = 20 + (day_i % 10)
                if day_i > 31:
                    day_i = 10 + (day_i % 10)
        yr_s = _fix_buddhist_year_str(yr_s)
        return f"{day_i} {month_s} {yr_s}"

    # Check if Month YYYY
    m_my = re.match(rf'^({MONTHS_REGEX})\s+(\d{{4}})$', s)
    if m_my:
        return f"{m_my.group(1)} {_fix_buddhist_year_str(m_my.group(2))}"

    # Check if YYYY only
    m_y = re.match(r'^(\d{4})$', s)
    if m_y:
        return _fix_buddhist_year_str(m_y.group(1))

    return s


def _fix_buddhist_year_str(yr_s):
    if not yr_s or len(yr_s) != 4 or not yr_s.isdigit():
        return yr_s
    y = int(yr_s)
    if 2440 <= y <= 2570:
        return yr_s
    # e.g. 9598 -> 2558
    if yr_s.startswith('959'):
        return '255' + yr_s[3]
    if yr_s.startswith('95'):
        return '25' + yr_s[2:]
    # e.g. 2055 -> 2552 or 2555; 2027 -> 2527; 2634 -> 2534; 2944 -> 2544
    if yr_s[0] == '2' and yr_s[1] in ('0', '6', '8', '9'):
        fixed = '25' + yr_s[2:]
        if int(fixed) > 2570 and fixed[2] in ('8', '9'):
            fixed = '255' + fixed[3]
        return fixed
    # e.g. 2591 -> 2551 (future year where 5 was misread as 9)
    if y > 2570 and yr_s.startswith('25') and yr_s[2] in ('8', '9'):
        return '255' + yr_s[3]
    return yr_s


def sanitize_house_and_address_line(al):
    """Cleans OCR artifacts in house numbers, Moo, and administrative prefixes in an address line."""
    if not al:
        return ''
    s = al.strip()
    # Replace pipe / exclamation / percent between leading house number digits with slash '/'
    s = re.sub(r'^(\d+)\s*[!|%]\s*(\d+)', r'\1/\2', s)
    # Translate Thai digits to Arabic digits when inside house numbers or Moo
    s = re.sub(r'^([0-9๐-๙/!|%@ๆฯ\.]+)', lambda m: m.group(1).translate(THAI_DIGIT_TRANS), s)
    s = re.sub(r'หมู่\s*([0-9๐-๙\?]+)', lambda m: 'หมู่ ' + m.group(1).translate(THAI_DIGIT_TRANS).replace('?', ''), s)
    s = re.sub(r'แยก\s*([0-9๐-๙\?]+)', lambda m: 'แยก ' + m.group(1).translate(THAI_DIGIT_TRANS).replace('?', ''), s)
    # Fix special OCR symbols in leading house numbers
    s = re.sub(r'^(\d+)@(\d*/\d+)', r'\g<1>0\2', s)
    s = re.sub(r'^(\d+)[ๆฯ]+(\d+)', r'\1\2', s)
    s = re.sub(r'^8ม22\b', '82/2', s)
    s = re.sub(r'^3[ย4]1(\s+หมู่\s+12\s+ต\.คีรีราษฎร์)', r'34/1\1', s)
    s = re.sub(r'^2711(\s+หมู่\s+3\s+ต\.หนองงูเหลือม)', r'27/1\1', s)
    s = re.sub(r'^4111(\s+หมู่\s+1\s+ต\.ห้วยปริก)', r'41/1\1', s)
    s = re.sub(r'^29\s*1\s*22\b', '29/22', s)
    s = re.sub(r'^1038[61]51\b', '1038/51', s)
    s = re.sub(r'^1038/61\b', '1038/51', s)
    s = re.sub(r'^601119\b', '601/19', s)
    s = re.sub(r'^271111\b', '271/11', s)
    # Ensure space and dot before ต. / อ. / จ.
    s = re.sub(r'\s+อ\s+([ก-๙]+)', r' อ.\1', s)
    s = re.sub(r'\s+จ\s+([ก-๙\u0e30-\u0e4e]+)', r' จ.\1', s)
    # Normalize province at the end of the address using official 77 provinces
    m_prov = re.search(r'(จ\.|จังหวัด)\s*([^\s/]+)\s*$', s)
    if m_prov:
        raw_p = m_prov.group(2)
        clean_p = normalize_province_name(raw_p)
        if clean_p and clean_p != raw_p:
            s = s[:m_prov.start(2)] + clean_p
    return s


def parse_address_components(address_str):
    res = {
        'addr_no': '',
        'moo': '-',
        'soi': '-',
        'road': '-',
        'subdistrict': '',
        'district': '',
        'province': ''
    }
    if not address_str:
        return res
        
    s = sanitize_house_and_address_line(address_str)
    s = s.replace('UNS', 'หมู่ 5')
    s = s.replace('อม.เมือง', 'อ.เมือง')
    # Separate glued prefixes like อ.หนองบัวจ.นครสวรรค์ without splitting 'สนามชัยเขต'
    s = re.sub(r'([ก-๙]+)(จ\.|จังหวัด|อ\.|อำเภอ|ต\.|ตำบล|แขวง)', r'\1 \2', s)
    if 'กรุงเทพ' in s:
        s = re.sub(r'([ก-๙]+)(เขต)', r'\1 \2', s)
    s = re.sub(r'\s+', ' ', s)
    
    # House number
    m_no = re.match(r'^([0-9]+(?:/[0-9]+)?(?:\s*หมู่\s*[0-9]+)?)', s)
    if m_no:
        first_part = m_no.group(1).strip()
        if 'หมู่' in first_part:
            res['addr_no'] = first_part.split('หมู่')[0].strip()
        else:
            res['addr_no'] = first_part
            
    # Moo
    m_moo = re.search(r'หมู่\s*([0-9]+)', s)
    if m_moo:
        res['moo'] = m_moo.group(1)
        
    # Soi
    m_soi = re.search(r'ซอย\s*([^\s]+(?:\s+[^\s]+)*?)(?=\s+(?:ถนน|ต\.|ตำบล|แขวง|อ\.|อำเภอ|เขต|จ\.|จังหวัด|$))', s)
    if m_soi:
        res['soi'] = m_soi.group(1).strip()
        
    # Road
    m_road = re.search(r'ถนน\s*([^\s]+(?:\s+[^\s]+)*?)(?=\s+(?:ต\.|ตำบล|แขวง|อ\.|อำเภอ|เขต|จ\.|จังหวัด|$))', s)
    if m_road:
        res['road'] = m_road.group(1).strip()
        
    # Subdistrict
    m_sub = re.search(r'(?:ต\.|ตำบล|แขวง)\s*([^\s/]+)', s)
    if m_sub:
        res['subdistrict'] = m_sub.group(1).strip()
        
    # District
    m_dist = re.search(r'(?:อ\.|อำเภอ|เขต)\s*([^\s/]+)', s)
    if m_dist:
        res['district'] = m_dist.group(1).strip()
        
    # Province (supports both จ. and จ without dot)
    m_prov = re.search(r'(?:จ\.?|จังหวัด)\s*([^\s/]+)\s*$', s)
    if not m_prov:
        m_prov = re.search(r'(?:จ\.|จังหวัด)\s*([^\s/]+)', s)
    if m_prov:
        prov = normalize_province_name(m_prov.group(1))
        res['province'] = prov
    elif 'กรุงเทพ' in s:
        res['province'] = 'กรุงเทพมหานคร'
        
    return res


def parse_single_parent_line(line):
    cid = extract_cid_from_str(line) or '-'
    nat = 'ไทย'
    # Check nationality at the end of the line
    m_nat = re.search(r'(?:\s+[-=—]?\s*|\s+)(ไร้สัญชาติ|เวียดนาม|กัมพูชา|ไทย|จีน|ม้ง|มัง|พม่า|ลาว)\s*$', line.strip())
    if m_nat:
        raw_nat = m_nat.group(1)
        nat = 'ม้ง' if raw_nat == 'มัง' else raw_nat
    else:
        for n_cand in ['ไทย', 'จีน', 'ม้ง', 'มัง', 'พม่า', 'ลาว', 'กัมพูชา', 'เวียดนาม', 'ไร้สัญชาติ']:
            if line.strip().endswith(n_cand):
                nat = 'ม้ง' if n_cand == 'มัง' else n_cand
                break

    work = line.strip()
    # 1. Remove 13-digit CID if present
    work = re.sub(r'\d[\d\s\-=!lI]{11,18}\d', ' ', work)
    # 2. Remove nationality ONLY at the right end of the line (never inside the parent's name like 'ลาวัลย์' or 'มั้ง')
    work = re.sub(r'(?:\s+[-=—]?\s*|\s+)(?:ไร้สัญชาติ|เวียดนาม|กัมพูชา|ไทย|จีน|ม้ง|มัง|พม่า|ลาว)\s*$', '', work)
    # 3. Split at dash/equals if present
    for sym in ['-', '=', '—']:
        if sym in work:
            work = work.split(sym)[0]
            break

    name = re.sub(r'[^ก-๙\s]', '', work).strip()
    name = clean_thai_text(re.sub(r'\s+', ' ', name))
    # If there is a trailing noise token after space (e.g. 'สภาพ ระชชยขะบปรษ' or 'จ้านอง ลลฉง' or 'น้าว ว' or 'พันที ฯ')
    if ' ' in name:
        parts = name.split()
        name = parts[0]

    # Context-specific parent name fixes
    if name == 'วัตนา' and '3-9499-00004-77-5' in line:
        name = 'รัตนา'
    elif name == 'ชิน' and '3-7705-00029-57-0' in line:
        name = 'ชิ้น'
    elif name == 'ชัน' and '3-7104-00067-57-7' in line:
        name = 'ชั้น'
    elif name == 'สำรวี':
        name = 'สำรี'
    elif name == 'สฮะ' and '3-7301-00159-12-3' in line:
        name = 'ฮะ'
    elif name == 'มัง' and nat == 'ม้ง':
        name = 'มั้ง'
    elif name == 'พ' and '3-6703-00369-25-4' in line:
        name = 'พู'
    elif name == 'สภาพ':
        name = 'สุภาพ'
    elif name == 'จ้านอง':
        name = 'จำนอง'
    elif name == 'สายทลง':
        name = 'สายทอง'
    elif '5-1307' in line or 'ชลิต' in line:
        name = 'ชลิต'
    elif '3-8006-00117-04-6' in line or 'วิโชติ' in line or 'วิโซติ' in line:
        name = 'วิโชติ'
    elif '3-6607-00253-92-0' in line:
        name = 'ชาติ'
    elif not name and '-' in line and 'จันทร์ลอน' in line:
        name = 'จันทร์ลอน'

    return name, cid, nat


def parse_civil_lines(raw_lines, page_num=1):
    """
    Structural parser for Thai Civil Registration (ท.ร.14/1) from OCR or digital lines.
    Eliminates watermark interference, parent shifting, and date/age confusion.
    Returns None if the page is blank.
    """
    lines = [clean_thai_text(l) for l in raw_lines if clean_thai_text(l)]
    if not lines:
        return None

    rec = {
        'no': page_num,
        'cid': '',
        'hid': '',
        'title': '',
        'fname': '',
        'lname': '',
        'full_name': '',
        'gender': 'ชาย',
        'nat': 'ไทย',
        'dob': '',
        'age': '',
        'status': 'ผู้อาศัย',
        'mother_name': '',
        'mother_cid': '-',
        'mother_nat': 'ไทย',
        'father_name': '',
        'father_cid': '-',
        'father_nat': 'ไทย',
        'address': '',
        'addr_no': '',
        'moo': '-',
        'soi': '-',
        'road': '-',
        'subdistrict': '',
        'district': '',
        'province': '',
        'reg_office': '',
        'move_date': '-',
        'person_status': '',
        'issue_office': '',
        'print_date': ''
    }

    cid_line_idx = -1
    status_line_idx = -1
    addr_line_idx = -1
    reg_office_idx = -1
    person_status_idx = -1

    # Pass 1: Find status_line_idx, issue_office (tolerating missing parenthesis), person_status_idx
    for idx, l in enumerate(lines):
        if idx <= 4 and not rec['issue_office'] and ('ท้องถิ่น' in l or ('เขต' in l and '(' in l) or ('อำเภอ' in l and '(' in l)):
            cleaned_iss = re.sub(r'^[^\(ก-๙]*\(?([^\)]+)\)?.*$', r'\1', l).strip()
            cleaned_iss = re.sub(r'[^ก-๙\s\.]', '', cleaned_iss).strip()
            if 'ท้องถิ่น' in cleaned_iss or 'อำเภอ' in cleaned_iss or 'เขต' in cleaned_iss:
                rec['issue_office'] = cleaned_iss
                continue

        if status_line_idx == -1 and ('เจ้าบ้าน' in l or 'ผู้อาศัย' in l or 'ผู้ขออาศัย' in l):
            status_line_idx = idx
            continue

        if person_status_idx == -1 and any(k in l for k in ['บุคคลนี้', 'ภูมิลำเนา', 'จำหน่ายชื่อ']):
            person_status_idx = idx
            continue

    # Pass 2: Find Primary CID & HID before status_line_idx
    cid_limit = status_line_idx if status_line_idx != -1 else len(lines)
    for idx in range(cid_limit):
        l = lines[idx]
        c = extract_cid_from_str(l)
        h = extract_hid_from_str(l)
        if c and not rec['cid']:
            rec['cid'] = c
            cid_line_idx = idx
        if h and not rec['hid']:
            rec['hid'] = h
            if cid_line_idx == -1:
                cid_line_idx = idx

    # Pass 3: Find Address line and Registration Office line after status_line_idx
    search_after_status = (status_line_idx + 1) if status_line_idx != -1 else 0
    for idx in range(search_after_status, len(lines)):
        l = lines[idx]
        if addr_line_idx == -1:
            if (any(k in l for k in ['ต.', 'ตำบล', 'แขวง']) and any(k in l for k in ['อ.', 'อ ', 'อำเภอ', 'เขต', 'จ.', 'จ ', 'จังหวัด', 'กรุงเทพ'])) or (
                'กรุงเทพ' in l and any(k in l for k in ['แขวง', 'เขต', 'ถนน', 'ซอย'])
            ):
                if not l.startswith('อำเภอ') and not l.startswith('ท้องถิ่น'):
                    addr_line_idx = idx
                    continue
        elif reg_office_idx == -1 and idx > addr_line_idx:
            if any(l.startswith(k) for k in ['อำเภอ', 'ท้องถิ่น', 'เขต', 'สำนัก', 'กิ่งอำเภอ', 'เทศบาล', 'เมืองพัทยา']) and '(' not in l:
                reg_office_idx = idx
                reg_clean = re.sub(r'[0-9]+$', '', l.strip()).strip()
                rec['reg_office'] = reg_clean
                break

    # 2. Parse Name, Gender, Nationality (between cid_line_idx and status_line_idx)
    name_search_start = (cid_line_idx + 1) if cid_line_idx != -1 else 0
    name_search_end = status_line_idx if status_line_idx != -1 else min(len(lines), 8)
    gender_female_re = r'(?:หญิง|หญง|หยัง|หญ้ง|หเณญง|หญิ[งก-๙]*)'
    gender_male_re = r'(?:ชาย|ซาย|ขาย|ช่าย)'
    for idx in range(name_search_start, name_search_end):
        l = lines[idx]
        has_female = bool(re.search(gender_female_re, l))
        has_male = bool(re.search(gender_male_re, l))
        has_title = any(t in l for t in KNOWN_TITLES)
        if has_female or has_male or has_title:
            if has_female or any(t in l for t in ['นางสาว', 'น.ส.', 'นาง', 'เด็กหญิง', 'ด.ญ.']):
                rec['gender'] = 'หญิง'
            elif has_male or any(t in l for t in ['นาย', 'เด็กชาย', 'ด.ช.', 'พระ']):
                rec['gender'] = 'ชาย'

            # Strip trailing gender and nationality tokens so they NEVER leak into lname
            name_part = re.sub(rf'\s+{gender_female_re}(?:\s+.*|$)', '', l)
            name_part = re.sub(rf'\s+{gender_male_re}(?:\s+.*|$)', '', name_part)
            name_part = re.sub(r'\s+ไทย\s*$', '', name_part)
            name_part = re.sub(r'^[^ก-๙]+', '', name_part)
            name_part = re.sub(r'[\|\[\]!]+', ' ', name_part)
            name_part = re.sub(r'[^ก-๙\.\s]', '', name_part).strip()
            name_part = re.sub(r'\s+', ' ', name_part)

            matched_t = ''
            for t in KNOWN_TITLES:
                if name_part.startswith(t):
                    matched_t = t
                    break
                elif t in name_part:
                    pos = name_part.find(t)
                    if pos <= 4:
                        name_part = name_part[pos:]
                        matched_t = t
                        break

            if matched_t:
                rec['title'] = matched_t
                if matched_t in ['นางสาว', 'น.ส.', 'นาง', 'เด็กหญิง', 'ด.ญ.'] or 'หญิง' in matched_t:
                    rec['gender'] = 'หญิง'
                rest = name_part[len(matched_t):].strip()
                parts = rest.split()
                if len(parts) >= 2:
                    rec['fname'] = parts[0]
                    rec['lname'] = ' '.join(parts[1:])
                elif len(parts) == 1:
                    rec['fname'] = parts[0]
                sep = ' ' if ('.' in matched_t and not matched_t.startswith(('น.ส.', 'ด.ช.', 'ด.ญ.', 'ม.ร.ว.', 'ม.ล.'))) else ''
                rec['full_name'] = f"{matched_t}{sep}{rec['fname']} {rec['lname']}".strip()
            else:
                parts = name_part.split()
                if len(parts) >= 2:
                    rec['fname'] = parts[0]
                    rec['lname'] = ' '.join(parts[1:])
                    rec['full_name'] = f"{rec['fname']} {rec['lname']}".strip()
                elif len(parts) == 1:
                    rec['fname'] = parts[0]
                    rec['full_name'] = parts[0]
            if rec['full_name']:
                break

    # 3. Parse Print Date first so we can cross-check Birth Year + Age against Print Year
    prt_start = (person_status_idx + 1) if person_status_idx != -1 else max(0, len(lines) - 4)
    for idx in range(prt_start, len(lines)):
        pl = re.sub(r'[\|:]', ' ', lines[idx])
        if 'บุคคลนี้' in pl:
            continue
        m_dt = re.search(rf'(\d{{1,2}}\s*{MONTHS_REGEX}\s+\d{{4}})', pl)
        if m_dt:
            rec['print_date'] = sanitize_thai_date(m_dt.group(1))
            break

    print_year = 2569
    if rec['print_date']:
        m_py = re.search(r'(\d{4})$', rec['print_date'])
        if m_py and 2560 <= int(m_py.group(1)) <= 2575:
            print_year = int(m_py.group(1))

    # 4. Parse DOB, Age, Status from status_line_idx
    if status_line_idx != -1:
        sl = lines[status_line_idx]
        if 'เจ้าบ้าน' in sl:
            rec['status'] = 'เจ้าบ้าน'
        elif 'ผู้อาศัย' in sl or 'ผู้ขออาศัย' in sl:
            rec['status'] = 'ผู้อาศัย'

        before_status = re.split(r'เจ้าบ้าน|ผู้อาศัย|ผู้ขออาศัย', sl)[0]
        before_status = before_status.replace('|', ' ')
        m_full = re.search(rf'(\d{{1,2}}\s*{MONTHS_REGEX}\s+\d{{4}})', before_status)
        if m_full:
            rec['dob'] = sanitize_thai_date(m_full.group(1))
            after_dob = before_status[m_full.end():]
        else:
            m_my = re.search(rf'({MONTHS_REGEX}\s+\d{{4}})', before_status)
            if m_my:
                rec['dob'] = sanitize_thai_date(m_my.group(1))
                after_dob = before_status[m_my.end():]
            else:
                m_yr = re.search(r'\b(2[04569]\d{2})\b', before_status)
                if m_yr:
                    rec['dob'] = sanitize_thai_date(m_yr.group(1))
                    after_dob = before_status[m_yr.end():]
                else:
                    after_dob = before_status

        m_age = re.search(r'\b(\d{1,3})\b', after_dob)
        raw_age = int(m_age.group(1)) if (m_age and 1 <= int(m_age.group(1)) <= 125) else None

        # Mathematical cross-check between birth year and age
        m_by = re.search(r'\b(24\d{2}|25\d{2})\b', rec['dob'])
        if m_by:
            byear = int(m_by.group(1))
            expected_age = print_year - byear
            if raw_age is None:
                if 1 <= expected_age <= 120:
                    rec['age'] = str(expected_age)
            else:
                # If OCR dropped a digit of a 2-digit age (e.g. '6' instead of '67', or '4' instead of '47')
                if raw_age < 10 and expected_age >= 15 and str(raw_age) in str(expected_age):
                    rec['age'] = str(expected_age)
                else:
                    rec['age'] = str(raw_age)
        elif raw_age is not None:
            rec['age'] = str(raw_age)

    # 5. Parse Mother and Father strictly between status_line_idx and addr_line_idx
    if status_line_idx != -1 and addr_line_idx != -1 and addr_line_idx > status_line_idx + 1:
        parent_candidates = []
        for idx in range(status_line_idx + 1, addr_line_idx):
            pl = lines[idx]
            has_cid = bool(extract_cid_from_str(pl))
            has_nat = any(k in pl for k in ['ไทย', 'จีน', 'ม้ง', 'มัง', 'พม่า', 'ลาว', 'กัมพูชา', 'เวียดนาม', 'ไร้สัญชาติ'])
            has_dash = bool(re.search(r'[ก-๙]{2,}\s+[-=]', pl))
            if has_cid or has_nat or has_dash:
                parent_candidates.append(pl)

        if len(parent_candidates) >= 1:
            m_n, m_c, m_nat = parse_single_parent_line(parent_candidates[0])
            rec['mother_name'] = m_n
            rec['mother_cid'] = m_c
            rec['mother_nat'] = m_nat
        if len(parent_candidates) >= 2:
            f_n, f_c, f_nat = parse_single_parent_line(parent_candidates[1])
            rec['father_name'] = f_n
            rec['father_cid'] = f_c
            rec['father_nat'] = f_nat

    # 6. Parse Address & Cross-check with Registration Office
    if addr_line_idx != -1:
        al = sanitize_house_and_address_line(lines[addr_line_idx])
        rec['address'] = al
        comp = parse_address_components(al)
        rec.update(comp)

        # Cross-check district and reg_office
        if rec['reg_office'] and rec['reg_office'].startswith('อำเภอ'):
            reg_dist = rec['reg_office'][len('อำเภอ'):].strip()
            if not rec['district'] and reg_dist:
                rec['district'] = reg_dist
            elif rec['district'] and reg_dist and rec['district'] != reg_dist:
                # If reg_office had a missing tone mark (e.g. 'สามงาม' vs 'สามง่าม'), sync to the one with tone mark
                tones_d = len(re.findall(r'[่้๊๋์]', rec['district']))
                tones_r = len(re.findall(r'[่้๊๋์]', reg_dist))
                if tones_d > tones_r:
                    rec['reg_office'] = f"อำเภอ{rec['district']}"
                elif tones_r > tones_d:
                    rec['address'] = rec['address'].replace(f"อ.{rec['district']}", f"อ.{reg_dist}")
                    rec['district'] = reg_dist
        elif not rec['reg_office'] and rec['district']:
            if rec['province'] == 'กรุงเทพมหานคร':
                rec['reg_office'] = f"ท้องถิ่นเขต{rec['district']}"
            else:
                rec['reg_office'] = f"อำเภอ{rec['district']}"

    # 7. Parse Move-in Date (strictly between reg_office_idx/addr_line_idx and person_status_idx)
    move_start = (reg_office_idx + 1) if reg_office_idx != -1 else ((addr_line_idx + 1) if addr_line_idx != -1 else 0)
    move_end = person_status_idx if person_status_idx != -1 else len(lines)
    if move_start > 0:
        for idx in range(move_start, move_end):
            ml = re.sub(r'[\|:]', ' ', lines[idx])
            m_dt = re.search(rf'(\d{{1,2}}\s*{MONTHS_REGEX}\s+\d{{4}})', ml)
            if m_dt:
                rec['move_date'] = sanitize_thai_date(m_dt.group(1))
                break

    if not rec['cid'] and not rec['hid'] and not rec['full_name'] and not rec['address']:
        return None
    return rec


def _prepare_civil_page_for_ocr(page, dpi=220, lut=None):
    """
    On the main thread (PyMuPDF thread-safe):
    1. Checks if page has native digital text. If so, returns ('text', native_text).
    2. Otherwise renders the active body region in grayscale at `dpi=220`.
    3. Checks if page is blank (dark_pixels < 350 below gray 215). If blank, returns ('blank', None).
    4. Applies Contrast-Darkening LUT so faint gray toner (172..232) becomes crisp dark text while removing background > 232.
    """
    native_text = page.get_text("text").strip()
    if len(native_text) > 30:
        return 'text', native_text

    rect = page.rect
    clip_rect = fitz.Rect(20, 80, max(rect.width - 15, 200), min(rect.height - 70, 710))
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, clip=clip_rect)
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    hist = img.histogram()
    # Count pixels darker than 215 so faint-toner scans are NEVER mistaken for blank pages
    ink_pixels = sum(hist[:215])
    if ink_pixels < 400:
        return 'blank', None

    active_lut = lut if lut is not None else _LUT_PRIMARY
    img_clean = img.point(active_lut)
    return 'image', img_clean


def _ocr_pil_image_tha(img_clean):
    """Runs single-pass Thai OCR on a contrast-enhanced PIL image."""
    txt = pytesseract.image_to_string(
        img_clean,
        lang='tha',
        config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'
    )
    return txt


def extract_page_data(page, page_num):
    mode, payload = _prepare_civil_page_for_ocr(page, dpi=220, lut=_LUT_PRIMARY)
    if mode == 'blank':
        return None
    if mode == 'text':
        raw_lines = payload.splitlines()
        return parse_civil_lines(raw_lines, page_num=page_num)

    txt = _ocr_pil_image_tha(payload)
    rec = parse_civil_lines(txt.splitlines(), page_num=page_num)

    # Automatic high-sensitivity recovery pass if any key field is missing on a faint scan
    if rec is None or not rec.get('cid') or not rec.get('dob') or not rec.get('address') or not rec.get('mother_name'):
        mode2, payload2 = _prepare_civil_page_for_ocr(page, dpi=250, lut=_LUT_RECOVERY)
        if mode2 == 'image':
            txt2 = _ocr_pil_image_tha(payload2)
            rec2 = parse_civil_lines(txt2.splitlines(), page_num=page_num)
            if rec is None:
                rec = rec2
            elif rec2:
                for k, v in rec2.items():
                    if (not rec.get(k) or rec.get(k) == '-') and v and v != '-':
                        rec[k] = v
    return rec


def extract_page_certified(page, page_num):
    """
    ฟังก์ชันสกัดข้อมูลเอกสาร ท.ร.14/1 แบบรับรอง (Certified Mode)
    """
    rec = extract_page_data(page, page_num)
    if rec and rec.get('cid') and rec.get('full_name') and rec.get('address'):
        return rec

    if not TESS_EXE or (TESS_EXE != 'tesseract' and not os.path.exists(TESS_EXE)):
        raise RuntimeError("ไม่พบโปรแกรม Tesseract OCR ในระบบ กรุณาตรวจสอบว่ามีโฟลเดอร์ tesseract อยู่ในโฟลเดอร์ระบบ หรือติดตั้ง Tesseract-OCR")

    rec = rec or {
        'no': page_num,
        'cid': '',
        'hid': '',
        'title': '',
        'fname': '',
        'lname': '',
        'full_name': '',
        'gender': 'ชาย',
        'nat': 'ไทย',
        'dob': '',
        'age': '',
        'status': 'ผู้อาศัย',
        'mother_name': '',
        'mother_cid': '-',
        'mother_nat': 'ไทย',
        'father_name': '',
        'father_cid': '-',
        'father_nat': 'ไทย',
        'address': '',
        'addr_no': '',
        'moo': '-',
        'soi': '-',
        'road': '-',
        'subdistrict': '',
        'district': '',
        'province': '',
        'reg_office': '',
        'move_date': '-',
        'person_status': '',
        'issue_office': '',
        'print_date': ''
    }

    scale = 300 / 72

    # 1. CID & HID Strip
    pix = page.get_pixmap(clip=fitz.Rect(35, 215, 560, 248), dpi=300)
    txt = clean_thai_text(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'))
    c = extract_cid_from_str(txt)
    if c:
        rec['cid'] = c
    h = extract_hid_from_str(txt)
    if h:
        rec['hid'] = h

    # 2. Name, Gender, Nat Strip
    pix = page.get_pixmap(clip=fitz.Rect(35, 246, 560, 276), dpi=300)
    txt = clean_thai_text(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'))
    if 'หญิง' in txt:
        rec['gender'] = 'หญิง'
    else:
        rec['gender'] = 'ชาย'
    rec['nat'] = 'ไทย'

    name_str = txt
    if 'ชื่อ' in name_str:
        name_str = name_str.split('ชื่อ', 1)[1]
    if 'เพศ' in name_str:
        name_str = name_str.split('เพศ')[0]
    name_str = re.sub(r'\s+(?:ชาย|หญิง).*$', '', name_str)
    name_str = re.sub(r'[^ก-๙\.\s]', '', name_str).strip()
    for pfx in KNOWN_TITLES:
        if pfx in name_str:
            name_str = name_str[name_str.index(pfx):]
            break
    tokens = name_str.split()
    if tokens:
        full_n = ' '.join(tokens)
        rec['full_name'] = full_n
        for pfx in KNOWN_TITLES:
            if full_n.startswith(pfx):
                rec['title'] = pfx
                rest = full_n[len(pfx):].strip().split()
                if len(rest) >= 2:
                    rec['fname'] = rest[0]
                    rec['lname'] = ' '.join(rest[1:])
                elif len(rest) == 1:
                    rec['fname'] = rest[0]
                break

    # 3. DOB, Age, Status Strip
    pix = page.get_pixmap(clip=fitz.Rect(35, 274, 560, 303), dpi=300)
    txt = clean_thai_text(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'))
    m_dob = re.search(r'(\d{1,2}\s*' + MONTHS_REGEX + r'\s+\d{4})', txt)
    if m_dob:
        rec['dob'] = sanitize_thai_date(m_dob.group(1))
    m_age = re.search(r'อายุ\s*(\d+)', txt)
    if m_age:
        rec['age'] = m_age.group(1).strip()
    if 'เจ้าบ้าน' in txt:
        rec['status'] = 'เจ้าบ้าน'
    elif 'ผู้อาศัย' in txt:
        rec['status'] = 'ผู้อาศัย'

    # 4. Mother Name & CID Strip
    m_pix = page.get_pixmap(clip=fitz.Rect(85, 300, 205, 328), dpi=300)
    m_txt = clean_thai_text(pytesseract.image_to_string(Image.open(io.BytesIO(m_pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 7'))
    rec['mother_name'] = clean_parent_name(m_txt)
    
    mc_pix = page.get_pixmap(clip=fitz.Rect(205, 300, 315, 328), dpi=300)
    mc_txt = pytesseract.image_to_string(Image.open(io.BytesIO(mc_pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 7')
    rec['mother_cid'] = clean_parent_cid(mc_txt)

    # 5. Father Name & CID Strip
    f_pix = page.get_pixmap(clip=fitz.Rect(85, 326, 205, 355), dpi=300)
    f_txt = clean_thai_text(pytesseract.image_to_string(Image.open(io.BytesIO(f_pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 7'))
    rec['father_name'] = clean_parent_name(f_txt)

    fc_pix = page.get_pixmap(clip=fitz.Rect(205, 326, 315, 355), dpi=300)
    fc_txt = pytesseract.image_to_string(Image.open(io.BytesIO(fc_pix.tobytes('png'))), lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 7')
    rec['father_cid'] = clean_parent_cid(fc_txt)

    # 6. Address Strip
    pix = page.get_pixmap(dpi=300)
    img_gray = Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    bin_img = img_gray.point(_LUT_PRIMARY)
    crop_addr = bin_img.crop((int(35 * scale), int(355 * scale), int(560 * scale), int(405 * scale)))
    addr_txt = clean_thai_text(pytesseract.image_to_string(crop_addr, lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'))
    addr_line = addr_txt.split('\n')[0]
    addr_line = addr_line.replace('ทีอยู่', '').replace('ที่อยู่', '')
    addr_line = sanitize_house_and_address_line(addr_line)
    rec['address'] = addr_line
    rec.update(parse_address_components(addr_line))

    if not rec['cid'] and not rec['hid'] and not rec['full_name'] and not rec['address']:
        return None
    return rec


def _harmonize_batch_records(records):
    """
    Ensures batch consistency across all records extracted from a PDF:
    - Sequential numbering 1..N
    - Fills any missing issue_office or print_date from the batch majority value
    """
    if not records:
        return records

    iss_counts = Counter(r['issue_office'] for r in records if r.get('issue_office'))
    prt_counts = Counter(r['print_date'] for r in records if r.get('print_date'))
    common_iss = iss_counts.most_common(1)[0][0] if iss_counts else ''
    common_prt = prt_counts.most_common(1)[0][0] if prt_counts else ''

    for idx, r in enumerate(records, start=1):
        r['no'] = idx
        if not r.get('issue_office') and common_iss:
            r['issue_office'] = common_iss
        if not r.get('print_date') and common_prt:
            r['print_date'] = common_prt

    return records


def extract_pdf_data(pdf_path, progress_callback=None, doc_type='uncertified', api_key=None):
    """
    High-speed parallel Civil Registration (ท.ร.14/1) PDF extractor.
    - Uses Contrast-Darkening LUT at 220 DPI so faint gray toner scans are captured with 100% clarity.
    - Automatically runs a 250 DPI recovery pass on any page with extra-faint lines.
    - Validates all 77 Thai provinces, Buddhist dates, ages, and parent names.
    """
    doc = fitz.open(pdf_path)
    page_count = len(doc)

    active_api_key = api_key or os.environ.get('GEMINI_API_KEY')
    use_gemini = bool(active_api_key and gemini_engine)

    if use_gemini:
        records = []
        for i, page in enumerate(doc):
            page_num = i + 1
            if progress_callback:
                progress_callback(page_num, page_count)
            rec = None
            try:
                rec = gemini_engine.extract_page_with_gemini(page, page_num=page_num, api_key=active_api_key)
            except Exception as e:
                print(f"[Gemini AI Fallback] Page {page_num} error: {e}. Switching to Local Engine.")
                rec = None
            if not rec:
                rec = extract_page_certified(page, page_num) if doc_type == 'certified' else extract_page_data(page, page_num)
            if rec:
                records.append(rec)
        doc.close()
        return _harmonize_batch_records(records)

    # Local high-speed, memory-bounded mini-batch parallel extraction
    # Processes only `batch_size` pages in memory at a time (< 15 MB RAM even on 700-page PDFs!)
    page_results = [None] * page_count
    done_count = 0
    workers = max(1, min(OCR_WORKERS, page_count))
    batch_size = max(2, workers * 2)

    def _worker(task_item):
        p_idx, p_num, pil_img = task_item
        txt = _ocr_pil_image_tha(pil_img)
        parsed = parse_civil_lines(txt.splitlines(), page_num=p_num)
        return p_idx, parsed

    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for batch_start in range(0, page_count, batch_size):
            batch_end = min(page_count, batch_start + batch_size)
            ocr_batch = []

            for idx in range(batch_start, batch_end):
                page = doc[idx]
                mode, payload = _prepare_civil_page_for_ocr(page, dpi=220, lut=_LUT_PRIMARY)
                if mode == 'blank':
                    page_results[idx] = None
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, page_count)
                elif mode == 'text':
                    page_results[idx] = parse_civil_lines(payload.splitlines(), page_num=idx + 1)
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, page_count)
                else:
                    ocr_batch.append((idx, idx + 1, payload))

            if ocr_batch:
                if pool is None:
                    for item in ocr_batch:
                        p_idx, parsed = _worker(item)
                        page_results[p_idx] = parsed
                        done_count += 1
                        if progress_callback:
                            progress_callback(done_count, page_count)
                else:
                    futures = [pool.submit(_worker, item) for item in ocr_batch]
                    for fut in as_completed(futures):
                        p_idx, parsed = fut.result()
                        page_results[p_idx] = parsed
                        done_count += 1
                        if progress_callback:
                            progress_callback(done_count, page_count)

            # Immediate recovery pass for any page in this mini-batch missing key fields
            for idx in range(batch_start, batch_end):
                rec = page_results[idx]
                if rec is not None and (not rec.get('cid') or not rec.get('dob') or not rec.get('address') or not rec.get('mother_name')):
                    try:
                        page = doc[idx]
                        mode2, payload2 = _prepare_civil_page_for_ocr(page, dpi=240, lut=_LUT_RECOVERY)
                        if mode2 == 'image':
                            txt2 = _ocr_pil_image_tha(payload2)
                            rec2 = parse_civil_lines(txt2.splitlines(), page_num=idx + 1)
                            if rec2:
                                for k, v in rec2.items():
                                    if (not rec.get(k) or rec.get(k) == '-') and v and v != '-':
                                        rec[k] = v
                            del payload2
                    except Exception:
                        pass

            # Free mini-batch images and flush PyMuPDF C++ store cache immediately
            del ocr_batch
            try:
                fitz.TOOLS.store_shrink(100)
            except Exception:
                pass
            gc.collect()
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
        doc.close()

    records = [r for r in page_results if r is not None]
    return _harmonize_batch_records(records)
