import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import os

def export_records_to_excel(records, output_path):
    wb = openpyxl.Workbook()
    
    # Sheet 1: Standard
    ws1 = wb.active
    ws1.title = 'ข้อมูลแบบมาตรฐาน'
    
    # Sheet 2: Detailed
    ws2 = wb.create_sheet(title='ข้อมูลแบบแยกละเอียด')
    
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
    data_font = Font(name='Segoe UI', size=10)
    thin_side = Side(border_style='thin', color='D9D9D9')
    data_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    center_align = Alignment(horizontal='center', vertical='center')
    left_align = Alignment(horizontal='left', vertical='center')
    
    # Setup Sheet 1
    headers1 = [
        'ลำดับ', 'เลขประจำตัวประชาชน', 'รหัสประจำบ้าน', 'ชื่อ-นามสกุล', 'เพศ',
        'สัญชาติ', 'วันเดือนปีเกิด', 'อายุ', 'สถานภาพ', 'มารดา', 'เลขบัตรมารดา',
        'สัญชาติมารดา', 'บิดา', 'เลขบัตรบิดา', 'สัญชาติบิดา', 'ที่อยู่ตามทะเบียนบ้าน',
        'สำนักทะเบียน', 'วันที่แจ้ง/ย้ายเข้า', 'สำนักทะเบียนที่ออก', 'วันที่พิมพ์'
    ]
    ws1.append(headers1)
    
    for r in records:
        full_name = f"{r.get('title','')}{r.get('fname','')} {r.get('lname','')}".strip()
        if not full_name:
            full_name = r.get('full_name', '')
            
        ws1.append([
            r.get('no', 1),
            r.get('cid', ''),
            r.get('hid', ''),
            full_name,
            r.get('gender', ''),
            r.get('nat', 'ไทย'),
            r.get('dob', ''),
            r.get('age', ''),
            r.get('status', ''),
            r.get('mother_name', ''),
            r.get('mother_cid', '-'),
            r.get('mother_nat', 'ไทย'),
            r.get('father_name', ''),
            r.get('father_cid', '-'),
            r.get('father_nat', 'ไทย'),
            r.get('address', ''),
            r.get('reg_office', ''),
            r.get('move_date', ''),
            r.get('issue_office', ''),
            r.get('print_date', '')
        ])
        
    # Setup Sheet 2
    headers2 = [
        'ลำดับ', 'เลขประจำตัวประชาชน', 'รหัสประจำบ้าน', 'คำนำหน้า', 'ชื่อตัว', 'นามสกุล',
        'เพศ', 'สัญชาติ', 'วันเดือนปีเกิด', 'อายุ', 'สถานภาพ',
        'มารดา', 'เลขบัตรมารดา', 'สัญชาติมารดา',
        'บิดา', 'เลขบัตรบิดา', 'สัญชาติบิดา',
        'บ้านเลขที่', 'หมู่ที่', 'ซอย', 'ถนน', 'ตำบล/แขวง', 'อำเภอ/เขต', 'จังหวัด',
        'สำนักทะเบียน', 'วันที่แจ้ง/ย้ายเข้า', 'สำนักทะเบียนที่ออก', 'วันที่พิมพ์'
    ]
    ws2.append(headers2)
    
    for r in records:
        ws2.append([
            r.get('no', 1),
            r.get('cid', ''),
            r.get('hid', ''),
            r.get('title', ''),
            r.get('fname', ''),
            r.get('lname', ''),
            r.get('gender', ''),
            r.get('nat', 'ไทย'),
            r.get('dob', ''),
            r.get('age', ''),
            r.get('status', ''),
            r.get('mother_name', ''),
            r.get('mother_cid', '-'),
            r.get('mother_nat', 'ไทย'),
            r.get('father_name', ''),
            r.get('father_cid', '-'),
            r.get('father_nat', 'ไทย'),
            r.get('addr_no', ''),
            r.get('moo', '-'),
            r.get('soi', '-'),
            r.get('road', '-'),
            r.get('subdistrict', ''),
            r.get('district', ''),
            r.get('province', ''),
            r.get('reg_office', ''),
            r.get('move_date', ''),
            r.get('issue_office', ''),
            r.get('print_date', '')
        ])
        
    for ws in [ws1, ws2]:
        ws.row_dimensions[1].height = 28
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
        
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=ws.max_column):
            ws.row_dimensions[row[0].row].height = 22
            for cell in row:
                cell.font = data_font
                cell.border = data_border
                val_str = str(cell.value or '')
                if cell.column == 1 or len(val_str) <= 6 or cell.column in [5, 6, 8, 9, 11, 12, 14, 15]:
                    cell.alignment = center_align
                else:
                    cell.alignment = left_align

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len * 2.0, 12)
            
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    return output_path
