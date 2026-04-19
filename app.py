#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import base64
import io
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from werkzeug.exceptions import HTTPException
from blueprints.auth import create_auth_blueprint
from blueprints.admin import create_admin_blueprint
from blueprints.assets import create_assets_blueprint
from services.ai_image_service import (
    analyze_reference_style,
    build_focus_constraints,
    build_mock_ai_image,
    estimate_fg_mask,
    extract_product_cutout,
    generate_image_optimize_by_prompt_via_qwen,
    generate_image_via_pollinations,
    generate_image_via_qwen,
    remove_watermark_via_qwen,
)
from services.auth_security import (
    ADMIN_RATE_LIMIT_COUNT,
    ADMIN_RATE_LIMIT_WINDOW_SEC,
    LOGIN_RATE_LIMIT_COUNT,
    LOGIN_RATE_LIMIT_WINDOW_SEC,
    PRIMARY_ADMIN_PASSWORD,
    PRIMARY_ADMIN_PASSWORD_HASH,
    PRIMARY_ADMIN_USERNAME,
    RATE_LIMITER,
    get_client_ip,
    is_primary_admin_username,
    verify_primary_admin_password,
)
from services.prompt_service import (
    PROMPT_LIBRARY,
    build_prompt,
    is_prompt_complete,
    refine_prompt_with_llm,
)
from services.asset_store import AssetStore
from services.user_store import UserStore

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
USERS_DIR = DATA_DIR / "users"
USERS_FILE = DATA_DIR / "users.json"

for d in [DATA_DIR, USERS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("PDD_MAX_CONTENT_LENGTH", str(25 * 1024 * 1024)))
app.config["JSON_AS_ASCII"] = False
app.config["SECRET_KEY"] = os.getenv("APP_SECRET_KEY", "change-this-in-production")
app.logger.setLevel(logging.INFO)
STORE_LOCK = threading.RLock()
USER_STORE = UserStore(USERS_FILE, STORE_LOCK)
ASSET_STORE = None

if not PRIMARY_ADMIN_PASSWORD_HASH and not PRIMARY_ADMIN_PASSWORD:
    app.logger.warning(
        "Primary admin password is not configured. Set PRIMARY_ADMIN_PASSWORD_HASH "
        "or PRIMARY_ADMIN_PASSWORD to enable %s login.",
        PRIMARY_ADMIN_USERNAME,
    )

@dataclass
class AssetMeta:
    asset_id: str
    filename: str
    original_name: str
    width: int
    height: int
    source: str
    content_hash: str
    folder: str
    category: str
    tags: List[str]
    created_at: str


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def rate_limited_response(message: str = "请求过于频繁，请稍后重试"):
    resp = jsonify({"ok": False, "error": message})
    resp.status_code = 429
    resp.headers["Retry-After"] = "60"
    return resp


def get_json_data() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if data is None:
        raise ValueError("请求体必须是 JSON")
    if not isinstance(data, dict):
        raise ValueError("JSON 请求体必须是对象")
    return data


def sanitize_username(name: str) -> str:
    return "".join(ch for ch in (name or "").strip() if ch.isalnum() or ch in {"_", "-", "."})


def sanitize_email(email: str) -> str:
    return str(email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    if not email:
        return False
    return re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email) is not None


def parse_expiry_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # Accept YYYY-MM-DD as end-of-day local time
    if len(raw) == 10:
        try:
            d = datetime.strptime(raw, "%Y-%m-%d")
            return d.replace(hour=23, minute=59, second=59)
        except Exception:
            return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def user_base_dir(username: str) -> Path:
    return USERS_DIR / username


def ensure_user_storage(username: str) -> None:
    root = user_base_dir(username)
    images = root / "images"
    meta = root / "assets.json"
    folders = root / "folders.json"
    images.mkdir(parents=True, exist_ok=True)
    if not meta.exists():
        meta.write_text("{}", encoding="utf-8")
    if not folders.exists():
        folders.write_text(json.dumps(["默认"], ensure_ascii=False, indent=2), encoding="utf-8")


ASSET_STORE = AssetStore(
    lock=STORE_LOCK,
    current_username_fn=lambda: current_username(),
    ensure_user_storage_fn=ensure_user_storage,
    user_base_dir_fn=user_base_dir,
)


def current_username() -> str:
    return str(session.get("username", "")).strip()


def current_user_record() -> Dict[str, Any] | None:
    username = current_username()
    if not username:
        return None
    if is_primary_admin_username(username):
        return {
            "username": PRIMARY_ADMIN_USERNAME,
            "email": "",
            "role": "admin",
            "disabled": False,
            "created_at": "built-in",
        }
    return USER_STORE.read().get(username)


def current_user_is_admin() -> bool:
    return is_primary_admin_username(current_username())


def admin_user_rows() -> List[Dict[str, Any]]:
    users_map = USER_STORE.read()
    rows: List[Dict[str, Any]] = []
    rows.append({
        "username": PRIMARY_ADMIN_USERNAME,
        "email": "(built-in)",
        "role": "admin",
        "disabled": False,
        "status": "approved",
        "expires_at": "",
        "created_at": "built-in",
        "assets_count": 0,
    })
    for uname, rec in users_map.items():
        if is_primary_admin_username(uname):
            continue
        base = user_base_dir(uname)
        assets_count = 0
        meta_path = base / "assets.json"
        if meta_path.exists():
            try:
                with meta_path.open("r", encoding="utf-8") as f:
                    m = json.load(f)
                if isinstance(m, dict):
                    assets_count = len(m)
            except Exception:
                assets_count = 0
        rows.append({
            "username": uname,
            "email": str(rec.get("email", "")).strip(),
            "role": str(rec.get("role", "user")),
            "disabled": bool(rec.get("disabled", False)),
            "status": str(rec.get("status", "pending")).strip().lower() or "pending",
            "expires_at": str(rec.get("expires_at", "")).strip(),
            "created_at": str(rec.get("created_at", "")),
            "assets_count": assets_count,
        })
    rows.sort(key=lambda x: x["created_at"], reverse=True)
    return rows


app.register_blueprint(create_admin_blueprint(
    current_user_is_admin=current_user_is_admin,
    current_username=current_username,
    admin_user_rows=admin_user_rows,
    sanitize_username=sanitize_username,
    sanitize_email=sanitize_email,
    is_valid_email=is_valid_email,
    is_primary_admin_username=is_primary_admin_username,
    primary_admin_username=PRIMARY_ADMIN_USERNAME,
    user_store=USER_STORE,
    ensure_user_storage=ensure_user_storage,
    now_iso=now_iso,
    parse_expiry_datetime=parse_expiry_datetime,
    user_base_dir=user_base_dir,
    get_json_data=get_json_data,
))

app.register_blueprint(create_auth_blueprint(
    current_username=current_username,
    current_user_record=current_user_record,
    current_user_is_admin=current_user_is_admin,
    sanitize_username=sanitize_username,
    sanitize_email=sanitize_email,
    is_valid_email=is_valid_email,
    is_primary_admin_username=is_primary_admin_username,
    verify_primary_admin_password=lambda pw: verify_primary_admin_password(pw, logger=app.logger),
    primary_admin_username=PRIMARY_ADMIN_USERNAME,
    user_store=USER_STORE,
    ensure_user_storage=ensure_user_storage,
    now_iso=now_iso,
    parse_expiry_datetime=parse_expiry_datetime,
    get_client_ip=get_client_ip,
    rate_limiter=RATE_LIMITER,
    login_rate_limit_count=LOGIN_RATE_LIMIT_COUNT,
    login_rate_limit_window_sec=LOGIN_RATE_LIMIT_WINDOW_SEC,
    rate_limited_response=rate_limited_response,
))

def current_store_paths() -> Dict[str, Path]:
    return ASSET_STORE.current_paths()


def read_meta() -> Dict[str, Dict[str, Any]]:
    return ASSET_STORE.read_meta()


def write_meta(meta: Dict[str, Dict[str, Any]]) -> None:
    ASSET_STORE.write_meta(meta)


def read_folders() -> List[str]:
    return ASSET_STORE.read_folders()


def write_folders(folders: List[str]) -> None:
    ASSET_STORE.write_folders(folders)


def classify_image(img: Image.Image) -> Tuple[str, List[str]]:
    w, h = img.size
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    brightness = float(arr.mean())
    saturation = float(np.std(arr, axis=2).mean())

    tags: List[str] = []
    if w / h > 1.2:
        layout = "banner"
        tags.append("横版")
    elif h / w > 1.2:
        layout = "poster"
        tags.append("竖版")
    else:
        layout = "square"
        tags.append("方图")
    tags.append("亮色" if brightness > 160 else "暗色")
    tags.append("高色彩" if saturation > 32 else "低色彩")
    return layout, tags


def _sanitize_upload_name(name: str) -> str:
    return ASSET_STORE.sanitize_upload_name(name)


def _unique_filename(base_name: str, fallback: str = "image.png") -> str:
    return ASSET_STORE.unique_filename(base_name, fallback=fallback)


def _image_content_hash(img: Image.Image) -> str:
    rgb = img.convert("RGB")
    w, h = rgb.size
    data = rgb.tobytes()
    hobj = hashlib.sha256()
    hobj.update(f"{w}x{h}|RGB|".encode("utf-8"))
    hobj.update(data)
    return hobj.hexdigest()


def save_asset(
    img: Image.Image,
    source: str,
    asset_id: str | None = None,
    original_name: str | None = None,
    preserve_filename: bool = False,
    auto_tags: bool = False,
) -> Dict[str, Any]:
    with STORE_LOCK:
        images_dir = current_store_paths()["images"]
        meta = read_meta()
        new_hash = _image_content_hash(img)

        # De-duplicate by exact image content hash.
        # If an identical image already exists, reuse it instead of creating a new asset.
        meta_touched = False
        for _, item in meta.items():
            existing_hash = str(item.get("content_hash", "")).strip()
            if not existing_hash:
                p = images_dir / str(item.get("filename", ""))
                if p.exists():
                    try:
                        existing_hash = _image_content_hash(Image.open(p).convert("RGB"))
                        item["content_hash"] = existing_hash
                        meta_touched = True
                    except Exception:
                        existing_hash = ""
            if existing_hash and existing_hash == new_hash:
                if meta_touched:
                    write_meta(meta)
                return item

        if meta_touched:
            write_meta(meta)

        aid = asset_id or str(uuid.uuid4())[:10]
        filename = _unique_filename(original_name or "", fallback=f"{aid}.png") if preserve_filename else f"{aid}.png"
        out = images_dir / filename

        ext = out.suffix.lower()
        save_img = img
        if ext in {".jpg", ".jpeg"} and save_img.mode not in {"RGB", "L"}:
            save_img = save_img.convert("RGB")
        save_img.save(out)
        display_name = filename if preserve_filename else (original_name or filename)

        category, inferred_tags = classify_image(img)
        tags = inferred_tags if auto_tags else []
        item = AssetMeta(
            asset_id=aid,
            filename=filename,
            original_name=display_name,
            width=img.width,
            height=img.height,
            source=source,
            content_hash=new_hash,
            folder="默认",
            category=category,
            tags=tags,
            created_at=now_iso(),
        )
        meta[aid] = asdict(item)
        write_meta(meta)
        return meta[aid]


def get_asset(asset_id: str) -> Dict[str, Any]:
    meta = read_meta()
    item = meta.get(asset_id)
    if not item:
        raise KeyError(f"素材不存在: {asset_id}")
    return item


def derive_output_name(base_name: str, suffix: str) -> str:
    raw = _sanitize_upload_name(base_name) or "image.png"
    p = Path(raw)
    stem = p.stem or "image"
    ext = p.suffix.lower() or ".png"
    return f"{stem}-{suffix}{ext}"


def derive_output_name_from_asset(asset_id: str, suffix: str) -> str:
    item = get_asset(asset_id)
    base_name = str(item.get("original_name") or item.get("filename") or "image.png")
    return derive_output_name(base_name, suffix)


def get_asset_path(asset_id: str) -> Path:
    images_dir = current_store_paths()["images"]
    item = get_asset(asset_id)
    p = images_dir / item["filename"]
    if not p.exists():
        raise FileNotFoundError(f"素材文件不存在: {p}")
    return p


def load_image(asset_id: str) -> Image.Image:
    return Image.open(get_asset_path(asset_id)).convert("RGB")


def delete_assets(asset_ids: List[str]) -> Dict[str, Any]:
    with STORE_LOCK:
        images_dir = current_store_paths()["images"]
        meta = read_meta()
        deleted: List[str] = []
        not_found: List[str] = []
        for aid in asset_ids:
            aid = str(aid).strip()
            if not aid:
                continue
            item = meta.get(aid)
            if not item:
                not_found.append(aid)
                continue
            p = images_dir / item["filename"]
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
            meta.pop(aid, None)
            deleted.append(aid)
        write_meta(meta)
        return {"deleted": deleted, "not_found": not_found}


def rename_asset(asset_id: str, new_name: str) -> Dict[str, Any]:
    with STORE_LOCK:
        images_dir = current_store_paths()["images"]
        meta = read_meta()
        item = meta.get(asset_id)
        if not item:
            raise KeyError(f"素材不存在: {asset_id}")
        raw = _sanitize_upload_name(new_name)
        if not raw:
            raise ValueError("新名称不能为空")

        current_filename = str(item.get("filename", ""))
        current_suffix = Path(current_filename).suffix.lower() or ".png"
        desired = Path(raw)
        desired_stem = desired.stem.strip() or "image"
        desired_suffix = desired.suffix.lower() if desired.suffix else ""
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        if desired_suffix not in allowed:
            desired_suffix = current_suffix if current_suffix in allowed else ".png"
        candidate = f"{desired_stem}{desired_suffix}"

        old_path = images_dir / current_filename
        if not old_path.exists():
            raise FileNotFoundError(f"素材文件不存在: {old_path}")

        if candidate != current_filename:
            i = 1
            final_name = candidate
            while (images_dir / final_name).exists():
                final_name = f"{desired_stem}_{i}{desired_suffix}"
                i += 1
            old_path.rename(images_dir / final_name)
        else:
            final_name = current_filename

        item["filename"] = final_name
        item["original_name"] = final_name
        meta[asset_id] = item
        write_meta(meta)
        item["url"] = f"/file/images/{final_name}"
        return item


def ensure_meta_defaults(item: Dict[str, Any]) -> bool:
    changed = False
    if "folder" not in item or not str(item.get("folder", "")).strip():
        item["folder"] = "默认"
        changed = True
    tags = item.get("tags", [])
    if not isinstance(tags, list):
        item["tags"] = []
        changed = True
    return changed


app.register_blueprint(create_assets_blueprint(
    data_dir=DATA_DIR,
    current_store_paths=current_store_paths,
    get_json_data=get_json_data,
    read_meta=read_meta,
    write_meta=write_meta,
    ensure_meta_defaults=ensure_meta_defaults,
    read_folders=read_folders,
    write_folders=write_folders,
    save_asset=save_asset,
    delete_assets=delete_assets,
    rename_asset=rename_asset,
))


def text_position_by_size(width: int, height: int) -> str:
    if width > height:
        return "右侧留白区"
    if height > width:
        return "顶部留白区"
    return "右上留白区"


def resolve_text_anchor(width: int, height: int) -> Tuple[int, int]:
    pos = text_position_by_size(width, height)
    if pos == "右侧留白区":
        return int(width * 0.72), int(height * 0.12)
    if pos == "顶部留白区":
        return int(width * 0.12), int(height * 0.08)
    return int(width * 0.64), int(height * 0.08)


def load_chinese_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_required_text(img: Image.Image, text: str, width: int, height: int) -> Image.Image:
    if not text:
        return img
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    x, y = resolve_text_anchor(width, height)

    # Safe drawing box (avoid clipping at right/bottom edge)
    max_w = int(width * 0.24) if width > height else int(width * 0.72)
    max_h = int(height * 0.2)
    max_w = max(100, min(max_w, width - x - 12))
    max_h = max(48, min(max_h, height - y - 12))

    # Find largest font that fits, fallback to wrapping if needed.
    fs = max(16, int(min(width, height) * 0.06))
    font = load_chinese_font(fs)

    def text_size(s: str, f: ImageFont.ImageFont) -> Tuple[int, int]:
        b = draw.textbbox((0, 0), s, font=f)
        return b[2] - b[0], b[3] - b[1]

    # shrink single-line font until width fits
    w0, h0 = text_size(text, font)
    while (w0 > max_w or h0 > max_h) and fs > 14:
        fs -= 1
        font = load_chinese_font(fs)
        w0, h0 = text_size(text, font)

    lines: List[str] = [text]
    if w0 > max_w:
        # simple CJK-friendly wrapping by character count
        line_chars = max(2, int(len(text) * (max_w / max(1, w0))))
        lines = []
        i = 0
        while i < len(text):
            lines.append(text[i:i + line_chars])
            i += line_chars
        # reduce font until all lines fit height
        while fs > 14:
            font = load_chinese_font(fs)
            line_h = text_size("测", font)[1] + 4
            if line_h * len(lines) <= max_h:
                break
            fs -= 1

    # draw text with halo
    line_h = text_size("测", font)[1] + 4
    ty = y
    for ln in lines:
        for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
            draw.text((x + dx, ty + dy), ln, font=font, fill=(255, 255, 255))
        draw.text((x, ty), ln, font=font, fill=(26, 36, 48))
        ty += line_h
    return out


def _replace_background_with_prompt_local_image(img: Image.Image, bg_prompt: str) -> Image.Image:
    rgb = np.asarray(img, dtype=np.uint8)
    fg = estimate_fg_mask(img).astype(np.float32)[..., None]

    w, h = img.size
    bg = Image.new("RGB", (w, h), "#eef2f6")
    d = ImageDraw.Draw(bg)
    low = np.array([245, 246, 248], dtype=np.float32)
    high = np.array([125, 168, 220], dtype=np.float32)
    p = bg_prompt.lower()
    if "green" in p or "绿" in bg_prompt:
        low = np.array([214, 243, 225], dtype=np.float32)
        high = np.array([79, 170, 112], dtype=np.float32)
    if "dark" in p or "深" in bg_prompt:
        low = np.array([58, 73, 89], dtype=np.float32)
        high = np.array([22, 31, 45], dtype=np.float32)
    for y in range(h):
        c = (low + (high - low) * (y / max(1, h - 1))).astype(np.uint8)
        d.line([(0, y), (w, y)], fill=(int(c[0]), int(c[1]), int(c[2])))

    bg_arr = np.asarray(bg, dtype=np.uint8)
    out = (rgb.astype(np.float32) * fg + bg_arr.astype(np.float32) * (1.0 - fg)).astype(np.uint8)
    return Image.fromarray(out)


def _replace_background_with_prompt_qwen_preserve_subject(img: Image.Image, bg_prompt: str) -> Image.Image:
    """
    First-version behavior:
    use direct Qwen image edit to replace background.
    """
    w, h = img.size
    edit_prompt = (
        "任务：仅替换背景，不修改商品主体。"
        f"背景要求：{bg_prompt}。"
        "硬性要求："
        "1) 商品主体保持一致：形状、颜色、材质、纹理、品牌文字与logo位置不变；"
        "2) 主体位置、大小、朝向不变，不裁切主体；"
        "3) 仅背景发生变化，背景需简洁、自然、有层次，适配电商主图；"
        "4) 光照方向与主体一致，避免主体边缘光影冲突；"
        "5) 禁止新增任何文字、英文、logo、水印、人物、额外商品、复杂道具；"
        f"6) 输出分辨率：{w}x{h}。"
        "输出一张可直接用于商品展示的背景替换图。"
    )
    edited = generate_image_optimize_by_prompt_via_qwen(
        source_img=img,
        user_prompt=edit_prompt,
        out_w=w,
        out_h=h,
        strength=0.65,
        variation_directive="",
    )
    return edited.convert("RGB")


def replace_background_with_prompt_image(
    img: Image.Image,
    bg_prompt: str,
    source_ref: str,
    output_name: str | None = None,
) -> Dict[str, Any]:
    try:
        out_img = _replace_background_with_prompt_qwen_preserve_subject(img, bg_prompt)
        out_meta = save_asset(
            out_img,
            source=f"bg_replace_qwen:{source_ref}",
            original_name=output_name,
            preserve_filename=bool(output_name),
        )
        out_meta["bg_engine"] = "qwen"
        out_meta["subject_preserve"] = "model_constrained"
    except Exception:
        out_img = _replace_background_with_prompt_local_image(img, bg_prompt)
        out_meta = save_asset(
            out_img,
            source=f"bg_replace_qwen_fallback_local:{source_ref}",
            original_name=output_name,
            preserve_filename=bool(output_name),
        )
        out_meta["bg_engine"] = "local_fallback"

    out_meta["url"] = f"/file/images/{out_meta['filename']}"
    return out_meta


def replace_background_with_prompt(asset_id: str, bg_prompt: str) -> Dict[str, Any]:
    img = load_image(asset_id)
    output_name = derive_output_name_from_asset(asset_id, "换背景")
    return replace_background_with_prompt_image(img, bg_prompt, source_ref=asset_id, output_name=output_name)


def build_variation_directive(index: int, total: int) -> Tuple[str, str]:
    presets = [
        "主体居中偏上，背景轻微景深，柔和侧光，阴影更干净",
        "主体居中偏下，背景层次增强，顶部补光，整体更通透",
        "主体三分位构图，背景渐变更明显，边缘反差略增强",
        "主体正中构图，背景纹理极简，冷暖对比轻微增强",
        "主体略放大，背景虚化更强，光线方向从左上到右下",
        "主体略缩小留白更多，背景纯净，光线更均匀柔和",
        "主体保持不变，背景加入轻微环境反射感，阴影更自然",
        "主体保持不变，背景明度略升，材质细节对比略增强",
    ]
    slot = index % len(presets)
    variation_id = f"v{index + 1:02d}-of-{total:02d}"
    # Keep a lightweight unique tag so model is less likely to collapse to identical outputs.
    nonce = random.randint(1000, 9999)
    directive = f"{presets[slot]}；变体标识 {variation_id}-{nonce}"
    return variation_id, directive


def remove_watermark_local(img: Image.Image, strength: int = 18) -> Image.Image:
    # 低成本去水印策略：针对四角区域做平滑和颜色补全（可替换为第三方 API）
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w, _ = arr.shape
    out = arr.copy()
    rw = max(36, int(w * 0.18))
    rh = max(36, int(h * 0.15))

    corners = [
        (0, 0, rw, rh),
        (w - rw, 0, w, rh),
        (0, h - rh, rw, h),
        (w - rw, h - rh, w, h),
    ]

    for x1, y1, x2, y2 in corners:
        patch = Image.fromarray(out[y1:y2, x1:x2]).filter(ImageFilter.GaussianBlur(radius=max(1, strength / 8)))
        out[y1:y2, x1:x2] = np.asarray(patch, dtype=np.uint8)

    return Image.fromarray(out, mode="RGB")


@app.before_request
def enforce_auth():
    open_paths = {
        "/login",
        "/register",
        "/auth/login",
        "/auth/register",
        "/healthz",
        "/file/logo",
    }
    if request.path in open_paths or request.path.startswith("/static/"):
        return None
    if request.path.startswith("/api/admin/"):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            ip = get_client_ip(request)
            bucket = f"admin_api:{ip}"
            if not RATE_LIMITER.check(bucket, ADMIN_RATE_LIMIT_COUNT, ADMIN_RATE_LIMIT_WINDOW_SEC):
                return rate_limited_response("管理员操作过于频繁，请稍后重试")
    if current_username():
        if request.path == "/wait-approval":
            return None
        if request.path == "/expired":
            return None
        username = current_username()
        if not is_primary_admin_username(username):
            users = USER_STORE.read()
            rec = users.get(username)
            if not rec:
                session.clear()
                if request.path.startswith("/api/") or request.path.startswith("/file/images/"):
                    return jsonify({"ok": False, "error": "登录状态已失效，请重新登录"}), 401
                return redirect(url_for("auth_bp.login_page", error="登录状态已失效，请重新登录"))
            status = str(rec.get("status", "pending")).strip().lower() or "pending"
            expires_at = str(rec.get("expires_at", "")).strip()
            allowed_when_unapproved = {"/auth/logout", "/account", "/auth/change-password"}
            if status != "approved" and request.path not in allowed_when_unapproved:
                if request.path.startswith("/api/") or request.path.startswith("/file/images/"):
                    return jsonify({"ok": False, "error": "账号未授权，请等待管理员审核"}), 403
                return redirect(url_for("auth_bp.wait_approval_page"))
            if status == "approved":
                exp_dt = parse_expiry_datetime(expires_at)
                if exp_dt and datetime.now() > exp_dt and request.path not in allowed_when_unapproved:
                    if request.path.startswith("/api/") or request.path.startswith("/file/images/"):
                        return jsonify({"ok": False, "error": "账号使用期限已到期，请联系管理员续期"}), 403
                    return redirect(url_for("auth_bp.expired_page"))
        if request.path.startswith("/admin") or request.path.startswith("/api/admin/"):
            if not current_user_is_admin():
                if request.path.startswith("/api/admin/"):
                    return jsonify({"ok": False, "error": "无管理员权限"}), 403
                return redirect(url_for("index"))
        return None
    if request.path.startswith("/api/") or request.path.startswith("/file/images/"):
        return jsonify({"ok": False, "error": "未登录"}), 401
    return redirect(url_for("auth_bp.login_page"))


@app.errorhandler(Exception)
def handle_exception(err: Exception):
    if isinstance(err, HTTPException):
        code = int(err.code or 500)
        # Keep explicit client-facing messages for 4xx, hide internals for 5xx.
        message = str(err.description or "请求失败") if code < 500 else "服务器内部错误"
        return jsonify({"ok": False, "error": message}), code

    if isinstance(err, KeyError):
        return jsonify({"ok": False, "error": str(err)}), 404
    if isinstance(err, ValueError):
        return jsonify({"ok": False, "error": str(err)}), 400

    app.logger.exception("Unhandled server error: %s", err)
    return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@app.route("/")
def index():
    if not current_username():
        return redirect(url_for("auth_bp.login_page"))
    return render_template("index.html")


@app.context_processor
def inject_user_context():
    return {
        "current_username": current_username(),
        "is_admin": current_user_is_admin(),
    }


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "status": "healthy", "time": now_iso()})


@app.get("/api/meta")
def api_meta():
    return jsonify({
        "ok": True,
        "providers": {
            "image_generation": ["qwen", "pollinations", "mock"],
            "remove_watermark": ["qwen", "local", "external_api"],
            "bg_replace": ["qwen", "local"],
        },
        "notes": "图像处理默认优先走 Qwen，失败时回退本地低成本兜底；并预留第三方 API 对接位",
    })


@app.post("/api/prompt/build")
def api_prompt_build():
    data = get_json_data()
    product_name = str(data.get("product_name", "商品"))
    primary_selling = str(data.get("primary_selling", ""))
    secondary_selling = str(data.get("secondary_selling", ""))
    other_selling = str(data.get("other_selling", ""))
    resolution = str(data.get("resolution", "1024x1024"))
    display_text = str(data.get("display_text", "")).strip()
    mode = str(data.get("mode", "template")).strip().lower()

    base_prompt = build_prompt(
        product_name=product_name,
        primary_selling=primary_selling,
        secondary_selling=secondary_selling,
        other_selling=other_selling,
        resolution=resolution,
        display_text=display_text,
    )

    if mode == "hybrid":
        try:
            prompt, model = refine_prompt_with_llm(
                base_prompt=base_prompt,
                product_name=product_name,
                primary_selling=primary_selling,
                secondary_selling=secondary_selling,
                other_selling=other_selling,
                resolution=resolution,
                display_text=display_text,
            )
            if resolution not in prompt:
                prompt = f"目标分辨率:{resolution}，按该画幅设计构图与留白，{prompt}".strip()
            if not is_prompt_complete(prompt, resolution):
                return jsonify({
                    "ok": True,
                    "mode": "template_fallback",
                    "prompt": base_prompt,
                    "base_prompt": base_prompt,
                    "warning": "LLM结果过短或不完整，已自动回退完整模板提示词。",
                    "library": PROMPT_LIBRARY,
                })
            return jsonify({
                "ok": True,
                "mode": "hybrid",
                "prompt": prompt,
                "base_prompt": base_prompt,
                "llm_model": model,
                "library": PROMPT_LIBRARY,
            })
        except Exception as e:
            # Fallback to template so workflow never blocks
            msg = str(e)
            if "429" in msg:
                msg = "OpenRouter免费模型限流，已自动回退模板模式。"
            elif "未返回可用文本" in msg:
                msg = "OpenRouter未返回有效结果，已自动回退模板模式。"
            else:
                msg = "LLM暂时不可用，已自动回退模板模式。"
            return jsonify({
                "ok": True,
                "mode": "template_fallback",
                "prompt": base_prompt,
                "base_prompt": base_prompt,
                "warning": msg,
                "library": PROMPT_LIBRARY,
            })

    prompt = base_prompt
    return jsonify({"ok": True, "mode": "template", "prompt": prompt, "library": PROMPT_LIBRARY})


@app.post("/api/ai/generate")
def api_ai_generate():
    data = get_json_data()
    prompt = str(data.get("prompt", "")).strip()
    provider = str(data.get("provider", "qwen")).strip()
    reference_asset_id = str(data.get("reference_asset_id", "")).strip()
    display_text = str(data.get("display_text", "")).strip()
    seed = data.get("seed", None)
    width = min(max(int(data.get("width", 1024)), 512), 1536)
    height = min(max(int(data.get("height", 1024)), 512), 1536)

    if not prompt:
        raise ValueError("prompt 不能为空")

    final_prompt = prompt
    ref_note = None
    if reference_asset_id:
        ref_note = analyze_reference_style(load_image(reference_asset_id))
        final_prompt = f"{prompt}。{ref_note}"
    focus_note = build_focus_constraints(width, height)
    text_pos = text_position_by_size(width, height)
    if display_text:
        text_note = (
            f"画面中仅展示这段文字：{display_text}。"
            f"文字固定放在{text_pos}，清晰可读，字号适中，不遮挡产品主体。"
        )
    else:
        text_note = "画面中不要出现任何文字、数字、字母、logo、水印、品牌标识。"
    final_prompt = (
        f"{final_prompt}。{focus_note}。"
        f"{text_note}。"
        "负向约束：不要多人/多产品堆叠，不要复杂场景，不要强干扰背景。"
    )

    if provider == "qwen":
        img = generate_image_via_qwen(final_prompt, width, height)
        source = "qwen"
        seed_val = None
    elif provider == "pollinations":
        try:
            seed_val = int(seed) if seed is not None else random.randint(1, 2_147_483_000)
            img = generate_image_via_pollinations(final_prompt, width, height, seed=seed_val)
            source = "pollinations"
        except Exception:
            img = build_mock_ai_image(final_prompt, (width, height))
            source = "mock-fallback"
            seed_val = None
    else:
        img = build_mock_ai_image(final_prompt, (width, height))
        source = "mock"
        seed_val = None

    # Ensure user-required text always appears in final image.
    if display_text:
        img = render_required_text(img, display_text, width, height)

    item = save_asset(
        img,
        source=f"ai_generate:{source}",
        original_name="AI图片生成-生成.png",
        preserve_filename=True,
    )
    item["url"] = f"/file/images/{item['filename']}"
    return jsonify({
        "ok": True,
        "item": item,
        "used_prompt": final_prompt,
        "seed": seed_val,
        "reference_asset_id": reference_asset_id or None,
        "reference_note": ref_note,
    })


@app.post("/api/ai/optimize-by-prompt")
def api_ai_optimize_by_prompt():
    data = get_json_data()
    asset_id = str(data.get("asset_id", "")).strip()
    prompt = str(data.get("prompt", "")).strip()
    strength = float(data.get("strength", 0.65))
    num_images = int(data.get("num_images", 1))
    width = int(data.get("width", 1024))
    height = int(data.get("height", 1024))

    if not asset_id:
        raise ValueError("asset_id 不能为空")
    if not prompt:
        raise ValueError("prompt 不能为空")

    width = min(max(width, 512), 1536)
    height = min(max(height, 512), 1536)
    num_images = min(max(num_images, 1), 6)
    strength = float(np.clip(strength, 0.0, 1.0))
    src_img = load_image(asset_id)
    items: List[Dict[str, Any]] = []
    variations: List[Dict[str, Any]] = []
    for i in range(num_images):
        variation_id, variation_directive = build_variation_directive(i, num_images)
        out_img = generate_image_optimize_by_prompt_via_qwen(
            source_img=src_img,
            user_prompt=prompt,
            out_w=width,
            out_h=height,
            strength=strength,
            variation_directive=variation_directive,
        )
        item = save_asset(
            out_img,
            source=f"qwen_opt_prompt:{asset_id}",
            original_name=derive_output_name_from_asset(asset_id, "优化"),
            preserve_filename=True,
        )
        item["url"] = f"/file/images/{item['filename']}"
        item["variation_id"] = variation_id
        items.append(item)
        variations.append({"variation_id": variation_id, "directive": variation_directive})
        # Slight pacing between requests to reduce provider throttling on batch generation.
        if i < num_images - 1:
            time.sleep(0.5)
    return jsonify({
        "ok": True,
        "item": items[0],
        "items": items,
        "total": len(items),
        "asset_id": asset_id,
        "used_prompt": prompt,
        "strength": strength,
        "num_images": num_images,
        "size": f"{width}x{height}",
        "variations": variations,
    })


@app.post("/api/layer/cutout-preview")
def api_layer_cutout_preview():
    data = get_json_data()
    asset_id = str(data.get("asset_id", "")).strip()
    image_base64 = str(data.get("image_base64", "")).strip()
    if image_base64:
        b64 = image_base64
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64)
            src_img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            raise ValueError("image_base64 无效")
    else:
        if not asset_id:
            raise ValueError("asset_id 不能为空")
        src_img = load_image(asset_id)
    box = data.get("box")
    use_box = isinstance(box, dict)
    if use_box:
        try:
            x1 = int(float(box.get("x1", 0)))
            y1 = int(float(box.get("y1", 0)))
            x2 = int(float(box.get("x2", 0)))
            y2 = int(float(box.get("y2", 0)))
        except Exception:
            raise ValueError("box 坐标格式错误")
        w, h = src_img.size
        x1 = max(0, min(x1, w - 1))
        x2 = max(1, min(x2, w))
        y1 = max(0, min(y1, h - 1))
        y2 = max(1, min(y2, h))
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box 坐标无效")
        cropped = src_img.crop((x1, y1, x2, y2))
        cutout_rgba, local_bbox = extract_product_cutout(cropped)
        bbox = (
            x1 + int(local_bbox[0]),
            y1 + int(local_bbox[1]),
            x1 + int(local_bbox[2]),
            y1 + int(local_bbox[3]),
        )
    else:
        cutout_rgba, bbox = extract_product_cutout(src_img)
    buf = io.BytesIO()
    cutout_rgba.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return jsonify({
        "ok": True,
        "asset_id": asset_id,
        "from_box": use_box,
        "image_data_url": f"data:image/png;base64,{b64}",
        "bbox": {
            "x1": int(bbox[0]),
            "y1": int(bbox[1]),
            "x2": int(bbox[2]),
            "y2": int(bbox[3]),
        },
        "size": {"width": int(cutout_rgba.width), "height": int(cutout_rgba.height)},
    })


@app.post("/api/ai/batch-process")
def api_ai_batch_process():
    data = get_json_data()
    asset_ids = data.get("asset_ids", [])
    do_remove_watermark = bool(data.get("do_remove_watermark", True))
    do_replace_bg = bool(data.get("do_replace_bg", True))
    bg_prompt = str(data.get("bg_prompt", "简洁电商背景")).strip()
    wm_provider = str(data.get("wm_provider", "local")).strip()

    if not isinstance(asset_ids, list) or not asset_ids:
        raise ValueError("asset_ids 不能为空")

    out: List[Dict[str, Any]] = []
    for aid in asset_ids:
        aid = str(aid).strip()
        img = load_image(aid)
        source_steps = []
        suffix_parts: List[str] = []

        if do_remove_watermark:
            if wm_provider == "qwen":
                try:
                    img = remove_watermark_via_qwen(img, strength=0.35)
                    source_steps.append("wm:qwen")
                    suffix_parts.append("去水印")
                except Exception:
                    img = remove_watermark_local(img)
                    source_steps.append("wm:qwen_fallback_local")
                    suffix_parts.append("去水印")
            elif wm_provider == "external_api":
                # 预留位：外部去水印 API 对接
                # 当前未配置第三方凭证时回退 local
                img = remove_watermark_local(img)
                source_steps.append("wm:external_api_fallback_local")
                suffix_parts.append("去水印")
            else:
                img = remove_watermark_local(img)
                source_steps.append("wm:local")
                suffix_parts.append("去水印")

        if do_replace_bg:
            suffix_parts.append("换背景")
            item = replace_background_with_prompt_image(
                img,
                bg_prompt,
                source_ref=aid,
                output_name=derive_output_name_from_asset(aid, "-".join(suffix_parts) or "处理"),
            )
            source_steps.append(f"bg:{item.get('bg_engine', 'qwen')}")
        else:
            item = save_asset(
                img,
                source=f"batch:{aid}|{'|'.join(source_steps)}",
                original_name=derive_output_name_from_asset(aid, "-".join(suffix_parts) or "处理"),
                preserve_filename=True,
            )
            item["url"] = f"/file/images/{item['filename']}"

        item["pipeline"] = source_steps
        out.append(item)

    return jsonify({"ok": True, "total": len(out), "items": out})


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8090"))
    debug = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
