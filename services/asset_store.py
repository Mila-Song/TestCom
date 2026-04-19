from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List


class AssetStore:
    def __init__(
        self,
        lock,
        current_username_fn: Callable[[], str],
        ensure_user_storage_fn: Callable[[str], None],
        user_base_dir_fn: Callable[[str], Path],
    ) -> None:
        self.lock = lock
        self.current_username_fn = current_username_fn
        self.ensure_user_storage_fn = ensure_user_storage_fn
        self.user_base_dir_fn = user_base_dir_fn

    def current_paths(self) -> Dict[str, Path]:
        username = self.current_username_fn()
        if not username:
            raise PermissionError("未登录")
        self.ensure_user_storage_fn(username)
        root = self.user_base_dir_fn(username)
        return {
            "root": root,
            "images": root / "images",
            "meta": root / "assets.json",
            "folders": root / "folders.json",
        }

    def read_meta(self) -> Dict[str, Dict[str, Any]]:
        with self.lock:
            meta_file = self.current_paths()["meta"]
            if not meta_file.exists():
                return {}
            with meta_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}

    def write_meta(self, meta: Dict[str, Dict[str, Any]]) -> None:
        with self.lock:
            meta_file = self.current_paths()["meta"]
            with meta_file.open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

    def read_folders(self) -> List[str]:
        with self.lock:
            folders_file = self.current_paths()["folders"]
            if not folders_file.exists():
                return ["默认"]
            with folders_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return ["默认"]
            out = [str(x).strip() for x in data if str(x).strip()]
            if "默认" not in out:
                out.insert(0, "默认")
            return out or ["默认"]

    def write_folders(self, folders: List[str]) -> None:
        with self.lock:
            uniq: List[str] = []
            for x in folders:
                s = str(x).strip()
                if s and s not in uniq:
                    uniq.append(s)
            if "默认" not in uniq:
                uniq.insert(0, "默认")
            folders_file = self.current_paths()["folders"]
            with folders_file.open("w", encoding="utf-8") as f:
                json.dump(uniq, f, ensure_ascii=False, indent=2)

    @staticmethod
    def sanitize_upload_name(name: str) -> str:
        base = Path(name or "").name.strip().replace("\x00", "")
        if not base:
            return ""
        return base.replace("/", "_").replace("\\", "_")

    def unique_filename(self, base_name: str, fallback: str = "image.png") -> str:
        images_dir = self.current_paths()["images"]
        candidate = self.sanitize_upload_name(base_name) or fallback
        stem = Path(candidate).stem or "image"
        suffix = Path(candidate).suffix.lower() or ".png"
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        if suffix not in allowed:
            suffix = ".png"
        filename = f"{stem}{suffix}"
        i = 1
        while (images_dir / filename).exists():
            filename = f"{stem}_{i}{suffix}"
            i += 1
        return filename
