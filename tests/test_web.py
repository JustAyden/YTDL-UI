import io
import time
import zipfile

from tests.conftest import make_app


def wait_job(client, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/jobs/{job_id}").get_json()
        if d["status"] not in ("queued", "running"):
            return d
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_index_and_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and b"id=\"url-form\"" in r.data
    assert b"__VERSION__" not in r.data
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options"):
        assert h in r.headers
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/api/health").get_json()["ok"]


def test_config(client):
    c = client.get("/api/config").get_json()
    assert c["processing"]["mode"] == "choice"
    assert {f["id"] for f in c["formats"]["audio"]} == {"mp3", "m4a", "opus", "flac", "wav"}
    assert "update" not in c                       # admin-only


def test_info_video_and_validation(client):
    d = client.post("/api/info", json={"url": "https://example.com/watch?v=abc"}).get_json()
    assert d["title"] == "Test Video" and d["duration_str"] == "2:05"
    assert [q["label"] for q in d["video_qualities"]] == ["1080p", "720p"]
    r = client.post("/api/info", json={"url": "http://127.0.0.1/"})
    assert r.status_code == 400 and "private" in r.get_json()["error"]
    r = client.post("/api/info", json={"url": "https://example.com/fail"})
    assert r.status_code == 422 and "private" in r.get_json()["error"].lower()
    r = client.post("/api/info", data="url=x")      # not JSON
    assert r.status_code == 400


def test_server_job_lifecycle(client):
    r = client.post("/api/jobs", json={"url": "https://example.com/watch?v=abc", "format": "mp4",
                                       "quality": "720p", "title": "Test Video"})
    assert r.status_code == 201
    job = wait_job(client, r.get_json()["id"])
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["files"][0]["name"] == "Test Video.mp4"
    f = client.get(f"/api/jobs/{job['id']}/file")
    assert f.status_code == 200 and len(f.data) == 2048
    assert "attachment" in f.headers["Content-Disposition"]
    ranged = client.get(f"/api/jobs/{job['id']}/file", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.data) == 100
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_playlist_job_zip_and_skips(client):
    r = client.post("/api/jobs", json={"url": "https://example.com/playlist-skip2", "format": "mp3",
                                       "playlist": True, "items": [1, 2, 3], "title": "My List"})
    job = wait_job(client, r.get_json()["id"])
    assert job["status"] == "done" and len(job["files"]) == 2 and job["skipped"] == 1
    z = client.get(f"/api/jobs/{job['id']}/file")
    assert z.mimetype == "application/zip" and "My%20List.zip" in z.headers["Content-Disposition"]
    names = zipfile.ZipFile(io.BytesIO(z.data)).namelist()
    assert names == ["001 - Song 1.mp3", "003 - Song 3.mp3"]
    one = client.get(f"/api/jobs/{job['id']}/files/1")
    assert one.status_code == 200 and "003" in one.headers["Content-Disposition"]


def test_failed_job_reports_friendly_error(client):
    r = client.post("/api/jobs", json={"url": "https://example.com/fail", "format": "mp4"})
    job = wait_job(client, r.get_json()["id"])
    assert job["status"] == "error" and "private" in job["error"].lower()


def test_cancel_running_job(client):
    r = client.post("/api/jobs", json={"url": "https://example.com/slow", "format": "mp4"})
    jid = r.get_json()["id"]
    for _ in range(50):
        if client.get(f"/api/jobs/{jid}").get_json()["status"] == "running":
            break
        time.sleep(0.05)
    assert client.get(f"/api/jobs/{jid}").get_json()["status"] == "running"
    svc = client.application.extensions["ytdlui"]
    job = svc.jobs._jobs[jid]
    client.delete(f"/api/jobs/{jid}")
    assert client.get(f"/api/jobs/{jid}").status_code == 404   # hidden immediately
    for _ in range(100):
        if jid not in svc.jobs._jobs:
            break
        time.sleep(0.05)
    assert jid not in svc.jobs._jobs and job.status == "cancelled"   # process killed, files gone


def test_per_client_limit(settings, monkeypatch):
    import ytdlui.security as sec
    monkeypatch.setattr(sec, "resolve_host", lambda h, p: ["93.184.216.34"])
    settings.max_jobs_per_client = 1
    settings.max_concurrent_jobs = 1
    c = make_app(settings).test_client()
    assert c.post("/api/jobs", json={"url": "https://example.com/slow", "format": "mp4"}).status_code == 201
    r = c.post("/api/jobs", json={"url": "https://example.com/slow", "format": "mp4"})
    assert r.status_code == 429


def test_rate_limit(settings, monkeypatch):
    import ytdlui.security as sec
    monkeypatch.setattr(sec, "resolve_host", lambda h, p: ["93.184.216.34"])
    settings.rate_limit = 2
    c = make_app(settings).test_client()
    codes = [c.post("/api/info", json={"url": "https://example.com/watch?v=1"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_browser_only_mode_blocks_server_jobs(settings, monkeypatch):
    import ytdlui.security as sec
    monkeypatch.setattr(sec, "resolve_host", lambda h, p: ["93.184.216.34"])
    settings.processing_mode = "browser"
    settings.fallback_to_server = False
    c = make_app(settings).test_client()
    assert c.post("/api/jobs", json={"url": "https://example.com/watch?v=1", "format": "mp4"}).status_code == 403
    assert c.post("/api/download", json={"url": "https://example.com/watch?v=1"}).status_code == 403
    assert c.get("/api/config").get_json()["server_downloads"] is False


def test_password_gate(settings):
    settings.password = "s3cret"
    c = make_app(settings).test_client()
    assert b"Private server" in c.get("/").data
    assert c.get("/api/config").status_code == 401
    assert c.get("/static/css/app.css").status_code == 200          # login page assets stay public
    assert c.post("/api/login", json={"password": "nope"}).status_code == 403
    assert c.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert c.get("/api/config").status_code == 200
    assert b"id=\"url-form\"" in c.get("/").data


def test_privacy_page_reflects_settings(client, app):
    r = client.get("/privacy")
    assert r.status_code == 200 and r.mimetype == "text/html"
    page = r.get_data(as_text=True)
    assert "{{" not in page and "Content-Security-Policy" in r.headers
    assert "deleted 30 minutes after they finish" in page
    assert "site name only" in page and "jsDelivr" in page
    assert "Visitors don't get any cookies" in page and "hasn't listed a contact" in page
    assert "In front of this server" not in page                     # trust_proxy = 0
    # the claim above must hold: browsing anonymously never sets a cookie
    for path in ("/", "/privacy", "/api/config"):
        assert "Set-Cookie" not in client.get(path).headers

    s = app.extensions["ytdlui"].settings
    s.job_ttl_minutes, s.log_urls, s.trust_proxy = 120, True, "cloudflare"
    s.processing_mode, s.wasm_source = "server", "cdn"
    s.privacy_contact = 'me@example.com"><script>'
    page = client.get("/privacy").get_data(as_text=True)
    assert "deleted 2 hours after they finish" in page and "full link" in page
    assert "Cloudflare" in page and "jsDelivr" not in page
    assert "<script>" not in page and "hasn't listed a contact" not in page   # escaped, not linked
    s.privacy_contact = "privacy@example.com"
    assert 'href="mailto:privacy@example.com"' in client.get("/privacy").get_data(as_text=True)
    s.privacy_contact = "javascript:alert(1)"
    assert 'href="javascript' not in client.get("/privacy").get_data(as_text=True)


def test_privacy_page_is_public_on_private_servers(settings):
    settings.password = "s3cret"
    c = make_app(settings).test_client()
    page = c.get("/privacy").get_data(as_text=True)
    assert "What the server receives" in page and "password-protected" in page
    assert c.get("/static/js/privacy.js").status_code == 200
    assert 'href="privacy"' in c.get("/").get_data(as_text=True)    # linked from the password screen


def test_admin_disabled(settings):
    settings.admin_enabled = False
    c = make_app(settings).test_client()
    assert c.get("/api/admin/status").status_code == 404
    assert c.post("/api/admin/login", json={"password": "x"}).status_code == 404


def test_legacy_download_endpoint(client):
    r = client.post("/api/download", json={"url": "https://example.com/watch?v=abc", "format": "mp3", "quality": "192k"})
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment")


def test_relay_rejects_unknown_tokens(client):
    assert client.get("/api/relay/not-a-token").status_code == 404


def test_resolve_signals_fallback_when_browser_cant_handle_it(client):
    # The fake extractor returns no direct stream URLs → the browser can't fetch it itself.
    r = client.post("/api/resolve", json={"url": "https://example.com/watch?v=abc", "format": "mp4"})
    assert r.status_code == 422 and r.get_json()["fallback"] is True


def test_resolve_disabled_in_server_mode(settings, monkeypatch):
    import ytdlui.security as sec
    monkeypatch.setattr(sec, "resolve_host", lambda h, p: ["93.184.216.34"])
    settings.processing_mode = "server"
    c = make_app(settings).test_client()
    assert c.post("/api/resolve", json={"url": "https://example.com/watch?v=1", "format": "mp4"}).status_code == 403
