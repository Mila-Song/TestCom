from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def create_auth_blueprint(
    *,
    current_username: Callable[[], str],
    current_user_record: Callable[[], Dict[str, Any] | None],
    current_user_is_admin: Callable[[], bool],
    sanitize_username: Callable[[str], str],
    sanitize_email: Callable[[str], str],
    is_valid_email: Callable[[str], bool],
    is_primary_admin_username: Callable[[str], bool],
    verify_primary_admin_password: Callable[..., bool],
    primary_admin_username: str,
    user_store: Any,
    ensure_user_storage: Callable[[str], None],
    now_iso: Callable[[], str],
    parse_expiry_datetime: Callable[[str], Any],
    get_client_ip: Callable[[Any], str],
    rate_limiter: Any,
    login_rate_limit_count: int,
    login_rate_limit_window_sec: int,
    rate_limited_response: Callable[[str], Any],
):
    bp = Blueprint("auth_bp", __name__)

    @bp.get("/wait-approval")
    def wait_approval_page():
        username = current_username()
        if not username:
            return redirect(url_for("auth_bp.login_page"))
        if is_primary_admin_username(username):
            return redirect(url_for("index"))
        users = user_store.read()
        rec = users.get(username, {})
        status = str(rec.get("status", "pending")).strip().lower() or "pending"
        if status == "approved":
            return redirect(url_for("index"))
        return render_template("wait_approval.html", username=username, status=status)

    @bp.get("/expired")
    def expired_page():
        username = current_username()
        if not username:
            return redirect(url_for("auth_bp.login_page"))
        if is_primary_admin_username(username):
            return redirect(url_for("index"))
        users = user_store.read()
        rec = users.get(username, {})
        expires_at = str(rec.get("expires_at", "")).strip()
        exp_dt = parse_expiry_datetime(expires_at)
        if not exp_dt or datetime.now() <= exp_dt:
            return redirect(url_for("index"))
        return render_template("expired.html", username=username, expires_at=exp_dt.strftime("%Y-%m-%d %H:%M:%S"))

    @bp.get("/account")
    def account_page():
        username = current_username()
        if not username:
            return redirect(url_for("auth_bp.login_page"))
        u = current_user_record() or {}
        return render_template(
            "account.html",
            username=username,
            email=str(u.get("email", "")).strip(),
            error=str(request.args.get("error", "")).strip(),
            info=str(request.args.get("info", "")).strip(),
        )

    @bp.get("/login")
    def login_page():
        if current_username():
            return redirect(url_for("index"))
        return render_template(
            "login.html",
            mode="login",
            error=str(request.args.get("error", "")).strip(),
            info=str(request.args.get("info", "")).strip(),
            username_prefill=str(request.args.get("username", "")).strip(),
            email_prefill=str(request.args.get("email", "")).strip(),
        )

    @bp.get("/register")
    def register_page():
        if current_username():
            return redirect(url_for("index"))
        return render_template(
            "login.html",
            mode="register",
            error=str(request.args.get("error", "")).strip(),
            info=str(request.args.get("info", "")).strip(),
            username_prefill=str(request.args.get("username", "")).strip(),
            email_prefill=str(request.args.get("email", "")).strip(),
        )

    @bp.post("/auth/register")
    def auth_register():
        payload = request.get_json(silent=True) if request.is_json else request.form
        username = sanitize_username(str(payload.get("username", "")))
        password = str(payload.get("password", ""))
        email = sanitize_email(str(payload.get("email", "")))
        if len(username) < 3:
            if request.is_json:
                return jsonify({"ok": False, "error": "用户名至少3位，只允许字母数字._-"}), 400
            return redirect(url_for("auth_bp.register_page", error="用户名至少3位，只允许字母数字._-", username=username, email=email))
        if len(password) < 6:
            if request.is_json:
                return jsonify({"ok": False, "error": "密码至少6位"}), 400
            return redirect(url_for("auth_bp.register_page", error="密码至少6位", username=username, email=email))
        if not is_valid_email(email):
            if request.is_json:
                return jsonify({"ok": False, "error": "请输入有效邮箱"}), 400
            return redirect(url_for("auth_bp.register_page", error="请输入有效邮箱", username=username, email=email))
        if is_primary_admin_username(username):
            msg = f"{primary_admin_username} 为内置管理员账号，禁止注册"
            if request.is_json:
                return jsonify({"ok": False, "error": msg}), 403
            return redirect(url_for("auth_bp.register_page", error=msg, username=username, email=email))
        users = user_store.read()
        if username in users:
            if request.is_json:
                return jsonify({"ok": False, "error": "用户名已存在"}), 400
            return redirect(url_for("auth_bp.register_page", error="用户名已存在", username=username, email=email))
        if user_store.find_username_by_email(email):
            if request.is_json:
                return jsonify({"ok": False, "error": "邮箱已被使用"}), 400
            return redirect(url_for("auth_bp.register_page", error="邮箱已被使用", username=username, email=email))
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
        session["username"] = username
        if request.is_json:
            return jsonify({"ok": True, "username": username})
        return redirect(url_for("index"))

    @bp.post("/auth/login")
    def auth_login():
        payload = request.get_json(silent=True) if request.is_json else request.form
        username = sanitize_username(str(payload.get("username", "")))
        password = str(payload.get("password", ""))
        ip = get_client_ip(request)
        if not rate_limiter.check(f"login_ip:{ip}", login_rate_limit_count, login_rate_limit_window_sec):
            if request.is_json:
                return rate_limited_response("登录尝试过于频繁，请稍后重试")
            return redirect(url_for("auth_bp.login_page", error="登录尝试过于频繁，请稍后重试", username=username))
        if not rate_limiter.check(f"login_ip_user:{ip}:{username or 'unknown'}", login_rate_limit_count, login_rate_limit_window_sec):
            if request.is_json:
                return rate_limited_response("登录尝试过于频繁，请稍后重试")
            return redirect(url_for("auth_bp.login_page", error="登录尝试过于频繁，请稍后重试", username=username))
        if is_primary_admin_username(username):
            if verify_primary_admin_password(password):
                session["username"] = primary_admin_username
                if request.is_json:
                    return jsonify({"ok": True, "username": primary_admin_username})
                return redirect(url_for("index"))
            if request.is_json:
                return jsonify({"ok": False, "error": "用户名或密码错误"}), 400
            return redirect(url_for("auth_bp.login_page", error="用户名或密码错误", username=username))

        users = user_store.read()
        u = users.get(username)
        if not u or not check_password_hash(str(u.get("password_hash", "")), password):
            if request.is_json:
                return jsonify({"ok": False, "error": "用户名或密码错误"}), 400
            return redirect(url_for("auth_bp.login_page", error="用户名或密码错误", username=username))
        if bool(u.get("disabled", False)):
            if request.is_json:
                return jsonify({"ok": False, "error": "账号已被禁用，请联系管理员"}), 403
            return redirect(url_for("auth_bp.login_page", error="账号已被禁用，请联系管理员", username=username))
        ensure_user_storage(username)
        session["username"] = username
        if request.is_json:
            return jsonify({"ok": True, "username": username})
        return redirect(url_for("index"))

    @bp.post("/auth/change-password")
    def auth_change_password():
        if not current_username():
            if request.is_json:
                return jsonify({"ok": False, "error": "未登录"}), 401
            return redirect(url_for("auth_bp.login_page"))
        payload = request.get_json(silent=True) if request.is_json else request.form
        old_password = str(payload.get("old_password", "")).strip()
        new_password = str(payload.get("new_password", "")).strip()
        if not old_password:
            if request.is_json:
                return jsonify({"ok": False, "error": "当前密码不能为空"}), 400
            return redirect(url_for("auth_bp.account_page", error="当前密码不能为空"))
        if len(new_password) < 6:
            if request.is_json:
                return jsonify({"ok": False, "error": "新密码至少6位"}), 400
            return redirect(url_for("auth_bp.account_page", error="新密码至少6位"))
        if old_password == new_password:
            if request.is_json:
                return jsonify({"ok": False, "error": "新密码不能与当前密码相同"}), 400
            return redirect(url_for("auth_bp.account_page", error="新密码不能与当前密码相同"))

        username = current_username()
        if is_primary_admin_username(username):
            if request.is_json:
                return jsonify({"ok": False, "error": "内置管理员密码固定，不能修改"}), 400
            return redirect(url_for("auth_bp.account_page", error="内置管理员密码固定，不能修改"))
        users = user_store.read()
        u = users.get(username)
        if not u:
            if request.is_json:
                return jsonify({"ok": False, "error": "用户不存在"}), 404
            return redirect(url_for("auth_bp.login_page", error="用户不存在，请重新登录"))
        if not check_password_hash(str(u.get("password_hash", "")), old_password):
            if request.is_json:
                return jsonify({"ok": False, "error": "当前密码错误"}), 400
            return redirect(url_for("auth_bp.account_page", error="当前密码错误"))

        u["password_hash"] = generate_password_hash(new_password, method="pbkdf2:sha256")
        users[username] = u
        user_store.write(users)
        if request.is_json:
            return jsonify({"ok": True, "message": "密码修改成功"})
        return redirect(url_for("auth_bp.account_page", info="密码修改成功"))

    @bp.post("/auth/logout")
    def auth_logout():
        session.clear()
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("auth_bp.login_page"))

    return bp
