import sqlite3

DB_NAME = "database.db"


def main():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=== USERS TABLE ===")
    try:
        cur.execute("SELECT id, username, password, role FROM users ORDER BY id")
        rows = cur.fetchall()
        if not rows:
            print("(empty)")
        else:
            for r in rows:
                print(f"id={r['id']}, username={repr(r['username'])}, password={repr(r['password'])}, role={repr(r['role'])}")
    except Exception as e:
        print("Error querying users:", e)

    print("\n=== USERS TABLE SCHEMA ===")
    try:
        cur.execute("PRAGMA table_info(users)")
        cols = cur.fetchall()
        for c in cols:
            # cid, name, type, notnull, dflt_value, pk
            print(dict(c))
    except Exception as e:
        print("Error getting table_info:", e)

    print("\n=== USERS INDEXES ===")
    try:
        cur.execute("PRAGMA index_list(users)")
        idxs = cur.fetchall()
        for idx in idxs:
            print(dict(idx))
            # show index details
            if "name" in idx.keys():
                cur.execute(f"PRAGMA index_info({idx['name']})")
                print("  -> columns:", [dict(r) for r in cur.fetchall()])
    except Exception as e:
        print("Error getting index_list:", e)

    conn.close()


if __name__ == "__main__":
    main()

