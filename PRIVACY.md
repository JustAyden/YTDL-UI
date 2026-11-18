# Privacy

YTDL-UI is self-hosted software. **The people who make it don't run your server and receive no data from it.** There are no accounts, ads, analytics or tracking.

Every running instance has a privacy page at **`/privacy`** (linked in the footer, in ⚙ Settings → About, and on the password screen). It's generated from that server's live settings, so visitors see what *that* server does, not just the defaults — plus, optionally, the operator's own policy from `data/privacy.md`. This document explains the same things for the software in general, and what you should know if you host it for other people.

---

## What a YTDL-UI server receives from visitors

| Data | Why | How long it's kept |
|---|---|---|
| The link a visitor pastes, and the format, quality and playlist items they choose | To look up and download the media | In memory with the download; deleted with it |
| The visitor's IP address | Rate limits, and the per-visitor download cap | In memory only: ~1 minute for rate limits, and with each server download until it's deleted. Gone on restart |
| IP address + failure count after a wrong password (site or admin) | Brute-force lockouts that survive a restart | On disk (`data/lockouts.json`) until a successful sign-in, or a day after the last failure once any lockout has ended |
| Downloaded files (server processing only) | So the visitor can save them | Deleted `job_ttl_minutes` (default **30**) after they finish; immediately if cancelled, failed or removed from the visitor's list |

With **browser processing** ("This device"), the server only asks yt-dlp which streams to fetch; the visitor's browser downloads and converts them and the file never touches the server's disk. Sites that lock their media URLs to the server's IP (YouTube, for example) are *relayed*: the server passes the bytes through as they arrive and never stores them. See [Server vs. browser processing](README.md#server-vs-browser-processing).

## What gets logged

YTDL-UI keeps **no access log** of pages or API requests. Its log contains:

- each server download's format, quality and **site name only** — the full link only if you set `log_urls = true`;
- yt-dlp error messages when a download fails, which can include the video's ID on that site;
- admin sign-ins, failed sign-ins and lockouts, with the IP address.

Where the log goes (console, `journalctl`, `docker logs`) and how long it's kept is up to whoever runs the server.

## Who can see it

The server's admin can see, in the admin console, the downloads currently on the server — title, site, format, and the IP address that started each one — until they're deleted, plus any locked-out IP addresses. The operator of course also has full access to the machine itself.

## Cookies and browser storage

- **Cookie:** one, `ytdlui_session` (HttpOnly, SameSite=Lax), set only when someone signs in with the site password or as admin. It keeps them signed in for 30 days and only records that they're signed in. Visitors on a server without a password get no cookies at all.
- **Local storage** (stays in the visitor's browser): theme and other Settings, last-used format and quality, and a list of up to 30 recent server downloads (title, thumbnail link, format) so it survives a reload. Only download IDs are sent back, to check progress.

## Other services involved

**A visitor's browser may contact:**

- the media site's image server, to show the thumbnail (no referrer is sent: `Referrer-Policy: no-referrer`);
- the media site directly, when processing on the device and the site allows it;
- [jsDelivr](https://www.jsdelivr.com/terms/privacy-policy-jsdelivr-net), once, for the ~31 MB ffmpeg converter used by browser processing — unless the server sets `wasm_source = local`, in which case the server serves it.

Fonts, icons and the app's own scripts are served by YTDL-UI itself; browsers never contact Google or any ad or analytics network. The Content-Security-Policy only allows scripts from the server and jsDelivr.

**The server contacts:**

- the media site, through yt-dlp (and the `proxy`, if you set one) — the site sees the server's IP, not the visitor's;
- GitHub and the Python Package Index, to check for and download updates to YTDL-UI, yt-dlp and ffmpeg (`update_check`, `ytdlp_auto_update`, `ffmpeg_auto_install`). These requests contain no visitor data. They identify themselves as `YTDL-UI/<version>`; the maintainers can only see the public download counts GitHub shows on each release.

## If you host YTDL-UI for other people

- **Set `privacy_contact`** (admin console → Settings → Server, or `config.ini`) to an email address or link, so visitors know how to reach you. It's shown on `/privacy`.
- **Add your own policy** — who runs the server, how long you keep logs, your legal basis — by copying [`privacy.example.md`](privacy.example.md) to `data/privacy.md` (Docker: `/app/data/privacy.md`) and editing it. It appears as an "About this server" card at the top of `/privacy`, above the generated sections. Because it lives in your data folder, updates never overwrite it and it never ends up in the repository or a release (change the location with `privacy_notes`). It supports a small, safe Markdown subset; HTML is shown as text.
- Leave **`log_urls`** off unless you need it.
- **Your reverse proxy or CDN keeps its own logs.** If you set `trust_proxy`, the privacy page tells visitors a proxy is in front (and names Cloudflare when `trust_proxy = cloudflare`). Links shared into the app from another app arrive in the page address (`/?url=…`), so they can show up in those logs.
- `processing_mode = browser` with `fallback_to_server = false` means the server never stores visitors' files.
- A `cookies_file` signs yt-dlp's requests in to **your** account; use a throwaway one.
- Depending on where you and your visitors are, the law (for example the GDPR) may require more from you than this page provides — such as a legal basis for processing, a named controller, and how to request deletion. The `/privacy` page describes what the software does; it isn't legal advice.

## The project itself

This repository is hosted on GitHub, and releases and the Docker image are downloaded from GitHub and `ghcr.io`; [GitHub's privacy statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement) applies there. Issues and pull requests you open are public.

Found something in the code that doesn't match this document? Please [open an issue](https://github.com/JustAyden/YTDL-UI/issues) — the page and this file are meant to describe exactly what the code does.
