from __future__ import annotations

import base64
import io
from typing import Any, Callable, Dict, List

from flask import Blueprint, jsonify, request, send_from_directory
from PIL import Image


def create_assets_blueprint(
    *,
    data_dir,
    current_store_paths: Callable[[], Dict[str, Any]],
    get_json_data: Callable[[], Dict[str, Any]],
    read_meta: Callable[[], Dict[str, Dict[str, Any]]],
    write_meta: Callable[[Dict[str, Dict[str, Any]]], None],
    ensure_meta_defaults: Callable[[Dict[str, Any]], bool],
    read_folders: Callable[[], List[str]],
    write_folders: Callable[[List[str]], None],
    save_asset: Callable[..., Dict[str, Any]],
    delete_assets: Callable[[List[str]], Dict[str, Any]],
    rename_asset: Callable[[str, str], Dict[str, Any]],
):
    bp = Blueprint("assets_bp", __name__)

    @bp.route("/file/images/<filename>")
    def file_image(filename: str):
        images_dir = current_store_paths()["images"]
        return send_from_directory(images_dir, filename)

    @bp.route("/file/logo")
    def file_logo():
        return send_from_directory(data_dir, "logo.png")

    @bp.get("/api/assets")
    def api_assets():
        meta = read_meta()
        meta_changed = False
        for _, it in meta.items():
            if ensure_meta_defaults(it):
                meta_changed = True
        if meta_changed:
            write_meta(meta)
        items = list(meta.values())
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        for x in items:
            x["url"] = f"/file/images/{x['filename']}"
        return jsonify({"ok": True, "total": len(items), "items": items})

    @bp.get("/api/folders")
    def api_folders():
        return jsonify({"ok": True, "folders": read_folders()})

    @bp.post("/api/folders/create")
    def api_folders_create():
        data = get_json_data()
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("文件夹名称不能为空")
        folders = read_folders()
        if name not in folders:
            folders.append(name)
            write_folders(folders)
        return jsonify({"ok": True, "folders": folders, "created": name})

    @bp.post("/api/assets/move-folder")
    def api_assets_move_folder():
        data = get_json_data()
        asset_ids = data.get("asset_ids", [])
        if isinstance(asset_ids, str):
            asset_ids = [asset_ids]
        folder = str(data.get("folder", "")).strip()
        if not folder:
            raise ValueError("folder 不能为空")
        if not isinstance(asset_ids, list) or not asset_ids:
            raise ValueError("asset_ids 不能为空")

        meta = read_meta()
        folders = read_folders()
        if folder not in folders:
            folders.append(folder)
            write_folders(folders)

        moved: List[str] = []
        for aid in asset_ids:
            key = str(aid).strip()
            item = meta.get(key)
            if not item:
                continue
            item["folder"] = folder
            moved.append(key)
        write_meta(meta)
        return jsonify({"ok": True, "moved": moved, "folder": folder, "total_moved": len(moved)})

    @bp.post("/api/assets/set-tags")
    def api_assets_set_tags():
        data = get_json_data()
        asset_ids = data.get("asset_ids", [])
        if isinstance(asset_ids, str):
            asset_ids = [asset_ids]
        tags_in = data.get("tags", [])
        mode = str(data.get("mode", "replace")).strip().lower()
        if isinstance(tags_in, str):
            tags = [x.strip() for x in tags_in.replace("，", ",").split(",") if x.strip()]
        elif isinstance(tags_in, list):
            tags = [str(x).strip() for x in tags_in if str(x).strip()]
        else:
            tags = []
        if not isinstance(asset_ids, list) or not asset_ids:
            raise ValueError("asset_ids 不能为空")

        meta = read_meta()
        updated: List[str] = []
        for aid in asset_ids:
            key = str(aid).strip()
            item = meta.get(key)
            if not item:
                continue
            old_tags = item.get("tags", [])
            if not isinstance(old_tags, list):
                old_tags = []
            if mode == "add":
                merged: List[str] = []
                for t in old_tags + tags:
                    if t and t not in merged:
                        merged.append(t)
                item["tags"] = merged
            else:
                item["tags"] = tags
            updated.append(key)
        write_meta(meta)
        return jsonify({"ok": True, "updated": updated, "total_updated": len(updated)})

    @bp.post("/api/assets/upload-batch")
    def api_assets_upload_batch():
        files = request.files.getlist("files")
        if not files:
            raise ValueError("缺少文件")
        items: List[Dict[str, Any]] = []
        for f in files:
            if not f or not getattr(f, "filename", ""):
                continue
            img = Image.open(f.stream)
            item = save_asset(
                img,
                source="upload_batch",
                original_name=f.filename or "",
                preserve_filename=True,
                auto_tags=False,
            )
            item["url"] = f"/file/images/{item['filename']}"
            items.append(item)
        if not items:
            raise ValueError("没有可用文件")
        return jsonify({"ok": True, "total": len(items), "items": items})

    @bp.post("/api/assets/delete")
    def api_assets_delete():
        data = get_json_data()
        asset_ids = data.get("asset_ids", [])
        if isinstance(asset_ids, str):
            asset_ids = [asset_ids]
        if not isinstance(asset_ids, list) or not asset_ids:
            raise ValueError("asset_ids 不能为空")
        result = delete_assets([str(x) for x in asset_ids])
        return jsonify({"ok": True, **result, "total_deleted": len(result["deleted"])})

    @bp.post("/api/assets/rename")
    def api_assets_rename():
        data = get_json_data()
        asset_id = str(data.get("asset_id", "")).strip()
        new_name = str(data.get("new_name", "")).strip()
        if not asset_id:
            raise ValueError("asset_id 不能为空")
        if not new_name:
            raise ValueError("new_name 不能为空")
        item = rename_asset(asset_id, new_name)
        return jsonify({"ok": True, "item": item})

    @bp.post("/api/assets/upload-base64")
    def api_assets_upload_base64():
        data = get_json_data()
        b64 = str(data.get("image_base64", "")).strip()
        original_name = str(data.get("original_name", "")).strip()
        if not b64:
            raise ValueError("image_base64 不能为空")
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        item = save_asset(
            img,
            source="canvas_export",
            original_name=original_name or None,
            preserve_filename=bool(original_name),
        )
        item["url"] = f"/file/images/{item['filename']}"
        return jsonify({"ok": True, "item": item})

    return bp
