from flask import Blueprint, jsonify
import sqlite3
from datetime import datetime

api = Blueprint('api', __name__)
DB = 'database.db'


def db():
    conn = sqlite3.connect(DB, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@api.route('/api/thongke')
def thongke():
    from thongke import thong_ke_theo_thang
    year = datetime.now().year
    data = thong_ke_theo_thang(year)
    return jsonify(data)


@api.route('/api/top')
def top():
    from thongke import top_nguoi_diem_cao
    data = top_nguoi_diem_cao(3)
    return jsonify(data)
