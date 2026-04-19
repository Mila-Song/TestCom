from __future__ import annotations

import os
import threading
import time
from typing import Dict, List

from werkzeug.security import check_password_hash

PRIMARY_ADMIN_USERNAME = str(os.getenv("PRIMARY_ADMIN_USERNAME", "Yaona")).strip() or "Yaona"
PRIMARY_ADMIN_PASSWORD = str(os.getenv("PRIMARY_ADMIN_PASSWORD", "")).strip()
PRIMARY_ADMIN_PASSWORD_HASH = str(os.getenv("PRIMARY_ADMIN_PASSWORD_HASH", "")).strip()

LOGIN_RATE_LIMIT_COUNT = int(str(os.getenv("LOGIN_RATE_LIMIT_COUNT", "12")).strip() or "12")
LOGIN_RATE_LIMIT_WINDOW_SEC = int(str(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SEC", "60")).strip() or "60")
ADMIN_RATE_LIMIT_COUNT = int(str(os.getenv("ADMIN_RATE_LIMIT_COUNT", "180")).strip() or "180")
ADMIN_RATE_LIMIT_WINDOW_SEC = int(str(os.getenv("ADMIN_RATE_LIMIT_WINDOW_SEC", "60")).strip() or "60")


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._buckets: Dict[str, List[float]] = {}

    def check(self, bucket: str, limit: int, window_sec: int) -> bool:
        now_ts = time.time()
        with self._lock:
            arr = self._buckets.get(bucket, [])
            cutoff = now_ts - max(1, window_sec)
            arr = [t for t in arr if t >= cutoff]
            if len(arr) >= max(1, limit):
                self._buckets[bucket] = arr
                return False
            arr.append(now_ts)
            self._buckets[bucket] = arr
            return True


RATE_LIMITER = InMemoryRateLimiter()


def is_primary_admin_username(username: str) -> bool:
    return str(username or "").strip().lower() == PRIMARY_ADMIN_USERNAME.lower()


def get_client_ip(req) -> str:
    xff = str(req.headers.get("X-Forwarded-For", "")).strip()
    if xff:
        return xff.split(",")[0].strip()
    xrip = str(req.headers.get("X-Real-IP", "")).strip()
    if xrip:
        return xrip
    return str(req.remote_addr or "unknown")


def verify_primary_admin_password(password: str, logger=None) -> bool:
    pw = str(password or "")
    if PRIMARY_ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(PRIMARY_ADMIN_PASSWORD_HASH, pw)
        except Exception:
            if logger:
                logger.exception("Invalid PRIMARY_ADMIN_PASSWORD_HASH format.")
            return False
    if PRIMARY_ADMIN_PASSWORD:
        return pw == PRIMARY_ADMIN_PASSWORD
    return False
