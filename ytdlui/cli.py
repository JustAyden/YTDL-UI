"""Command line entry point: ``python server.py [options]`` / ``ytdl-ui [options]``."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import time

from . import __version__


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("waitress.queue", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _run_ytdlp(argv: list[str]) -> int:
    """Frozen builds: act as yt-dlp itself (``ytdl-ui --run-ytdlp <args>``)."""
    import yt_dlp
    try:
        yt_dlp.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if not isinstance(exc.code, str) else 1
    return 0


def _port_free(host: str, port: int) -> bool:
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
        except OSError:
            return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ytdl-ui", description="YTDL-UI — self-hosted web UI for yt-dlp.",
        epilog="Every setting in config.example.ini can be passed with --set name=value "
               "or as an environment variable YTDLUI_<NAME>.")
    p.add_argument("-p", "--port", type=int, help="port to listen on (default 5000)")
    p.add_argument("--host", help="address to bind (default 127.0.0.1; 0.0.0.0 = all interfaces)")
    p.add_argument("-c", "--config", help="path to config.ini (default: next to the app)")
    p.add_argument("--data-dir", help="folder for temp files, caches and state")
    p.add_argument("--processing", choices=("server", "browser", "choice"),
                   help="where downloads are processed (see README)")
    p.add_argument("--trust-proxy", help="number of reverse proxies in front, or 'cloudflare'")
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                   help="override any setting (repeatable)")
    p.add_argument("--open", action="store_true", help="open the UI in a browser after starting")
    g = p.add_argument_group("maintenance commands (run, then exit)")
    g.add_argument("--version", action="store_true", help="print versions and exit")
    g.add_argument("--check-update", action="store_true", help="check GitHub for a newer release")
    g.add_argument("--update", action="store_true",
                   help="update YTDL-UI (git pull / release download) and yt-dlp, then exit")
    g.add_argument("--update-ytdlp", action="store_true", help="update yt-dlp only")
    g.add_argument("--install-ffmpeg", action="store_true",
                   help="download a static ffmpeg build into data/bin (Windows/Linux)")
    g.add_argument("--fetch-wasm", action="store_true",
                   help="download the ffmpeg.wasm core for wasm_source = local")
    g.add_argument("--print-config", action="store_true", help="print the effective settings")
    g.add_argument("--write-example-config", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--wait-for-port", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--run-ytdlp"]:
        return _run_ytdlp(argv[1:])

    from .config import ConfigError, load_settings, render_example

    args = build_parser().parse_args(argv)
    from .config import FROZEN
    if FROZEN and not argv:
        args.open = True            # double-clicked executable: show the UI right away
    if args.write_example_config:
        sys.stdout.write(render_example())
        return 0

    overrides: dict[str, object] = {}
    for item in args.set:
        if "=" not in item:
            print(f"--set expects NAME=VALUE, got {item!r}", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        overrides[k.strip().lower()] = v
    for key, val in (("host", args.host), ("port", args.port), ("data_dir", args.data_dir),
                     ("processing_mode", args.processing), ("trust_proxy", args.trust_proxy)):
        if val is not None:
            overrides[key] = val
    try:
        settings = load_settings(args.config, overrides)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    settings.ensure_dirs()
    _setup_logging()
    log = logging.getLogger("ytdlui")

    if args.print_config:
        for k, v in settings.as_dict().items():
            if k in ("password", "admin_password") and v:
                v = "********"
            print(f"{k} = {v}")
        return 0

    from .updater import AppUpdater, YtdlpUpdater, install_ffmpeg, install_method
    from .ytdlp import YtDlp

    if args.version or args.check_update or args.update or args.update_ytdlp or args.install_ffmpeg \
            or args.fetch_wasm:
        ytdlp = YtDlp(settings)
        if args.version:
            print(f"YTDL-UI {__version__} ({install_method()} install)")
            print(f"yt-dlp  {ytdlp.version() or 'not found'} [{ytdlp.tools.ytdlp_kind}] {ytdlp.tools.ytdlp_location}")
            print(f"ffmpeg  {ytdlp.ffmpeg_version() or 'not found'} {ytdlp.tools.ffmpeg or ''}")
            print(f"js      {ytdlp.tools.js_runtime or 'none'} {ytdlp.tools.js_runtime_path or ''}")
        code = 0
        if args.check_update or args.update:
            st = AppUpdater(settings).check()
            if st["latest"]:
                state = "update available" if st["update_available"] else "up to date"
                print(f"YTDL-UI {st['current']} — latest release {st['latest']} ({state})")
                if st["update_available"] and st["url"]:
                    print(f"  {st['url']}")
            else:
                print(f"Couldn't check for updates: {st['error']}")
                code = 1
        if args.update:
            res = AppUpdater(settings).apply()
            print(res["message"])
            code = 0 if res["ok"] else 1
        if args.update or args.update_ytdlp:
            res = YtdlpUpdater(settings, ytdlp, lambda: True).update()
            print(res["message"])
            code = code or (0 if res["ok"] else 1)
        if args.install_ffmpeg:
            try:
                print("Downloading ffmpeg… (this is a large file)")
                path = install_ffmpeg(settings, None)
                print(f"ffmpeg installed: {path}")
            except Exception as exc:
                print(f"ffmpeg install failed: {exc}")
                code = 1
        if args.fetch_wasm:
            from .web import Services
            svc = Services.__new__(Services)
            svc.settings = settings
            svc.fetch_wasm()
            print(f"ffmpeg.wasm core: {svc.wasm_state} ({settings.vendor_path})")
            code = code or (0 if svc.wasm_state == "ready" else 1)
        if args.update and code == 0:
            print("Restart YTDL-UI to finish the update.")
        return code

    # ── serve ───────────────────────────────────────────────────────────
    host, port = settings.host, settings.port
    if args.wait_for_port:
        deadline = time.time() + 20
        while not _port_free(host, port) and time.time() < deadline:
            time.sleep(0.5)
    if not _port_free(host, port):
        log.error("Port %s on %s is already in use. Pick another with --port.", port, host)
        return 1

    from .web import create_app
    app = create_app(settings)
    svc = app.extensions["ytdlui"]

    shown = "localhost" if host in ("127.0.0.1", "::1", "localhost") else host
    url = f"http://{'YOUR-IP' if host in ('0.0.0.0', '::') else shown}:{port}"
    t = svc.ytdlp.tools
    print()
    print(f"  YTDL-UI {__version__}")
    print(f"  ➜  {url}")
    print(f"  yt-dlp {svc.ytdlp.version() or 'missing'} [{t.ytdlp_kind}] · ffmpeg "
          f"{'ok' if t.ffmpeg else 'missing'} · JS runtime {t.js_runtime or 'none'} · "
          f"processing: {settings.processing_mode}")
    if settings.config_path:
        print(f"  config: {settings.config_path}")
    print(flush=True)
    if host in ("0.0.0.0", "::"):
        log.warning("Listening on all interfaces — anyone who can reach this machine can use it.")
        if not settings.password:
            log.warning("No password is set. Consider `password = ...` in config.ini.")
    if not t.ffmpeg and settings.server_downloads_enabled:
        log.warning("ffmpeg not found: server downloads can't merge video+audio or convert audio. "
                    "Install it, or run with --install-ffmpeg.")
    if not t.js_runtime:
        log.warning("No JavaScript runtime (deno/node) found: YouTube may only offer low "
                    "qualities. Install deno (https://deno.land) or Node.js.")

    if args.open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    try:
        from waitress import serve
    except ImportError:
        log.info("waitress not installed — using Flask's built-in server.")
        app.run(host=host, port=port, debug=False, threaded=True)
    else:
        serve(app, host=host, port=port, threads=max(4, settings.threads), ident="YTDL-UI",
              channel_timeout=300, connection_limit=500, cleanup_interval=30,
              outbuf_high_watermark=8 * 1024 * 1024, asyncore_use_poll=True)
    svc.stop.set()
    svc.jobs.shutdown()
    return 0
