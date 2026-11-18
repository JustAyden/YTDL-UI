"""The /privacy page: what this server does with visitors' data, filled in from its live settings.

Everything stated here must match what the code actually does. If you change what is logged,
stored or sent anywhere, update this file and PRIVACY.md in the same change.

Operators add their own notes (who runs the server, log retention, which laws apply) in a Markdown
file in their data folder, so those never end up in the repository or a release.
"""

from __future__ import annotations

import os
import re
from html import escape

from .config import Settings

_EMAIL = re.compile(r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+")
NOTES_MAX_BYTES = 64 * 1024

# Inline Markdown the operator's notes may use: `code`, [text](https://… or mailto:…), **bold**,
# and bare https:// links or email addresses. Everything else is shown as plain, escaped text.
_INLINE = re.compile(
    r"`(?P<code>[^`]+)`"
    r"|\[(?P<text>[^\]]+)\]\((?P<href>(?:https?://|mailto:)[^\s)]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|(?P<url>https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"])"
    r"|(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})")


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(escape(text[pos:m.start()]))
        if m["code"] is not None:
            out.append(f"<code>{escape(m['code'])}</code>")
        elif m["href"] is not None:
            out.append(f'<a href="{escape(m["href"])}" rel="noopener nofollow" target="_blank">'
                       f"{escape(m['text'])}</a>")
        elif m["bold"] is not None:
            out.append(f"<strong>{escape(m['bold'])}</strong>")
        elif m["url"] is not None:
            out.append(f'<a href="{escape(m["url"])}" rel="noopener nofollow" target="_blank">'
                       f"{escape(m['url'])}</a>")
        else:
            out.append(f'<a href="mailto:{escape(m["email"])}">{escape(m["email"])}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def render_notes(md: str) -> str:
    """A small, safe Markdown subset: headings, paragraphs, bullet lists and the inline forms above.

    Raw HTML is never passed through; it shows up as text.
    """
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    md = re.sub(r"<!--.*?(?:-->|\Z)", "", md, flags=re.S)      # <!-- comments --> are for the operator
    for line in md.splitlines():
        text = line.strip()
        if not text:
            flush()
        elif (m := re.match(r"#{1,6}\s+(.*)", text)):
            flush()
            out.append(f"<h3>{_inline(m.group(1))}</h3>")
        elif (m := re.match(r"[-*]\s+(.*)", text)):
            if para:
                flush()
            items.append(m.group(1))
        elif items and line[:1].isspace():          # an indented line continues the list item
            items[-1] += " " + text
        else:
            if items:
                flush()
            para.append(text)
    flush()
    return "\n".join(out)


def load_notes(s: Settings) -> str:
    """The operator's own notes (``privacy_notes``, by default data/privacy.md) as HTML, or ''."""
    path = str(s.privacy_notes or "").strip()
    if not path:
        return ""
    if not os.path.isabs(path):
        path = os.path.join(s.data_path, path)
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return render_notes(fh.read(NOTES_MAX_BYTES))
    except OSError:
        return ""


def _contact_html(raw: str) -> str:
    raw = raw.strip()[:200]
    if not raw:
        return ""
    if _EMAIL.fullmatch(raw):
        return f'<a href="mailto:{escape(raw)}">{escape(raw)}</a>'
    if re.match(r"https?://[^\s<>\"']+$", raw):
        return f'<a href="{escape(raw)}" rel="noopener nofollow" target="_blank">{escape(raw)}</a>'
    return escape(raw)


def _minutes(n: int) -> str:
    if n % 60 == 0 and n >= 120:
        return f"{n // 60} hours"
    return f"{n} minute{'s' if n != 1 else ''}"


def render(template: str, s: Settings, version: str) -> str:
    """Fill the privacy template's ``{{name}}`` slots from the current settings."""
    ttl = _minutes(max(0, int(s.job_ttl_minutes)))
    server_on, browser_on = s.server_downloads_enabled, s.browser_processing_enabled

    if browser_on and not server_on:
        mode = ("<strong>This server processes downloads on your device.</strong> It looks up which "
                "streams to fetch, but never downloads or stores the files itself.")
    elif browser_on:
        mode = ("<strong>This server can process downloads on the server or on your device</strong> "
                "(Settings → <em>Process downloads on</em>, when it's offered). On your device, the "
                "server never stores the files.")
    else:
        mode = "<strong>This server processes every download on the server.</strong>"
    if browser_on and server_on and s.fallback_to_server:
        mode += (" If a link can't be handled on your device, the server takes over and the "
                 "<em>On the server</em> rules below apply.")

    log_line = ("the <strong>full link</strong>" if s.log_urls
                else "the <strong>site name only</strong> (for example <em>youtube.com</em>), not the link")

    if browser_on and s.wasm_source == "cdn":
        wasm = ("<li><strong>jsDelivr</strong> (cdn.jsdelivr.net) — only when you process a download on "
                "your device: your browser loads the ~31&nbsp;MB ffmpeg converter from it once. jsDelivr "
                "sees your IP address; no cookies are sent. "
                '<a href="https://www.jsdelivr.com/terms/privacy-policy-jsdelivr-net" rel="noopener" '
                'target="_blank">jsDelivr\'s privacy policy</a>.</li>')
    elif browser_on:
        wasm = ("<li>The ffmpeg converter used for on-device processing is served by this server, "
                "not a third party.</li>")
    else:
        wasm = ""
    relay = ("this server relays the bytes to you as they arrive and doesn't save them" if s.relay
             else "the download is handled on the server instead")
    direct = ("<li><strong>The media site itself</strong> — when you process on your device and the "
              "site allows browsers to fetch its files directly. When it doesn't (YouTube, for "
              f"example), {relay}.</li>") if browser_on else ""

    tp = str(s.trust_proxy).strip().lower()
    logs = ("may log your IP address and the addresses you request — including a link you share into "
            "this app from another app, which arrives as part of the page address.")
    if tp == "cloudflare":
        proxy = ("Connections to this server pass through <strong>Cloudflare</strong> first. Cloudflare "
                 "handles them under its own <a href=\"https://www.cloudflare.com/privacypolicy/\" "
                 f"rel=\"noopener\" target=\"_blank\">privacy policy</a> and {logs}")
    elif tp.isdigit() and int(tp) > 0:
        proxy = ("This server sits behind a reverse proxy or tunnel run by its operator. It handles "
                 f"your connection before YTDL-UI does and {logs}")
    else:
        proxy = ""
    if proxy:
        proxy = ('<section class="card" aria-labelledby="h-proxy"><h2 id="h-proxy">'
                 '<span class="msr">language</span>In front of this server</h2>'
                 f"<p>{proxy}</p></section>")

    if s.password:
        cookie = ("This server is password-protected, so signing in (as a visitor or as admin) sets "
                  "one cookie, <code>ytdlui_session</code>, that keeps you signed in for 30 days.")
    else:
        cookie = ("Visitors don't get any cookies. Only signing in as admin sets one, "
                  "<code>ytdlui_session</code>, that keeps you signed in for 30 days.")
    cookie += " It only records that you're signed in and isn't used to track you."

    notes = load_notes(s)
    if notes:
        notes = ('<section class="card notes" aria-labelledby="h-notes"><h2 id="h-notes">'
                 '<span class="msr">dns</span>About this server</h2>' + notes + "</section>")

    contact = _contact_html(s.privacy_contact)
    contact_html = (f"<p>Questions about this server? Contact its operator: {contact}.</p>" if contact
                    else "<p>The operator of this server hasn't listed a contact.</p>")
    via_proxy = " through a proxy set by its operator" if s.proxy else ""

    slots = {
        "version": escape(version),
        "ttl": ttl,
        "mode": mode,
        "log_line": log_line,
        "wasm": wasm,
        "direct": direct,
        "proxy_section": proxy,
        "notes": notes,
        "cookie": cookie,
        "contact": contact_html,
        "via_proxy": via_proxy,
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: slots[m.group(1)], template)
