# nhatky.py
import sqlite3
from datetime import datetime

DB_NAME = "database.db"


def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def lay_nhat_ky(limit=50):
    """Lấy danh sách nhật ký"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        SELECT 
            id,
            action,
            record_id,
            user_name,
            time,
            details
        FROM logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    
    rows = c.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        result.append({
            "id": row['id'],
            "action": row['action'],
            "record_id": row['record_id'],
            "user_name": row['user_name'] or "System",
            "time": row['time'],
            "details": row['details'] or ""
        })
    
    return result


def them_nhat_ky(action, record_id=None, user_name=None, details=None):
    """Thêm nhật ký mới"""
    conn = get_db()
    c = conn.cursor()
    
    if user_name is None:
        user_name = "System"
    
    c.execute("""
        INSERT INTO logs(action, record_id, user_name, time, details)
        VALUES(?, ?, ?, ?, ?)
    """, (
        action,
        record_id,
        user_name,
        datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        details or ""
    ))
    
    conn.commit()
    conn.close()
