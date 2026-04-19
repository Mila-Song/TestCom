from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class UserStore:
    def __init__(self, users_file: Path, lock) -> None:
        self.users_file = users_file
        self.lock = lock

    def read(self) -> Dict[str, Dict[str, Any]]:
        with self.lock:
            if not self.users_file.exists():
                return {}
            with self.users_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}

    def write(self, data: Dict[str, Dict[str, Any]]) -> None:
        with self.lock:
            with self.users_file.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def find_username_by_email(self, email: str) -> str | None:
        target = str(email or "").strip().lower()
        if not target:
            return None
        users = self.read()
        for uname, rec in users.items():
            rec_email = str(rec.get("email", "")).strip().lower()
            if rec_email == target:
                return uname
        return None
