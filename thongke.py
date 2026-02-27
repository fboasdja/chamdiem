# thongke.py
import sqlite3
from datetime import datetime

DB_NAME = "database.db"


def get_db():
    # timeout + check_same_thread để tránh database is locked
    conn = sqlite3.connect(DB_NAME, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def thong_ke_theo_thang(nam=None, so=None):
    if not nam:
        nam = datetime.now().year

    conn = get_db()
    c = conn.cursor()

    # Kiểm tra xem có cột created_at không
    try:
        c.execute("SELECT created_at FROM records LIMIT 1")
        has_created_at = True
    except:
        has_created_at = False

    # Kiểm tra có cột so không
    try:
        c.execute("SELECT so FROM records LIMIT 1")
        has_so = True
    except:
        has_so = False

    if has_created_at:
        if has_so and so in ("TRU", "LS"):
            c.execute("""
                SELECT 
                    strftime('%m', created_at) AS thang,
                    SUM(tong_an) AS tong_ps
                FROM records
                WHERE strftime('%Y', created_at) = ?
                  AND so = ?
                GROUP BY thang
                ORDER BY thang
            """, (str(nam), so))
        else:
            c.execute("""
                SELECT 
                    strftime('%m', created_at) AS thang,
                    SUM(tong_an) AS tong_ps
                FROM records
                WHERE strftime('%Y', created_at) = ?
                GROUP BY thang
                ORDER BY thang
            """, (str(nam),))
    else:
        # Nếu không có created_at, trả về tổng PS cho tất cả records
        if has_so and so in ("TRU", "LS"):
            c.execute("""
                SELECT 
                    '01' AS thang, SUM(tong_an) AS tong_ps
                FROM records
                WHERE so = ?
            """, (so,))
        else:
            c.execute("""
                SELECT 
                    '01' AS thang, SUM(tong_an) AS tong_ps
                FROM records
            """)

    rows = c.fetchall()
    conn.close()

    result = []
    # Lấy tháng hiện tại
    current_month = datetime.now().month
    
    # Tạo dữ liệu từ tháng 1 đến tháng hiện tại
    months_data = {}
    for i in range(1, current_month + 1):
        months_data[str(i).zfill(2)] = 0
    
    for row in rows:
        thang = row['thang'] if hasattr(row, 'keys') else row[0]
        ps = row['tong_ps'] if hasattr(row, 'keys') else row[1]
        if thang and thang in months_data:
            months_data[thang] = ps or 0
    
    for thang in sorted(months_data.keys()):
        result.append({
            "month": f"Tháng {int(thang)}",
            "value": months_data[thang]
        })

    return result


def top_nguoi_diem_cao(limit=3, so=None):
    conn = get_db()
    c = conn.cursor()

    # Kiểm tra có cột so không
    try:
        c.execute("SELECT so FROM records LIMIT 1")
        has_so = True
    except:
        has_so = False

    if has_so and so in ("TRU", "LS"):
        c.execute("""
            SELECT name, SUM(diem) AS tong_diem
            FROM records
            WHERE so = ?
            GROUP BY name
            ORDER BY tong_diem DESC
            LIMIT ?
        """, (so, limit))
    else:
        c.execute("""
            SELECT name, SUM(diem) AS tong_diem
            FROM records
            GROUP BY name
            ORDER BY tong_diem DESC
            LIMIT ?
        """, (limit,))

    rows = c.fetchall()
    conn.close()

    result = []
    for row in rows:
        name = row['name'] if hasattr(row, 'keys') else row[0]
        score = row['tong_diem'] if hasattr(row, 'keys') else row[1]
        result.append({"name": name, "score": score or 0})
    
    return result
