import os
import uuid
from functools import wraps
from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory, abort
)
from werkzeug.utils import secure_filename

import database
import extractor
import land_extractor
import excel_exporter
import hardware_id

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'dev-only-change-me'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('RENDER', '').lower() == 'true',
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Initialize Database
database.init_db()

# Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('กรุณาเข้าสู่ระบบก่อนใช้งาน', 'warning')
            return redirect(url_for('login', next=request.url))
            
        # Check if user is still active in DB
        user = database.get_user_by_id(session['user_id'])
        if not user or user['status'] == 'suspended':
            session.clear()
            flash('บัญชีของคุณถูกระงับการใช้งาน กรุณาติดต่อผู้ดูแลระบบ', 'danger')
            return redirect(url_for('login'))
            
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('คุณไม่มีสิทธิ์เข้าถึงหน้านี้ (สำหรับผู้ดูแลระบบเท่านั้น)', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def user_can_civil(user):
    if not user:
        return False
    if user.get('role') == 'admin':
        return True
    return bool(user.get('allow_civil', 1))

def user_can_land(user):
    if not user:
        return False
    if user.get('role') == 'admin':
        return True
    return bool(user.get('allow_land', 1))

# Context Processor for Templates
@app.context_processor
def inject_user():
    user = None
    pending_count = 0
    can_civil = False
    can_land = False
    if 'user_id' in session:
        user = database.get_user_by_id(session['user_id'])
        if user:
            can_civil = user_can_civil(user)
            can_land = user_can_land(user)
        if session.get('role') == 'admin':
            try:
                pending_count = len(database.get_pending_users())
            except Exception:
                pending_count = 0
    gemini_key = database.get_setting('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
    gemini_active = bool(gemini_key)
    return dict(
        current_user=user,
        can_civil=can_civil,
        can_land=can_land,
        pending_count=pending_count,
        now=datetime.now(),
        gemini_active=gemini_active
    )

# Routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('dashboard'))

# Auth Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        user, err = database.authenticate_user(username, password)
        if err:
            flash(err, 'danger')
            return render_template('login.html', username=username)
            
        # Check device binding ONLY for Desktop App installations (Online web users have unlimited devices)
        client_type = request.form.get('client_type', '').strip() or request.headers.get('X-Client-Type', '').strip()
        if client_type == 'desktop' and user['role'] != 'admin':
            client_mac = request.form.get('device_mac', '').strip() or request.headers.get('X-Device-MAC', '').strip() or hardware_id.get_primary_mac_address()
            device_name = request.form.get('device_name', '').strip() or f"{hardware_id.get_machine_info()['hostname']}"
            
            if client_mac:
                dev_success, dev_msg, dev_id = database.register_user_device(user['id'], client_mac, device_name)
                if not dev_success:
                    return render_template('device_limit.html', 
                                           username=user['username'], 
                                           full_name=user['full_name'],
                                           mac=client_mac, 
                                           error=dev_msg)
                session['device_mac'] = client_mac

        # Set session
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['full_name'] = user['full_name']
        session['role'] = user['role']
        
        database.update_last_login(user['id'])
        flash(f"ยินดีต้อนรับคุณ {user['full_name']}", 'success')
        
        if user['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('dashboard'))

    return render_template('login.html')
        
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        phone = request.form.get('phone', '').strip()
        plan_type = request.form.get('plan_type', 'rental_1m').strip()
        access_scope = request.form.get('access_scope', 'civil').strip()
        if access_scope == 'land':
            allow_civil, allow_land = 0, 1
        elif access_scope == 'both':
            allow_civil, allow_land = 1, 1
        else:
            allow_civil, allow_land = 1, 0
        
        if not username or not password or not full_name:
            flash('กรุณากรอกข้อมูลให้ครบถ้วนในช่องที่มีเครื่องหมาย *', 'danger')
            return render_template('register.html')
            
        if password != confirm_password:
            flash('รหัสผ่านและยืนยันรหัสผ่านไม่ตรงกัน', 'warning')
            return render_template('register.html')
            
        if len(password) < 4:
            flash('รหัสผ่านต้องมีความยาวอย่างน้อย 4 ตัวอักษร', 'warning')
            return render_template('register.html')
            
        success, msg = database.register_user(
            username, password, full_name, phone, plan_type,
            allow_civil=allow_civil, allow_land=allow_land
        )
        if success:
            flash(msg, 'success')
            return redirect(url_for('login'))
        else:
            flash(msg, 'danger')
            return render_template('register.html')
            
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('ออกจากระบบเรียบร้อยแล้ว', 'info')
    return redirect(url_for('login'))

@app.route('/change-password', methods=['POST'])
@login_required
def change_my_password():
    current_password = request.form.get('current_password', '').strip()
    new_password = request.form.get('new_password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    
    if not current_password or not new_password or not confirm_password:
        return jsonify({'success': False, 'message': 'กรุณากรอกข้อมูลให้ครบทุกช่อง'}), 400
        
    if new_password != confirm_password:
        return jsonify({'success': False, 'message': 'รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน'}), 400
        
    if len(new_password) < 4:
        return jsonify({'success': False, 'message': 'รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 4 ตัวอักษร'}), 400
        
    success, msg = database.change_password(session['user_id'], current_password, new_password)
    if not success:
        return jsonify({'success': False, 'message': msg}), 400
        
    return jsonify({'success': True, 'message': msg})


# User Portal
@app.route('/dashboard')
@login_required
def dashboard():
    user = database.get_user_by_id(session['user_id'])
    can_civil = user_can_civil(user)
    can_land = user_can_land(user)

    raw_type = request.args.get('type', '').strip()
    if not raw_type:
        doc_type = 'uncertified' if can_civil else ('land' if can_land else 'uncertified')
    else:
        doc_type = raw_type if raw_type in ['uncertified', 'certified', 'land'] else 'uncertified'

    if doc_type in ['uncertified', 'certified'] and not can_civil:
        if can_land:
            if raw_type:
                flash('บัญชีของคุณได้รับสิทธิ์เฉพาะ "ระบบแปลงข้อมูลที่ดิน" เท่านั้น (ไม่มีสิทธิ์ใช้งานระบบแปลง ท.ร.14/1)', 'warning')
            return redirect(url_for('dashboard', type='land'))
    elif doc_type == 'land' and not can_land:
        if can_civil:
            if raw_type:
                flash('บัญชีของคุณได้รับสิทธิ์เฉพาะ "ระบบแปลง ท.ร.14/1" เท่านั้น (ไม่มีสิทธิ์ใช้งานระบบแปลงข้อมูลที่ดิน)', 'warning')
            return redirect(url_for('dashboard', type='uncertified'))

    return render_template('dashboard.html', active_doc_type=doc_type)

MERGED_LAND_CACHE = {}

@app.route('/pdf-merger')
@login_required
def pdf_merger():
    user = database.get_user_by_id(session['user_id'])
    if not user_can_land(user):
        flash('บัญชีของคุณไม่มีสิทธิ์ใช้งานระบบแปลงและรวมไฟล์ที่ดิน (ได้รับสิทธิ์เฉพาะระบบแปลง ท.ร.14/1)', 'warning')
        return redirect(url_for('dashboard', type='uncertified'))
    return render_template('pdf_merger.html')

@app.route('/api/merge-land-pdf', methods=['POST'])
@login_required
def merge_land_pdf_api():
    user = database.get_user_by_id(session['user_id'])
    if not user_can_land(user):
        return jsonify({'success': False, 'error': 'บัญชีของคุณไม่มีสิทธิ์ใช้งานระบบรวมและแปลงไฟล์ที่ดิน กรุณาติดต่อผู้ดูแลระบบ'}), 403
    files = request.files.getlist('pdf_files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'success': False, 'error': 'กรุณาเลือกไฟล์ PDF อย่างน้อย 1 ไฟล์'}), 400

    saved_paths = []
    orig_filenames = []
    unique_prefix = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6]

    try:
        for idx, f in enumerate(files):
            if f and f.filename and f.filename.lower().endswith('.pdf'):
                orig_filenames.append(f.filename)
                clean_name = secure_filename(f.filename) or f'file_{idx+1}.pdf'
                sp = os.path.join(UPLOAD_FOLDER, f"merge_{unique_prefix}_{idx+1}_{clean_name}")
                f.save(sp)
                saved_paths.append(sp)

        if not saved_paths:
            return jsonify({'success': False, 'error': 'ไม่พบไฟล์ PDF ที่ถูกต้อง'}), 400

        sort_strategy = request.form.get('sort_strategy', 'smart_deed').strip()
        merged_pdf_name = f"Merged_Sorted_Land_{unique_prefix}.pdf"
        merged_pdf_path = os.path.join(OUTPUT_FOLDER, merged_pdf_name)

        report, pages_meta = land_extractor.merge_and_sort_land_pdfs(
            saved_paths, merged_pdf_path, sort_strategy=sort_strategy, return_pages_meta=True
        )

        # Also extract 25-column Excel from the pre-parsed pages (single pass, no double PDF parsing!)
        records = land_extractor.extract_land_pdf_to_records(saved_paths, _preparsed_pages=pages_meta)
        excel_name = f"Export_Land_Merged_{unique_prefix}.xlsx"
        excel_path = os.path.join(OUTPUT_FOLDER, excel_name)
        excel_exporter.export_land_records_to_excel(records, excel_path)

        page_count = report.get('total_output_pages', len(records))
        row_count = len(records)
        first_orig = orig_filenames[0] if orig_filenames else merged_pdf_name
        display_orig = first_orig if len(orig_filenames) == 1 else f"{first_orig} (+รวม {len(orig_filenames)} ไฟล์)"
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

        log_id = database.log_conversion(
            user_id=session['user_id'],
            original_filename=f"[ข้อมูลที่ดิน] {display_orig}",
            stored_filename=os.path.basename(saved_paths[0]),
            page_count=page_count,
            row_count=row_count,
            excel_filename=excel_name,
            status='success',
            ip_address=client_ip
        )

        payload = {
            'success': True,
            'doc_type': 'land',
            'merged_id': unique_prefix,
            'log_id': log_id,
            'page_count': page_count,
            'row_count': row_count,
            'report': report,
            'discrepancy_report': report,
            'records': records,
            'merged_pdf_url': url_for('download_file', filename=merged_pdf_name),
            'excel_url': url_for('download_file', filename=excel_name),
            'download_url': url_for('download_file', filename=excel_name),
            'convert_page_url': url_for('dashboard', type='land', merged_id=unique_prefix)
        }
        MERGED_LAND_CACHE[unique_prefix] = payload
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'error': f'เกิดข้อผิดพลาดในการรวมและจัดเรียงไฟล์: {str(e)}'}), 500

@app.route('/api/merged-land-result/<merged_id>')
@login_required
def get_merged_land_result(merged_id):
    user = database.get_user_by_id(session['user_id'])
    if not user_can_land(user):
        return jsonify({'success': False, 'error': 'บัญชีของคุณไม่มีสิทธิ์ใช้งานระบบแปลงข้อมูลที่ดิน'}), 403
    if merged_id in MERGED_LAND_CACHE:
        return jsonify(MERGED_LAND_CACHE[merged_id])
    # Fallback if server restarted: read from outputs folder
    safe_id = secure_filename(merged_id)
    merged_pdf_name = f"Merged_Sorted_Land_{safe_id}.pdf"
    merged_pdf_path = os.path.join(OUTPUT_FOLDER, merged_pdf_name)
    excel_name = f"Export_Land_Merged_{safe_id}.xlsx"
    excel_path = os.path.join(OUTPUT_FOLDER, excel_name)
    if not os.path.exists(merged_pdf_path):
        return jsonify({'success': False, 'error': 'ไม่พบข้อมูลไฟล์ที่รวมไว้ กรุณารวมไฟล์ใหม่อีกครั้ง'}), 404
    report = land_extractor.merge_and_sort_land_pdfs([merged_pdf_path], merged_pdf_path)
    records = land_extractor.extract_land_pdf_to_records([merged_pdf_path])
    if not os.path.exists(excel_path):
        excel_exporter.export_land_records_to_excel(records, excel_path)
    payload = {
        'success': True,
        'doc_type': 'land',
        'merged_id': safe_id,
        'page_count': report.get('total_output_pages', len(records)),
        'row_count': len(records),
        'report': report,
        'discrepancy_report': report,
        'records': records,
        'merged_pdf_url': url_for('download_file', filename=merged_pdf_name),
        'excel_url': url_for('download_file', filename=excel_name),
        'download_url': url_for('download_file', filename=excel_name)
    }
    MERGED_LAND_CACHE[safe_id] = payload
    return jsonify(payload)

@app.route('/history')
@login_required
def history():
    conversions = database.get_user_conversions(session['user_id'])
    return render_template('history.html', conversions=conversions)

CONVERSION_PROGRESS = {}

@app.route('/api/upload-progress/<job_id>')
def upload_progress(job_id):
    info = CONVERSION_PROGRESS.get(job_id, {
        'progress': 0,
        'message': 'กำลังเตรียมการประมวลผล...',
        'done': False
    })
    return jsonify(info)

@app.route('/api/upload', methods=['POST'])
@login_required
def upload_pdf():
    files = request.files.getlist('pdf_file')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'success': False, 'error': 'ไม่พบไฟล์ PDF'}), 400
        
    doc_type = request.form.get('doc_type', 'uncertified').strip()
    job_id = request.form.get('job_id', '').strip()
    
    if doc_type not in ['uncertified', 'certified', 'land']:
        doc_type = 'uncertified'

    user = database.get_user_by_id(session['user_id'])
    if doc_type == 'land' and not user_can_land(user):
        return jsonify({'success': False, 'error': 'บัญชีของคุณไม่มีสิทธิ์ใช้งานระบบแปลงไฟล์ข้อมูลที่ดิน กรุณาติดต่อผู้ดูแลระบบเพื่อขอเปิดสิทธิ์'}), 403
    if doc_type in ['uncertified', 'certified'] and not user_can_civil(user):
        return jsonify({'success': False, 'error': 'บัญชีของคุณไม่มีสิทธิ์ใช้งานระบบแปลงไฟล์ ท.ร.14/1 กรุณาติดต่อผู้ดูแลระบบเพื่อขอเปิดสิทธิ์'}), 403

    valid_files = [f for f in files if f and f.filename and f.filename.lower().endswith('.pdf')]
    if not valid_files:
        return jsonify({'success': False, 'error': 'ระบบรองรับเฉพาะไฟล์ .pdf เท่านั้น'}), 400
        
    orig_name = valid_files[0].filename if len(valid_files) == 1 else f"{valid_files[0].filename} (+รวม {len(valid_files)} ไฟล์)"
    type_label = "Land" if doc_type == 'land' else ("Certified" if doc_type == 'certified' else "Standard")
    unique_prefix = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6]

    saved_pdf_paths = []
    for idx, f in enumerate(valid_files):
        clean_orig = secure_filename(f.filename) or f'document_{idx+1}.pdf'
        s_name = f"{unique_prefix}_{idx+1}_{clean_orig}" if len(valid_files) > 1 else f"{unique_prefix}_{clean_orig}"
        p_path = os.path.join(UPLOAD_FOLDER, s_name)
        f.save(p_path)
        saved_pdf_paths.append(p_path)

    stored_pdf_name = os.path.basename(saved_pdf_paths[0])
    pdf_path = saved_pdf_paths[0]
    
    if job_id:
        CONVERSION_PROGRESS[job_id] = {
            'progress': 10,
            'message': 'บันทึกไฟล์เรียบร้อยแล้ว กำลังเริ่มการสกัดข้อมูล...',
            'done': False
        }
        
    gemini_key = (database.get_setting('gemini_api_key') or os.environ.get('GEMINI_API_KEY')) if doc_type != 'land' else None
    ai_lbl = " [Enterprise AI ⚡]" if gemini_key else ""

    def on_progress(current_page, total_pages):
        if job_id:
            pct = 15 + int((current_page / max(total_pages, 1)) * 70)
            CONVERSION_PROGRESS[job_id] = {
                'progress': pct,
                'message': f'กำลังอ่านและสกัดข้อมูลหน้าที่ {current_page}/{total_pages}{ai_lbl} ({pct}%)...',
                'done': False
            }
            
    try:
        excel_name = f"Export_{type_label}_{unique_prefix}.xlsx"
        excel_path = os.path.join(OUTPUT_FOLDER, excel_name)
        discrepancy_report = None
        merged_pdf_url = None

        if doc_type == 'land':
            # Single-pass merge, sort, and 25-column extraction with real-time progress
            merged_pdf_name = f"Merged_Sorted_Land_{unique_prefix}.pdf"
            merged_pdf_path = os.path.join(OUTPUT_FOLDER, merged_pdf_name)
            discrepancy_report, pages_meta = land_extractor.merge_and_sort_land_pdfs(
                saved_pdf_paths, merged_pdf_path, progress_callback=on_progress, return_pages_meta=True
            )
            merged_pdf_url = url_for('download_file', filename=merged_pdf_name)

            records = land_extractor.extract_land_pdf_to_records(saved_pdf_paths, _preparsed_pages=pages_meta)
            page_count = discrepancy_report.get('total_input_pages', len(records))
            row_count = len(records)

            if job_id:
                CONVERSION_PROGRESS[job_id] = {
                    'progress': 90,
                    'message': f'สกัดข้อมูลที่ดินสำเร็จ {row_count} แปลง กำลังสร้างไฟล์ Excel 25 คอลัมน์ (.xlsx)...',
                    'done': False
                }
            excel_exporter.export_land_records_to_excel(records, excel_path)
        else:
            # Extract data from PDF (multi-page supported)
            records = []
            for p_path in saved_pdf_paths:
                recs = extractor.extract_pdf_data(p_path, progress_callback=on_progress, doc_type=doc_type, api_key=gemini_key)
                for r in recs:
                    r['no'] = len(records) + 1
                    records.append(r)
            page_count = len(records)
            row_count = page_count
            
            if job_id:
                CONVERSION_PROGRESS[job_id] = {
                    'progress': 90,
                    'message': f'สกัดข้อมูลสำเร็จ {page_count} หน้า กำลังสร้างไฟล์ Excel (.xlsx)...',
                    'done': False
                }
                
            excel_exporter.export_records_to_excel(records, excel_path)
        
        # Log to Database
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if doc_type == 'land':
            type_prefix_thai = "ข้อมูลที่ดิน"
        elif doc_type == 'certified':
            type_prefix_thai = "แบบรับรอง"
        else:
            type_prefix_thai = "แบบไม่รับรอง"

        log_id = database.log_conversion(
            user_id=session['user_id'],
            original_filename=f"[{type_prefix_thai}] {orig_name}",
            stored_filename=stored_pdf_name,
            page_count=page_count,
            row_count=row_count,
            excel_filename=excel_name,
            status='success',
            ip_address=client_ip
        )
        
        # Save extracted records for fast view
        if doc_type != 'land':
            database.save_extracted_records(log_id, records)
        
        if job_id:
            CONVERSION_PROGRESS[job_id] = {
                'progress': 100,
                'message': f'แปลงไฟล์สำเร็จ {row_count} รายการ เรียบร้อย 100%!',
                'done': True
            }
            
        return jsonify({
            'success': True,
            'doc_type': doc_type,
            'page_count': page_count,
            'row_count': row_count,
            'records': records,
            'discrepancy_report': discrepancy_report,
            'merged_pdf_url': merged_pdf_url,
            'log_id': log_id,
            'download_url': url_for('download_file', filename=excel_name)
        })
        
    except Exception as e:
        if job_id:
            CONVERSION_PROGRESS[job_id] = {
                'progress': 100,
                'message': f'เกิดข้อผิดพลาด: {str(e)}',
                'error': str(e),
                'done': True
            }
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        database.log_conversion(
            user_id=session['user_id'],
            original_filename=orig_name,
            stored_filename=stored_pdf_name,
            page_count=0,
            row_count=0,
            excel_filename='',
            status='failed',
            ip_address=client_ip
        )
        return jsonify({'success': False, 'error': f'เกิดข้อผิดพลาดในการแปลงไฟล์: {str(e)}'}), 500

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    safe_name = secure_filename(filename)
    file_path = os.path.join(OUTPUT_FOLDER, safe_name)
    if not os.path.exists(file_path):
        abort(404)
    return send_from_directory(OUTPUT_FOLDER, safe_name, as_attachment=True)

# Admin Portal
@app.route('/admin')
@admin_required
def admin_dashboard():
    month = request.args.get('month', '').strip() or None
    stats = database.get_system_stats(month=month)
    available_months = database.get_available_months()
    users = database.get_all_users()
    recent_conversions = database.get_all_conversions(month=month)[:50]
    return render_template(
        'admin_dashboard.html', 
        stats=stats, 
        users=users, 
        recent_conversions=recent_conversions,
        available_months=available_months,
        selected_month=month or ''
    )

@app.route('/admin/conversions/delete/<int:log_id>', methods=['POST'])
@admin_required
def admin_delete_conversion(log_id):
    """Admin ลบประวัติการแปลงไฟล์รายการเดียว"""
    success, msg = database.delete_conversion_log(log_id)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/conversions/delete-batch', methods=['POST'])
@admin_required
def admin_delete_conversions_batch():
    """Admin ลบประวัติการแปลงไฟล์หลายรายการพร้อมกัน"""
    data = request.get_json(silent=True) or {}
    log_ids = data.get('log_ids', [])
    if not log_ids:
        return jsonify({'success': False, 'message': 'กรุณาเลือกรายการที่ต้องการลบอย่างน้อย 1 รายการ'}), 400
    success, msg = database.delete_conversion_logs_batch(log_ids)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/users')
@admin_required
def admin_users():
    users = database.get_all_users()
    pending_users = database.get_pending_users()
    return render_template('admin_users.html', users=users, pending_users=pending_users)

@app.route('/admin/users/approve/<int:user_id>', methods=['POST'])
@admin_required
def admin_approve_user(user_id):
    data = request.get_json(silent=True) or {}
    allow_civil = data.get('allow_civil', request.form.get('allow_civil'))
    allow_land = data.get('allow_land', request.form.get('allow_land'))
    ac = (int(allow_civil) == 1) if allow_civil is not None else None
    al = (int(allow_land) == 1) if allow_land is not None else None
    success, msg = database.approve_user(user_id, allow_civil=ac, allow_land=al)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/users/reject/<int:user_id>', methods=['POST'])
@admin_required
def admin_reject_user(user_id):
    success, msg = database.reject_user(user_id)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/users/toggle/<int:user_id>', methods=['POST'])
@admin_required
def admin_toggle_user(user_id):
    success, message = database.toggle_user_status(user_id)
    return jsonify({'success': success, 'message': message})

@app.route('/admin/users/permissions/<int:user_id>', methods=['POST'])
@admin_required
def admin_update_permissions(user_id):
    data = request.get_json(silent=True) or {}
    allow_civil = data.get('allow_civil')
    allow_land = data.get('allow_land')
    if allow_civil is None:
        allow_civil = request.form.get('allow_civil', '0') == '1'
    if allow_land is None:
        allow_land = request.form.get('allow_land', '0') == '1'
    success, msg = database.update_user_permissions(user_id, bool(int(allow_civil)), bool(int(allow_land)))
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/users/create', methods=['POST'])
@admin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', 'user').strip()
    plan_type = request.form.get('plan_type', 'lifetime').strip()
    phone = request.form.get('phone', '').strip()
    allow_civil = 1 if request.form.get('allow_civil') == '1' else 0
    allow_land = 1 if request.form.get('allow_land') == '1' else 0
    
    if not username or not password or not full_name:
        flash('กรุณากรอกข้อมูลให้ครบทุกช่อง', 'danger')
        return redirect(url_for('admin_users'))
        
    success, msg = database.create_user(
        username, password, full_name, role, plan_type, phone,
        allow_civil=allow_civil, allow_land=allow_land
    )
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/reset-password/<int:user_id>', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    new_password = request.form.get('new_password', '').strip()
    if not new_password or len(new_password) < 4:
        return jsonify({'success': False, 'message': 'รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 4 ตัวอักษร'}), 400
        
    success, msg = database.update_user_password(user_id, new_password)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    success, msg = database.delete_user(user_id)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/users/extend/<int:user_id>', methods=['POST'])
@admin_required
def admin_extend_user(user_id):
    days = request.form.get('days')
    plan_type = request.form.get('plan_type')
    days_val = int(days) if days and days.isdigit() else (None if days == 'lifetime' else 30)
    success, msg = database.extend_subscription(user_id, days_val, plan_type)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/reports')
@admin_required
def admin_reports():
    conversions = database.get_all_conversions()
    return render_template('admin_reports.html', conversions=conversions)

# Device Management Endpoints
@app.route('/api/device-info')
def api_device_info():
    """ส่งคืนข้อมูล MAC Address และชื่อเครื่องสำหรับ Client / Desktop UI"""
    info = hardware_id.get_machine_info()
    return jsonify(info)

@app.route('/device-limit')
def device_limit():
    mac = request.args.get('mac', hardware_id.get_primary_mac_address())
    username = request.args.get('username', '')
    error = request.args.get('error', 'บัญชีนี้ใช้งานครบโควต้า 3 เครื่องแล้ว')
    return render_template('device_limit.html', mac=mac, username=username, error=error)

@app.route('/admin/users/<int:user_id>/devices')
@admin_required
def admin_get_user_devices(user_id):
    """ดึงรายการเครื่องที่ผูกไว้ทั้งหมดของ User"""
    devices = database.get_user_devices(user_id)
    user = database.get_user_by_id(user_id)
    return jsonify({
        'success': True,
        'user': user,
        'devices': devices,
        'count': len(devices),
        'max_devices': database.MAX_DEVICES_PER_USER
    })

@app.route('/admin/devices/revoke/<int:device_id>', methods=['POST'])
@admin_required
def admin_revoke_device(device_id):
    """Admin ปลดล็อก/ถอดสิทธิ์เครื่องนี้ คืนโควต้าสิทธิ์ให้ User"""
    success, msg = database.revoke_user_device(device_id)
    return jsonify({'success': success, 'message': msg})

@app.route('/admin/devices/reset/<int:user_id>', methods=['POST'])
@admin_required
def admin_reset_devices(user_id):
    """Admin รีเซ็ตเครื่องทั้งหมดของ User (คืนโควต้าครบ 3 สิทธิ์)"""
    success, msg = database.reset_all_user_devices(user_id)
    return jsonify({'success': success, 'message': msg})


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        gemini_key = request.form.get('gemini_api_key', '').strip()
        database.set_setting('gemini_api_key', gemini_key)
        flash('บันทึกการตั้งค่า Google Gemini API Key เรียบร้อยแล้ว', 'success')
        return redirect(url_for('admin_settings'))
        
    current_key = database.get_setting('gemini_api_key', '')
    masked_key = ''
    if current_key:
        if len(current_key) > 8:
            masked_key = current_key[:6] + '...' + current_key[-4:]
        else:
            masked_key = '********'
            
    return render_template('admin_settings.html', current_key=current_key, masked_key=masked_key)

@app.route('/api/test-gemini', methods=['POST'])
@admin_required
def api_test_gemini():
    data = request.get_json(silent=True) or {}
    key = data.get('api_key', '').strip() or database.get_setting('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
    if not key:
        return jsonify({'success': False, 'message': 'กรุณาระบุ Gemini API Key ก่อนทำการทดสอบ'}), 400
    try:
        import gemini_engine
        ok, msg = gemini_engine.test_gemini_connection(key)
        return jsonify({'success': ok, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': f'เกิดข้อผิดพลาด: {str(e)}'}), 500

if __name__ == '__main__':
    print("=" * 60)
    print("ระบบแปลงไฟล์ PDF เป็น Excel (Web Application)")
    print("URL: http://127.0.0.1:5000")
    print("บัญชีเริ่มต้น:")
    print("  - Admin: username='admin', password='2512044'")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)
