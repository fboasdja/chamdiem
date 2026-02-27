from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3, datetime, time
from thongke import thong_ke_theo_thang, top_nguoi_diem_cao
from api import api
from nhatky import lay_nhat_ky


app = Flask(__name__)
app.secret_key = "secret_xulyan"
DB_NAME = "database.db"

# Thời gian timeout session (giây) - 1 tiếng
SESSION_TIMEOUT = 60 * 60

# ================= HEALTHCHECK =================
@app.get("/health")
def health():
    return jsonify(ok=True)

# ================= DB =================
def get_db():
    conn = sqlite3.connect(
        DB_NAME,
        timeout=15,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # WAL MODE chống lock
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT DEFAULT 'user'
    )
    """)
    
    # Thêm cột role nếu chưa có
    try:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS records(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chuc_vu TEXT,
        name TEXT,
        giao_thong INTEGER,
        xa_1_4 INTEGER,
        xa_5_6 INTEGER,
        giam_sat INTEGER,
        an_sai INTEGER,
        tong_an INTEGER,
        diem INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        user_id TEXT
    )
    """)
    
    # Thêm cột created_at nếu chưa có
    try:
        c.execute("ALTER TABLE records ADD COLUMN created_at TEXT DEFAULT (datetime('now'))")
    except:
        pass
    
    # Thêm cột user_id nếu chưa có
    try:
        c.execute("ALTER TABLE records ADD COLUMN user_id TEXT")
    except:
        pass

    # Thêm cột giao_thong nếu chưa có
    try:
        c.execute("ALTER TABLE records ADD COLUMN giao_thong INTEGER DEFAULT 0")
    except:
        pass

    # Cập nhật công thức: Tổng = 1-5 + 6; Điểm = giao_thong + 1-5 + 6*2 + GS - án sai*5
    try:
        c.execute("""
            UPDATE records SET
                tong_an = COALESCE(xa_1_4,0) + COALESCE(xa_5_6,0),
                diem = COALESCE(giao_thong,0) + COALESCE(xa_1_4,0) + COALESCE(xa_5_6,0)*2 + COALESCE(giam_sat,0) - COALESCE(an_sai,0)*5
        """)
    except:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        record_id INTEGER,
        user_name TEXT,
        time TEXT,
        details TEXT
    )
    """)
    
    # Thêm cột user_name và details nếu chưa có
    try:
        c.execute("ALTER TABLE logs ADD COLUMN user_name TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE logs ADD COLUMN details TEXT")
    except:
        pass
    
    # NOTE: bảng login_codes (nếu đã tồn tại từ bản cũ) sẽ giữ nguyên để không phá DB,
    # nhưng hệ thống không còn dùng login bằng code nữa.

    # Đảm bảo account admin có role admin
    c.execute(
        "INSERT OR IGNORE INTO users(username,password,role) VALUES('admin','admin','admin')"
    )
    # Cập nhật lại role cho admin nếu đã tồn tại
    c.execute(
        "UPDATE users SET role='admin' WHERE username='admin'"
    )

    conn.commit()
    conn.close()

init_db()

# Đăng ký Blueprint API
app.register_blueprint(api)


def _is_api_request():
    """Xác định request hiện tại có phải là API (JSON) hay không."""
    path = request.path or ""
    return path.startswith("/api/")


@app.before_request
def check_session_and_user():
    """
    - Tự động timeout sau SESSION_TIMEOUT nếu không hoạt động.
    - Mỗi request sẽ refresh lại role từ DB để F5 là cập nhật quyền mới.
    - Nếu tài khoản bị xóa, trả về thông báo phù hợp và đưa về trang login.
    """
    # Bỏ qua static và healthcheck
    if request.path.startswith("/static/") or request.path == "/health":
        return

    # Trang login tự xử lý, không cần kiểm tra
    if request.path == "/" and request.method in ("GET", "POST"):
        return

    # Nếu chưa đăng nhập
    if not session.get("login"):
        if _is_api_request():
            return jsonify(success=False, error="Chưa đăng nhập"), 401
        # Cho phép Flask view tự redirect nếu cần
        return

    now = time.time()
    last_active = session.get("last_active", now)

    # Timeout session
    if now - last_active > SESSION_TIMEOUT:
        session.clear()
        if _is_api_request():
            return jsonify(success=False, error="Phiên đăng nhập đã hết hạn"), 401
        return redirect("/")

    # Update lại last_active
    session["last_active"] = now

    username = session.get("username")
    if not username:
        return

    # Kiểm tra user còn tồn tại và lấy lại role từ DB
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, role FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if not row:
        # Tài khoản đã bị xóa
        session.clear()
        msg = "Tài khoản không tồn tại hoặc đã bị xoá"
        if _is_api_request():
            return jsonify(success=False, error=msg), 403
        # Trả về trang nhỏ hiển thị alert rồi về login
        return render_template("account_deleted.html", message=msg)

    # Cập nhật lại role trong session theo DB
    db_role = row["role"] or "user"
    if db_role == "admin" or username == "admin":
        session["role"] = "admin"
    else:
        session["role"] = db_role

# ================= API FOR DISCORD BOT =================
@app.route("/api/addaccount", methods=["POST"])
def add_account():
    """API endpoint để admin tạo tài khoản từ web"""
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    data = request.json or {}
    
    username = data.get("username")
    password = data.get("password")
    role = data.get("role", "user")
    
    # Validate role
    if role not in ["admin", "editer", "user"]:
        return jsonify(success=False, error="Role không hợp lệ. Chỉ có: admin, editer, user")
    
    if not username or not password:
        return jsonify(success=False, error="Thiếu username hoặc password")
    
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Kiểm tra trước xem username đã tồn tại chưa
        c.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        existed = c.fetchone() is not None
        print("[/api/addaccount] username=", username, "existed=", existed)
        if existed:
            print("[/api/addaccount] -> reject: existed")
            return jsonify(success=False, error="Tài khoản đã tồn tại")

        c.execute(
            "INSERT INTO users(username, password, role) VALUES(?, ?, ?)",
            (username, password, role)
        )
        print("[/api/addaccount] -> inserted OK, committing...")
        conn.commit()
    except sqlite3.IntegrityError as e:
        # Chỉ báo "đã tồn tại" nếu thật sự do UNIQUE username
        msg = str(e)
        if conn:
            conn.rollback()
        if "users.username" in msg or "UNIQUE constraint failed: users.username" in msg:
            return jsonify(success=False, error="Tài khoản đã tồn tại")
        return jsonify(success=False, error=f"Lỗi dữ liệu: {msg}")
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        # Trả lỗi chi tiết để dễ debug (bao gồm database is locked nếu có)
        return jsonify(success=False, error=str(e))
    finally:
        if conn:
            conn.close()
    
    # Ghi log tạo tài khoản bằng connection RIÊNG sau khi đã commit user
    try:
        from nhatky import them_nhat_ky
        creator = session.get("username", "Admin")
        them_nhat_ky("CREATE_ACCOUNT", None, creator, f"Tạo tài khoản: {username} với quyền {role}")
    except Exception as log_err:
        # Không để lỗi log làm hỏng việc tạo tài khoản
        print("[/api/addaccount] log error:", log_err)

    return jsonify(
        success=True, 
        message=f"Đã thêm tài khoản {username} với quyền {role}"
    )

@app.get("/api/users")
def api_users():
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, username, password, role FROM users ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return jsonify(
        success=True,
        users=[
            {
                "id": r["id"],
                "username": r["username"],
                "password": r["password"],
                "role": r["role"],
            }
            for r in rows
        ],
    )

@app.post("/api/users/<int:user_id>/role")
def api_update_user_role(user_id: int):
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    data = request.json or {}
    role = (data.get("role") or "").strip()
    if role not in ["admin", "editer", "user"]:
        return jsonify(success=False, error="Role không hợp lệ"), 400
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    conn.commit()
    conn.close()
    from nhatky import them_nhat_ky
    them_nhat_ky("EDIT_ROLE", None, session.get("username", "Admin"), f"Đổi quyền user_id={user_id} -> {role}")
    return jsonify(success=True)

@app.delete("/api/users/<int:user_id>")
def api_delete_user(user_id: int):
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error="User không tồn tại"), 404
    if row["username"] == "admin":
        conn.close()
        return jsonify(success=False, error="Không thể xoá admin"), 400
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    from nhatky import them_nhat_ky
    them_nhat_ky("DELETE_USER", None, session.get("username", "Admin"), f"Xóa user {row['username']} (id={user_id})")
    return jsonify(success=True)

def write_log(c, action, record_id, user_name=None, details=None):
    if user_name is None:
        user_name = "System"
    c.execute(
        "INSERT INTO logs(action,record_id,user_name,time,details) VALUES(?,?,?,?,?)",
        (action, record_id, user_name,
         datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S"), details or "")
    )

# Helper function để đảm bảo admin có full quyền
def get_user_role(session):
    """Lấy role từ session và đảm bảo admin có full quyền"""
    role = session.get("role", "user")
    # Đảm bảo admin luôn có full quyền
    if role == "admin":
        return "admin"
    return role

def can_edit(session):
    """Kiểm tra xem user có quyền chỉnh sửa không"""
    role = get_user_role(session)
    return role in ["admin", "editer"]

def can_delete(session):
    """Kiểm tra xem user có quyền xóa không"""
    role = get_user_role(session)
    return role in ["admin", "editer"]

def can_view_main(session):
    """Kiểm tra xem user có quyền xem Main không"""
    role = get_user_role(session)
    return role in ["admin", "editer"]

def can_view_logs(session):
    """Kiểm tra xem user có quyền xem nhật ký không"""
    role = get_user_role(session)
    # Chỉ admin được xem nhật ký
    return role == "admin"

def can_view_diem(session):
    """Trang Điểm: user, editer, admin đều xem được (chỉ đọc)"""
    role = get_user_role(session)
    return role in ["admin", "editer", "user"]

# ================= LOGIN =================
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (u, p)
        )
        ok = c.fetchone()
        conn.close()

        if ok:
            session["login"] = True
            session["username"] = u
            # Đảm bảo admin luôn có full quyền
            user_role = ok["role"] if "role" in ok.keys() else "user"
            # Kiểm tra và đảm bảo admin có full quyền
            if user_role == "admin" or u == "admin":
                session["role"] = "admin"
            else:
                session["role"] = user_role
            return redirect("/dashboard")

    return render_template("login.html")

# ================= DASHBOARD =================
@app.route("/dashboard", methods=["GET","POST"])
def dashboard():
    if not session.get("login"):
        return redirect("/")

    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        # Chỉ admin và editer mới được thêm
        if not can_edit(session):
            conn.close()
            return redirect("/dashboard")
        
        chuc_vu = request.form.get("chuc_vu", "Thực tập").strip()
        if chuc_vu not in ("Thực tập", "Cảnh sát viên", "Sĩ quan dự bị"):
            chuc_vu = "Thực tập"
        name = request.form.get("name", "").strip()
        # Mặc định tất cả là 0 nếu không nhập
        # Giao thông chỉ có với Thực tập
        if chuc_vu == "Thực tập":
            giao_thong = int(request.form.get("giao_thong", 0) or 0)
            giam_sat = 0
        else:
            giao_thong = 0
            giam_sat = int(request.form.get("giam_sat", 0) or 0)
        xa_1_4 = int(request.form.get("xa_1_4", 0) or 0)   # hiển thị 1-5
        xa_5_6 = int(request.form.get("xa_5_6", 0) or 0)   # hiển thị 6
        an_sai = int(request.form.get("an_sai", 0) or 0)
        user_id = request.form.get("user_id", "").strip()

        # Tổng = chỉ 1-5 + 6 (không giao thông, không GS, không án sai)
        tong_an = xa_1_4 + xa_5_6
        # Điểm = giao_thong + 1-5 (+1) + 6 (+2) + GS (+1), án sai -5
        diem = giao_thong + xa_1_4 + xa_5_6*2 + giam_sat - an_sai*5

        c.execute("""
        INSERT INTO records
        (chuc_vu,name,giao_thong,xa_1_4,xa_5_6,giam_sat,an_sai,tong_an,diem,created_at,user_id)
        VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),?)
        """,(chuc_vu,name,giao_thong,xa_1_4,xa_5_6,giam_sat,an_sai,tong_an,diem,user_id))

        user_name = session.get("username", "Unknown")
        write_log(c, "ADD", c.lastrowid, user_name, f"Thêm record: {name}")
        conn.commit()

    # Kiểm tra quyền
    user_role = get_user_role(session)
    
    can_see_main = can_view_main(session)
    can_see_diem = can_view_diem(session)
    
    # Load data cho Main và/hoặc tab Điểm
    if can_see_main or can_see_diem:
        c.execute("""
            SELECT * FROM records
            ORDER BY
                CASE chuc_vu
                    WHEN 'Cảnh sát viên' THEN 1
                    WHEN 'Sĩ quan dự bị' THEN 2
                    WHEN 'Thực tập' THEN 3
                    ELSE 4
                END,
                diem DESC
        """)
        data = c.fetchall()
    else:
        data = []
    
    # Lấy nhật ký - chỉ admin mới thấy
    nhat_ky = []
    can_see_logs = can_view_logs(session)
    if can_see_logs:
        nhat_ky = lay_nhat_ky(50)
    
    conn.close()

    return render_template(
        "dashboard.html",
        data=data,
        user_role=user_role,
        can_see_main=can_see_main,
        can_see_diem=can_see_diem,
        can_see_logs=can_see_logs,
        nhat_ky=nhat_ky,
    )

# ================= INLINE EDIT (ENTER LƯU) =================
@app.route("/inline_edit", methods=["POST"])
def inline_edit():
    # Chỉ admin và editer mới được chỉnh sửa
    if not can_edit(session):
        return jsonify(success=False, error="Không có quyền")
    
    data = request.json
    rid = int(data["id"])
    field = data["field"]
    value = data.get("value")

    # Ép kiểu số cho các cột số - xóa hết thì mặc định 0
    saved_value = None
    if field in ("giao_thong", "xa_1_4", "xa_5_6", "giam_sat", "an_sai"):
        raw = (value if value is not None else "").strip() if isinstance(value, str) else value
        try:
            value = int(float(str(raw))) if raw not in ("", None) else 0
        except (ValueError, TypeError):
            value = 0
        value = max(0, value)  # Không cho số âm
        saved_value = value

    conn = get_db()
    c = conn.cursor()

    allowed_fields = {"chuc_vu", "name", "giao_thong", "xa_1_4", "xa_5_6", "giam_sat", "an_sai", "user_id"}
    if field not in allowed_fields:
        conn.close()
        return jsonify(success=False, error="Trường không hợp lệ")

    # Chức vụ chỉ cho phép 3 giá trị
    if field == "chuc_vu":
        if value not in ("Thực tập", "Cảnh sát viên", "Sĩ quan dự bị"):
            conn.close()
            return jsonify(success=False, error="Chức vụ không hợp lệ")
        if value == "Thực tập":
            c.execute("UPDATE records SET chuc_vu=?, giam_sat=0 WHERE id=?", (value, rid))
        else:
            c.execute("UPDATE records SET chuc_vu=?, giao_thong=0 WHERE id=?", (value, rid))
    else:
        c.execute(f"UPDATE records SET {field}=? WHERE id=?", (value, rid))

    c.execute(
        "SELECT chuc_vu,giao_thong,xa_1_4,xa_5_6,giam_sat,an_sai FROM records WHERE id=?",
        (rid,)
    )
    r = c.fetchone()

    chuc_vu = r["chuc_vu"]
    giao_thong = int(r["giao_thong"] or 0)
    xa_1_4 = int(r["xa_1_4"])
    xa_5_6 = int(r["xa_5_6"])
    if chuc_vu == "Thực tập":
        giam_sat = 0
        c.execute("UPDATE records SET giam_sat=0 WHERE id=?", (rid,))
    else:
        giam_sat = int(r["giam_sat"])
    an_sai = int(r["an_sai"])

    # Tổng = chỉ 1-5 + 6 (không giao thông, không GS, không án sai)
    tong_an = xa_1_4 + xa_5_6
    # Điểm = giao_thong + 1-5 (+1) + 6 (+2) + GS (+1), án sai -5
    diem = giao_thong + xa_1_4 + xa_5_6*2 + giam_sat - an_sai*5

    c.execute(
        "UPDATE records SET tong_an=?, diem=? WHERE id=?",
        (tong_an, diem, rid)
    )

    user_name = session.get("username", "Unknown")
    write_log(c, "INLINE_EDIT", rid, user_name, f"Chỉnh sửa {field} = {value}")
    conn.commit()
    conn.close()

    resp = {"success": True, "tong_an": tong_an, "diem": diem, "giao_thong": giao_thong, "giam_sat": giam_sat}
    if saved_value is not None:
        resp["saved_value"] = saved_value
    return jsonify(resp)

# ================= DELETE =================
@app.route("/delete/<int:id>")
def delete(id):
    if not session.get("login"):
        return redirect("/")
    
    # Chỉ admin và editer mới được xóa
    if not can_delete(session):
        return redirect("/dashboard")

    conn = get_db()
    c = conn.cursor()
    # Lấy tên record trước khi xóa
    c.execute("SELECT name FROM records WHERE id=?", (id,))
    record = c.fetchone()
    record_name = record["name"] if record else "Unknown"
    
    c.execute("DELETE FROM records WHERE id=?", (id,))
    user_name = session.get("username", "Unknown")
    write_log(c, "DELETE", id, user_name, f"Xóa record: {record_name}")
    conn.commit()
    conn.close()

    return redirect("/dashboard")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ================= KHỞI ĐỘNG BOT DISCORD =================
def start_discord_bot():
    """Khởi động Discord bot trong thread riêng"""
    import threading
    
    def run_bot():
        try:
            # Import trực tiếp từ discord_bot module
            import discord_bot
            discord_bot.run_bot()
        except Exception as e:
            import traceback
            print(f"Lỗi khi chạy Discord bot: {e}")
            traceback.print_exc()
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("Discord bot đang khởi động...")

if __name__ == "__main__":
    # Khởi động bot Discord
    try:
        start_discord_bot()
    except Exception as e:
        print(f"Không thể khởi động Discord bot: {e}")
    
    # IMPORTANT: tắt reloader để tránh chạy bot 2 lần (gây lỗi Discord interaction)
    app.run(debug=True, use_reloader=False)
