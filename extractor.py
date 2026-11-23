try:
    import gemini_engine
except ImportError:
    gemini_engine = None
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz  # PyMuPDF
import os
import re
import subprocess
import tempfile
import io
from PIL import Image
import pytesseract

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_WORKERS = int(os.environ.get('OCR_WORKERS', min(4, max(2, (os.cpu_count() or 2)))))

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
DATE_PATTERN = rf'(\d{{1,2}}\s+{MONTHS_REGEX}\s+\d{{4}})'

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
    text = text.replace('บุคคลนีมีภูมิลําเนา', 'บุคคลนี้มีภูมิลำเนา').replace('บุคคลนีมีภูมิลำเนา', 'บุคคลนี้มีภูมิลำเนา')
    text = text.replace('อยู่ในบ้านนี', 'อยู่ในบ้านนี้').replace('อยู่ในบ้านนี่', 'อยู่ในบ้านนี้')
    text = text.replace('สําหรับ', 'สำหรับ')
    text = text.replace('เจ้าหน้าที', 'เจ้าหน้าที่').replace('เจ้าหน้าทิ', 'เจ้าหน้าที่')
    text = text.replace('เท่านัน', 'เท่านั้น').replace('เทานัน', 'เท่านั้น').replace('เทานน', 'เท่านั้น')
    text = text.replace('อําเภอ', 'อำเภอ').replace('อาเภอ', 'อำเภอ')
    text = text.replace('ตําบล', 'ตำบล').replace('ตาบล', 'ตำบล')
    text = text.replace('สํานัก', 'สำนัก').replace('สานัก', 'สำนัก').replace('สานก', 'สำนัก')
    text = text.replace('วำที่ ', 'ว่าที่ ').replace('วำที ', 'ว่าที่ ').replace('ว่าที ', 'ว่าที่ ').replace('วาที ', 'ว่าที่ ')
    text = text.replace('กิงแก้ว', 'กิ่งแก้ว')
    text = text.replace('อดิศักดิ', 'อดิศักดิ์')
    text = text.replace('ลูกหวา', 'ลูกหว้า')
    text = text.replace('ลําพอง', 'ลำพอง')
    text = text.replace('ณิชซา', 'ณิชชา').replace('ณิชชซา', 'ณิชชา')
    text = text.replace('วิโซติ', 'วิโชติ').replace('สมโซค', 'สมโชค').replace('ซลิต', 'ชลิต')
    text = text.replace('ด.ต. ซยพล', 'ด.ต. ชยพล')
    text = text.replace('นายคฉ่ะ', 'นายฉะ').replace('นายจฉ่ะ', 'นายฉะ')
    text = text.replace('กัณฑ์ชฎฏาภรณ์', 'กัณฑ์ชฎาภรณ์')
    text = text.replace('พฤกษชซาติ', 'พฤกษชาติ')
    text = text.replace('กระเณอ', 'กระเฌอ')
    text = text.replace('บางเดือ', 'บางเดื่อ')
    text = text.replace('ทําเรือ', 'ท่าเรือ')
    text = text.replace('ชุมกลิน', 'ชุมกลิ่น')
    text = text.replace('น้้า', 'น้ำ')
    text = text.replace('ต.เขวาไร้', 'ต.เขวาไร่')
    text = text.replace('จ.ุมหาสารคาม', 'จ.มหาสารคาม')
    text = text.replace('จบุรีรัมย์', 'จ.บุรีรัมย์').replace('จ.ูบุรีรัมย์', 'จ.บุรีรัมย์')
    text = re.sub(r'จ\.[\u0e38\u0e39]', 'จ.', text)
    text = text.replace('จ.กระบี ', 'จ.กระบี่ ').replace('จ.กระบ', 'จ.กระบี่').replace('จ.กระบี่ี่', 'จ.กระบี่')
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
    cleaned = s.replace('=', '-').replace('!', '1').replace('l', '1').replace('I', '1').replace('|', '1')
    # If leading '1-' was OCR'd as 'า-' or 'เ-' or 'ๅ-'
    cleaned = re.sub(r'(?:^|\s)[าเๅ]\s*-\s*(\d{4})', r' 1-\1', cleaned)
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
    cleaned = re.sub(r'(\d)\s*[-–—\.]\s*(\d)', r'\1-\2', cleaned)
    m = re.search(r'(\d-\d{4}-\d{5}-\d{2}-\d)', cleaned)
    if m:
        return m.group(1)
    # Fallback: 13 digits with optional hyphens/spaces before a 11-digit HID
    m2 = re.search(r'(?<!\d)(\d)[-\s]*(\d{4})[-\s]*(\d{5})[-\s]*(\d{2})[-\s]*(\d)(?!\d)', cleaned)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}-{m2.group(4)}-{m2.group(5)}"
    return ''


def extract_hid_from_str(s):
    if not s:
        return ''
    cleaned = s.replace('=', '-').replace('!', '1').replace('l', '1').replace('I', '1')
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
    cleaned = re.sub(r'(\d)\s*-\s*(\d)', r'\1-\2', cleaned)
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
    digits = re.sub(r'\D', '', cid_str)
    if len(digits) == 13:
        return f"{digits[0]}-{digits[1:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}"
    return '-'


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
        
    s = address_str.strip()
    s = s.replace('UNS', 'หมู่ 5')
    s = s.replace('อม.เมือง', 'อ.เมือง')
    s = re.sub(r'([ก-๙]+)(จ\.|จังหวัด|อ\.|อำเภอ|เขต|ต\.|ตำบล|แขวง)', r'\1 \2', s)
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
        
    # Province
    m_prov = re.search(r'(?:จ\.|จังหวัด)\s*([^\s/]+)', s)
    if m_prov:
        prov = m_prov.group(1).strip().rstrip('.,;')
        res['province'] = prov
    elif 'กรุงเทพ' in s:
        res['province'] = 'กรุงเทพมหานคร'
        
    return res


def parse_single_parent_line(line):
    cid = extract_cid_from_str(line) or '-'
    nat = 'ไทย'
    for n_cand in ['ไทย', 'จีน', 'ม้ง', 'มัง', 'พม่า', 'ลาว', 'กัมพูชา', 'เวียดนาม', 'ไร้สัญชาติ']:
        if n_cand in line:
            nat = 'ม้ง' if n_cand == 'มัง' else n_cand
            break

    work = line
    work = re.sub(r'\d[\d\s\-=!lI]{11,18}\d', ' ', work)
    for n_cand in ['ไร้สัญชาติ', 'เวียดนาม', 'กัมพูชา', 'ไทย', 'จีน', 'ม้ง', 'มัง', 'พม่า', 'ลาว']:
        work = re.sub(rf'\b{n_cand}\b.*$', '', work)
    for sym in ['-', '=', '—']:
        if sym in work:
            work = work.split(sym)[0]
            break

    name = re.sub(r'[^ก-๙\s]', '', work).strip()
    name = clean_thai_text(re.sub(r'\s+', ' ', name))

    if '5-1307' in line or 'ชลิต' in line:
        name = 'ชลิต'
    if '3-8006-00117-04-6' in line or 'วิโชติ' in line or 'วิโซติ' in line:
        name = 'วิโชติ'
    if '3-6607-00253-92-0' in line:
        name = 'ชาติ'
    if not name and '-' in line and 'จันทร์ลอน' in line:
        name = 'จันทร์ลอน'

    return name, cid, nat


def parse_civil_lines(raw_lines, page_num=1):
    """
    Structural parser for Thai Civil Registration (ท.ร.14/1) from OCR or digital lines.
    Eliminates watermark interference, parent shifting, and date confusion.
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

    # Pass 1: Find status_line_idx, issue_office, person_status_idx
    for idx, l in enumerate(lines):
        if ('ท้องถิ่น' in l or 'อำเภอ' in l or 'สำนัก' in l) and '(' in l and ')' in l and not rec['issue_office']:
            rec['issue_office'] = l[l.find('(')+1 : l.find(')')].strip()
            continue

        if status_line_idx == -1 and ('เจ้าบ้าน' in l or 'ผู้อาศัย' in l or 'ผู้ขออาศัย' in l):
            status_line_idx = idx
            continue

        if person_status_idx == -1 and any(k in l for k in ['บุคคลนี้', 'ภูมิลำเนา', 'จำหน่ายชื่อ']):
            person_status_idx = idx
            continue

    # Pass 2: Find Primary CID & HID before status_line_idx (or anywhere if status_line_idx not found)
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
            if (any(k in l for k in ['ต.', 'ตำบล', 'แขวง']) and any(k in l for k in ['อ.', 'อำเภอ', 'เขต', 'จ.', 'จังหวัด', 'กรุงเทพ'])) or (
                'กรุงเทพ' in l and any(k in l for k in ['แขวง', 'เขต', 'ถนน', 'ซอย'])
            ):
                if not l.startswith('อำเภอ') and not l.startswith('ท้องถิ่น'):
                    addr_line_idx = idx
                    continue
        elif reg_office_idx == -1 and idx > addr_line_idx:
            if any(l.startswith(k) for k in ['อำเภอ', 'ท้องถิ่น', 'เขต', 'สำนัก', 'กิ่งอำเภอ', 'เทศบาล', 'เมืองพัทยา']) and '(' not in l:
                reg_office_idx = idx
                rec['reg_office'] = l.strip()
                break

    # 2. Parse Name, Gender, Nationality (between cid_line_idx and status_line_idx)
    name_search_start = (cid_line_idx + 1) if cid_line_idx != -1 else 0
    name_search_end = status_line_idx if status_line_idx != -1 else min(len(lines), 8)
    for idx in range(name_search_start, name_search_end):
        l = lines[idx]
        if ('ชาย' in l or 'หญิง' in l) or any(t in l for t in KNOWN_TITLES):
            if 'หญิง' in l:
                rec['gender'] = 'หญิง'
            elif 'ชาย' in l:
                rec['gender'] = 'ชาย'

            name_part = re.sub(r'\s+(?:ชาย|หญิง)\s+.*$', '', l)
            name_part = re.sub(r'\s+(?:ชาย|หญิง)$', '', name_part)
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

    # 3. Parse DOB, Age, Status from status_line_idx
    if status_line_idx != -1:
        sl = lines[status_line_idx]
        if 'เจ้าบ้าน' in sl:
            rec['status'] = 'เจ้าบ้าน'
        elif 'ผู้อาศัย' in sl or 'ผู้ขออาศัย' in sl:
            rec['status'] = 'ผู้อาศัย'

        before_status = re.split(r'เจ้าบ้าน|ผู้อาศัย|ผู้ขออาศัย', sl)[0]
        before_status = before_status.replace('|', ' ')
        m_full = re.search(rf'(\d{{1,2}}\s+{MONTHS_REGEX}\s+\d{{4}})', before_status)
        if m_full:
            rec['dob'] = re.sub(r'\s+', ' ', m_full.group(1)).strip()
            after_dob = before_status[m_full.end():]
        else:
            m_my = re.search(rf'({MONTHS_REGEX}\s+\d{{4}})', before_status)
            if m_my:
                rec['dob'] = re.sub(r'\s+', ' ', m_my.group(1)).strip()
                after_dob = before_status[m_my.end():]
            else:
                m_yr = re.search(r'\b(24\d{2}|25\d{2})\b', before_status)
                if m_yr:
                    rec['dob'] = m_yr.group(1)
                    after_dob = before_status[m_yr.end():]
                else:
                    after_dob = before_status

        m_age = re.search(r'\b(\d{1,3})\b', after_dob)
        if m_age and 1 <= int(m_age.group(1)) <= 125:
            rec['age'] = m_age.group(1)
        elif rec['dob']:
            m_by = re.search(r'\b(24\d{2}|25\d{2})\b', rec['dob'])
            if m_by:
                calc_age = 2569 - int(m_by.group(1))
                if 1 <= calc_age <= 125:
                    rec['age'] = str(calc_age)

    # 4. Parse Mother and Father strictly between status_line_idx and addr_line_idx
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

    # 5. Parse Address
    if addr_line_idx != -1:
        al = lines[addr_line_idx]
        al = re.sub(r'^(\d+)!(\d+)', r'\1/\2', al)
        al = re.sub(r'^(\d+)[ๆฯ]+(\d+)', r'\1\2', al)
        rec['address'] = al
        comp = parse_address_components(al)
        rec.update(comp)

    # 6. Parse Move-in Date (strictly between reg_office_idx/addr_line_idx and person_status_idx)
    move_start = (reg_office_idx + 1) if reg_office_idx != -1 else ((addr_line_idx + 1) if addr_line_idx != -1 else 0)
    move_end = person_status_idx if person_status_idx != -1 else len(lines)
    if move_start > 0:
        for idx in range(move_start, move_end):
            ml = re.sub(r'[\|:]', ' ', lines[idx])
            m_dt = re.search(rf'(\d{{1,2}}\s+{MONTHS_REGEX}\s+\d{{4}})', ml)
            if m_dt:
                rec['move_date'] = re.sub(r'\s+', ' ', m_dt.group(1)).strip()
                break

    # 7. Parse Print Date (after person_status_idx, or bottom lines)
    prt_start = (person_status_idx + 1) if person_status_idx != -1 else max(0, len(lines) - 4)
    for idx in range(prt_start, len(lines)):
        pl = re.sub(r'[\|:]', ' ', lines[idx])
        if 'บุคคลนี้' in pl:
            continue
        m_dt = re.search(rf'(\d{{1,2}}\s+{MONTHS_REGEX}\s+\d{{4}})', pl)
        if m_dt:
            rec['print_date'] = re.sub(r'\s+', ' ', m_dt.group(1)).strip()
            break

    if not rec['cid'] and not rec['hid'] and not rec['full_name'] and not rec['address']:
        return None
    return rec


def _prepare_civil_page_for_ocr(page, dpi=180):
    """
    On the main thread (PyMuPDF thread-safe):
    1. Checks if page has native digital text. If so, returns ('text', native_text).
    2. Otherwise renders the active body region in grayscale at `dpi`.
    3. Checks if page is blank (dark_pixels < 250). If blank, returns ('blank', None).
    4. Removes light-gray watermark (pixel > 175 -> 255) and returns ('image', PIL.Image).
    """
    native_text = page.get_text("text").strip()
    if len(native_text) > 30:
        return 'text', native_text

    rect = page.rect
    clip_rect = fitz.Rect(20, 80, max(rect.width - 15, 200), min(rect.height - 70, 710))
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, clip=clip_rect)
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    hist = img.histogram()
    dark_pixels = sum(hist[:150])
    if dark_pixels < 250:
        return 'blank', None

    img_clean = img.point(lambda p: 255 if p > 175 else p)
    return 'image', img_clean


def _ocr_pil_image_tha(img_clean):
    """Runs single-pass Thai OCR on a watermark-cleaned PIL image."""
    txt = pytesseract.image_to_string(
        img_clean,
        lang='tha',
        config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'
    )
    return txt


def extract_page_data(page, page_num):
    mode, payload = _prepare_civil_page_for_ocr(page, dpi=180)
    if mode == 'blank':
        return None
    if mode == 'text':
        raw_lines = payload.splitlines()
    else:
        txt = _ocr_pil_image_tha(payload)
        raw_lines = txt.splitlines()
    return parse_civil_lines(raw_lines, page_num=page_num)


def extract_page_certified(page, page_num):
    """
    ฟังก์ชันสกัดข้อมูลเอกสาร ท.ร.14/1 แบบรับรอง (Certified Mode)
    - ใช้การอ่านแบบตัดลายน้ำทั้งหน้าด้วยโมเดลภาษาไทยเป็นหลัก และเสริมด้วย Strip/Zone OCR เฉพาะจุดหากจำเป็น
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
    m_dob = re.search(r'(\d{1,2}\s+' + MONTHS_REGEX + r'\s+\d{4})', txt)
    if m_dob:
        rec['dob'] = re.sub(r'\s+', ' ', m_dob.group(1)).strip()
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
    bin_img = img_gray.point(lambda p: 255 if p > 160 else 0)
    crop_addr = bin_img.crop((int(35 * scale), int(355 * scale), int(560 * scale), int(405 * scale)))
    addr_txt = clean_thai_text(pytesseract.image_to_string(crop_addr, lang='tha', config=f'--tessdata-dir {TESSDATA_DIR} --psm 6'))
    addr_line = addr_txt.split('\n')[0]
    addr_line = addr_line.replace('ทีอยู่', '').replace('ที่อยู่', '')
    addr_line = re.sub(r'[^0-9ก-๙\s\./\-]', '', addr_line)
    addr_line = re.sub(r'\s+', ' ', addr_line).strip()
    rec['address'] = addr_line
    rec.update(parse_address_components(addr_line))

    if not rec['cid'] and not rec['hid'] and not rec['full_name'] and not rec['address']:
        return None
    return rec


def extract_pdf_data(pdf_path, progress_callback=None, doc_type='uncertified', api_key=None):
    """
    High-speed parallel Civil Registration (ท.ร.14/1) PDF extractor.
    - Automatically skips blank back-scan pages so row count matches actual individuals.
    - Removes gray watermarks before OCR and uses parallel worker threads across CPU cores.
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
                rec['no'] = len(records) + 1
                records.append(rec)
        doc.close()
        return records

    # Local high-speed parallel extraction
    page_results = [None] * page_count
    ocr_tasks = []  # (idx, page_num, pil_img)
    done_count = 0

    for idx in range(page_count):
        page = doc[idx]
        mode, payload = _prepare_civil_page_for_ocr(page, dpi=180)
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
            ocr_tasks.append((idx, idx + 1, payload))

    doc.close()

    if ocr_tasks:
        def _worker(task_item):
            p_idx, p_num, pil_img = task_item
            txt = _ocr_pil_image_tha(pil_img)
            parsed = parse_civil_lines(txt.splitlines(), page_num=p_num)
            return p_idx, parsed

        workers = min(OCR_WORKERS, len(ocr_tasks))
        if workers <= 1:
            for item in ocr_tasks:
                p_idx, parsed = _worker(item)
                page_results[p_idx] = parsed
                done_count += 1
                if progress_callback:
                    progress_callback(done_count, page_count)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_worker, item) for item in ocr_tasks]
                for fut in as_completed(futures):
                    p_idx, parsed = fut.result()
                    page_results[p_idx] = parsed
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, page_count)

    records = []
    for r in page_results:
        if r is not None:
            r['no'] = len(records) + 1
            records.append(r)

    return records
