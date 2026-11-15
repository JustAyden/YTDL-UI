import base64
import json
import time

from tests.conftest import make_app
from ytdlui.auth import (AdminStore, LoginGuard, password_problem, totp_at, totp_match, verify_password,
                         hash_password)


def test_totp_rfc6238_vectors():
    secret = base64.b32encode(b"12345678901234567890").decode()
    # RFC 6238 Appendix B (SHA-1), last 6 digits of the 8-digit values
    assert totp_at(secret, 59 // 30, digits=8) == "94287082"
    assert totp_at(secret, 1111111109 // 30, digits=8) == "07081804"
    assert totp_at(secret, 20000000000 // 30, digits=8) == "65353130"
    assert totp_match(secret, "287082", now=59) == 1
    assert totp_match(secret, "287082", now=59 + 30) == 1      # one step of clock drift is fine
    assert totp_match(secret, "287082", now=59 + 95) is None
    assert totp_match(secret, "28708", now=59) is None


def test_password_hashing_and_policy():
    rec = hash_password("correct horse")
    assert verify_password("correct horse", rec) and not verify_password("wrong", rec)
    assert "correct horse" not in json.dumps(rec)
    assert password_problem("short", "x")
    assert password_problem("aaaaaaaaaaaa", "x")
    assert password_problem("same-password", "same-password")
    assert password_problem("good-new-password", "old") is None


def test_admin_store_codes_once(tmp_path):
    store = AdminStore(str(tmp_path), "initial-pass")
    assert store.setup_step() == "totp" and store.check_password("initial-pass")
    secret = store.begin_totp()
    now_step = int(time.time() // 30)
    codes = store.confirm_totp(totp_at(secret, now_step))
    assert codes and len(codes) == 8 and store.setup_step() == "password"
    assert not store.check_second_factor(totp_at(secret, now_step))         # replay rejected
    assert store.check_second_factor(totp_at(secret, now_step + 1))         # next code ok
    assert store.check_second_factor(codes[0].upper())                       # recovery code works…
    assert not store.check_second_factor(codes[0])                           # …only once
    assert store.recovery_left() == 7
    store.set_password("brand-new-password")
    assert store.setup_step() == "done"
    again = AdminStore(str(tmp_path), "ignored-now")                         # persisted
    assert again.check_password("brand-new-password") and again.totp_enabled


def test_generated_initial_password(tmp_path):
    store = AdminStore(str(tmp_path), "")
    pw = store.generated_password
    assert pw and len(pw) >= 20 and store.check_password(pw)
    assert (tmp_path / "initial-admin-password.txt").read_text().strip() == pw
    store.set_password("another-good-password")
    assert not (tmp_path / "initial-admin-password.txt").exists()


def test_lockout_escalates_and_persists(tmp_path):
    path = str(tmp_path / "lockouts.json")
    g = LoginGuard(path, max_attempts=3, lockout_minutes=1)
    assert g.failure("admin", "1.1.1.1") == 0 and g.failure("admin", "1.1.1.1") == 0
    assert g.failure("admin", "1.1.1.1") == 60
    assert 55 <= g.locked_for("admin", "1.1.1.1") <= 61
    assert g.locked_for("admin", "2.2.2.2") == 0 and g.locked_for("site", "1.1.1.1") == 0
    assert LoginGuard(path, 3, 1).locked_for("admin", "1.1.1.1") > 0        # survives restart
    g.state["admin|1.1.1.1"]["until"] = 0                                   # lock expired
    for _ in range(2):
        g.failure("admin", "1.1.1.1")
    assert g.failure("admin", "1.1.1.1") == 120                             # second lock doubles
    assert g.lockouts()[0]["ip"] == "1.1.1.1"
    assert g.unlock("admin", "1.1.1.1") and g.locked_for("admin", "1.1.1.1") == 0
    g.failure("site", "3.3.3.3")
    g.success("site", "3.3.3.3")
    assert g.remaining("site", "3.3.3.3") == 3


def _code(app):
    """Current code; the 'already used' marker is reset so a test can log in several times
    within one 30 s window (replay protection itself is tested above)."""
    store = app.extensions["ytdlui"].admin
    store.data["totp_last_step"] = 0
    return totp_at(store.data["totp_secret"] or store.data["totp_pending"], int(time.time() // 30))


def test_first_login_flow(settings):
    settings.admin_password = "initial-pass"
    app = make_app(settings)
    c = app.test_client()
    assert c.post("/api/admin/login", json={"password": "nope"}).status_code == 403
    r = c.post("/api/admin/login", json={"password": "initial-pass"})
    assert r.get_json() == {"ok": True, "setup": "totp"}
    assert c.get("/api/config").get_json()["auth"]["admin_setup"] == "totp"
    assert c.get("/api/admin/status").get_json()["setup"] == "totp"          # panel blocked until set up
    assert c.post("/api/admin/password", json={}).status_code == 403          # 2FA must come first
    t = c.post("/api/admin/totp/begin", json={}).get_json()
    assert t["secret"] and t["uri"].startswith("otpauth://totp/") and t["qr"].startswith("data:image/svg+xml")
    assert c.post("/api/admin/totp/confirm", json={"code": "000000"}).status_code == 403
    r = c.post("/api/admin/totp/confirm", json={"code": _code(app)}).get_json()
    assert r["setup"] == "password" and len(r["recovery_codes"]) == 8
    assert c.get("/api/admin/status").get_json()["setup"] == "password"
    bad = c.post("/api/admin/password", json={"current": "initial-pass", "new": "short", "code": _code(app)})
    assert bad.status_code == 400 and "10 characters" in bad.get_json()["error"]
    ok = c.post("/api/admin/password", json={"current": "initial-pass", "new": "a-much-better-pass", "code": _code(app)})
    assert ok.status_code == 200
    st = c.get("/api/admin/status").get_json()
    assert st["security"]["totp"] and st["app"]["current"]
    # second login now needs password + code
    c2 = app.test_client()
    r = c2.post("/api/admin/login", json={"password": "a-much-better-pass"})
    assert r.status_code == 401 and r.get_json()["need_code"]
    assert c2.post("/api/admin/login", json={"password": "a-much-better-pass", "code": _code(app)}).status_code == 200
    assert c2.get("/api/admin/status").status_code == 200
    # changing the password from one session signs the other out
    c2.post("/api/admin/password", json={"current": "a-much-better-pass", "new": "yet-another-pass-1", "code": _code(app)})
    assert c.get("/api/admin/status").status_code == 401
    assert c2.get("/api/admin/status").status_code == 200


def test_admin_lockout_via_api(settings):
    settings.admin_password = "initial-pass"
    settings.login_max_attempts = 3
    c = make_app(settings).test_client()
    codes = [c.post("/api/admin/login", json={"password": f"bad{i}"}).status_code for i in range(3)]
    assert codes == [403, 403, 429]
    r = c.post("/api/admin/login", json={"password": "initial-pass"})          # even the right password
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert "Try again in" in r.get_json()["error"]


def test_site_password_lockout(settings):
    settings.password = "site-pass"
    settings.login_max_attempts = 2
    c = make_app(settings).test_client()
    assert c.post("/api/login", json={"password": "x"}).status_code == 403
    assert c.post("/api/login", json={"password": "y"}).status_code == 429
    assert c.post("/api/login", json={"password": "site-pass"}).status_code == 429
