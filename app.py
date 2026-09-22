import os
import uuid
from functools import wraps
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory, abort
)
from werkzeug.utils import secure_filename

import database
import extractor
import excel_exporter
import hardware_id

app = Flask(__name__)
app.secret_key = os.urandom(24)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'outputs')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Initialize Database safely
try:
    database.init_db()
except Exception as e:
    print(f"[Database Init] {e}")

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

# Context Processor for Templates
@app.context_processor
def inject_user():
    user = None
    pending_count = 0
    if 'user_id' in session:
        user = database.get_user_by_id(session['user_id'])
        if session.get('role') == 'admin':
            try:
                pending_count = len(database.get_pending_users())
            except Exception:
                pending_count = 0
    gemini_key = database.get_setting('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
    gemini_active = bool(gemini_key)
    return dict(current_user=user, pending_count=pending_count, now=datetime.now(), gemini_active=gemini_active)

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
        
        if not username or not password or not full_name:
            flash('กรุณากรอกข้อมูลให้ครบถ้วนในช่องที่มีเครื่องหมาย *', 'danger')
            return render_template('register.html')
            
        if password != confirm_password:
            flash('รหัสผ่านและยืนยันรหัสผ่านไม่ตรงกัน', 'warning')
            return render_template('register.html')
            
        if len(password) < 4:
            flash('รหัสผ่านต้องมีความยาวอย่างน้อย 4 ตัวอักษร', 'warning')
            return render_template('register.html')
            
        success, msg = database.register_user(username, password, full_name, phone, plan_type)
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
    doc_type = request.args.get('type', 'uncertified').strip()
    if doc_type not in ['uncertified', 'certified']:
        doc_type = 'uncertified'
    return render_template('dashboard.html', active_doc_type=doc_type)

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
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'error': 'ไม่พบไฟล์ PDF'}), 400
        
    file = request.files['pdf_file']
    doc_type = request.form.get('doc_type', 'uncertified').strip()
    job_id = request.form.get('job_id', '').strip()
    
    if doc_type not in ['uncertified', 'certified']:
        doc_type = 'uncertified'

    if file.filename == '':
        return jsonify({'success': False, 'error': 'กรุณาเลือกไฟล์ PDF'}), 400
        
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'success': False, 'error': 'ระบบรองรับเฉพาะไฟล์ .pdf เท่านั้น'}), 400
        
    orig_name = file.filename
    clean_orig_name = secure_filename(orig_name) or 'document.pdf'
    type_label = "Certified" if doc_type == 'certified' else "Standard"
    unique_prefix = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6]
    stored_pdf_name = f"{unique_prefix}_{clean_orig_name}"
    pdf_path = os.path.join(UPLOAD_FOLDER, stored_pdf_name)
    file.save(pdf_path)
    
    if job_id:
        CONVERSION_PROGRESS[job_id] = {
            'progress': 10,
            'message': 'บันทึกไฟล์เรียบร้อยแล้ว กำลังเริ่มการสกัดข้อมูล...',
            'done': False
        }
        
    def on_progress(current_page, total_pages):
        if job_id:
            pct = 15 + int((current_page / max(total_pages, 1)) * 70)
            ai_lbl = " [Enterprise AI ⚡]" if (database.get_setting('gemini_api_key') or os.environ.get('GEMINI_API_KEY')) else ""
            CONVERSION_PROGRESS[job_id] = {
                'progress': pct,
                'message': f'กำลังอ่านและสกัดข้อมูลหน้าที่ {current_page}/{total_pages}{ai_lbl} ({pct}%)...',
                'done': False
            }
            
    try:
        # Extract data from PDF (multi-page supported)
        gemini_key = database.get_setting('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
        records = extractor.extract_pdf_data(pdf_path, progress_callback=on_progress, doc_type=doc_type, api_key=gemini_key)
        page_count = len(records)
        row_count = page_count
        
        if job_id:
            CONVERSION_PROGRESS[job_id] = {
                'progress': 90,
                'message': f'สกัดข้อมูลสำเร็จ {page_count} หน้า กำลังสร้างไฟล์ Excel (.xlsx)...',
                'done': False
            }
            
        # Generate Excel
        excel_name = f"Export_{type_label}_{unique_prefix}.xlsx"
        excel_path = os.path.join(OUTPUT_FOLDER, excel_name)
        excel_exporter.export_records_to_excel(records, excel_path)
        
        # Log to Database
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        type_prefix_thai = "แบบรับรอง" if doc_type == 'certified' else "แบบไม่รับรอง"
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
        database.save_extracted_records(log_id, records)
        
        if job_id:
            CONVERSION_PROGRESS[job_id] = {
                'progress': 100,
                'message': f'แปลงไฟล์สำเร็จ {page_count} หน้า เรียบร้อย 100%!',
                'done': True
            }
            
        return jsonify({
            'success': True,
            'doc_type': doc_type,
            'page_count': page_count,
            'row_count': row_count,
            'records': records,
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

@app.route('/download-server-package')
def download_server_package():
    desktop_dir = os.path.join(os.environ.get('USERPROFILE', 'C:\\Users\\comk25'), 'Desktop')
    zip_path = os.path.join(desktop_dir, 'PDF_to_Excel_Server_System.zip')
    if not os.path.exists(zip_path):
        abort(404)
    return send_from_directory(desktop_dir, 'PDF_to_Excel_Server_System.zip', as_attachment=True)

@app.route('/download-tha-traineddata')
def download_tha_traineddata():
    for d in [os.path.join(BASE_DIR, 'tessdata'), BASE_DIR]:
        p = os.path.join(d, 'tha.traineddata')
        if os.path.exists(p):
            return send_from_directory(d, 'tha.traineddata', as_attachment=True)
    abort(404)

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
    success, msg = database.approve_user(user_id)
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

@app.route('/admin/users/create', methods=['POST'])
@admin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', 'user').strip()
    plan_type = request.form.get('plan_type', 'lifetime').strip()
    phone = request.form.get('phone', '').strip()
    
    if not username or not password or not full_name:
        flash('กรุณากรอกข้อมูลให้ครบทุกช่อง', 'danger')
        return redirect(url_for('admin_users'))
        
    success, msg = database.create_user(username, password, full_name, role, plan_type, phone)
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
        
    current_key = database.get_setting('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', '')
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
        import importlib
        importlib.reload(gemini_engine)
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
