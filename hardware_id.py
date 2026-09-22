"""
hardware_id.py - ตรวจจับและดึงค่า MAC Address ของเครื่องคอมพิวเตอร์อย่างแม่นยำ
รองรับ Windows และ Cross-platform พร้อม Fallback
"""

import os
import re
import socket
import subprocess
import uuid
import platform

def get_all_mac_addresses():
    """
    ดึงค่า MAC Address ทั้งหมดของเครื่องที่มีในระบบ (ทั้ง Wi-Fi, Ethernet, Virtual)
    คืนค่าเป็น list ของ MAC Address รูปแบบ XX:XX:XX:XX:XX:XX (พิมพ์ใหญ่)
    """
    macs = set()

    # วิธีที่ 1: ตรวจสอบผ่านคำสั่ง 'getmac' บน Windows
    if platform.system() == "Windows":
        try:
            output = subprocess.check_output(
                ['getmac', '/fo', 'csv', '/nh'],
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            ).decode('utf-8', errors='ignore')
            
            for line in output.strip().splitlines():
                parts = [p.strip('"') for p in line.split(',')]
                if parts:
                    mac_candidate = parts[0].strip().upper().replace('-', ':')
                    if re.match(r'^([0-9A-F]{2}[:-]){5}([0-9A-F]{2})$', mac_candidate):
                        if mac_candidate != "00:00:00:00:00:00":
                            macs.add(mac_candidate)
        except Exception:
            pass

        # วิธีที่ 2: ตรวจสอบผ่าน PowerShell Get-NetAdapter (Physical MAC เป็นหลัก)
        try:
            cmd = "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -or $_.Status -eq 'Disconnected' } | Select-Object -ExpandProperty MacAddress"
            ps_out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", cmd],
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            ).decode('utf-8', errors='ignore')
            for line in ps_out.strip().splitlines():
                m = line.strip().upper().replace('-', ':')
                if re.match(r'^([0-9A-F]{2}[:-]){5}([0-9A-F]{2})$', m) and m != "00:00:00:00:00:00":
                    macs.add(m)
        except Exception:
            pass

    # วิธีที่ 3: ตรวจสอบผ่าน uuid.getnode() (มาตรฐาน Python)
    try:
        node = uuid.getnode()
        mac = ':'.join(['{:02X}'.format((node >> ele) & 0xff) for ele in range(0, 8 * 6, 8)][::-1])
        if mac != "00:00:00:00:00:00":
            macs.add(mac)
    except Exception:
        pass

    return sorted(list(macs))

def get_primary_mac_address():
    """
    ดึงค่า MAC Address หลักของเครื่องปัจจุบัน (เลือกตัวแรกที่เป็น Physical)
    """
    all_macs = get_all_mac_addresses()
    if all_macs:
        return all_macs[0]
    return "UNKNOWN-MAC"

def get_machine_info():
    """
    ดึงข้อมูลเครื่องเบื้องต้น: ชื่อเครื่อง (Hostname), OS, Primary MAC, All MACs
    """
    hostname = socket.gethostname()
    system_os = f"{platform.system()} {platform.release()}"
    all_macs = get_all_mac_addresses()
    primary_mac = all_macs[0] if all_macs else "UNKNOWN-MAC"
    
    return {
        'hostname': hostname,
        'os': system_os,
        'primary_mac': primary_mac,
        'all_macs': all_macs
    }

def is_mac_matching(target_mac, candidate_macs):
    """
    ตรวจสอบว่า target_mac ตรงกับ candidate_macs หรือไม่ (normalize รูปแบบก่อนเทียบ)
    """
    if not target_mac or not candidate_macs:
        return False
        
    def norm(m):
        return re.sub(r'[^0-9A-F]', '', m.upper())
        
    norm_target = norm(target_mac)
    for c in candidate_macs:
        if norm(c) == norm_target:
            return True
    return False

if __name__ == '__main__':
    info = get_machine_info()
    print("=== ข้อมูลเครื่องคอมพิวเตอร์ปัจจุบัน ===")
    print(f"ชื่อเครื่อง (Hostname): {info['hostname']}")
    print(f"ระบบปฏิบัติการ (OS):    {info['os']}")
    print(f"Primary MAC:            {info['primary_mac']}")
    print(f"MAC Addresses ทั้งหมด:   {', '.join(info['all_macs'])}")
