from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from typing import Any, Callable, Dict

from flask import Blueprint, jsonify, redirect, render_template
from werkzeug.security import generate_password_hash


def create_admin_blueprint(
    *,
    current_user_is_admin: Callable[[], bool],
    current_username: Callable[[], str],
    admin_user_rows: Callable[[], list],
    sanitize_username: Callable[[str], str],
    sanitize_email: Callable[[str], str],
    is_valid_email: Callable[[str], bool],
    is_primary_admin_username: Callable[[str], bool],
    primary_admin_username: str,
    user_store: Any,
    ensure_user_storage: Callable[[str], None],
    now_iso: Callable[[], str],
    parse_expiry_datetime: Callable[[str], Any],
    user_base_dir: Callable[[str], Any],
    get_json_data: Callable[[], Dict[str, Any]],
):
    bp = Blueprint("admin_bp", __name__)

    def admin_denied():
        return jsonify({"ok": False, "error": "无管理员权限"}), 403

    @bp.get("/admin/users")
    def admin_users_page():
        if not current_user_is_admin():
            return redirect("/")
        return render_template("admin_users.html", users=admin_user_rows(), current_username=current_username())

    @bp.get("/api/admin/users")
    def api_admin_users():
        if not current_user_is_admin():
            return admin_denied()
        rows = admin_user_rows()
        return jsonify({"ok": True, "total": len(rows), "users": rows})

    @bp.post("/api/admin/users/create")
    def api_admin_users_create():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        email = sanitize_email(str(data.get("email", "")))
        password = str(data.get("password", ""))
        if len(username) < 3:
            raise ValueError("用户名至少3位，只允许字母数字._-")
        if not is_valid_email(email):
            raise ValueError("请输入有效邮箱")
        if len(password) < 6:
            raise ValueError("密码至少6位")
        users = user_store.read()
        if username in users:
            raise ValueError("用户名已存在")
        if is_primary_admin_username(username):
            raise ValueError(f"{primary_admin_username} 为内置管理员账号，不能创建")
        if user_store.find_username_by_email(email):
            raise ValueError("邮箱已被使用")
        users[username] = {
            "username": username,
            "email": email,
            "password_hash": generate_password_hash(password, method="pbkdf2:sha256"),
            "role": "user",
            "disabled": False,
            "status": "pending",
            "expires_at": "",
            "created_at": now_iso(),
        }
        user_store.write(users)
        ensure_user_storage(username)
        return jsonify({"ok": True, "created": username, "users": admin_user_rows()})

    @bp.post("/api/admin/users/approve")
    def api_admin_users_approve():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        if not username:
            raise ValueError("username 不能为空")
        if is_primary_admin_username(username):
            return jsonify({"ok": True, "username": username, "status": "approved", "users": admin_user_rows()})
        users = user_store.read()
        item = users.get(username)
        if not item:
            raise ValueError("用户不存在")
        item["status"] = "approved"
        item["approved_at"] = now_iso()
        item["approved_by"] = current_username()
        users[username] = item
        user_store.write(users)
        return jsonify({"ok": True, "username": username, "status": "approved", "users": admin_user_rows()})

    @bp.post("/api/admin/users/reject")
    def api_admin_users_reject():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        if not username:
            raise ValueError("username 不能为空")
        if is_primary_admin_username(username):
            raise ValueError(f"不能驳回管理员 {primary_admin_username}")
        users = user_store.read()
        item = users.get(username)
        if not item:
            raise ValueError("用户不存在")
        item["status"] = "rejected"
        item["approved_at"] = now_iso()
        item["approved_by"] = current_username()
        users[username] = item
        user_store.write(users)
        return jsonify({"ok": True, "username": username, "status": "rejected", "users": admin_user_rows()})

    @bp.post("/api/admin/users/set-expiry")
    def api_admin_users_set_expiry():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        days_raw = str(data.get("days", "")).strip()
        expires_at_raw = str(data.get("expires_at", "")).strip()
        if not username:
            raise ValueError("username 不能为空")
        if is_primary_admin_username(username):
            raise ValueError(f"管理员 {primary_admin_username} 不设置使用期限")
        users = user_store.read()
        item = users.get(username)
        if not item:
            raise ValueError("用户不存在")

        if days_raw != "":
            try:
                days = int(days_raw)
            except Exception:
                raise ValueError("days 必须是整数（单位：天）")
            if days <= 0:
                raise ValueError("days 必须大于 0（单位：天）")
            if days > 3650:
                raise ValueError("days 不能超过 3650 天")
            exp_dt = datetime.now() + timedelta(days=days)
            item["expires_at"] = exp_dt.isoformat(timespec="seconds")
            users[username] = item
            user_store.write(users)
            return jsonify({"ok": True, "username": username, "expires_at": item["expires_at"], "days": days, "users": admin_user_rows()})

        if not expires_at_raw:
            item["expires_at"] = ""
            users[username] = item
            user_store.write(users)
            return jsonify({"ok": True, "username": username, "expires_at": "", "users": admin_user_rows()})

        exp_dt = parse_expiry_datetime(expires_at_raw)
        if not exp_dt:
            raise ValueError("expires_at 格式错误，支持 YYYY-MM-DD 或 ISO 日期时间")
        item["expires_at"] = exp_dt.isoformat(timespec="seconds")
        users[username] = item
        user_store.write(users)
        return jsonify({"ok": True, "username": username, "expires_at": item["expires_at"], "users": admin_user_rows()})

    @bp.post("/api/admin/users/set-status")
    def api_admin_users_set_status():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        disabled = bool(data.get("disabled", False))
        me = current_username()
        if not username:
            raise ValueError("username 不能为空")
        if username == me and disabled:
            raise ValueError("不能禁用当前登录管理员")
        if is_primary_admin_username(username) and disabled:
            raise ValueError(f"不能禁用管理员 {primary_admin_username}")
        users = user_store.read()
        item = users.get(username)
        if not item:
            raise ValueError("用户不存在")
        item["disabled"] = disabled
        users[username] = item
        user_store.write(users)
        return jsonify({"ok": True, "username": username, "disabled": disabled, "users": admin_user_rows()})

    @bp.post("/api/admin/users/reset-password")
    def api_admin_users_reset_password():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        new_password = str(data.get("new_password", ""))
        if not username:
            raise ValueError("username 不能为空")
        if len(new_password) < 6:
            raise ValueError("新密码至少6位")
        users = user_store.read()
        item = users.get(username)
        if not item:
            raise ValueError("用户不存在")
        item["password_hash"] = generate_password_hash(new_password, method="pbkdf2:sha256")
        users[username] = item
        user_store.write(users)
        return jsonify({"ok": True, "username": username})

    @bp.post("/api/admin/users/delete")
    def api_admin_users_delete():
        if not current_user_is_admin():
            return admin_denied()
        data = get_json_data()
        username = sanitize_username(str(data.get("username", "")))
        me = current_username()
        if not username:
            raise ValueError("username 不能为空")
        if username == me:
            raise ValueError("不能删除当前登录管理员")
        if is_primary_admin_username(username):
            raise ValueError(f"不能删除管理员 {primary_admin_username}")

        users = user_store.read()
        item = users.get(username)
        if not item:
            raise ValueError("用户不存在")
        users.pop(username, None)
        user_store.write(users)

        user_dir = user_base_dir(username)
        if user_dir.exists():
            shutil.rmtree(user_dir, ignore_errors=True)
        return jsonify({"ok": True, "deleted": username, "users": admin_user_rows()})

    return bp
