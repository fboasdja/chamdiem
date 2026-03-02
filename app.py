from flask import Flask, render_template, request, redirect, session, jsonify
import datetime, time, os, sqlite3, json
from pathlib import Path
from thongke import thong_ke_theo_thang, top_nguoi_diem_cao
from api import api


app = Flask(__name__)
app.secret_key = "secret_xulyan"

# DB: Render dùng Postgres (DATABASE_URL), local dùng SQLite
from database import get_db, init_db as init_db_shared, is_postgres

# Thời gian timeout session (giây) - 1 tiếng
SESSION_TIMEOUT = 60 * 60
UPDATE_CONFIG_PATH = Path(__file__).with_name("update.json")
DEFAULT_UPDATE_CONFIG = {
    "update_mode": False,
    "update_until": "20h ngày 1/3/2026",
    "update_message_enabled": True,
    "update_message": "trong thời gian này anh em giành thời gian cho gia đình nhé !:3",
}

# ================= HEALTHCHECK =================
@app.get("/health")
def health():
    return jsonify(ok=True)

# ================= DB =================
def init_db():
    init_db_shared()


init_db()

# Đăng ký Blueprint API
app.register_blueprint(api)


def _is_api_request():
    """Xác định request hiện tại có phải là API (JSON) hay không."""
    path = request.path or ""
    return path.startswith("/api/")


def _normalize_so(value: str | None) -> str:
    v = (value or "").strip().upper()
    return v if v in ("TRU", "LS") else "TRU"


def _effective_so_for_session(role: str, so_allowed: str, requested_so: str | None) -> str:
    req = _normalize_so(requested_so)
    allowed = (so_allowed or "TRU").strip().upper()
    if role == "admin" or allowed == "ALL":
        return req
    # user/editer: luôn bị khóa theo sở được cấp
    return _normalize_so(allowed)


def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return default
    return row["value"] or default


def load_update_config() -> dict:
    """
    Đọc cấu hình trạng thái update từ update.json.
    Nếu file thiếu/lỗi thì fallback về mặc định để app luôn chạy.
    """
    cfg = dict(DEFAULT_UPDATE_CONFIG)
    try:
        if UPDATE_CONFIG_PATH.exists():
            raw = json.loads(UPDATE_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
    except Exception:
        # fallback mặc định nếu json lỗi
        pass
    return cfg


def is_root_admin_session() -> bool:
    return (
        bool(session.get("login"))
        and session.get("username") == "admin"
        and session.get("role") == "admin"
    )


@app.before_request
def check_session_and_user():
    """
    - Tự động timeout sau SESSION_TIMEOUT nếu không hoạt động.
    - Mỗi request sẽ refresh lại role từ DB để F5 là cập nhật quyền mới.
    - Nếu tài khoản bị xóa, trả về thông báo phù hợp và đưa về trang login.
    """
    # Bỏ qua static, healthcheck và trang heal trắng
    if request.path.startswith("/static/") or request.path in ("/health", "/heal"):
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

    # Khi bật update_mode: buộc tất cả user đã đăng nhập thoát ngay
    update_cfg = load_update_config()
    if bool(update_cfg.get("update_mode", False)):
        session.clear()
        if _is_api_request():
            return jsonify(success=False, error="Hệ thống đang cập nhật, vui lòng đăng nhập lại sau"), 401
        return redirect("/")

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
    c.execute("SELECT username, role, so_allowed FROM users WHERE username=?", (username,))
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

    # Cập nhật lại so_allowed trong session theo DB
    db_so_allowed = (row["so_allowed"] or "TRU").strip().upper() if "so_allowed" in row.keys() else "TRU"
    if session.get("role") == "admin" or username == "admin":
        session["so_allowed"] = "ALL"
    else:
        session["so_allowed"] = db_so_allowed if db_so_allowed in ("TRU", "LS", "ALL") else "TRU"

    # Đảm bảo current_so hợp lệ theo quyền hiện tại
    session["current_so"] = _effective_so_for_session(
        session.get("role", "user"),
        session.get("so_allowed", "TRU"),
        session.get("current_so", "TRU"),
    )

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
    so = (data.get("so") or "TRU").strip().upper()
    
    # Validate role
    if role not in ["admin", "editer", "user"]:
        return jsonify(success=False, error="Role không hợp lệ. Chỉ có: admin, editer, user")

    # Role admin thì không cần chọn sở (luôn ALL)
    if role == "admin":
        so = "ALL"
    else:
        if so not in ["TRU", "LS"]:
            so = "TRU"
    
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
            "INSERT INTO users(username, password, role, so_allowed) VALUES(?, ?, ?, ?)",
            (username, password, role, "ALL" if role == "admin" else so)
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
    c.execute("SELECT id, username, password, role, so_allowed FROM users ORDER BY id ASC")
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
                "so": (
                    "ALL"
                    if (r["role"] or "").strip().lower() == "admin"
                    else (r["so_allowed"] if (r["so_allowed"] or "").upper() in ("TRU", "LS") else "TRU")
                ),
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
    c.execute("SELECT username, role FROM users WHERE id=?", (user_id,))
    target = c.fetchone()
    if not target:
        conn.close()
        return jsonify(success=False, error="User không tồn tại"), 404
    target_username = (target["username"] or "").strip()
    target_role = (target["role"] or "").strip().lower()
    if target_username == "admin":
        conn.close()
        return jsonify(success=False, error="Không thể đổi quyền admin gốc"), 400
    if target_role == "admin":
        conn.close()
        return jsonify(success=False, error="Tài khoản admin không thể chỉnh role"), 400
    c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    # Nếu set admin thì cho ALL sở
    if role == "admin":
        try:
            c.execute("UPDATE users SET so_allowed='ALL' WHERE id=?", (user_id,))
        except Exception:
            pass
    conn.commit()
    conn.close()
    from nhatky import them_nhat_ky
    them_nhat_ky("EDIT_ROLE", None, session.get("username", "Admin"), f"Đổi quyền user_id={user_id} -> {role}")
    return jsonify(success=True)


@app.post("/api/users/<int:user_id>/so")
def api_update_user_so(user_id: int):
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    data = request.json or {}
    so = (data.get("so") or "").strip().upper()
    if so not in ("TRU", "LS", "ALL"):
        return jsonify(success=False, error="Sở không hợp lệ"), 400
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, role FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error="User không tồn tại"), 404
    if row["username"] == "admin":
        conn.close()
        return jsonify(success=False, error="Không thể đổi sở của admin"), 400
    # Admin luôn ALL sở (nếu user là admin thì không cho set TRU/LS)
    if (row["role"] or "").strip().lower() == "admin":
        c.execute("UPDATE users SET so_allowed='ALL' WHERE id=?", (user_id,))
    else:
        if so == "ALL":
            conn.close()
            return jsonify(success=False, error="User thường không thể chọn ALL"), 400
        c.execute("UPDATE users SET so_allowed=? WHERE id=?", (so, user_id))
    conn.commit()
    conn.close()
    from nhatky import them_nhat_ky
    them_nhat_ky("EDIT_SO", None, session.get("username", "Admin"), f"Đổi sở user_id={user_id} -> {so}")
    return jsonify(success=True)

@app.delete("/api/users/<int:user_id>")
def api_delete_user(user_id: int):
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, role FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error="User không tồn tại"), 404
    # Không cho xóa tài khoản admin gốc
    if row["username"] == "admin":
        conn.close()
        return jsonify(success=False, error="Không thể xoá tài khoản admin gốc"), 400
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    from nhatky import them_nhat_ky
    them_nhat_ky("DELETE_USER", None, session.get("username", "Admin"), f"Xóa user {row['username']} (id={user_id})")
    return jsonify(success=True)


@app.post("/api/admin/reset_data")
def api_admin_reset_data():
    """
    Reset dữ liệu records theo sở (TRU/LS) hoặc ALL.
    Dùng để đảm bảo 2 sở là 2 data riêng, và tiện dọn dữ liệu test.
    """
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    if not is_root_admin_session():
        return jsonify(success=False, error="Chỉ admin gốc mới được reset dữ liệu"), 403

    data = request.json or {}
    so = (data.get("so") or "CURRENT").strip().upper()
    if so == "CURRENT":
        so = _normalize_so(session.get("current_so", "TRU"))
    if so not in ("TRU", "LS", "ALL"):
        return jsonify(success=False, error="Tham số so không hợp lệ"), 400

    conn = get_db()
    c = conn.cursor()
    try:
        if so == "ALL":
            # Xoá logs trước để tránh giữ record_id mồ côi (không bắt buộc, nhưng sạch)
            c.execute("DELETE FROM logs")
            c.execute("DELETE FROM records")
        else:
            # Xóa logs thuộc records của sở đó
            try:
                c.execute("DELETE FROM logs WHERE record_id IN (SELECT id FROM records WHERE so=?)", (so,))
            except Exception:
                pass
            c.execute("DELETE FROM records WHERE so=?", (so,))

        write_log(c, "RESET_DATA", None, session.get("username", "Admin"), f"Reset dữ liệu so={so}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(success=False, error=str(e)), 500
    conn.close()
    return jsonify(success=True, so=so)


@app.post("/api/main/reset_scores")
def api_main_reset_scores():
    """
    Đặt toàn bộ điểm về 0 theo sở hiện tại đang chọn.
    Giữ nguyên tên/chức vụ, chỉ reset số liệu.
    """
    if not can_edit(session):
        return jsonify(success=False, error="Không có quyền"), 403
    so = _effective_so_for_session(
        get_user_role(session),
        session.get("so_allowed", "TRU"),
        session.get("current_so", "TRU"),
    )
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE records
            SET giao_thong=0,
                xa_1_4=0,
                xa_5_6=0,
                giam_sat=0,
                giam_sat_1_5=0,
                giam_sat_6=0,
                an_sai=0,
                tong_an=0,
                diem=0
            WHERE so=?
            """,
            (so,),
        )
        affected = c.rowcount if c.rowcount is not None else 0
        write_log(c, "RESET_SCORES", None, session.get("username", "Admin"), f"Reset điểm so={so}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(success=False, error=str(e)), 500
    conn.close()
    return jsonify(success=True, so=so, affected=affected, mode="update_only")


@app.post("/api/main/reset_all")
def api_main_reset_all():
    """
    Xoá toàn bộ records theo sở hiện tại đang chọn.
    """
    if not can_edit(session):
        return jsonify(success=False, error="Không có quyền"), 403
    so = _effective_so_for_session(
        get_user_role(session),
        session.get("so_allowed", "TRU"),
        session.get("current_so", "TRU"),
    )
    conn = get_db()
    c = conn.cursor()
    try:
        try:
            c.execute("DELETE FROM logs WHERE record_id IN (SELECT id FROM records WHERE so=?)", (so,))
        except Exception:
            pass
        c.execute("DELETE FROM records WHERE so=?", (so,))
        affected = c.rowcount if c.rowcount is not None else 0
        write_log(c, "RESET_ALL", None, session.get("username", "Admin"), f"Reset all so={so}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify(success=False, error=str(e)), 500
    conn.close()
    return jsonify(success=True, so=so, affected=affected)

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
    # Đọc trạng thái update từ update.json (không còn dùng tab ADMIN)
    update_cfg = load_update_config()
    maintenance_enabled = bool(update_cfg.get("update_mode", False))
    maintenance_until = str(update_cfg.get("update_until", DEFAULT_UPDATE_CONFIG["update_until"]) or "")
    show_update_message = bool(update_cfg.get("update_message_enabled", True))
    maintenance_message = (
        str(update_cfg.get("update_message", DEFAULT_UPDATE_CONFIG["update_message"]) or "")
        if show_update_message
        else ""
    )

    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = (request.form.get("password") or "").strip()

        if maintenance_enabled:
            # Khi update_mode=true: ẩn form và chặn login hoàn toàn
            return render_template(
                "login.html",
                login_error="Hệ thống đang cập nhật, tạm thời không thể đăng nhập.",
                last_username=u,
                maintenance_enabled=True,
                maintenance_until=maintenance_until,
                maintenance_message=maintenance_message,
            )

        if not u or not p:
            return render_template(
                "login.html",
                login_error="Vui lòng nhập đầy đủ tài khoản và mật khẩu",
                last_username=u,
                maintenance_enabled=maintenance_enabled,
                maintenance_until=maintenance_until,
                maintenance_message=maintenance_message,
            )

        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (u, p)
        )
        ok = c.fetchone()
        conn.close()

        if ok:
            user_role = ok["role"] if "role" in ok.keys() else "user"

            session["login"] = True
            session["username"] = u
            # Đảm bảo admin luôn có full quyền
            # Kiểm tra và đảm bảo admin có full quyền
            if user_role == "admin" or u == "admin":
                session["role"] = "admin"
                session["so_allowed"] = "ALL"
            else:
                session["role"] = user_role
                so_allowed = (ok["so_allowed"] if "so_allowed" in ok.keys() else "TRU") or "TRU"
                so_allowed = so_allowed.strip().upper()
                session["so_allowed"] = so_allowed if so_allowed in ("TRU", "LS", "ALL") else "TRU"

            session["current_so"] = _effective_so_for_session(
                session.get("role", "user"),
                session.get("so_allowed", "TRU"),
                session.get("current_so", "TRU"),
            )
            # Render UI thành công trước rồi redirect bằng JS (mượt hơn)
            return render_template(
                "login.html",
                login_success=True,
                last_username=u,
                maintenance_enabled=maintenance_enabled,
                maintenance_until=maintenance_until,
                maintenance_message=maintenance_message,
            )

        return render_template(
            "login.html",
            login_error="Sai tài khoản hoặc mật khẩu",
            last_username=u,
            maintenance_enabled=maintenance_enabled,
            maintenance_until=maintenance_until,
            maintenance_message=maintenance_message,
        )

    return render_template(
        "login.html",
        maintenance_enabled=maintenance_enabled,
        maintenance_until=maintenance_until,
        maintenance_message=maintenance_message,
    )


@app.get("/heal")
def heal_page():
    """Trang trắng đơn giản để treo / ping server."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Heal</title></head>"
        "<body style='background:#0b1120;color:#e5e7eb;font-family:system-ui, sans-serif;'>"
        "<div style='display:flex;align-items:center;justify-content:center;height:100vh;'>"
        "<div>Heal OK</div>"
        "</div>"
        "</body></html>"
    )

# ================= DASHBOARD =================
@app.route("/dashboard", methods=["GET","POST"])
def dashboard():
    if not session.get("login"):
        return redirect("/")

    conn = get_db()
    c = conn.cursor()

    username = session.get("username")

    # Xác định sở hiện tại (TRU/LS) theo quyền và query param
    requested_so = request.args.get("so")
    user_role = get_user_role(session)
    so_allowed = session.get("so_allowed", "TRU")
    current_so = _effective_so_for_session(user_role, so_allowed, requested_so or session.get("current_so", "TRU"))
    session["current_so"] = current_so

    if request.method == "POST":
        # Chỉ admin và editer mới được thêm
        if not can_edit(session):
            conn.close()
            return redirect("/dashboard")
        
        chuc_vu = request.form.get("chuc_vu", "Thực tập").strip()
        if chuc_vu not in ("Thực tập", "Cảnh sát viên", "Sĩ quan dự bị"):
            chuc_vu = "Thực tập"
        name = request.form.get("name", "").strip()
        # Giao thông: Thực tập lấy từ form, Cảnh sát/Sĩ quan = 0
        if chuc_vu == "Thực tập":
            giao_thong = int(request.form.get("giao_thong", 0) or 0)
            giam_sat_1_5 = 0
            giam_sat_6 = 0
        else:
            giao_thong = 0
            giam_sat_1_5 = int(request.form.get("giam_sat_1_5", 0) or 0)
            giam_sat_6 = int(request.form.get("giam_sat_6", 0) or 0)
        xa_1_4 = int(request.form.get("xa_1_4", 0) or 0)   # hiển thị 1-5
        xa_5_6 = int(request.form.get("xa_5_6", 0) or 0)   # hiển thị 6
        an_sai = int(request.form.get("an_sai", 0) or 0)
        giam_sat = giam_sat_1_5 + giam_sat_6

        # Tổng = án 1-5 + án 6 + giám sát 1-5 + giám sát 6
        tong_an = xa_1_4 + xa_5_6 + giam_sat_1_5 + giam_sat_6
        # Điểm: giám sát 1-5 tính như án 1-5, giám sát 6 tính như án 6
        diem = giao_thong*1 + (xa_1_4 + giam_sat_1_5)*2 + (xa_5_6 + giam_sat_6)*4 - an_sai*5

        if is_postgres():
            c.execute("""
            INSERT INTO records
            (so,chuc_vu,name,giao_thong,xa_1_4,xa_5_6,giam_sat,giam_sat_1_5,giam_sat_6,an_sai,tong_an,diem)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            RETURNING id
            """,(current_so,chuc_vu,name,giao_thong,xa_1_4,xa_5_6,giam_sat,giam_sat_1_5,giam_sat_6,an_sai,tong_an,diem))
            new_id_row = c.fetchone()
            new_id = (new_id_row.get("id") if isinstance(new_id_row, dict) else new_id_row[0]) if new_id_row else None
        else:
            c.execute("""
            INSERT INTO records
            (so,chuc_vu,name,giao_thong,xa_1_4,xa_5_6,giam_sat,giam_sat_1_5,giam_sat_6,an_sai,tong_an,diem,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            """,(current_so,chuc_vu,name,giao_thong,xa_1_4,xa_5_6,giam_sat,giam_sat_1_5,giam_sat_6,an_sai,tong_an,diem))
            new_id = c.lastrowid

        user_name = session.get("username", "Unknown")
        write_log(c, "ADD", new_id, user_name, f"Thêm record: {name}")
        conn.commit()

    can_see_main = can_view_main(session)
    can_see_diem = can_view_diem(session)
    
    # Load data cho Main và/hoặc tab Điểm
    if can_see_main or can_see_diem:
        c.execute("""
            SELECT * FROM records
            WHERE so = ?
            ORDER BY
                CASE chuc_vu
                    WHEN 'Cảnh sát viên' THEN 1
                    WHEN 'Sĩ quan dự bị' THEN 2
                    WHEN 'Thực tập' THEN 3
                    ELSE 4
                END,
                diem DESC
        """, (current_so,))
        data = c.fetchall()
    else:
        data = []
    
    # Nhật ký load theo phân trang qua API để nhìn gọn hơn
    nhat_ky = []
    can_see_logs = can_view_logs(session)
    
    conn.close()

    return render_template(
        "dashboard.html",
        data=data,
        username=username,
        user_role=user_role,
        can_see_main=can_see_main,
        can_see_diem=can_see_diem,
        can_see_logs=can_see_logs,
        nhat_ky=nhat_ky,
        current_so=current_so,
        so_allowed=so_allowed,
        monthly_title=get_setting(
            f"monthly_title_{current_so}",
            get_setting("monthly_title", "THỐNG KÊ ĐIỂM THÁNG"),
        ),
        can_edit_title=(user_role == "admin"),
        stats_title=get_setting(f"stats_title_{current_so}", f"Thống kê điểm Sở {current_so}"),
        stats_label=get_setting(f"stats_label_{current_so}", "Tổng số PS"),
        can_edit_stats=(user_role == "admin"),
    )


@app.get("/api/logs")
def api_logs():
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403

    try:
        page = int((request.args.get("page") or "1").strip())
    except Exception:
        page = 1
    if page < 1:
        page = 1

    page_size = 12
    offset = (page - 1) * page_size

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(1) AS total FROM logs")
    total_row = c.fetchone()
    total = int((total_row["total"] if total_row and "total" in total_row.keys() else 0) or 0)

    c.execute(
        """
        SELECT id, action, record_id, user_name, time, details
        FROM logs
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset),
    )
    rows = c.fetchall()
    conn.close()

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    data = [
        {
            "id": r["id"],
            "action": r["action"] or "",
            "record_id": r["record_id"],
            "user_name": r["user_name"] or "System",
            "time": r["time"] or "",
            "details": r["details"] or "",
        }
        for r in rows
    ]
    return jsonify(
        success=True,
        logs=data,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
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
    if field in ("giao_thong", "xa_1_4", "xa_5_6", "giam_sat_1_5", "giam_sat_6", "an_sai"):
        raw = (value if value is not None else "").strip() if isinstance(value, str) else value
        try:
            value = int(float(str(raw))) if raw not in ("", None) else 0
        except (ValueError, TypeError):
            value = 0
        value = max(0, value)  # Không cho số âm
        saved_value = value

    conn = get_db()
    c = conn.cursor()

    # Chặn sửa record khác sở (trừ admin khi đang chọn sở đó)
    current_so = _effective_so_for_session(
        get_user_role(session),
        session.get("so_allowed", "TRU"),
        session.get("current_so", "TRU"),
    )
    try:
        c.execute("SELECT so FROM records WHERE id=?", (rid,))
        row_so = c.fetchone()
        if not row_so:
            conn.close()
            return jsonify(success=False, error="Record không tồn tại"), 404
        if _normalize_so(row_so["so"]) != _normalize_so(current_so):
            conn.close()
            return jsonify(success=False, error="Không có quyền sửa dữ liệu sở khác"), 403
    except Exception:
        # Nếu DB cũ chưa có cột so, bỏ qua
        pass

    allowed_fields = {"chuc_vu", "name", "giao_thong", "xa_1_4", "xa_5_6", "giam_sat_1_5", "giam_sat_6", "an_sai"}
    if field not in allowed_fields:
        conn.close()
        return jsonify(success=False, error="Trường không hợp lệ")

    # Chức vụ chỉ cho phép 3 giá trị
    if field == "chuc_vu":
        if value not in ("Thực tập", "Cảnh sát viên", "Sĩ quan dự bị"):
            conn.close()
            return jsonify(success=False, error="Chức vụ không hợp lệ")
        if value == "Thực tập":
            c.execute("UPDATE records SET chuc_vu=?, giam_sat_1_5=0, giam_sat_6=0, giam_sat=0 WHERE id=?", (value, rid))
        else:
            c.execute("UPDATE records SET chuc_vu=?, giao_thong=0 WHERE id=?", (value, rid))
    else:
        c.execute(f"UPDATE records SET {field}=? WHERE id=?", (value, rid))

    c.execute(
        "SELECT chuc_vu,giao_thong,xa_1_4,xa_5_6,giam_sat_1_5,giam_sat_6,an_sai FROM records WHERE id=?",
        (rid,)
    )
    r = c.fetchone()

    chuc_vu = r["chuc_vu"]
    giao_thong = int(r["giao_thong"] or 0)
    xa_1_4 = int(r["xa_1_4"])
    xa_5_6 = int(r["xa_5_6"])
    giam_sat_1_5 = int(r["giam_sat_1_5"] or 0)
    giam_sat_6 = int(r["giam_sat_6"] or 0)
    an_sai = int(r["an_sai"])

    # Ràng buộc theo chức vụ
    if chuc_vu == "Thực tập":
        giam_sat_1_5 = 0
        giam_sat_6 = 0
        c.execute("UPDATE records SET giam_sat_1_5=0, giam_sat_6=0, giam_sat=0 WHERE id=?", (rid,))
    else:
        giao_thong = 0
        c.execute("UPDATE records SET giao_thong=0 WHERE id=?", (rid,))

    # Tổng = án 1-5 + án 6 + giám sát 1-5 + giám sát 6
    tong_an = xa_1_4 + xa_5_6 + giam_sat_1_5 + giam_sat_6
    # Điểm: giám sát 1-5 tính như án 1-5, giám sát 6 tính như án 6
    diem = giao_thong*1 + (xa_1_4 + giam_sat_1_5)*2 + (xa_5_6 + giam_sat_6)*4 - an_sai*5

    c.execute(
        "UPDATE records SET giam_sat=?, tong_an=?, diem=? WHERE id=?",
        (giam_sat_1_5 + giam_sat_6, tong_an, diem, rid)
    )

    user_name = session.get("username", "Unknown")
    write_log(c, "INLINE_EDIT", rid, user_name, f"Chỉnh sửa {field} = {value}")
    conn.commit()
    conn.close()

    resp = {
        "success": True,
        "tong_an": tong_an,
        "diem": diem,
        "giao_thong": giao_thong,
        "giam_sat_1_5": giam_sat_1_5,
        "giam_sat_6": giam_sat_6,
        "chuc_vu": chuc_vu,
    }
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
    c.execute("SELECT name, so FROM records WHERE id=?", (id,))
    record = c.fetchone()
    record_name = record["name"] if record else "Unknown"

    # Chặn xóa record khác sở
    try:
        current_so = _effective_so_for_session(
            get_user_role(session),
            session.get("so_allowed", "TRU"),
            session.get("current_so", "TRU"),
        )
        if record and _normalize_so(record["so"]) != _normalize_so(current_so):
            conn.close()
            return redirect("/dashboard")
    except Exception:
        pass
    
    c.execute("DELETE FROM records WHERE id=?", (id,))
    user_name = session.get("username", "Unknown")
    write_log(c, "DELETE", id, user_name, f"Xóa record: {record_name}")
    conn.commit()
    conn.close()

    return redirect("/dashboard")


@app.post("/api/settings/monthly_title")
def api_set_monthly_title():
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    data = request.json or {}
    title = (data.get("title") or "").strip()
    so = _normalize_so(data.get("so") or session.get("current_so") or "TRU")
    if not title:
        return jsonify(success=False, error="Tiêu đề không được để trống"), 400
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"monthly_title_{so}", title),
    )
    conn.commit()
    conn.close()
    return jsonify(success=True, title=title, so=so)


@app.post("/api/settings/stats")
def api_set_stats_settings():
    if not session.get("login") or session.get("role") != "admin":
        return jsonify(success=False, error="Không có quyền (chỉ admin)"), 403
    data = request.json or {}
    title = (data.get("title") or "").strip()
    label = (data.get("label") or "").strip()
    so = _normalize_so(data.get("so"))
    if not title:
        return jsonify(success=False, error="Tiêu đề không được để trống"), 400
    if not label:
        return jsonify(success=False, error="Nhãn trục không được để trống"), 400
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"stats_title_{so}", title),
    )
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"stats_label_{so}", label),
    )
    conn.commit()
    conn.close()
    return jsonify(success=True, title=title, label=label, so=so)

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
