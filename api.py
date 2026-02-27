from flask import Blueprint, jsonify, session, request
from datetime import datetime

api = Blueprint('api', __name__)
from database import get_db


@api.route('/api/thongke')
def thongke():
    from thongke import thong_ke_theo_thang
    year = datetime.now().year
    role = session.get("role", "user")
    so_allowed = session.get("so_allowed", "TRU")
    so = (request.args.get("so") or session.get("current_so") or "TRU").strip().upper()
    if so not in ("TRU", "LS"):
        so = "TRU"
    if role != "admin" and so_allowed != "ALL":
        so = (so_allowed or "TRU")
    data = thong_ke_theo_thang(year, so=so)
    return jsonify(data)


@api.route('/api/top')
def top():
    from thongke import top_nguoi_diem_cao
    role = session.get("role", "user")
    so_allowed = session.get("so_allowed", "TRU")
    so = (request.args.get("so") or session.get("current_so") or "TRU").strip().upper()
    if so not in ("TRU", "LS"):
        so = "TRU"
    if role != "admin" and so_allowed != "ALL":
        so = (so_allowed or "TRU")
    data = top_nguoi_diem_cao(3, so=so)
    return jsonify(data)
