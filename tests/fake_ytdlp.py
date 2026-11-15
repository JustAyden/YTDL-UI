"""A stand-in for yt-dlp used by the tests: prints the same progress markers and
writes small output files, without touching the network."""

import json
import os
import sys
import time


def main(argv):
    if "--version" in argv:
        print("2026.10.26")
        return 0
    url = argv[-1]
    if "fail" in url:
        print("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", file=sys.stderr)
        return 1
    if "--dump-single-json" in argv:
        if "playlist" in url:
            print(json.dumps({"_type": "playlist", "id": "PL1", "title": "My List", "uploader": "someone",
                              "entries": [{"id": f"v{i}", "title": f"Song {i}", "duration": 60 * i,
                                           "url": f"https://example.com/watch?v={i}"} for i in range(1, 4)]}))
        else:
            print(json.dumps({"id": "abc", "title": "Test Video", "uploader": "Tester", "duration": 125,
                              "thumbnail": "https://example.com/t.jpg", "extractor_key": "Fake",
                              "formats": [
                                  {"format_id": "137", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 5_000_000},
                                  {"format_id": "136", "height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 2_000_000},
                                  {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128, "filesize": 1_000_000},
                              ]}))
        return 0

    out = argv[argv.index("-o") + 1]
    items = [None]
    if "--yes-playlist" in argv:
        sel = argv[argv.index("-I") + 1]
        items = [int(x) for x in sel.split(",")] if ":" not in sel else [1, 2, 3]
    ext = "mp4"
    if "--audio-format" in argv:
        ext = argv[argv.index("--audio-format") + 1]
    elif "--merge-output-format" in argv:
        ext = argv[argv.index("--merge-output-format") + 1]
    for idx in items:
        if idx == 2 and "skip2" in url:
            print("ERROR: [youtube] v2: Video unavailable", file=sys.stderr, flush=True)
            continue
        title = f"Song {idx}" if idx else "Test Video"
        print(f"@@I {idx if idx else 'NA'}|3|2000|137+140|{title}", flush=True)
        for _fmt, total in (("137", 1500), ("140", 500)):
            for done in (0, total // 2, total):
                print(f"@@P downloading|{done}|{total}|NA|1000|1|NA|NA", flush=True)
                time.sleep(0.01)
            print(f"@@P finished|{total}|{total}|NA|1000|NA|NA|NA", flush=True)
        print("@@PP started|Merger", flush=True)
        name = out.replace("%(playlist_index)03d", f"{idx:03d}" if idx else "") \
                  .replace("%(title).170B", title).replace("%(title).180B", title).replace("%(ext)s", ext)
        with open(name, "wb") as fh:
            fh.write(os.urandom(2048))
        print("@@PP finished|Merger", flush=True)
        print(f"@@F {name}", flush=True)
        if "slow" in url:
            time.sleep(30)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
