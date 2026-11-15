"""Admin credentials, TOTP two-factor auth and brute-force lockouts.

Admin credentials live in ``data/admin.json`` (password as a salted scrypt hash,
TOTP secret, hashed one-time recovery codes). On first start the password comes
from ``admin_password`` in the config or, if that's empty, is generated and printed.
The first admin to log in must set up TOTP and then replace that password.

Lockouts: after ``login_max_attempts`` failures from one IP within the lockout
window, that IP is blocked for ``login_lockout_minutes``; every further lockout of
the same IP doubles the time (capped at 24 h). Lockouts are saved to disk, so a
restart doesn't reset them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
import time
from typing import Any
from urllib.parse import quote

log = logging.getLogger("ytdlui")

TOTP_PERIOD = 30
TOTP_DIGITS = 6
MIN_PASSWORD_LENGTH = 10
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


# ── passwords ────────────────────────────────────────────────────────────────
def hash_password(password: str, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return {"salt": salt.hex(), "hash": digest.hex(), **_SCRYPT}


def verify_password(password: str, record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    try:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(record["salt"]), dklen=32,
                                n=record["n"], r=record["r"], p=record["p"])
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), record["hash"])


def password_problem(new: str, current: str) -> str | None:
    if len(new) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if new == current:
        return "The new password must be different from the current one."
    if new.strip() != new:
        return "The password can't start or end with spaces."
    if len(set(new)) < 4:
        return "That password is too simple."
    return None


# ── TOTP (RFC 6238) ──────────────────────────────────────────────────────────
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32(secret: str) -> bytes:
    s = secret.upper().replace(" ", "")
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp_at(secret: str, step: int, digits: int = TOTP_DIGITS) -> str:
    mac = hmac.new(_b32(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_match(secret: str, code: str, now: float | None = None, window: int = 1) -> int | None:
    """Return the matching time step (±``window`` steps for clock drift), or None."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != TOTP_DIGITS:
        return None
    step = int((time.time() if now is None else now) // TOTP_PERIOD)
    for s in range(step - window, step + window + 1):
        if hmac.compare_digest(totp_at(secret, s), code):
            return s
    return None


def totp_uri(secret: str, account: str, issuer: str = "YTDL-UI") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD}")


def qr_svg_data_uri(text: str) -> str | None:
    try:
        import io

        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#0d1117", light="#ffffff")
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def _norm_recovery(code: str) -> str:
    return "".join(c for c in code.lower() if c.isalnum())


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_norm_recovery(code).encode()).hexdigest()


def _atomic_write(path: str, data: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


# ── admin account ────────────────────────────────────────────────────────────
class AdminStore:
    def __init__(self, data_path: str, initial_password: str = ""):
        self.path = os.path.join(data_path, "admin.json")
        self.initial_file = os.path.join(data_path, "initial-admin-password.txt")
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {}
        self.generated_password: str | None = None
        self._load_or_create(initial_password)

    def _load_or_create(self, initial: str) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
            return
        except (OSError, ValueError):
            pass
        if not initial:
            alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no look-alikes
            initial = "-".join("".join(secrets.choice(alphabet) for _ in range(6)) for _ in range(4))
            self.generated_password = initial
            try:
                fd = os.open(self.initial_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(initial + "\n")
            except OSError:
                pass
        self.data = {"password": hash_password(initial), "must_change_password": True,
                     "totp_secret": None, "totp_pending": None, "totp_last_step": 0,
                     "recovery": [], "version": 1, "created": time.time()}
        self._save()

    def _save(self) -> None:
        _atomic_write(self.path, self.data)

    # state
    @property
    def version(self) -> int:
        return int(self.data.get("version", 1))

    @property
    def totp_enabled(self) -> bool:
        return bool(self.data.get("totp_secret"))

    def setup_step(self) -> str:
        if not self.totp_enabled:
            return "totp"
        if self.data.get("must_change_password"):
            return "password"
        return "done"

    # checks
    def check_password(self, password: str) -> bool:
        return verify_password(password, self.data.get("password"))

    def check_second_factor(self, code: str) -> bool:
        """TOTP code (each code works once) or an unused recovery code (consumed)."""
        with self._lock:
            secret = self.data.get("totp_secret")
            if not secret:
                return False
            step = totp_match(secret, code)
            if step is not None:
                if step <= int(self.data.get("totp_last_step", 0)):
                    return False                 # replay of an already-used code
                self.data["totp_last_step"] = step
                self._save()
                return True
            h = _hash_recovery(code)
            if len(_norm_recovery(code)) >= 10 and h in self.data.get("recovery", []):
                self.data["recovery"].remove(h)
                self._save()
                log.warning("Admin used a recovery code (%d left)", len(self.data["recovery"]))
                return True
            return False

    # TOTP setup
    def begin_totp(self) -> str:
        with self._lock:
            secret = new_totp_secret()
            self.data["totp_pending"] = secret
            self._save()
            return secret

    def confirm_totp(self, code: str) -> list[str] | None:
        with self._lock:
            pending = self.data.get("totp_pending")
            step = totp_match(pending, code) if pending else None
            if step is None:
                return None
            self.data.update(totp_secret=pending, totp_pending=None, totp_last_step=step)
            codes = self._new_recovery_codes()
            self.data["version"] = self.version + 1
            self._save()
            return codes

    def regenerate_recovery(self) -> list[str]:
        with self._lock:
            codes = self._new_recovery_codes()
            self._save()
            return codes

    def _new_recovery_codes(self) -> list[str]:
        codes = ["-".join(secrets.token_hex(3) for _ in range(2)) for _ in range(8)]
        self.data["recovery"] = [_hash_recovery(c) for c in codes]
        return codes

    def recovery_left(self) -> int:
        return len(self.data.get("recovery", []))

    def set_password(self, new: str) -> None:
        with self._lock:
            self.data["password"] = hash_password(new)
            self.data["must_change_password"] = False
            self.data["version"] = self.version + 1     # signs out every other admin session
            self.data["password_changed"] = time.time()
            self._save()
        try:
            os.remove(self.initial_file)
        except OSError:
            pass


# ── lockouts ─────────────────────────────────────────────────────────────────
class LoginGuard:
    """Per-IP failure tracking with escalating, persisted lockouts."""

    def __init__(self, path: str | None, max_attempts: int = 5, lockout_minutes: float = 15):
        self.path = path
        self.max_attempts = max(1, max_attempts)
        self.base = max(1.0, lockout_minutes) * 60
        self._lock = threading.Lock()
        self.state: dict[str, dict[str, Any]] = {}
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    self.state = json.load(fh)
            except (OSError, ValueError):
                self.state = {}

    def _key(self, scope: str, ip: str) -> str:
        return f"{scope}|{ip}"

    def _save(self) -> None:
        if not self.path:
            return
        now = time.time()
        # forget entries that are idle for a day and not locked
        self.state = {k: v for k, v in self.state.items()
                      if v.get("until", 0) > now or now - v.get("last", 0) < 86400}
        try:
            _atomic_write(self.path, self.state)
        except OSError:
            pass

    def locked_for(self, scope: str, ip: str) -> int:
        """Seconds until ``ip`` may try again (0 = not locked)."""
        with self._lock:
            e = self.state.get(self._key(scope, ip))
            left = (e or {}).get("until", 0) - time.time()
            return int(left) + 1 if left > 0 else 0

    def failure(self, scope: str, ip: str) -> int:
        """Record a failed attempt. Returns the lockout length in seconds if this one triggered it."""
        with self._lock:
            now = time.time()
            e = self.state.setdefault(self._key(scope, ip), {"fails": 0, "level": 0, "until": 0, "last": 0})
            if now - e.get("last", 0) > self.base:      # old failures expire
                e["fails"] = 0
            e["fails"] += 1
            e["last"] = now
            lock = 0
            if e["fails"] >= self.max_attempts:
                lock = int(min(self.base * (2 ** e["level"]), 86400))
                e.update(until=now + lock, fails=0, level=e["level"] + 1)
                log.warning("Locked %s logins from %s for %d min after repeated failures", scope, ip, lock // 60)
            self._save()
            return lock

    def success(self, scope: str, ip: str) -> None:
        with self._lock:
            if self.state.pop(self._key(scope, ip), None) is not None:
                self._save()

    def remaining(self, scope: str, ip: str) -> int:
        with self._lock:
            e = self.state.get(self._key(scope, ip)) or {}
            fails = e.get("fails", 0) if time.time() - e.get("last", 0) <= self.base else 0
            return max(0, self.max_attempts - fails)

    def lockouts(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [{"scope": k.split("|", 1)[0], "ip": k.split("|", 1)[1], "seconds": int(v["until"] - now)}
                    for k, v in self.state.items() if v.get("until", 0) > now]

    def unlock(self, scope: str, ip: str) -> bool:
        with self._lock:
            ok = self.state.pop(self._key(scope, ip), None) is not None
            self._save()
            return ok
