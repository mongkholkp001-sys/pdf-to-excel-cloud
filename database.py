import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.environ.get('DB_DIR', BASE_DIR)
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, 'system.db')

PLANS = {
    'rental_1m': {'name': 'เช่า 1 เดือน (3,000 บาท)', 'price': 3000, 'days': 30},
    'rental_3m': {'name': 'เช่า 3 เดือน (8,700 บาท)', 'price': 8700, 'days': 90},
    'rental_6m': {'name': 'เช่า 6 เดือน (16,000 บาท)', 'price': 16000, 'days': 180},
    'rental_1y': {'name': 'เช่า 1 ปี (25,000 บาท)', 'price': 25000, 'days': 365},
    'lifetime':  {'name': 'ซื้อขาด (สิทธิ์ตลอดชีพ)', 'price': 0, 'days': None},
}

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA busy_timeout=10000;')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Table: users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            phone TEXT,
            plan_type TEXT DEFAULT 'lifetime',
            plan_name TEXT DEFAULT 'ซื้อขาด',
            role TEXT NOT NULL DEFAULT 'user', -- 'admin' or 'user'
            status TEXT NOT NULL DEFAULT 'active', -- 'active', 'pending', 'suspended', 'expired'
            expires_at DATETIME,
            approved_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        )
    ''')
    
    # Migration: Add columns if they do not exist
    cursor.execute("PRAGMA table_info(users)")
    cols = [col[1] for col in cursor.fetchall()]
    if 'phone' not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    if 'plan_type' not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN plan_type TEXT DEFAULT 'lifetime'")
    if 'plan_name' not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN plan_name TEXT DEFAULT 'ซื้อขาด'")
    if 'expires_at' not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN expires_at DATETIME")
    if 'approved_at' not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN approved_at DATETIME")
        
    # Table: conversion_logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversion_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 0,
            row_count INTEGER NOT NULL DEFAULT 0,
            excel_filename TEXT,
            status TEXT NOT NULL DEFAULT 'success',
            ip_address TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    # Table: extracted_records
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS extracted_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversion_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            cid TEXT,
            hid TEXT,
            title TEXT,
            fname TEXT,
            lname TEXT,
            full_name TEXT,
            gender TEXT,
            dob TEXT,
            age TEXT,
            status TEXT,
            mother_name TEXT,
            mother_cid TEXT,
            father_name TEXT,
            father_cid TEXT,
            address TEXT,
            subdistrict TEXT,
            district TEXT,
            province TEXT,
            reg_office TEXT,
            move_date TEXT,
            person_status TEXT,
            issue_office TEXT,
            print_date TEXT,
            FOREIGN KEY (conversion_id) REFERENCES conversion_logs (id) ON DELETE CASCADE
        )
    ''')
    
    # Table: user_devices (ตรวจจับและบันทึก MAC Address สูงสุด 3 เครื่องต่อบัญชี)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mac_address TEXT NOT NULL,
            device_name TEXT,
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_devices ON user_devices(user_id, mac_address, is_active)')
    
    # Ensure Admin account exists with password: '2512044' (Atomic upsert safe for multi-worker Gunicorn)
    admin_pass = generate_password_hash('2512044')
    try:
        cursor.execute('''
            INSERT INTO users (username, password_hash, full_name, role, status, plan_type, plan_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash
        ''', ('admin', admin_pass, 'ผู้ดูแลระบบ (Admin)', 'admin', 'active', 'lifetime', 'ผู้ดูแลระบบ', datetime.now()))
    except sqlite3.IntegrityError:
        pass
    except Exception as e:
        print(f"Admin setup warning: {e}")
        
    conn.commit()
    conn.close()

# User Management Functions
def authenticate_user(username, password):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return None, "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    
    if not check_password_hash(user['password_hash'], password):
        conn.close()
        return None, "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
        
    if user['status'] == 'pending':
        conn.close()
        return None, "บัญชีของคุณอยู่ระหว่างรอผู้ดูแลระบบตรวจสอบและอนุมัติการใช้งาน"
        
    if user['status'] == 'suspended':
        conn.close()
        return None, "บัญชีของคุณถูกระงับการใช้งาน กรุณาติดต่อผู้ดูแลระบบ"
        
    # Check expiration date if any
    if user['expires_at']:
        try:
            exp_date = datetime.fromisoformat(user['expires_at'].replace('Z', ''))
            if datetime.now() > exp_date:
                cursor.execute("UPDATE users SET status = 'expired' WHERE id = ?", (user['id'],))
                conn.commit()
                conn.close()
                return None, f"แพ็กเกจการใช้งานของคุณหมดอายุแล้วเมื่อวันที่ {exp_date.strftime('%d/%m/%Y')} กรุณาติดต่อผู้ดูแลระบบเพื่อต่ออายุ"
        except Exception:
            pass
            
    conn.close()
    return dict(user), None

def register_user(username, password, full_name, phone, plan_type):
    conn = get_db()
    cursor = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        plan_info = PLANS.get(plan_type, PLANS['rental_1m'])
        cursor.execute('''
            INSERT INTO users (username, password_hash, full_name, phone, plan_type, plan_name, role, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'user', 'pending', ?)
        ''', (username, pw_hash, full_name, phone, plan_type, plan_info['name'], datetime.now()))
        conn.commit()
        return True, "สมัครสมาชิกเรียบร้อยแล้ว บัญชีของคุณอยู่ระหว่างรอผู้ดูแลระบบอนุมัติการใช้งาน"
    except sqlite3.IntegrityError:
        return False, "ชื่อผู้ใช้นี้มีอยู่ในระบบแล้ว กรุณาเลือกชื่อผู้ใช้อื่น"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def approve_user(user_id, custom_days=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        return False, "ไม่พบผู้ใช้"
        
    plan_key = user['plan_type'] or 'rental_1m'
    plan_info = PLANS.get(plan_key, PLANS['rental_1m'])
    
    days = custom_days if custom_days is not None else plan_info['days']
    expires_at = None
    if days is not None:
        expires_at = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        
    cursor.execute('''
        UPDATE users 
        SET status = 'active', approved_at = ?, expires_at = ?
        WHERE id = ?
    ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), expires_at, user_id))
    conn.commit()
    conn.close()
    return True, f"อนุมัติผู้ใช้ '{user['username']}' เรียบร้อยแล้ว (แพ็กเกจ: {plan_info['name']})"

def reject_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE id = ? AND status = "pending"', (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return (True, "ปฏิเสธคำขอสมัครเรียบร้อยแล้ว") if deleted > 0 else (False, "ไม่พบคำขอ")

def extend_subscription(user_id, days=30, plan_type=None):
    """Admin ต่ออายุการใช้งานสัญญาเช่าให้ User"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        return False, "ไม่พบข้อมูลผู้ใช้งาน"
        
    current_exp = None
    if user['expires_at']:
        try:
            current_exp = datetime.fromisoformat(user['expires_at'].replace('Z', ''))
        except Exception:
            pass
            
    base_date = max(datetime.now(), current_exp) if current_exp else datetime.now()
    new_exp = (base_date + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S') if days is not None else None
    
    plan_name = user['plan_name']
    if plan_type and plan_type in PLANS:
        plan_name = PLANS[plan_type]['name']
        
    cursor.execute('''
        UPDATE users 
        SET status = 'active', expires_at = ?, plan_type = COALESCE(?, plan_type), plan_name = COALESCE(?, plan_name)
        WHERE id = ?
    ''', (new_exp, plan_type, plan_name, user_id))
    conn.commit()
    conn.close()
    exp_display = new_exp if new_exp else "สิทธิ์ตลอดชีพ"
    return True, f"ต่ออายุการใช้งานสำเร็จ (วันหมดอายุใหม่: {exp_display})"


def get_user_by_id(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def get_all_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.*, 
               COUNT(c.id) AS total_conversions,
               COALESCE(SUM(c.page_count), 0) AS total_pages,
               (SELECT COUNT(*) FROM user_devices d WHERE d.user_id = u.id AND d.is_active = 1) AS device_count
        FROM users u
        LEFT JOIN conversion_logs c ON u.id = c.user_id
        GROUP BY u.id
        ORDER BY 
            CASE WHEN u.status = 'pending' THEN 0 ELSE 1 END,
            u.role DESC, u.id ASC
    ''')
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users

def get_pending_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE status = "pending" ORDER BY created_at DESC')
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users

def create_user(username, password, full_name, role='user', plan_type='lifetime', phone=''):
    conn = get_db()
    cursor = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        plan_info = PLANS.get(plan_type, PLANS['lifetime'])
        days = plan_info['days']
        expires_at = None
        if days is not None:
            expires_at = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
            
        cursor.execute('''
            INSERT INTO users (username, password_hash, full_name, phone, plan_type, plan_name, role, status, expires_at, created_at, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        ''', (username, pw_hash, full_name, phone, plan_type, plan_info['name'], role, expires_at, datetime.now(), datetime.now()))
        conn.commit()
        return True, "สร้างผู้ใช้งานสำเร็จ"
    except sqlite3.IntegrityError:
        return False, "ชื่อผู้ใช้นี้มีอยู่ในระบบแล้ว"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def toggle_user_status(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role, status FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        return False, "ไม่พบผู้ใช้"
    if user['role'] == 'admin':
        conn.close()
        return False, "ไม่สามารถระงับสิทธิ์บัญชี Admin หลักได้"
    
    new_status = 'suspended' if user['status'] == 'active' else 'active'
    cursor.execute('UPDATE users SET status = ? WHERE id = ?', (new_status, user_id))
    conn.commit()
    conn.close()
    status_th = "ระงับการใช้งานแล้ว" if new_status == 'suspended' else "เปิดใช้งานแล้ว"
    return True, f"เปลี่ยนสถานะเป็น {status_th}"

def update_user_password(user_id, new_password):
    conn = get_db()
    cursor = conn.cursor()
    pw_hash = generate_password_hash(new_password)
    cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (pw_hash, user_id))
    conn.commit()
    conn.close()
    return True, "เปลี่ยนรหัสผ่านสำเร็จ"

def change_password(user_id, current_password, new_password):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT password_hash FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "ไม่พบผู้ใช้งาน"
    if not check_password_hash(row['password_hash'], current_password):
        conn.close()
        return False, "รหัสผ่านเดิมไม่ถูกต้อง"
    pw_hash = generate_password_hash(new_password)
    cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (pw_hash, user_id))
    conn.commit()
    conn.close()
    return True, "เปลี่ยนรหัสผ่านสำเร็จเรียบร้อยแล้ว"

def delete_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        return False, "ไม่พบผู้ใช้"
    if user['role'] == 'admin':
        conn.close()
        return False, "ไม่สามารถลบบัญชี Admin ได้"
    
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    return True, "ลบผู้ใช้สำเร็จ"

def update_last_login(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET last_login = ? WHERE id = ?', (datetime.now(), user_id))
    conn.commit()
    conn.close()

# Conversion Logs Functions
def log_conversion(user_id, original_filename, stored_filename, page_count, row_count, excel_filename, status='success', ip_address=''):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversion_logs 
        (user_id, original_filename, stored_filename, page_count, row_count, excel_filename, status, ip_address, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, original_filename, stored_filename, page_count, row_count, excel_filename, status, ip_address, datetime.now()))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id

def save_extracted_records(conversion_id, records):
    conn = get_db()
    cursor = conn.cursor()
    for r in records:
        cursor.execute('''
            INSERT INTO extracted_records
            (conversion_id, page_number, cid, hid, title, fname, lname, full_name, gender, dob, age, status,
             mother_name, mother_cid, father_name, father_cid, address, subdistrict, district, province,
             reg_office, move_date, person_status, issue_office, print_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            conversion_id, r.get('no', 1), r.get('cid', ''), r.get('hid', ''),
            r.get('title', ''), r.get('fname', ''), r.get('lname', ''),
            f"{r.get('title','')}{r.get('fname','')} {r.get('lname','')}".strip(),
            r.get('gender', ''), r.get('dob', ''), str(r.get('age', '')), r.get('status', ''),
            r.get('mother_name', ''), r.get('mother_cid', ''),
            r.get('father_name', ''), r.get('father_cid', ''),
            r.get('address', ''), r.get('subdistrict', ''), r.get('district', ''), r.get('province', ''),
            r.get('reg_office', ''), r.get('move_date', ''), r.get('person_status', ''),
            r.get('issue_office', ''), r.get('print_date', '')
        ))
    conn.commit()
    conn.close()

def get_records_by_conversion_id(conversion_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM extracted_records 
        WHERE conversion_id = ? 
        ORDER BY page_number ASC
    ''', (conversion_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_user_conversions(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM conversion_logs 
        WHERE user_id = ? 
        ORDER BY created_at DESC
    ''', (user_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_available_months():
    """ดึงรายการเดือนทั้งหมดที่มีการแปลงไฟล์ในระบบ"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT strftime('%Y-%m', created_at) as ym 
        FROM conversion_logs 
        WHERE created_at IS NOT NULL 
        ORDER BY ym DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    thai_months = [
        "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
        "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
    ]
    
    months = []
    now = datetime.now()
    curr_ym = now.strftime('%Y-%m')
    found_curr = False
    
    for r in rows:
        ym = r['ym']
        if not ym or len(ym) != 7:
            continue
        if ym == curr_ym:
            found_curr = True
        try:
            year, month = map(int, ym.split('-'))
            th_year = year + 543
            th_month = thai_months[month] if 1 <= month <= 12 else ym
            months.append({'value': ym, 'label': f"{th_month} {th_year}"})
        except Exception:
            months.append({'value': ym, 'label': ym})
            
    if not found_curr:
        th_year = now.year + 543
        th_month = thai_months[now.month]
        months.insert(0, {'value': curr_ym, 'label': f"{th_month} {th_year}"})
        
    return months

def get_all_conversions(month=None):
    conn = get_db()
    cursor = conn.cursor()
    if month:
        cursor.execute('''
            SELECT c.*, u.username, u.full_name 
            FROM conversion_logs c
            JOIN users u ON c.user_id = u.id
            WHERE strftime('%Y-%m', c.created_at) = ?
            ORDER BY c.created_at DESC
        ''', (month,))
    else:
        cursor.execute('''
            SELECT c.*, u.username, u.full_name 
            FROM conversion_logs c
            JOIN users u ON c.user_id = u.id
            ORDER BY c.created_at DESC
        ''')
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_system_stats(month=None):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active'")
    active_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'suspended'")
    suspended_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'pending'")
    pending_users = cursor.fetchone()[0]
    
    if month:
        cursor.execute('''
            SELECT COUNT(*), COALESCE(SUM(page_count), 0), COALESCE(SUM(row_count), 0) 
            FROM conversion_logs
            WHERE strftime('%Y-%m', created_at) = ?
        ''', (month,))
    else:
        cursor.execute('SELECT COUNT(*), COALESCE(SUM(page_count), 0), COALESCE(SUM(row_count), 0) FROM conversion_logs')
        
    conv_count, total_pages, total_rows = cursor.fetchone()
    
    conn.close()
    return {
        'total_users': total_users,
        'active_users': active_users,
        'suspended_users': suspended_users,
        'pending_users': pending_users,
        'total_conversions': conv_count,
        'total_pages': total_pages,
        'total_rows': total_rows
    }

def delete_conversion_log(log_id):
    """ลบประวัติการแปลงไฟล์ 1 รายการ พร้อมไฟล์จริง"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT stored_filename, excel_filename FROM conversion_logs WHERE id = ?', (log_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "ไม่พบข้อมูลประวัติการแปลงไฟล์นี้"
        
    stored_pdf = row['stored_filename']
    excel_file = row['excel_filename']
    
    cursor.execute('DELETE FROM extracted_records WHERE conversion_id = ?', (log_id,))
    cursor.execute('DELETE FROM conversion_logs WHERE id = ?', (log_id,))
    conn.commit()
    conn.close()
    
    # ลบไฟล์ออกจากโฟลเดอร์ uploads และ outputs
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if stored_pdf:
            p_pdf = os.path.join(base_dir, 'uploads', stored_pdf)
            if os.path.exists(p_pdf):
                os.remove(p_pdf)
        if excel_file:
            p_xls = os.path.join(base_dir, 'outputs', excel_file)
            if os.path.exists(p_xls):
                os.remove(p_xls)
    except Exception:
        pass
        
    return True, "ลบประวัติการแปลงไฟล์สำเร็จ"

def delete_conversion_logs_batch(log_ids):
    """ลบประวัติการแปลงไฟล์แบบหลายรายการพร้อมกัน"""
    if not log_ids:
        return False, "ไม่ได้เลือกรายการที่ต้องการลบ"
    
    success_count = 0
    for lid in log_ids:
        try:
            ok, _ = delete_conversion_log(int(lid))
            if ok:
                success_count += 1
        except Exception:
            pass
            
    return True, f"ลบประวัติการแปลงไฟล์สำเร็จ {success_count} รายการ"

# ==========================================
# Device Management (ตรวจจับ MAC สูงสุด 3 เครื่อง)
# ==========================================
MAX_DEVICES_PER_USER = 3

def normalize_mac(mac):
    """Normalize MAC address string to standard format (XX:XX:XX:XX:XX:XX)"""
    if not mac:
        return ""
    import re
    cleaned = re.sub(r'[^0-9A-Fa-f]', '', mac.strip()).upper()
    if len(cleaned) == 12:
        return ':'.join(cleaned[i:i+2] for i in range(0, 12, 2))
    return mac.strip().upper().replace('-', ':')

def get_user_devices(user_id):
    """ดึงรายการเครื่องทั้งหมดที่ผูกไว้กับ User (เฉพาะ is_active = 1)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM user_devices 
        WHERE user_id = ? AND is_active = 1 
        ORDER BY registered_at ASC
    ''', (user_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def register_user_device(user_id, mac_address, device_name=None, max_devices=MAX_DEVICES_PER_USER):
    """
    ลงทะเบียน MAC Address ให้กับ User
    - ถ้าเป็นเครื่องเดิม: อัปเดตวันเวลา last_seen
    - ถ้าเป็นเครื่องใหม่ และยังไม่เกิน max_devices (3 เครื่อง): เพิ่มเครื่องใหม่
    - ถ้าเกิน max_devices: ปฏิเสธการลงทะเบียน
    """
    norm_mac = normalize_mac(mac_address)
    if not norm_mac:
        return False, "ไม่พบค่า MAC Address ที่ถูกต้อง", None
        
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # ตรวจสอบว่า MAC นี้เคยลงทะเบียนไว้และยัง active อยู่หรือไม่
    cursor.execute('''
        SELECT * FROM user_devices 
        WHERE user_id = ? AND mac_address = ? AND is_active = 1
    ''', (user_id, norm_mac))
    existing = cursor.fetchone()
    
    if existing:
        dev_id = existing['id']
        cursor.execute('''
            UPDATE user_devices 
            SET last_seen = ?, device_name = COALESCE(?, device_name)
            WHERE id = ?
        ''', (now_str, device_name, dev_id))
        conn.commit()
        conn.close()
        return True, "เครื่องนี้ได้รับการเปิดใช้งานแล้ว", dev_id
        
    # ตรวจสอบจำนวนเครื่องที่ใช้งานอยู่ปัจจุบัน
    cursor.execute('''
        SELECT COUNT(*) FROM user_devices 
        WHERE user_id = ? AND is_active = 1
    ''', (user_id,))
    active_count = cursor.fetchone()[0]
    
    if active_count >= max_devices:
        conn.close()
        return False, f"บัญชีนี้ใช้งานครบโควต้า {max_devices} เครื่องแล้ว ไม่สามารถผูกเครื่องใหม่ได้", None
        
    # เพิ่มเครื่องใหม่
    name = device_name or f"เครื่องที่ {active_count + 1}"
    cursor.execute('''
        INSERT INTO user_devices (user_id, mac_address, device_name, registered_at, last_seen, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
    ''', (user_id, norm_mac, name, now_str, now_str))
    dev_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return True, f"ลงทะเบียนเครื่องสำเร็จ (ใช้งาน {active_count + 1}/{max_devices} เครื่อง)", dev_id

def verify_user_device(user_id, mac_candidates):
    """
    ตรวจสอบว่า MAC ใด ๆ ใน mac_candidates ได้รับอนุญาตสำหรับ user_id หรือไม่
    คืนค่า (True, device_record) หรือ (False, None)
    """
    if isinstance(mac_candidates, str):
        mac_candidates = [mac_candidates]
        
    normalized_list = [normalize_mac(m) for m in mac_candidates if m]
    if not normalized_list:
        return False, None
        
    devices = get_user_devices(user_id)
    reg_macs = {d['mac_address']: d for d in devices}
    
    for candidate in normalized_list:
        if candidate in reg_macs:
            # อัปเดต last_seen
            try:
                conn = get_db()
                conn.cursor().execute(
                    "UPDATE user_devices SET last_seen = ? WHERE id = ?",
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), reg_macs[candidate]['id'])
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
            return True, reg_macs[candidate]
            
    return False, None

def revoke_user_device(device_id):
    """ถอดสิทธิ์เครื่อง (ตั้ง is_active = 0) เพื่อคืนโควต้าสล็อตเครื่องให้ User"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE user_devices SET is_active = 0 WHERE id = ?', (device_id,))
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return (True, "ถอดสิทธิ์เครื่องเรียบร้อยแล้ว คืนโควต้าสิทธิ์การติดตั้งแล้ว") if updated > 0 else (False, "ไม่พบข้อมูลอุปกรณ์")

def reset_all_user_devices(user_id):
    """รีเซ็ตสิทธิ์อุปกรณ์ทั้งหมดของ User (คืนโควต้าครบ 3 เครื่อง)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE user_devices SET is_active = 0 WHERE user_id = ?', (user_id,))
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return True, f"รีเซ็ตสิทธิ์อุปกรณ์ทั้งหมด {updated} รายการเรียบร้อยแล้ว"


# System Settings Functions
def get_setting(key, default=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key, value):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO system_settings (key, value, updated_at) 
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    conn.close()
