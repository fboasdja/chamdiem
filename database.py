import os
import sqlite3
from typing import Any, Optional, Sequence


_RAW_DATABASE_URL = os.environ.get("DATABASE_URL")
# Render env đôi khi bị dính newline khi paste -> gây lỗi sslmode="require\n"
DATABASE_URL = (_RAW_DATABASE_URL.strip() if isinstance(_RAW_DATABASE_URL, str) else None) or None


def is_postgres() -> bool:
    return bool(DATABASE_URL)


def get_db():
    """
    - Render/Prod: dùng Postgres từ env DATABASE_URL (psycopg2)
    - Local: fallback SQLite database.db
    """
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        import socket
        from urllib.parse import urlparse, parse_qs, unquote

        class _AdaptRealDictCursor(psycopg2.extras.RealDictCursor):
            def execute(self, query, vars=None):
                if isinstance(query, str) and "?" in query:
                    query = query.replace("?", "%s")
                return super().execute(query, vars)

            def executemany(self, query, vars_list):
                if isinstance(query, str) and "?" in query:
                    query = query.replace("?", "%s")
                return super().executemany(query, vars_list)

        # Render đôi khi không có route IPv6 -> Supabase resolve ra IPv6 sẽ lỗi "Network is unreachable".
        # Ép ưu tiên IPv4 bằng hostaddr. Nếu cần có thể set env DATABASE_HOSTADDR=IPv4.
        hostaddr = None
        sslmode = None
        try:
            u = urlparse(DATABASE_URL)
            if u.hostname:
                # 1) ưu tiên env override
                env_hostaddr = (os.environ.get("DATABASE_HOSTADDR") or "").strip()
                if env_hostaddr:
                    hostaddr = env_hostaddr
                else:
                    # 2) gethostbyname: query A record, không phụ thuộc IPv4 interface (tránh AI_ADDRCONFIG)
                    try:
                        hostaddr = socket.gethostbyname(u.hostname)
                    except Exception:
                        # 3) fallback getaddrinfo AF_INET
                        port = u.port or 5432
                        infos = socket.getaddrinfo(u.hostname, port, socket.AF_INET, socket.SOCK_STREAM)
                        if infos:
                            hostaddr = infos[0][4][0]
            qs = parse_qs(u.query or "")
            if "sslmode" in qs and qs["sslmode"]:
                # lấy sslmode từ URL nhưng phải strip để tránh newline
                sslmode = str(qs["sslmode"][0]).strip()
                if not sslmode:
                    sslmode = "require"
            else:
                sslmode = "require"
        except Exception:
            # fallback: dùng default behavior
            hostaddr = None
            sslmode = "require"

        # IMPORTANT:
        # Với URI DSN (postgresql://...), libpq có thể bỏ qua hostaddr.
        # => Luôn parse URL và connect bằng keyword args để hostaddr (IPv4) có tác dụng.
        u = urlparse(DATABASE_URL)
        dbname = (u.path or "").lstrip("/") or "postgres"
        user = unquote(u.username or "")
        password = unquote(u.password or "")
        host = (u.hostname or "").strip()
        port = u.port or 5432

        kwargs = {
            "dbname": dbname,
            "user": user,
            "password": password,
            "host": host,
            "port": port,
            "cursor_factory": _AdaptRealDictCursor,
            "connect_timeout": 10,
            "sslmode": (sslmode or "require").strip(),
        }
        if hostaddr:
            kwargs["hostaddr"] = hostaddr
        return psycopg2.connect(**kwargs)

    conn = sqlite3.connect("database.db", timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def adapt_sql(sql: str) -> str:
    """
    Convert sqlite-style placeholders (?) to psycopg2 style (%s) when using Postgres.
    Assumption: queries in this project don't contain literal '?'.
    """
    if DATABASE_URL:
        return sql.replace("?", "%s")
    return sql


def execute(cur, sql: str, params: Optional[Sequence[Any]] = None):
    if params is None:
        params = ()
    cur.execute(adapt_sql(sql), params)
    return cur


def init_db():
    """
    Tạo schema tương thích cả SQLite và Postgres.
    """
    # SQLite mode: tạo folder database/ + 2 file TRU/LS (phục vụ backup / tách sau này)
    if not DATABASE_URL:
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_dir = os.path.join(base_dir, "database")
            os.makedirs(db_dir, exist_ok=True)
            for name in ("databaseTRU.db", "databaseLS.db"):
                path = os.path.join(db_dir, name)
                if not os.path.exists(path):
                    tmp = sqlite3.connect(path)
                    tmp.close()
        except Exception:
            pass

    conn = get_db()
    cur = conn.cursor()

    if DATABASE_URL:
        execute(
            cur,
            """
            CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                password TEXT,
                role TEXT DEFAULT 'user',
                so_allowed TEXT DEFAULT 'TRU'
            );
            """,
        )
        execute(
            cur,
            """
            CREATE TABLE IF NOT EXISTS records(
                id SERIAL PRIMARY KEY,
                so TEXT DEFAULT 'TRU',
                chuc_vu TEXT,
                name TEXT,
                giao_thong INTEGER DEFAULT 0,
                xa_1_4 INTEGER DEFAULT 0,
                xa_5_6 INTEGER DEFAULT 0,
                giam_sat INTEGER DEFAULT 0,
                giam_sat_1_5 INTEGER DEFAULT 0,
                giam_sat_6 INTEGER DEFAULT 0,
                an_sai INTEGER DEFAULT 0,
                tong_an INTEGER DEFAULT 0,
                diem INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """,
        )
        execute(
            cur,
            """
            CREATE TABLE IF NOT EXISTS logs(
                id SERIAL PRIMARY KEY,
                action TEXT,
                record_id INTEGER,
                user_name TEXT,
                time TEXT,
                details TEXT
            );
            """,
        )
        execute(
            cur,
            """
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """,
        )
        execute(
            cur,
            """
            INSERT INTO settings(key,value) VALUES('monthly_title','THỐNG KÊ ĐIỂM THÁNG')
            ON CONFLICT(key) DO NOTHING;
            """,
        )
        execute(
            cur,
            """
            INSERT INTO settings(key,value) VALUES('monthly_title_TRU','THỐNG KÊ ĐIỂM THÁNG')
            ON CONFLICT(key) DO NOTHING;
            """,
        )
        execute(
            cur,
            """
            INSERT INTO settings(key,value) VALUES('monthly_title_LS','THỐNG KÊ ĐIỂM THÁNG')
            ON CONFLICT(key) DO NOTHING;
            """,
        )
        execute(
            cur,
            """
            INSERT INTO users(username,password,role,so_allowed)
            VALUES('admin','admin','admin','ALL')
            ON CONFLICT(username) DO NOTHING;
            """,
        )
        execute(cur, "UPDATE users SET role='admin', so_allowed='ALL' WHERE username='admin';")
        execute(cur, "ALTER TABLE records ADD COLUMN IF NOT EXISTS giam_sat_1_5 INTEGER DEFAULT 0;")
        execute(cur, "ALTER TABLE records ADD COLUMN IF NOT EXISTS giam_sat_6 INTEGER DEFAULT 0;")
        conn.commit()
        conn.close()
        return

    # SQLite
    execute(cur, "PRAGMA journal_mode=WAL;")
    execute(cur, "PRAGMA synchronous=NORMAL;")
    execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'user',
            so_allowed TEXT DEFAULT 'TRU'
        )
        """,
    )
    execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            so TEXT DEFAULT 'TRU',
            chuc_vu TEXT,
            name TEXT,
            giao_thong INTEGER,
            xa_1_4 INTEGER,
            xa_5_6 INTEGER,
            giam_sat INTEGER,
            giam_sat_1_5 INTEGER DEFAULT 0,
            giam_sat_6 INTEGER DEFAULT 0,
            an_sai INTEGER,
            tong_an INTEGER,
            diem INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """,
    )
    execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            record_id INTEGER,
            user_name TEXT,
            time TEXT,
            details TEXT
        )
        """,
    )
    execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """,
    )
    execute(
        cur,
        "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
        ("monthly_title", "THỐNG KÊ ĐIỂM THÁNG"),
    )
    execute(
        cur,
        "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
        ("monthly_title_TRU", "THỐNG KÊ ĐIỂM THÁNG"),
    )
    execute(
        cur,
        "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
        ("monthly_title_LS", "THỐNG KÊ ĐIỂM THÁNG"),
    )
    execute(
        cur,
        "INSERT OR IGNORE INTO users(username,password,role,so_allowed) VALUES('admin','admin','admin','ALL')",
    )
    execute(cur, "UPDATE users SET role='admin', so_allowed='ALL' WHERE username='admin'")
    try:
        execute(cur, "ALTER TABLE records ADD COLUMN giam_sat_1_5 INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        execute(cur, "ALTER TABLE records ADD COLUMN giam_sat_6 INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.commit()
    conn.close()

