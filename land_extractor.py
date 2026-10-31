"""
land_extractor.py - ระบบสกัดข้อมูลรายงานการตรวจสอบหลักทรัพย์และโฉนดที่ดิน (23 คอลัมน์)
และระบบรวมไฟล์ PDF + จัดเรียงหน้าอัตโนมัติ (หน้าหลัก -> หน้ารายละเอียดตามเลขโฉนด) + รายงานส่วนที่ขาดหาย
"""

import os
import re
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz  # PyMuPDF
from PIL import Image
import pytesseract

OCR_WORKERS = int(os.environ.get('OCR_WORKERS', min(4, max(2, (os.cpu_count() or 2)))))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

for _t_candidate in [
    os.path.join(BASE_DIR, 'tesseract', 'tesseract.exe'),
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
]:
    if os.path.exists(_t_candidate):
        pytesseract.pytesseract.tesseract_cmd = _t_candidate
        break

_tessdata_dir = os.path.join(BASE_DIR, 'tessdata')
if os.path.exists(os.path.join(_tessdata_dir, 'tha.traineddata')):
    os.environ['TESSDATA_PREFIX'] = _tessdata_dir
elif os.path.exists(os.path.join(BASE_DIR, 'tha.traineddata')):
    os.environ['TESSDATA_PREFIX'] = BASE_DIR

LAND_COLUMNS_23 = [
    'ลำดับ',
    'พิมพ์เมื่อ',
    'เลขบัตรประชาชน',
    'รายชื่อ',
    'ผลการสืบทรัพย์',
    'ประเภทหลักประกัน',
    'เลขที่โฉนด',
    'หน้าสำรวจ',
    'เลขที่ดิน',
    'ตำบล/แขวง',
    'อำเภอ/เขต',
    'จังหวัด',
    'อาคารโรงเรือน (ประเภทและเลขที่)',
    'เนื้อที่',
    'ชื่อผู้ถือกรรมสิทธิ์',
    'อายุ',
    'ประเภทการได้มา',
    'ทุนทรัพย์จดทะเบียน (บาท)',
    'ราคาประเมินที่ดิน (บาท)',
    'ราคาประเมินสิ่งปลูกสร้าง (บาท)',
    'ผู้รับจำนอง/ภาระผูกพัน',
    'วงเงินจำนอง (บาท)',
    'สถานะการอายัด',
    'สำนักงานที่ดิน',
    'เบอร์โทรศัพท์'
]


def _normalize_deed_no(val):
    if not val:
        return ''
    s = str(val).strip()
    # Keep digits, commas, slashes, Thai letters (if any)
    s = re.sub(r'\s+', '', s)
    return s


def _clean_digits(val):
    if not val:
        return ''
    return re.sub(r'\D', '', str(val))


def _format_cid_dashed(cid_str):
    digits = _clean_digits(cid_str)
    if len(digits) == 13:
        return f"{digits[0]}-{digits[1:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}"
    return cid_str.strip() if cid_str else '-'


def _parse_int_amount(val_str):
    if not val_str:
        return 0
    s = str(val_str).strip()
    if s in ['-', 'ไม่มี', '']:
        return 0
    # Extract numbers
    m = re.search(r'([\d,]+(?:\.\d+)?)', s)
    if not m:
        return 0
    num_s = m.group(1).replace(',', '')
    try:
        fval = float(num_s)
        if fval.is_integer():
            return int(fval)
        return fval
    except Exception:
        return 0


def _ocr_image_bytes_to_lines(img_bytes):
    """Run fast resized grayscale OCR on image bytes and return (lines_list, full_text)."""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        if img.width > 960:
            new_h = int(img.height * (960 / img.width))
            img = img.resize((960, new_h), Image.Resampling.BILINEAR)
        try:
            ocr_txt = pytesseract.image_to_string(img, lang='tha', config='--psm 6')
        except Exception:
            ocr_txt = pytesseract.image_to_string(img, lang='tha+eng')
        ocr_txt = ocr_txt.replace('\u0e4d\u0e32', '\u0e33')
        raw_lines = [line.strip() for line in ocr_txt.splitlines() if line.strip()]
        lines_list = [{'y': idx * 15.0, 'words': [(0, 100, l)], 'text': l, 'is_ocr': True} for idx, l in enumerate(raw_lines)]
        return lines_list, "\n".join(raw_lines)
    except Exception:
        return [], ""


def _get_page_lines_with_coords(page):
    """
    Extract lines from a PDF page using native text words sorted by Y and X.
    Falls back to OCR ONLY if the page has no native text AND contains a scanned raster image.
    Returns (lines_list, full_text_str)
    where each item in lines_list is {'y': float, 'words': [(x0, x1, text), ...], 'text': str}
    """
    words = page.get_text("words")
    meaningful_words = [w for w in words if re.sub(r'[\.\-_\s]+', '', w[4])]
    if not meaningful_words:
        imgs = page.get_images(full=True)
        # If there are no raster images on the page, it's a blank/separator page (e.g. dotted line only)
        if not imgs:
            return [], ""
        # Fallback to fast OCR ONLY if scanned image page with no native text
        try:
            img_bytes = None
            if len(imgs) == 1 and page.parent is not None:
                try:
                    base_img = page.parent.extract_image(imgs[0][0])
                    if base_img and base_img.get("width", 0) >= 600:
                        img_bytes = base_img["image"]
                except Exception:
                    img_bytes = None
            if img_bytes is None:
                pix = page.get_pixmap(dpi=180)
                img_bytes = pix.tobytes("png")
            return _ocr_image_bytes_to_lines(img_bytes)
        except Exception:
            return [], ""

    lines = []
    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        text = text.replace('\u0e4d\u0e32', '\u0e33')
        if not text.strip():
            continue
        placed = False
        for line in lines:
            if abs(line['y'] - y0) <= 3.8:
                line['words'].append((x0, x1, text.strip()))
                placed = True
                break
        if not placed:
            lines.append({'y': y0, 'words': [(x0, x1, text.strip())]})

    lines.sort(key=lambda l: l['y'])
    for l in lines:
        l['words'].sort(key=lambda w: w[0])
        l['text'] = " ".join(w[2] for w in l['words'])

    full_text = "\n".join(l['text'] for l in lines)
    return lines, full_text


def is_land_pdf(pdf_paths):
    """
    Fast check (without OCR) to determine if uploaded PDF(s) are Department of Lands documents.
    Used to auto-switch mode if a user uploads a Land PDF while on the default Civil Registration tab.
    """
    if isinstance(pdf_paths, str):
        pdf_paths = [pdf_paths]
    land_keywords = [
        'รายละเอียดอนุมัติการจดทะเบียนสิทธิและนิติกรรม',
        'รายการผลการสอบถามข้อมูลตรวจสอบหลักทรัพย์',
        'ผู้ถือกรรมสิทธิ์',
        'โฉนดที่ดิน',
        'หน้าสำรวจ',
        'หนังสือกรรมสิทธิ์ห้องชุด',
        'หนังสือรับรองการทำประโยชน์',
        'ราคาประเมินที่ดิน',
        'รายการภาระผูกพัน'
    ]
    for path in pdf_paths[:3]:
        try:
            doc = fitz.open(path)
            for pno in range(min(len(doc), 5)):
                txt = doc[pno].get_text("text") or ""
                if any(k in txt for k in land_keywords):
                    doc.close()
                    return True
            doc.close()
        except Exception:
            pass
    return False


def classify_land_page(page, pre_lines=None):
    """
    Classifies a page in a Land Department PDF:
    Returns a dict:
      - page_type: 'summary' | 'summary_cont' | 'detail' | 'detail_cont' | 'other'
      - print_date: str
      - cid: str (13 digits)
      - cid_dashed: str
      - owner_name: str
      - found_count: int
      - summary_items: list of dicts (from summary table)
      - detail_data: dict (if page_type == 'detail')
    """
    if pre_lines is not None:
        lines, full_text = pre_lines
    else:
        lines, full_text = _get_page_lines_with_coords(page)

    info = {
        'page_type': 'other',
        'print_date': '',
        'cid': '',
        'cid_dashed': '',
        'owner_name': '',
        'found_count': 0,
        'summary_items': [],
        'detail_data': None,
        'detail_cont_pages': [],
        'lines': lines,
        'full_text': full_text
    }

    if not lines:
        return info

    # Extract print date (tolerates OCR missing tone marks / karans)
    m_date = re.search(r'(?:วันท[ี่ิ]*พิมพ[์]*|พิมพ[์]*เมื่อ)\s*[:\-]?\s*(\d{1,2}\s+[^\s\n]+\s+\d{4})', full_text)
    if m_date:
        info['print_date'] = m_date.group(1).strip()

    # 1. Check if Summary Page (First page of a person's report - supports both digital text and OCR)
    is_summary_page = (
        'รายการผลการสอบถาม' in full_text
        or 'ตรวจสอบหลักทรัพย' in full_text
        or 'ชื่อเจ้าของหลักทรัพย' in full_text
        or 'ชื่อเจาของหลักทรัพย' in full_text
        or bool(re.search(r'ช[ื่ิ]*อเจ[้่า]*ของหลักทรัพย', full_text))
        or ('รวม 462 สำนักงาน' in full_text and 'เลขประจำตัวประชาชน' in full_text)
    )
    if is_summary_page:
        info['page_type'] = 'summary'
        m_cid = re.search(r'เลขประจ[ำํา]ตัวประชาชน\s*[:\-]?\s*([\d\-\s]{13,17})', full_text)
        if m_cid:
            info['cid'] = _clean_digits(m_cid.group(1))
            info['cid_dashed'] = _format_cid_dashed(m_cid.group(1))

        m_name = re.search(r'ช[ื่ิ]*อเจ[้่า]*ของหลักทรัพย[์]*\s*[:\-]?\s*([^\n\r]+)', full_text)
        if m_name:
            raw_name = m_name.group(1).strip()
            raw_name = re.split(r'\s{2,}|เลขประจ', raw_name)[0].strip()
            info['owner_name'] = raw_name

        m_cnt = re.search(r'จ[ำํา]นวนรายการท[ี่ิ]*ค[้่]*นพบ\s*(\d+)\s*รายการ', full_text)
        if m_cnt:
            info['found_count'] = int(m_cnt.group(1))
        elif ('ไม่พบข้อมูล' in full_text or 'ไมพบขอมูล' in full_text) and 'ตรวจสอบพบ' not in full_text:
            info['found_count'] = 0

        items = parse_summary_table(lines)
        if not items:
            items = parse_summary_table_ocr(full_text)
        info['summary_items'] = items
        return info

    # 2. Check if Detail Start Page:
    # Either has 'รายละเอียดอนุมัติการจดทะเบียนสิทธิและนิติกรรม' OR has the top property header
    # ('ผู้ถือกรรมสิทธิ์ ( รวม ... ราย )' or top asset type header like โฉนดที่ดิน / ห้องชุด / น.ส.3 / อาคารโรงเรือน + ระวาง/เลขที่ดิน/โฉนดที่ดินเลขที่)
    has_top_asset_header = any(
        l['y'] <= 140 and any(k in l['text'] for k in [
            'โฉนดที่ดิน', 'หนังสือรับรองการทำประโยชน์', 'หนังสือกรรมสิทธิ์ห้องชุด',
            'หนังสือแสดงกรรมสิทธิ์ห้องชุด', 'อาคารโรงเรือน', 'ระวาง', 'หน้าสำรวจ'
        ])
        for l in lines
    )
    is_detail_start = (
        'รายละเอียดอนุมัติการจดทะเบียนสิทธิและนิติกรรม' in full_text
        or bool(re.search(r'ผู้ถือกรรมสิทธิ์\s*\(\s*รวม', full_text))
        or (has_top_asset_header and 'ผู้ถือกรรมสิทธิ์' in full_text)
    )
    if is_detail_start:
        info['page_type'] = 'detail'
        detail = parse_detail_page(lines, full_text, info['print_date'])
        info['detail_data'] = detail
        info['cid'] = detail.get('cid', '')
        info['cid_dashed'] = detail.get('cid_dashed', '')
        info['owner_name'] = detail.get('owner_name', '')
        return info

    # 3. Check if continuation of summary page (continuation of summary asset table or list of 462 land offices)
    if (
        ('ลำดับที่' in full_text and 'ประเภทหลักทรัพย์' in full_text and 'เลขที่หลักทรัพย์' in full_text)
        or ('สำนักงานที่ดิน' in full_text and (
            'ตรวจสอบไม่พบข้อมูล' in full_text
            or 'ผลการตรวจสอบข้อมูลจากสำนักงานที่ดิน' in full_text
            or re.search(r'^\d+\.\s*สำนักงานที่ดิน', full_text, re.M)
        ))
    ):
        info['page_type'] = 'summary_cont'
        info['summary_items'] = parse_summary_table(lines)
        return info

    # 4. Check if continuation of a Detail Page (e.g. Page 2 of a deed with remaining building owners, registration, or mortgages)
    if any(k in full_text for k in [
        'ผู้ถือกรรมสิทธิ์', 'ประเภทการจดทะเบียน', 'ทุนทรัพย์จดทะเบียน',
        'ราคาประเมินที่ดิน', 'ราคาประเมินสิ่งปลูกสร้าง', 'รายการอายัด',
        'รายการภาระผูกพัน', 'รายการสิ่งปลูกสร้าง', 'วงเงิน', 'จำนอง'
    ]):
        info['page_type'] = 'detail_cont'
        return info

    return info


def _attach_detail_continuations(all_pages_meta):
    """
    Attaches 'detail_cont' pages (and trailing blank 'other' pages of a detail PDF)
    to their parent 'detail' page and re-parses the combined lines.
    """
    n = len(all_pages_meta)
    attached_indices = set()

    # Pass 1: Check for 'detail_cont' pages that immediately precede a 'detail' page right after a 'summary'/'summary_cont'
    for i in range(n):
        p = all_pages_meta[i]
        if p['page_type'] == 'detail_cont' and i not in attached_indices:
            prev_type = all_pages_meta[i - 1]['page_type'] if i > 0 else None
            next_type = all_pages_meta[i + 1]['page_type'] if i + 1 < n else None
            if prev_type in ('summary', 'summary_cont') and next_type == 'detail':
                parent_det = all_pages_meta[i + 1]
                parent_det['detail_cont_pages'].append(p)
                attached_indices.add(i)

    # Pass 2: Standard order - 'detail_cont' (or trailing blank 'other' page) follows a 'detail' page
    current_detail = None
    for i in range(n):
        p = all_pages_meta[i]
        ptype = p['page_type']
        if ptype == 'detail':
            current_detail = p
        elif ptype in ('summary', 'summary_cont'):
            current_detail = None
        elif ptype == 'detail_cont' and i not in attached_indices:
            if current_detail is not None:
                current_detail['detail_cont_pages'].append(p)
                attached_indices.add(i)
            elif i + 1 < n and all_pages_meta[i + 1]['page_type'] == 'detail':
                all_pages_meta[i + 1]['detail_cont_pages'].append(p)
                attached_indices.add(i)
        elif ptype == 'other' and not p['lines'] and current_detail is not None:
            # Blank trailing page after a detail page stays attached to that detail page
            current_detail['detail_cont_pages'].append(p)
            attached_indices.add(i)

    # Re-parse any 'detail' page that received continuation pages
    for p in all_pages_meta:
        if p['page_type'] == 'detail' and p['detail_cont_pages']:
            combined_lines = list(p['lines'])
            combined_texts = [p['full_text']]
            for idx, cp in enumerate(p['detail_cont_pages'], start=1):
                y_offset = idx * 1000.0
                for l in cp['lines']:
                    combined_lines.append({
                        'y': l['y'] + y_offset,
                        'words': l['words'],
                        'text': l['text']
                    })
                combined_texts.append(cp['full_text'])
            full_combined_text = "\n".join(combined_texts)
            detail = parse_detail_page(combined_lines, full_combined_text, p['print_date'])
            p['detail_data'] = detail
            p['cid'] = detail.get('cid', '')
            p['cid_dashed'] = detail.get('cid_dashed', '')
            p['owner_name'] = detail.get('owner_name', '')


def parse_summary_table(lines):
    """
    Parse the asset summary table on Page 1:
    Columns in Page 1:
      - ลำดับที่ (x ~ 15..55)
      - ประเภทหลักทรัพย์ (x ~ 55..148)
      - เลขที่หลักทรัพย์ (x ~ 148..230)
      - ที่ตั้งหลักทรัพย์ (x ~ 230..375)
      - สำนักงานที่ตั้งหลักทรัพย์ (x ~ 375..500)
      - เนื้อที่ (x >= 500)
    """
    header_y = None
    footer_y = 99999.0

    for l in lines:
        t = l['text']
        if 'ลำดับที่' in t and ('ประเภทหลักทรัพย์' in t or 'เลขที่หลักทรัพย์' in t):
            header_y = l['y']
        elif header_y is not None and ('ผลการตรวจสอบข้อมูล' in t or 'ตรวจสอบพบข้อมูล' in t or 'ตรวจสอบไม่พบข้อมูล' in t):
            footer_y = l['y']
            break

    if header_y is None:
        return []

    table_lines = [l for l in lines if l['y'] > header_y + 10 and l['y'] < footer_y - 2]
    items = []
    current_item = None

    for l in table_lines:
        # Check if there is an index number in col 0 (x < 55)
        col0_words = [w[2] for w in l['words'] if w[0] < 55]
        col0_txt = "".join(col0_words).strip()

        if re.match(r'^\d+$', col0_txt):
            if current_item:
                items.append(_finalize_summary_item(current_item))
            current_item = {
                'seq': int(col0_txt),
                'col_type': [],
                'col_deed': [],
                'col_loc': [],
                'col_office': [],
                'col_area': []
            }

        if current_item is not None:
            for x0, x1, wtxt in l['words']:
                if x0 < 55 and wtxt == str(current_item['seq']):
                    continue
                elif 55 <= x0 < 148:
                    current_item['col_type'].append(wtxt)
                elif 148 <= x0 < 235:
                    current_item['col_deed'].append(wtxt)
                elif 235 <= x0 < 378:
                    current_item['col_loc'].append(wtxt)
                elif 378 <= x0 < 505:
                    current_item['col_office'].append(wtxt)
                elif x0 >= 505:
                    current_item['col_area'].append(wtxt)

    if current_item:
        items.append(_finalize_summary_item(current_item))

    return items


def _finalize_summary_item(raw):
    # Join Thai split words smoothly when broken across lines
    asset_type = "".join(raw['col_type']).replace('หนังสือรับรองการทําประโยชน์', 'หนังสือรับรองการทำประโยชน์')
    asset_type = re.sub(r'\s*\(\s*', ' (', asset_type).strip()
    if 'น.ส.3ก' in asset_type:
        asset_type = asset_type.replace('น.ส.3ก', 'น.ส.3 ก')
    if 'หนังสือกรรมสิทธิ์ห้อ' in asset_type and 'งชุด' not in asset_type:
        asset_type = 'หนังสือกรรมสิทธิ์ห้องชุด'

    deed_no = " ".join(raw['col_deed']).strip()
    loc_text = " ".join(raw['col_loc']).strip()
    office_parts = raw['col_office']
    office_str = ""
    for p in office_parts:
        if not office_str:
            office_str = p
        elif office_str.endswith('จังหวัด') or office_str.endswith('มหานคร'):
            office_str += p
        elif p.startswith('สาขา') or p.startswith('ส่วนแยก'):
            office_str += " " + p
        else:
            office_str += " " + p
    office_str = re.sub(r'\s+', ' ', office_str).strip()

    survey_page = '-'
    m_surv = re.search(r'หน้าสำรวจ\s*(\d+)', loc_text)
    if m_surv:
        survey_page = m_surv.group(1)

    land_no = '-'
    m_land = re.search(r'เลขที่ดิน\s*(\d+)', loc_text)
    if m_land:
        land_no = m_land.group(1)

    tambon = '-'
    m_tb = re.search(r'(?:ต\.|ตำบล|แขวง)([^\s]+)', loc_text)
    if m_tb:
        tambon = m_tb.group(1).strip()

    amphoe = '-'
    m_am = re.search(r'(?:อ\.|อำเภอ|เขต)([^\s]+)', loc_text)
    if m_am:
        amphoe = m_am.group(1).strip()

    province = '-'
    m_pv = re.search(r'(?:จ\.|จังหวัด)([^\s]+)', loc_text)
    if m_pv:
        province = m_pv.group(1).strip()

    # Check if loc_text describes a building (อาคารโรงเรือน)
    building_info = '-'
    if 'อาคารโรงเรือน' in asset_type:
        m_b = re.search(r'^(.+?)\s*บ้านเลขที่\s*([^\s]+)', loc_text)
        if m_b:
            b_type = m_b.group(1).strip()
            b_no = m_b.group(2).strip()
            building_info = f"{b_type} | เลขที่ {b_no}"

    raw_area = " ".join(raw['col_area']).strip()

    return {
        'seq': raw['seq'],
        'asset_type': asset_type or 'โฉนดที่ดิน',
        'deed_no': _normalize_deed_no(deed_no),
        'loc_text': loc_text,
        'survey_page': survey_page,
        'land_no': land_no,
        'tambon': tambon,
        'amphoe': amphoe,
        'province': province,
        'land_office': office_str,
        'area': raw_area,
        'building_info': building_info
    }


def _clean_ocr_office_name(office_str):
    s = re.sub(r'\s+', ' ', (office_str or '')).strip()
    s = re.sub(r'ส[ำํา]นักงานท[ี่ิ]*ดิน', 'สำนักงานที่ดิน', s)
    s = re.sub(r'บุรีรัมย(?!์)', 'บุรีรัมย์', s)
    s = re.sub(r'สุราษฎร(?!์)', 'สุราษฎร์', s)
    s = re.sub(r'นครสวรรค(?!์)', 'นครสวรรค์', s)
    s = re.sub(r'เพชรบูรณ(?!์)', 'เพชรบูรณ์', s)
    s = re.sub(r'กาญจนบุร[ีิ]', 'กาญจนบุรี', s)
    s = re.sub(r'นนทบุร[ีิ]', 'นนทบุรี', s)
    return s


def parse_summary_table_ocr(full_text):
    """
    Fallback parser when Page 1 (Summary Page) is a scanned image (OCR text without X coordinates).
    Extracts each asset row + matches the clean Land Office name from the bottom summary section.
    """
    if not full_text:
        return []

    raw_lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    # 1. Extract clean list of found land offices from bottom section:
    # e.g. "1. สำนักงานทีดินจังหวัดนนทบุรี สาขาบางใหญ่ พบ 2 รายการ"
    found_offices = []
    in_found_section = False
    for l in raw_lines:
        if 'ผลการตรวจสอบ' in l or 'ตรวจสอบพบข' in l:
            in_found_section = True
            continue
        if in_found_section and ('ตรวจสอบไม่พบ' in l or 'ตรวจสอบไมพบ' in l):
            break
        if in_found_section:
            m_off = re.match(r'^\d+[\.\s]+(ส[ำํา]นักงาน.+?)(?:\s+พบ\s+\d+\s*รายการ.*)?$', l)
            if m_off:
                clean_off = _clean_ocr_office_name(m_off.group(1))
                if clean_off and clean_off not in found_offices:
                    found_offices.append(clean_off)

    # 2. Locate table body lines between header and footer
    start_idx = 0
    end_idx = len(raw_lines)
    for idx, l in enumerate(raw_lines):
        if any(k in l for k in ['ลำดับท', 'ประเภทหลักทรัพย', 'ตร.ว.)/(ตร.ม.)']):
            start_idx = idx + 1
        elif start_idx > 0 and any(k in l for k in ['ผลการตรวจสอบข', 'ตรวจสอบพบข', 'ตรวจสอบไม่พบ', 'ตรวจสอบไมพบ']):
            end_idx = idx
            break

    body_lines = raw_lines[start_idx:end_idx] if start_idx > 0 else raw_lines

    # 3. Group lines into item blocks starting with "<seq> <asset_type>..."
    row_blocks = []
    cur_block = None
    row_start_re = re.compile(
        r'^(\d+)\s+[^\d]{0,6}?(โฉนด[^\s]*|หนังสือรับรอง[^\d]*|หนังสือกรรมสิทธ[^\d]*|หนังสือแสดง[^\d]*|อาคารโรงเรือน|ห้องชุด|น\.ส\.[^\s]*)(.*)$'
    )

    for l in body_lines:
        m_start = row_start_re.match(l)
        if m_start:
            if cur_block:
                row_blocks.append(cur_block)
            cur_block = {
                'seq': int(m_start.group(1)),
                'raw_type': m_start.group(2).strip(),
                'first_rest': m_start.group(3).strip(),
                'lines': [l]
            }
        elif cur_block is not None:
            cur_block['lines'].append(l)

    if cur_block:
        row_blocks.append(cur_block)

    items = []
    for blk in row_blocks:
        seq = blk['seq']
        raw_type = blk['raw_type']
        first_rest = blk['first_rest']
        block_text = "\n".join(blk['lines'])

        if 'อาคารโรงเรือน' in raw_type or 'อาคารโรงเรือน' in block_text:
            asset_type = 'อาคารโรงเรือน'
        elif 'ห้องชุด' in raw_type or 'ห้องชุด' in block_text:
            asset_type = 'หนังสือกรรมสิทธิ์ห้องชุด'
        elif 'รับรองการท' in raw_type or 'น.ส.3' in block_text:
            asset_type = 'หนังสือรับรองการทำประโยชน์ (น.ส.3 ก)'
        else:
            asset_type = 'โฉนดที่ดิน'

        deed_no = ''
        if asset_type != 'อาคารโรงเรือน':
            m_dn = re.search(r'(?:โฉนดเลขท[ี่ิ]*|เลขท[ี่ิ]*หลักทรัพย[์]*|ห[้่]*องชุดเลขท[ี่ิ]*)\s*([\d/\-,]+)', block_text)
            if m_dn:
                deed_no = _normalize_deed_no(m_dn.group(1))
            else:
                m_dn2 = re.match(r'^([\d/\-,]+)\b', first_rest)
                if m_dn2:
                    deed_no = _normalize_deed_no(m_dn2.group(1))

        survey_page = '-'
        m_surv = re.search(r'หน[้่า]*ส[ำํา]รวจ\s*(\d+)', block_text)
        if not m_surv:
            m_surv = re.search(r'(\d+)\s+ระวาง', block_text)
        if m_surv:
            survey_page = m_surv.group(1).strip()

        land_no = '-'
        m_lno = re.search(r'เลขท[ี่ิ]*ดิน\s*(\d+)', block_text)
        if m_lno:
            land_no = m_lno.group(1).strip()

        tambon = '-'
        m_tb = re.search(r'(?:ต\.|ตำบล|แขวง)\s*([^\s|\n]+)', block_text)
        if m_tb:
            tambon = m_tb.group(1).strip()

        amphoe = '-'
        m_am = re.search(r'(?:อ\.|อำเภอ|เขต)\s*([^\s|\n]+)', block_text)
        if m_am:
            amphoe = m_am.group(1).strip()

        province = '-'
        m_pv = re.search(r'(?:จ\.|จังหวัด)\s*([^\s|\n]+)', block_text)
        if m_pv and m_pv.group(1).strip() not in ['นนทบุรี', 'บุรีรัมย์', 'นครปฐม'] and 'สำนักงาน' in m_pv.group(0):
            pass
        m_pv_explicit = re.search(r'จ\.?\s*([^\s|\n]+)', block_text)
        if m_pv_explicit:
            province = _clean_ocr_office_name(m_pv_explicit.group(1).strip())

        area_str = ''
        if asset_type == 'อาคารโรงเรือน' or 'ห้องชุด' in asset_type:
            m_ar_b = re.search(r'(\d+(?:\.\d+)?)\s*$', blk['lines'][0].strip())
            if m_ar_b:
                area_str = m_ar_b.group(1).strip()
        else:
            m_ar_l = re.search(r'(\d+)\s*-\s*(\d+)\s*-\s*([\d\.]+)', blk['lines'][0])
            if m_ar_l:
                r_v, n_v, w_v = m_ar_l.group(1), m_ar_l.group(2), m_ar_l.group(3)
                # Fix OCR missing decimal point in tarang-wa e.g. '620' -> '62.0' (since tarang-wa is always < 100)
                if '.' not in w_v and len(w_v) >= 3 and int(w_v) >= 100:
                    w_v = f"{int(w_v[:-1])}.{w_v[-1]}"
                area_str = f"{r_v}-{n_v}-{w_v}"

        building_info = '-'
        if asset_type == 'อาคารโรงเรือน':
            b_type = 'บ้านพักอาศัย'
            m_bt = re.search(r'(บ[้่า]*นพักอาศัย[^\s|\n]+|ทาวน[์]*เฮาส[์]*[^\s|\n]+|ตึกแถว[^\s|\n]+|อาคาร[^\s|\n]+)', block_text)
            if m_bt:
                b_type = m_bt.group(1).replace('บานพัก', 'บ้านพัก').replace('สองชัน', 'สองชั้น').replace('ชันเดียว', 'ชั้นเดียว')
            m_bno = re.search(r'บ[้่า]*นเลขท[ี่ิ]*\s*([^\s|\n]+)', block_text)
            if m_bno:
                building_info = f"{b_type} | เลขที่ {m_bno.group(1).strip()}"

        # Match land office from found_offices
        office_str = '-'
        if len(found_offices) == 1:
            office_str = found_offices[0]
        elif len(found_offices) > 1:
            best_off = None
            best_score = -1
            clean_blk = _clean_ocr_office_name(block_text)
            for cand_off in found_offices:
                # Extract tokens from cand_off like จังหวัดX, สาขาY
                tokens = re.findall(r'(?:จังหวัด|สาขา|ส่วนแยก)([^\s]+)', cand_off)
                score = sum(1 for tk in tokens if tk in clean_blk)
                if score > best_score:
                    best_score = score
                    best_off = cand_off
            office_str = best_off or found_offices[0]

        items.append({
            'seq': seq,
            'asset_type': asset_type,
            'deed_no': deed_no,
            'loc_text': block_text,
            'survey_page': survey_page,
            'land_no': land_no,
            'tambon': tambon,
            'amphoe': amphoe,
            'province': province,
            'land_office': office_str,
            'area': area_str,
            'building_info': building_info
        })

    return items


def parse_detail_page(lines, full_text, print_date=''):
    """
    Parse a Detail Page (รายละเอียดอนุมัติการจดทะเบียนสิทธิและนิติกรรม)
    into structured fields needed for the 25-column format.
    """
    data = {
        'print_date': print_date,
        'asset_type': 'โฉนดที่ดิน',
        'deed_no': '-',
        'survey_page': '-',
        'land_no': '-',
        'tambon': '-',
        'amphoe': '-',
        'province': '-',
        'building_info': '-',
        'area_text': '-',
        'land_area': '',
        'building_area': '',
        'owner_name': '',
        'cid': '',
        'cid_dashed': '',
        'all_cids': [],
        'all_names': [],
        'owner_formatted': '-',
        'age': '-',
        'phone': '-',
        'acquisition_type': '-',
        'reg_capital': 0,
        'land_value': 0,
        'building_value': 0,
        'mortgage_info': '-',
        'mortgage_amount': '-',
        'seizure_status': 'ไม่มี',
        'land_office': '-'
    }

    # 1. Asset Type (typically around Y=65..85 on first page)
    for l in lines:
        if 60 <= l['y'] <= 90:
            t = l['text'].strip()
            if any(k in t for k in ['โฉนดที่ดิน', 'หนังสือรับรองการทำประโยชน์', 'หนังสือแสดงกรรมสิทธิ์ห้องชุด', 'หนังสือกรรมสิทธิ์ห้องชุด', 'อ.ช.2', 'น.ส.3', 'อาคารโรงเรือน']):
                data['asset_type'] = t
                break

    # 2. Top section (Y < 200 on first page): deed_no, survey_page, land_no, tambon, amphoe, province, land_area
    top_lines = [l for l in lines if 80 <= l['y'] <= 200]
    condo_name = ''
    for l in top_lines:
        t = l['text']
        # เลขที่ดิน
        m_lno = re.search(r'เลขที่ดิน\s+([^\s]+)', t)
        if m_lno and m_lno.group(1) not in ['ตำบล', 'อำเภอ', 'จังหวัด', 'เล่ม', 'หน้า']:
            data['land_no'] = m_lno.group(1).strip()

        # หน้าสำรวจ
        m_surv = re.search(r'หน้าสำรวจ\s+([^\s]+)', t)
        if m_surv and m_surv.group(1) not in ['ตำบล', 'อำเภอ', 'จังหวัด']:
            data['survey_page'] = m_surv.group(1).strip()

        # ห้องชุดเลขที่ (for Condo อ.ช.2)
        m_condo = re.search(r'ห้องชุดเลขที่\s*([^\s,]+)', t)
        if m_condo:
            data['deed_no'] = _normalize_deed_no(m_condo.group(1))

        m_cname = re.search(r'ชื่ออาคารชุด\s+(.+)$', t)
        if m_cname:
            condo_name = m_cname.group(1).strip()

        m_carea = re.search(r'เนื้อที่(?:ประมาณ)?\s*([\d\.,]+)\s*ตารางเมตร', t)
        if m_carea:
            data['building_area'] = f"{m_carea.group(1).strip()} ตารางเมตร"

        # Standalone อาคารโรงเรือน on Detail Page (e.g. บ้านพักอาศัยตึกชั้นเดียว เลขที่ 10/3 เล่ม 385)
        if 'อาคารโรงเรือน' in data['asset_type']:
            m_std_b = re.search(r'^(.+?)\s+เลขที่\s+([^\s]+)', t)
            if m_std_b and 'ตำบล' not in m_std_b.group(1):
                b_type_top = m_std_b.group(1).strip()
                b_no_top = m_std_b.group(2).strip()
                if b_no_top not in ['เล่ม', 'หน้า', 'ขนาด', 'กว้าง', '-', 'ตำบล', 'หมู่ที่']:
                    data['building_info'] = f"{b_type_top} | เลขที่ {b_no_top}"
                    data['house_no'] = _normalize_deed_no(b_no_top)
                else:
                    data['building_info'] = b_type_top

        # เลขที่ (โฉนด / น.ส.3) - be careful not to overwrite ห้องชุดเลขที่ or match เลขที่ดิน or บ้านเลขที่
        if data['deed_no'] == '-' and 'อาคารโรงเรือน' not in data['asset_type']:
            for idx, (x0, x1, wtxt) in enumerate(l['words']):
                if wtxt == 'เลขที่':
                    if idx + 1 < len(l['words']):
                        nxt = l['words'][idx + 1][2]
                        if nxt not in ['เล่ม', 'หน้า', 'ตำบล', 'อำเภอ']:
                            data['deed_no'] = _normalize_deed_no(nxt)

        # จำนวนที่ดิน X ไร่ Y งาน Z ตารางวา
        if 'จำนวนที่ดิน' in t:
            m_area = re.search(r'จำนวนที่ดิน\s*([\d\.\-]+)\s*ไร่\s*([\d\.\-]+)\s*งาน\s*([\d\.\-]+)\s*ตารางวา', t)
            if m_area:
                rai = '0' if m_area.group(1).strip() == '-' else m_area.group(1).strip()
                ngan = '0' if m_area.group(2).strip() == '-' else m_area.group(2).strip()
                wa = '0' if m_area.group(3).strip() == '-' else m_area.group(3).strip()
                data['land_area'] = f"{rai} ไร่ {ngan} งาน {wa} ตารางวา"

        # ตำบล / แขวง / อำเภอ / จังหวัด in top section
        for idx, (x0, x1, wtxt) in enumerate(l['words']):
            if wtxt in ['ตำบล', 'แขวง'] and idx + 1 < len(l['words']):
                nxt = l['words'][idx + 1][2]
                if nxt not in ['เล่ม', 'หน้า', 'เลขที่', 'อำเภอ', 'หมู่ที่']:
                    data['tambon'] = nxt.strip()
            elif wtxt in ['อำเภอ', 'เขต'] and idx + 1 < len(l['words']):
                nxt = l['words'][idx + 1][2]
                if nxt not in ['เลขที่ดิน', 'จังหวัด', 'เล่ม', 'หน้า', 'ทะเบียนอาคารชุดเลขที่']:
                    data['amphoe'] = nxt.strip()
            elif wtxt == 'จังหวัด' and idx + 1 < len(l['words']):
                nxt = l['words'][idx + 1][2]
                if nxt not in ['เล่ม', 'หน้า', 'เลขที่', 'เนื้อที่ประมาณ']:
                    data['province'] = nxt.strip()

    if 'ห้องชุด' in data['asset_type'] and data['building_info'] == '-':
        if condo_name and data['deed_no'] != '-':
            data['building_info'] = f"ห้องชุด {condo_name} | เลขที่ {data['deed_no']}"
        elif data['deed_no'] != '-':
            data['building_info'] = f"ห้องชุด | เลขที่ {data['deed_no']}"

    # 3. Parse รายการสิ่งปลูกสร้าง section (buildings on the deed)
    buildings_list = []
    building_areas_list = []
    in_building_section = False
    cur_b_type = ''
    cur_b_no = ''
    for l in lines:
        t = l['text'].strip()
        if 'รายการสิ่งปลูกสร้าง' in t:
            in_building_section = True
            continue
        if in_building_section:
            if 'ผู้ถือกรรมสิทธิ์สิ่งปลูกสร้าง' in t or 'ประเภทการจดทะเบียน' in t or 'รายการอายัด' in t or 'รายการภาระผูกพัน' in t:
                if cur_b_type:
                    b_str = f"{cur_b_type} | เลขที่ {cur_b_no}" if (cur_b_no and cur_b_no != '-') else cur_b_type
                    if b_str not in buildings_list:
                        buildings_list.append(b_str)
                    cur_b_type = ''
                    cur_b_no = ''
                if 'ประเภทการจดทะเบียน' in t or 'รายการอายัด' in t or 'รายการภาระผูกพัน' in t:
                    in_building_section = False
                continue

            if 'รายการสิ่งปลูกสร้างลำดับที่' in t:
                if cur_b_type:
                    b_str = f"{cur_b_type} | เลขที่ {cur_b_no}" if (cur_b_no and cur_b_no != '-') else cur_b_type
                    if b_str not in buildings_list:
                        buildings_list.append(b_str)
                    cur_b_type = ''
                    cur_b_no = ''
                continue

            if 'ประเภทสิ่งปลูกสร้าง' in t:
                m_bt = re.search(r'ประเภทสิ่งปลูกสร้าง\s*(.+?)(?:\s+ปี\s*พ\.ศ\.|\s+ปลูกสร้าง|$)', t)
                if m_bt:
                    cur_b_type = m_bt.group(1).strip()

            if 'ขนาด' in t or 'ชั้นเนื้อที่' in t or (cur_b_type and t.startswith('เลขที่')):
                m_bno = re.search(r'เลขที่\s*([^\s]+?)(?:\s+ขนาด|\s+กว้าง|$)', t)
                if m_bno:
                    cand_bno = m_bno.group(1).strip()
                    if cand_bno not in ['เล่ม', 'หน้า', 'ขนาด', 'กว้าง', '-', 'ตำบล', 'หมู่ที่']:
                        cur_b_no = cand_bno
                m_ba = re.search(r'เนื้อที่\s*([\d\.,\-]+)\s*ตารางเมตร', t)
                if m_ba and m_ba.group(1).strip() != '-':
                    ba_str = f"{m_ba.group(1).strip()} ตารางเมตร"
                    if ba_str not in building_areas_list:
                        building_areas_list.append(ba_str)

    if cur_b_type:
        b_str = f"{cur_b_type} | เลขที่ {cur_b_no}" if (cur_b_no and cur_b_no != '-') else cur_b_type
        if b_str not in buildings_list:
            buildings_list.append(b_str)

    if buildings_list:
        data['building_info'] = "\n".join(buildings_list)
    if building_areas_list and not data['building_area']:
        data['building_area'] = ", ".join(building_areas_list)

    # Compose area_text (เนื้อที่)
    if data['land_area'] and data['building_area']:
        data['area_text'] = f"{data['land_area']} | อาคาร {data['building_area']}"
    elif data['land_area']:
        data['area_text'] = data['land_area']
    elif data['building_area']:
        data['area_text'] = data['building_area']

    # 4. Owner section (ผู้ถือกรรมสิทธิ์) & All Phone Numbers
    owners = []
    seen_owners = set()
    ages_list = []
    phones_list = []
    current_seq = 1
    in_bldg_owners = False

    for l in lines:
        t = l['text']
        if 'ผู้ถือกรรมสิทธิ์สิ่งปลูกสร้าง' in t:
            in_bldg_owners = True

        m_seq = re.search(r'ผู้ถือกรรมสิทธิ์(?:ที่ดิน|ห้องชุด)?ลำดับที่\s*(\d+)', t)
        if m_seq and not in_bldg_owners:
            current_seq = int(m_seq.group(1))

        if 'ชื่อผู้ถือกรรมสิทธิ์' in t:
            m_own = re.search(r'ชื่อผู้ถือกรรมสิทธิ์\s*(.+?)\s*เลขที่บัตรประชาชน\s*([\d\-]+)', t)
            if m_own:
                oname = m_own.group(1).strip()
                ocid = m_own.group(2).strip()
                ocid_clean = _clean_digits(ocid)
                ocid_dashed = _format_cid_dashed(ocid)

                if ocid_clean and ocid_clean not in data['all_cids']:
                    data['all_cids'].append(ocid_clean)
                if oname and oname not in data['all_names']:
                    data['all_names'].append(oname)

                owner_key = (oname, ocid_dashed)
                if not in_bldg_owners and owner_key not in seen_owners:
                    seen_owners.add(owner_key)
                    owners.append((current_seq, oname, ocid_dashed))
                    if not data['owner_name']:
                        data['owner_name'] = oname
                        data['cid'] = ocid_clean
                        data['cid_dashed'] = ocid_dashed

            m_age = re.search(r'อายุ\s*(\d+)\s*ปี', t)
            if m_age and not in_bldg_owners:
                age_val = m_age.group(1).strip()
                if age_val not in ages_list:
                    ages_list.append(age_val)

        # Extract ALL phone numbers across all owners on this deed (deduplicated in order)
        if 'โทรศัพท์' in t:
            after_ph = t.split('โทรศัพท์', 1)[1]
            # Find all phone-like patterns starting with 0 (e.g. 08-0456-8814, 08-3345-6179, 097-9246774)
            raw_candidates = re.findall(r'0[\d\-]{7,13}', after_ph)
            if not raw_candidates:
                # Fallback if separated by spaces
                raw_candidates = [p.strip() for p in re.split(r'[,/;\s]+', after_ph) if p.strip()]
            for cand in raw_candidates:
                ph_digits = _clean_digits(cand)
                if len(ph_digits) >= 9 and ph_digits not in phones_list:
                    phones_list.append(ph_digits)

    if owners:
        owner_strs = [f"({seq}) {oname}  ({ocid})" for seq, oname, ocid in owners]
        data['owner_formatted'] = "\n".join(owner_strs)

    if ages_list:
        data['age'] = ages_list[0] if len(ages_list) == 1 else ", ".join(ages_list)

    if phones_list:
        data['phone'] = ", ".join(phones_list)

    # 5. Acquisition Type (การได้มาของที่ดิน -> ประเภทการได้มา)
    for l in lines:
        t = l['text']
        if 'ประเภทการได้มา' in t and data['acquisition_type'] == '-':
            m_acq = re.search(r'ประเภทการได้มา\s+(.+?)\s+วันที่ได้มา', t)
            if m_acq:
                data['acquisition_type'] = m_acq.group(1).strip()
            else:
                m_acq2 = re.search(r'ประเภทการได้มา\s+([^\s]+)', t)
                if m_acq2:
                    data['acquisition_type'] = m_acq2.group(1).strip()

    # 6. Registration Capital & Appraised Values
    for l in lines:
        t = l['text']
        if 'ทุนทรัพย์จดทะเบียน' in t:
            m_cap = re.search(r'ทุนทรัพย์จดทะเบียน\s+([^\s]+)\s*บาท', t)
            if m_cap:
                data['reg_capital'] = _parse_int_amount(m_cap.group(1))
        if 'ราคาประเมินที่ดิน' in t:
            m_lval = re.search(r'ราคาประเมินที่ดิน\s+([^\s]+)\s*บาท', t)
            if m_lval:
                data['land_value'] = _parse_int_amount(m_lval.group(1))
        if 'ราคาประเมินสิ่งปลูกสร้าง' in t:
            m_bval = re.search(r'ราคาประเมินสิ่งปลูกสร้าง\s+([^\s]+)\s*บาท', t)
            if m_bval:
                data['building_value'] = _parse_int_amount(m_bval.group(1))

    # On standalone อาคารโรงเรือน pages, Department of Lands prints the building appraised value in the first slot
    if 'อาคารโรงเรือน' in data['asset_type'] and data['building_value'] == 0 and data['land_value'] > 0:
        data['building_value'] = data['land_value']
        data['land_value'] = 0

    # 7. Seizure Status (รายการอายัด) & Obligation/Mortgage (รายการภาระผูกพัน)
    seizure_y = None
    obligation_y = None
    for l in lines:
        t = l['text']
        if 'รายการอายัด' in t and seizure_y is None:
            seizure_y = l['y']
        elif 'รายการภาระผูกพัน' in t and obligation_y is None:
            obligation_y = l['y']

    if seizure_y is not None:
        end_seiz_y = obligation_y if obligation_y is not None else 999999.0
        seiz_lines = [l['text'] for l in lines if seizure_y - 2 <= l['y'] < end_seiz_y - 2]
        seiz_text = " ".join(seiz_lines).strip()
        seiz_text = re.sub(r'^รายการอายัด\s*', '', seiz_text).strip()
        if not seiz_text or seiz_text == 'ไม่มี':
            data['seizure_status'] = 'ไม่มี'
        else:
            data['seizure_status'] = seiz_text

    if obligation_y is not None:
        obl_lines = [l for l in lines if l['y'] >= obligation_y - 2]
        mort_info, mort_amt = _parse_obligations_section(obl_lines)
        data['mortgage_info'] = mort_info
        data['mortgage_amount'] = mort_amt

    return data


def _parse_obligations_section(obl_lines):
    """
    Formats the รายการภาระผูกพัน section to match the user's exact sample format:
    Column 19 (ผู้รับจำนอง/ภาระผูกพัน):
      ภาระผูกพัน N ราย
      (1) จำนอง วันที่ 8 มกราคม 2559 วงเงิน 540,000.00 บาท รวม 1 แปลง, ผู้รับ ธนาคาร... เลขที่บัตรประชาชน -
    Column 20 (วงเงินจำนอง (บาท)):
      (1) วงเงิน 540,000.00 บาท
      รวมเป็นเงินจำนวน 540,000.00 บาท
    """
    if not obl_lines:
        return '-', '-'

    full_obl = "\n".join(l['text'] for l in obl_lines)
    if 'ไม่มี' in full_obl and len(obl_lines) <= 2:
        return '-', '-'

    # Count how many obligations
    m_cnt = re.search(r'\(\s*ภาระผูกพัน\s*(\d+)\s*รายการ\s*\)', full_obl)
    total_cnt = int(m_cnt.group(1)) if m_cnt else 0

    # Group lines by obligation item starting with "1." or "1 " at x ~ 40
    items = []
    current = None
    for l in obl_lines[1:]:  # skip header line "รายการภาระผูกพัน ..."
        t = l['text'].strip()
        if not t:
            continue
        m_start = re.match(r'^(\d+)[\.\s]+(.+)$', t)
        # Ensure it's not "ลำดับที่ 1"
        if m_start and not t.startswith('ลำดับที่'):
            if current:
                items.append(current)
            current = {
                'idx': int(m_start.group(1)),
                'lines': [m_start.group(2).strip()]
            }
        else:
            if current is None:
                current = {'idx': 1, 'lines': [t]}
            else:
                current['lines'].append(t)

    if current:
        items.append(current)

    if not items:
        return '-', '-'

    if total_cnt == 0:
        total_cnt = len(items)

    formatted_items = []
    amt_lines = []
    total_amount_val = 0.0

    for item in items:
        idx = item['idx']
        raw_block = " ".join(item['lines'])
        # Normalize spaces inวันที่8 -> วันที่ 8, เลขที่บัตรประชาชน- -> เลขที่บัตรประชาชน -
        raw_block = re.sub(r'วันที่(\d)', r'วันที่ \1', raw_block)
        raw_block = re.sub(r'เลขที่บัตรประชาชน\s*([-\d]+)', r'เลขที่บัตรประชาชน \1', raw_block)

        # Extract type and date: e.g. จำนอง วันที่ 8 มกราคม 2559
        m_type_date = re.search(r'^([^\d]+?)\s*(วันที่\s*\d{1,2}\s+[^\s]+\s+\d{4})', raw_block)
        obl_type_date = m_type_date.group(0).strip() if m_type_date else item['lines'][0]

        # Extract amount: วงเงิน 540,000 บาท
        m_amt = re.search(r'วงเงิน\s*([\d,]+(?:\.\d+)?)\s*บาท', raw_block)
        amt_val = 0.0
        amt_formatted = "0.00"
        if m_amt:
            amt_val = float(m_amt.group(1).replace(',', ''))
            total_amount_val += amt_val
            amt_formatted = f"{amt_val:,.2f}"

        # Extract plots count: รวม 1 แปลง
        m_plots = re.search(r'(รวม\s*\d+\s*แปลง)', raw_block)
        plots_str = m_plots.group(1) if m_plots else "รวม 1 แปลง"

        # Extract mortgagee: ผู้รับ ... เลขที่บัตรประชาชน ...
        m_rec = re.search(r'(ผู้รับ\s*.+)', raw_block)
        rec_str = m_rec.group(1).strip() if m_rec else "ผู้รับ - เลขที่บัตรประชาชน -"

        desc = f"({idx}) {obl_type_date} วงเงิน {amt_formatted} บาท {plots_str}, {rec_str}"
        formatted_items.append(desc)
        amt_lines.append(f"({idx}) วงเงิน {amt_formatted} บาท")

    header_str = f"ภาระผูกพัน {total_cnt} ราย"
    col19 = header_str + "\n" + "\n".join(formatted_items)

    amt_lines.append(f"รวมเป็นเงินจำนวน {total_amount_val:,.2f} บาท")
    col20 = "\n".join(amt_lines)

    return col19, col20


def _format_summary_area(s_item):
    raw_area = (s_item.get('area') or '').strip()
    if not raw_area:
        return '-'
    m_land = re.match(r'^(\d+)\s*-\s*(\d+)\s*-\s*([\d\.]+)$', raw_area)
    if m_land:
        return f"{m_land.group(1)} ไร่ {m_land.group(2)} งาน {m_land.group(3)} ตารางวา"
    if any(k in s_item.get('asset_type', '') for k in ['อาคารโรงเรือน', 'ห้องชุด']):
        return f"{raw_area} ตารางเมตร" if 'ตารางเมตร' not in raw_area else raw_area
    return raw_area


def _match_detail_page_for_summary_item(s_item, candidate_pages, used_detail_ids, person_cid='', person_name=''):
    deed_no = s_item.get('deed_no', '')
    surv_no = s_item.get('survey_page', '-')
    asset_type = s_item.get('asset_type', '')

    # Special case: Standalone อาคารโรงเรือน on summary page (deed_no is empty, but house no is in building_info)
    if 'อาคารโรงเรือน' in asset_type and not deed_no:
        b_info = s_item.get('building_info', '-')
        for dp in candidate_pages:
            if id(dp) in used_detail_ids:
                continue
            det = dp['detail_data']
            if 'อาคารโรงเรือน' in det.get('asset_type', ''):
                if b_info != '-' and det.get('building_info') == b_info:
                    return dp
                if person_cid and (person_cid == det.get('cid') or person_cid in det.get('all_cids', [])):
                    return dp
                if person_name and any(person_name in n or n in person_name for n in det.get('all_names', [])):
                    return dp
        return None

    if not deed_no:
        return None

    # Pass 1: Match both deed_no AND survey_page (when survey_page is known on both)
    if surv_no and surv_no != '-':
        for dp in candidate_pages:
            if id(dp) in used_detail_ids:
                continue
            det = dp['detail_data']
            if det.get('deed_no') == deed_no and det.get('survey_page') == surv_no:
                return dp

    # Pass 2: Match deed_no + owner CID/Name
    for dp in candidate_pages:
        if id(dp) in used_detail_ids:
            continue
        det = dp['detail_data']
        if det.get('deed_no') == deed_no:
            if person_cid and (person_cid == det.get('cid') or person_cid in det.get('all_cids', [])):
                return dp
            if person_name and any(person_name in n or n in person_name for n in det.get('all_names', [])):
                return dp

    # Pass 3: Match deed_no
    for dp in candidate_pages:
        if id(dp) in used_detail_ids:
            continue
        if dp['detail_data'].get('deed_no') == deed_no:
            return dp

    return None


def _classify_pdf_pages_fast(pdf_paths, progress_callback=None, open_docs=None):
    """
    Classifies all pages across pdf_paths.
    Digital text and blank pages are classified instantaneously on the main thread (thread-safe PyMuPDF),
    while scanned image pages are queued and OCR'd in parallel across CPU worker threads.
    """
    if isinstance(pdf_paths, str):
        pdf_paths = [pdf_paths]

    local_docs = {}
    docs_map = open_docs if open_docs is not None else local_docs

    try:
        total_pages_all = 0
        for path in pdf_paths:
            if path not in docs_map:
                docs_map[path] = fitz.open(path)
            total_pages_all += len(docs_map[path])

        all_pages_meta = [None] * total_pages_all
        ocr_tasks = []  # list of (global_idx, path, fname, pno, img_bytes)
        processed_pages = 0
        global_idx = 0

        for path in pdf_paths:
            doc = docs_map[path]
            fname = os.path.basename(path)
            for pno in range(len(doc)):
                page = doc[pno]
                words = page.get_text("words")
                meaningful_words = [w for w in words if re.sub(r'[\.\-_\s]+', '', w[4])]

                if meaningful_words:
                    # Digital text page -> classify immediately without OCR
                    p_info = classify_land_page(page)
                    p_info['file_path'] = path
                    p_info['file_name'] = fname
                    p_info['page_idx'] = pno
                    p_info['page_no'] = pno + 1
                    all_pages_meta[global_idx] = p_info
                    processed_pages += 1
                    if progress_callback:
                        progress_callback(processed_pages, total_pages_all)
                else:
                    imgs = page.get_images(full=True)
                    if not imgs:
                        # Blank / separator page
                        p_info = classify_land_page(None, pre_lines=([], ""))
                        p_info['file_path'] = path
                        p_info['file_name'] = fname
                        p_info['page_idx'] = pno
                        p_info['page_no'] = pno + 1
                        all_pages_meta[global_idx] = p_info
                        processed_pages += 1
                        if progress_callback:
                            progress_callback(processed_pages, total_pages_all)
                    else:
                        # Scanned image page -> extract bytes on main thread and queue for parallel OCR
                        img_bytes = None
                        if len(imgs) == 1:
                            try:
                                base_img = doc.extract_image(imgs[0][0])
                                if base_img and base_img.get("width", 0) >= 600:
                                    img_bytes = base_img["image"]
                            except Exception:
                                img_bytes = None
                        if img_bytes is None:
                            pix = page.get_pixmap(dpi=180)
                            img_bytes = pix.tobytes("png")
                        ocr_tasks.append((global_idx, path, fname, pno, img_bytes))

                global_idx += 1

        if ocr_tasks:
            def _ocr_worker(task_tuple):
                g_idx, f_path, f_name, p_no, ibytes = task_tuple
                pre_lines = _ocr_image_bytes_to_lines(ibytes)
                p_info = classify_land_page(None, pre_lines=pre_lines)
                p_info['file_path'] = f_path
                p_info['file_name'] = f_name
                p_info['page_idx'] = p_no
                p_info['page_no'] = p_no + 1
                return g_idx, p_info

            workers = min(OCR_WORKERS, len(ocr_tasks))
            if workers <= 1:
                for t_item in ocr_tasks:
                    g_idx, p_info = _ocr_worker(t_item)
                    all_pages_meta[g_idx] = p_info
                    processed_pages += 1
                    if progress_callback:
                        progress_callback(processed_pages, total_pages_all)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_ocr_worker, t_item) for t_item in ocr_tasks]
                    for fut in as_completed(futures):
                        g_idx, p_info = fut.result()
                        all_pages_meta[g_idx] = p_info
                        processed_pages += 1
                        if progress_callback:
                            progress_callback(processed_pages, total_pages_all)

        return all_pages_meta
    finally:
        for doc in local_docs.values():
            try:
                doc.close()
            except Exception:
                pass


def extract_land_pdf_to_records(pdf_paths, progress_callback=None, start_seq=1, _preparsed_pages=None):
    """
    Accepts either a single PDF path (str) or a list of PDF paths.
    Extracts all land records in the 25-column structure.
    """
    if _preparsed_pages is not None:
        all_pages_meta = _preparsed_pages
    else:
        all_pages_meta = _classify_pdf_pages_fast(pdf_paths, progress_callback=progress_callback)
        # Attach detail continuation pages (detail_cont) to their parent detail pages and re-parse
        _attach_detail_continuations(all_pages_meta)

    # Group by person / summary report, or pair summary items with detail pages by deed_no + survey_page
    summary_groups = []
    current_group = None

    for p_info in all_pages_meta:
        ptype = p_info['page_type']
        if ptype == 'summary':
            current_group = {
                'summary_page': p_info,
                'cont_pages': [],
                'detail_pages': []
            }
            summary_groups.append(current_group)
        elif ptype == 'summary_cont':
            if current_group:
                current_group['cont_pages'].append(p_info)
                if current_group['summary_page'] and p_info.get('summary_items'):
                    existing_seqs = {it['seq'] for it in current_group['summary_page']['summary_items']}
                    for it in p_info['summary_items']:
                        if it['seq'] not in existing_seqs:
                            current_group['summary_page']['summary_items'].append(it)
                            existing_seqs.add(it['seq'])
        elif ptype == 'detail':
            if current_group:
                current_group['detail_pages'].append(p_info)
            else:
                current_group = {
                    'summary_page': None,
                    'cont_pages': [],
                    'detail_pages': [p_info]
                }
                summary_groups.append(current_group)

    all_detail_pages = [p for p in all_pages_meta if p['page_type'] == 'detail']
    used_detail_ids = set()

    # Pass 1: Pre-match explicit summary_items across all summary groups so an earlier group's
    # leftover/orphan loop never steals a detail page that belongs to a later summary page's table
    prematched_map = {}  # (id(grp), item_idx) -> matched_dp
    for grp in summary_groups:
        sum_page = grp['summary_page']
        if not sum_page:
            continue
        cid = sum_page['cid']
        owner_name = sum_page['owner_name']
        sum_items = sum_page['summary_items']
        grp_details = grp['detail_pages']

        for item_idx, s_item in enumerate(sum_items):
            # First check within same group's detail pages, then global detail pages
            matched_det = _match_detail_page_for_summary_item(s_item, grp_details, used_detail_ids, cid, owner_name)
            if not matched_det:
                matched_det = _match_detail_page_for_summary_item(s_item, all_detail_pages, used_detail_ids, cid, owner_name)
            if matched_det:
                used_detail_ids.add(id(matched_det))
                prematched_map[(id(grp), item_idx)] = matched_det

    records = []
    person_seq = start_seq

    for grp in summary_groups:
        sum_page = grp['summary_page']
        grp_details = grp['detail_pages']

        if sum_page:
            cid = sum_page['cid']
            owner_name = sum_page['owner_name']
            print_date = sum_page['print_date']
            sum_items = sum_page['summary_items']

            if not sum_items and sum_page['found_count'] == 0:
                records.append({
                    'no': person_seq,
                    'print_date': print_date or '-',
                    'cid': cid or '-',
                    'full_name': owner_name or '-',
                    'search_result': 'ไม่พบทรัพย์',
                    'asset_type': '-',
                    'deed_no': '-',
                    'survey_page': '-',
                    'land_no': '-',
                    'tambon': '-',
                    'amphoe': '-',
                    'province': '-',
                    'building_info': '-',
                    'area_text': '-',
                    'owner_formatted': '-',
                    'age': '-',
                    'acquisition_type': '-',
                    'reg_capital': 0,
                    'land_value': 0,
                    'building_value': 0,
                    'mortgage_info': '-',
                    'mortgage_amount': '-',
                    'seizure_status': '-',
                    'land_office': '-',
                    'phone': '-'
                })
                person_seq += 1
                continue

            has_land_or_condo = any('อาคารโรงเรือน' not in it.get('asset_type', '') and it.get('deed_no') for it in sum_items)
            summary_bldg_items = [it for it in sum_items if 'อาคารโรงเรือน' in it.get('asset_type', '') and not it.get('deed_no')]
            person_records = []

            # Prefer clean digital owner_name from detail page if summary page was OCR
            if grp_details and any(l.get('is_ocr') for l in sum_page.get('lines', [])):
                for dp_cand in grp_details:
                    d_name = (dp_cand.get('detail_data') or {}).get('owner_name')
                    if d_name:
                        owner_name = d_name
                        sum_page['owner_name'] = d_name
                        break

            for item_idx, s_item in enumerate(sum_items):
                deed_no = s_item['deed_no']
                is_bldg_row = ('อาคารโรงเรือน' in s_item.get('asset_type', '') and not deed_no)

                matched_det = prematched_map.get((id(grp), item_idx))

                if is_bldg_row and not matched_det and has_land_or_condo:
                    continue

                det = matched_det['detail_data'] if matched_det else {}

                b_info = det.get('building_info', '-')
                if b_info == '-' and s_item.get('building_info', '-') != '-':
                    b_info = s_item['building_info']

                # If this is a standalone อาคารโรงเรือน detail page and this person already has land/condo rows,
                # merge any missing building house number, building value, or building area into the matching land row
                # and skip creating a redundant อาคารโรงเรือน row with deed_no = '-'
                if is_bldg_row and has_land_or_condo and person_records:
                    b_house_no = det.get('house_no', '')
                    b_tambon = det.get('tambon') if det.get('tambon', '-') != '-' else s_item.get('tambon', '-')
                    b_amphoe = det.get('amphoe') if det.get('amphoe', '-') != '-' else s_item.get('amphoe', '-')
                    target_pr = None
                    for pr in person_records:
                        if (b_info != '-' and b_info in pr.get('building_info', '')) or (b_house_no and f"เลขที่ {b_house_no}" in pr.get('building_info', '')):
                            target_pr = pr
                            break
                    if target_pr is None:
                        for pr in person_records:
                            if b_tambon != '-' and pr.get('tambon') == b_tambon:
                                target_pr = pr
                                break
                    if target_pr is None:
                        for pr in person_records:
                            if b_amphoe != '-' and pr.get('amphoe') == b_amphoe:
                                target_pr = pr
                                break
                    if target_pr is None:
                        target_pr = person_records[-1]

                    if b_info != '-':
                        cur_bi = target_pr.get('building_info', '-')
                        if cur_bi == '-' or (cur_bi in b_info and len(b_info) > len(cur_bi)):
                            target_pr['building_info'] = b_info
                        elif b_info not in cur_bi and (not b_house_no or f"เลขที่ {b_house_no}" not in cur_bi):
                            target_pr['building_info'] = f"{cur_bi}\n{b_info}"
                    if det.get('building_value', 0) > 0 and target_pr.get('building_value', 0) == 0:
                        target_pr['building_value'] = det['building_value']
                    b_area = det.get('building_area') or (_format_summary_area(s_item) if s_item.get('area') else '')
                    if b_area and b_area != '-' and 'อาคาร' not in target_pr.get('area_text', ''):
                        target_pr['area_text'] = f"{target_pr['area_text']} | อาคาร {b_area}"
                    continue

                area_val = det.get('area_text', '-')
                if area_val == '-' or not area_val:
                    area_val = _format_summary_area(s_item)

                rec = {
                    'no': person_seq,
                    'print_date': det.get('print_date') or print_date or '-',
                    'cid': cid or det.get('cid') or '-',
                    'full_name': det.get('owner_name') or owner_name or '-',
                    'search_result': 'พบทรัพย์',
                    'asset_type': det.get('asset_type') or s_item.get('asset_type') or 'โฉนดที่ดิน',
                    'deed_no': (det.get('deed_no') if det.get('deed_no', '-') != '-' else deed_no) or '-',
                    'survey_page': det.get('survey_page') if det.get('survey_page', '-') != '-' else s_item.get('survey_page', '-'),
                    'land_no': det.get('land_no') if det.get('land_no', '-') != '-' else s_item.get('land_no', '-'),
                    'tambon': det.get('tambon') if det.get('tambon', '-') != '-' else s_item.get('tambon', '-'),
                    'amphoe': det.get('amphoe') if det.get('amphoe', '-') != '-' else s_item.get('amphoe', '-'),
                    'province': det.get('province') if det.get('province', '-') != '-' else s_item.get('province', '-'),
                    'building_info': b_info,
                    'area_text': area_val,
                    'owner_formatted': det.get('owner_formatted') or (f"(1) {owner_name}  ({sum_page['cid_dashed']})" if owner_name else '-'),
                    'age': det.get('age', '-'),
                    'acquisition_type': det.get('acquisition_type', '-'),
                    'reg_capital': det.get('reg_capital', 0),
                    'land_value': det.get('land_value', 0),
                    'building_value': det.get('building_value', 0),
                    'mortgage_info': det.get('mortgage_info', '-'),
                    'mortgage_amount': det.get('mortgage_amount', '-'),
                    'seizure_status': det.get('seizure_status', 'ไม่มี'),
                    'land_office': s_item.get('land_office') or det.get('land_office', '-'),
                    'phone': det.get('phone', '-')
                }
                person_records.append(rec)

            # Fallback: if only summary page was uploaded (no detail page matched) and there was 1 land deed + 1 summary building item
            if len(person_records) == 1 and summary_bldg_items and person_records[0]['building_info'] == '-':
                sb = summary_bldg_items[0]
                if sb.get('building_info', '-') != '-':
                    person_records[0]['building_info'] = sb['building_info']
                if sb.get('area') and 'อาคาร' not in person_records[0]['area_text']:
                    person_records[0]['area_text'] = f"{person_records[0]['area_text']} | อาคาร {sb['area']} ตารางเมตร"

            records.extend(person_records)

            # Check if there are any remaining detail pages in grp_details that were not in ANY summary page's table
            for dp in grp_details:
                if id(dp) not in used_detail_ids:
                    used_detail_ids.add(id(dp))
                    det = dp['detail_data']
                    records.append({
                        'no': person_seq,
                        'print_date': det.get('print_date') or print_date or '-',
                        'cid': det.get('cid') or cid or '-',
                        'full_name': det.get('owner_name') or owner_name or '-',
                        'search_result': 'พบทรัพย์',
                        'asset_type': det.get('asset_type', 'โฉนดที่ดิน'),
                        'deed_no': det.get('deed_no', '-'),
                        'survey_page': det.get('survey_page', '-'),
                        'land_no': det.get('land_no', '-'),
                        'tambon': det.get('tambon', '-'),
                        'amphoe': det.get('amphoe', '-'),
                        'province': det.get('province', '-'),
                        'building_info': det.get('building_info', '-'),
                        'area_text': det.get('area_text', '-'),
                        'owner_formatted': det.get('owner_formatted', '-'),
                        'age': det.get('age', '-'),
                        'acquisition_type': det.get('acquisition_type', '-'),
                        'reg_capital': det.get('reg_capital', 0),
                        'land_value': det.get('land_value', 0),
                        'building_value': det.get('building_value', 0),
                        'mortgage_info': det.get('mortgage_info', '-'),
                        'mortgage_amount': det.get('mortgage_amount', '-'),
                        'seizure_status': det.get('seizure_status', 'ไม่มี'),
                        'land_office': det.get('land_office', '-'),
                        'phone': det.get('phone', '-')
                    })

            person_seq += 1
        else:
            # Group with only detail pages (no summary page)
            for dp in grp_details:
                if id(dp) not in used_detail_ids:
                    used_detail_ids.add(id(dp))
                    det = dp['detail_data']
                    records.append({
                        'no': person_seq,
                        'print_date': det.get('print_date', '-'),
                        'cid': det.get('cid', '-'),
                        'full_name': det.get('owner_name', '-'),
                        'search_result': 'พบทรัพย์',
                        'asset_type': det.get('asset_type', 'โฉนดที่ดิน'),
                        'deed_no': det.get('deed_no', '-'),
                        'survey_page': det.get('survey_page', '-'),
                        'land_no': det.get('land_no', '-'),
                        'tambon': det.get('tambon', '-'),
                        'amphoe': det.get('amphoe', '-'),
                        'province': det.get('province', '-'),
                        'building_info': det.get('building_info', '-'),
                        'area_text': det.get('area_text', '-'),
                        'owner_formatted': det.get('owner_formatted', '-'),
                        'age': det.get('age', '-'),
                        'acquisition_type': det.get('acquisition_type', '-'),
                        'reg_capital': det.get('reg_capital', 0),
                        'land_value': det.get('land_value', 0),
                        'building_value': det.get('building_value', 0),
                        'mortgage_info': det.get('mortgage_info', '-'),
                        'mortgage_amount': det.get('mortgage_amount', '-'),
                        'seizure_status': det.get('seizure_status', 'ไม่มี'),
                        'land_office': det.get('land_office', '-'),
                        'phone': det.get('phone', '-')
                    })
                    person_seq += 1

    return records


def merge_and_sort_land_pdfs(pdf_paths, output_pdf_path, sort_strategy='smart_deed', progress_callback=None, return_pages_meta=False):
    """
    Merges multiple Land PDF files, automatically sorting pages so that:
      For each person (Summary Page):
        1. Summary Page (+ any immediate summary continuation pages)
        2. Detail Pages (+ their detail_cont_pages) ordered according to the sequence of Deed Numbers on the Summary Page!
        3. Any additional Detail Pages belonging to the SAME person immediately follow that person's block!
    """
    open_docs = {}
    all_pages_meta = []

    try:
        all_pages_meta = _classify_pdf_pages_fast(pdf_paths, progress_callback=progress_callback, open_docs=open_docs)

        # Attach detail continuation pages (detail_cont) to their parent detail pages
        _attach_detail_continuations(all_pages_meta)
        attached_cont_ids = {id(cp) for p in all_pages_meta if p['page_type'] == 'detail' for cp in p.get('detail_cont_pages', [])}

        # Separate pages into summary groups and detail pool
        summary_groups = []
        current_sum = None
        detail_pool = []
        other_pages = []

        for p_info in all_pages_meta:
            if id(p_info) in attached_cont_ids:
                continue
            ptype = p_info['page_type']
            if ptype == 'summary':
                current_sum = {
                    'summary': p_info,
                    'cont_pages': []
                }
                summary_groups.append(current_sum)
            elif ptype == 'summary_cont':
                if current_sum is not None:
                    current_sum['cont_pages'].append(p_info)
                    if p_info.get('summary_items'):
                        existing_seqs = {it['seq'] for it in current_sum['summary']['summary_items']}
                        for it in p_info['summary_items']:
                            if it['seq'] not in existing_seqs:
                                current_sum['summary']['summary_items'].append(it)
                                existing_seqs.add(it['seq'])
                else:
                    other_pages.append(p_info)
            elif ptype == 'detail':
                detail_pool.append(p_info)
            else:
                if current_sum is not None:
                    current_sum['cont_pages'].append(p_info)
                else:
                    other_pages.append(p_info)

        used_details = set()
        ordered_pages = []  # list of p_info in target order
        report = {
            'total_files': len(pdf_paths),
            'total_input_pages': len(all_pages_meta),
            'total_persons': len(summary_groups),
            'matched_count': 0,
            'missing_details': [],
            'orphan_details': [],
            'person_summaries': []
        }

        def _append_detail_with_cont(dp_item):
            ordered_pages.append(dp_item)
            for cp in dp_item.get('detail_cont_pages', []):
                ordered_pages.append(cp)

        # Pass 1: Pre-match all explicit summary_items across all summary_groups so an earlier group's
        # orphan loop never steals a detail page that is explicitly listed on a later summary page
        prematched_merge_map = {}  # (id(grp), item_idx) -> matched_dp
        for grp in summary_groups:
            sp = grp['summary']
            owner_name = sp['owner_name'] or ''
            cid = sp['cid'] or ''
            for item_idx, s_item in enumerate(sp['summary_items']):
                matched_dp = _match_detail_page_for_summary_item(s_item, detail_pool, used_details, cid, owner_name)
                if matched_dp:
                    used_details.add(id(matched_dp))
                    prematched_merge_map[(id(grp), item_idx)] = matched_dp

        for grp_idx, grp in enumerate(summary_groups, start=1):
            sp = grp['summary']
            ordered_pages.append(sp)
            for cp in grp['cont_pages']:
                ordered_pages.append(cp)

            owner_name = sp['owner_name'] or 'ไม่ระบุชื่อ'
            cid = sp['cid'] or ''
            cid_dashed = sp['cid_dashed'] or '-'
            sum_items = sp['summary_items']
            has_land_or_condo = any('อาคารโรงเรือน' not in it.get('asset_type', '') and it.get('deed_no') for it in sum_items)

            # Upgrade owner_name from matched digital detail page if summary page was OCR
            if any(l.get('is_ocr') for l in sp.get('lines', [])):
                for item_idx in range(len(sum_items)):
                    mdp = prematched_merge_map.get((id(grp), item_idx))
                    if mdp and (mdp.get('detail_data') or {}).get('owner_name'):
                        owner_name = mdp['detail_data']['owner_name']
                        sp['owner_name'] = owner_name
                        break

            person_rep = {
                'seq': grp_idx,
                'owner_name': owner_name,
                'cid': cid_dashed,
                'source_file': sp['file_name'],
                'source_page': sp['page_no'],
                'expected_count': len(sum_items),
                'matched_deeds': [],
                'missing_deeds': []
            }

            for item_idx, s_item in enumerate(sum_items):
                target_deed = s_item['deed_no']
                is_bldg_row = ('อาคารโรงเรือน' in s_item.get('asset_type', '') and not target_deed)

                matched_dp = prematched_merge_map.get((id(grp), item_idx))

                if is_bldg_row and not matched_dp and has_land_or_condo:
                    # Building is included inside the land deed's detail page (รายการสิ่งปลูกสร้าง)
                    continue

                if matched_dp:
                    _append_detail_with_cont(matched_dp)
                    report['matched_count'] += 1
                    det_d = matched_dp['detail_data']
                    disp_deed = target_deed or det_d.get('deed_no', '-')
                    if disp_deed == '-' and det_d.get('house_no'):
                        disp_deed = f"บ้านเลขที่ {det_d['house_no']}"
                    person_rep['matched_deeds'].append({
                        'seq': s_item['seq'],
                        'deed_no': disp_deed,
                        'asset_type': s_item['asset_type'],
                        'detail_file': matched_dp['file_name'],
                        'detail_page': matched_dp['page_no']
                    })
                else:
                    missing_entry = {
                        'owner_name': owner_name,
                        'cid': cid_dashed,
                        'seq': s_item['seq'],
                        'deed_no': target_deed or '-',
                        'asset_type': s_item['asset_type'],
                        'land_office': s_item['land_office'],
                        'source_file': sp['file_name'],
                        'source_page': sp['page_no'],
                        'reason': f'มีชื่ออยู่ในตารางสรุปหน้าแรก (ลำดับที่ {s_item["seq"]}) แต่ไม่พบหน้ารายละเอียดของเลขโฉนด {target_deed or "-"}'
                    }
                    report['missing_details'].append(missing_entry)
                    person_rep['missing_deeds'].append(missing_entry)

            person_rep['expected_count'] = len(person_rep['matched_deeds']) + len(person_rep['missing_deeds'])

            # Pass 2 for this person: Keep any truly unmatched detail pages belonging to THIS person right here
            for dp in detail_pool:
                if id(dp) in used_details:
                    continue
                det = dp['detail_data']
                dp_cid = det.get('cid', '')
                dp_all_cids = det.get('all_cids', [])
                dp_name = det.get('owner_name', '')
                dp_all_names = det.get('all_names', [])

                same_person = False
                if cid and (cid == dp_cid or cid in dp_all_cids):
                    same_person = True
                elif owner_name and (
                    (dp_name and (owner_name == dp_name or owner_name in dp_name or dp_name in owner_name))
                    or any(owner_name in n or n in owner_name for n in dp_all_names)
                ):
                    same_person = True

                if same_person:
                    used_details.add(id(dp))
                    _append_detail_with_cont(dp)
                    report['orphan_details'].append({
                        'owner_name': det.get('owner_name') or owner_name,
                        'cid': det.get('cid_dashed') or cid_dashed,
                        'deed_no': det.get('deed_no') or '-',
                        'asset_type': det.get('asset_type') or '-',
                        'source_file': dp['file_name'],
                        'source_page': dp['page_no'],
                        'reason': f'พบหน้ารายละเอียดของโฉนดเลขที่ {det.get("deed_no", "-")} ({det.get("owner_name", "-")}) แต่ไม่พบเลขโฉนดนี้ในตารางสรุปหน้าแรก (จัดเรียงไว้ต่อท้ายรายชื่อเดียวกันให้แล้ว)'
                    })

            report['person_summaries'].append(person_rep)

        # Check for any remaining unused detail pages (Orphan details with no matching person at all)
        for dp in detail_pool:
            if id(dp) not in used_details:
                used_details.add(id(dp))
                _append_detail_with_cont(dp)
                det = dp['detail_data']
                report['orphan_details'].append({
                    'owner_name': det.get('owner_name') or 'ไม่ระบุชื่อ',
                    'cid': det.get('cid_dashed') or '-',
                    'deed_no': det.get('deed_no') or '-',
                    'asset_type': det.get('asset_type') or '-',
                    'source_file': dp['file_name'],
                    'source_page': dp['page_no'],
                    'reason': f'พบหน้ารายละเอียดของโฉนดเลขที่ {det.get("deed_no", "-")} ({det.get("owner_name", "-")}) แต่ไม่พบหน้าสรุปหลักทรัพย์หน้าแรกของบุคคลนี้'
                })

        # Append any remaining unclassified pages
        for op in other_pages:
            ordered_pages.append(op)

        final_pages_to_write = all_pages_meta if sort_strategy == 'strict_file_order' else ordered_pages

        # Build output merged PDF
        out_doc = fitz.open()
        for p_info in final_pages_to_write:
            src_doc = open_docs[p_info['file_path']]
            out_doc.insert_pdf(src_doc, from_page=p_info['page_idx'], to_page=p_info['page_idx'])

        os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)
        out_doc.save(output_pdf_path)
        out_doc.close()

        report['total_output_pages'] = len(final_pages_to_write)
        report['is_complete'] = (len(report['missing_details']) == 0 and len(report['orphan_details']) == 0)
        if return_pages_meta:
            return report, all_pages_meta
        return report

    finally:
        for doc in open_docs.values():
            try:
                doc.close()
            except Exception:
                pass

