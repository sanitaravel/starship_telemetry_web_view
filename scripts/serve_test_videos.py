"""Serve locally downloaded broadcast HLS streams over HTTP for pipeline testing.

Each flight lives in test_videos/<flight>/ (output.m3u8 plus seg_*.ts) at the
repo root. That folder is local-only and never committed. Naming a flight makes
it the web root, so the pipeline reads http://localhost:8001/output.m3u8; with
no flight every one is served at http://localhost:8001/<flight>/output.m3u8.

Usage:
    python scripts/serve_test_videos.py ift-12            # one flight at /output.m3u8
    python scripts/serve_test_videos.py                   # all flights at /<flight>/output.m3u8
    python scripts/serve_test_videos.py ift-13 --port 8002
    python scripts/serve_test_videos.py --dir D:/videos --host 0.0.0.0
"""

import argparse
import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = ROOT / "test_videos"
PLAYLIST = "output.m3u8"


class HLSRequestHandler(SimpleHTTPRequestHandler):
    """Static file handler with HLS MIME types, open CORS and quiet segment logging."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
    }

    def end_headers(self) -> None:
        # Lets browser players (hls.js and the like) on another origin read the stream.
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_request(self, code="-", size="-") -> None:
        # A stream is thousands of segments; only log them when something goes wrong.
        if self.path.split("?", 1)[0].endswith(".ts") and str(code).startswith(("2", "3")):
            return
        super().log_request(code, size)


class QuietServer(ThreadingHTTPServer):
    """Threaded server that ignores clients hanging up mid-response (ffmpeg seeks do that)."""

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def flights(videos_dir: Path) -> list[str]:
    """Return the names of the subfolders of ``videos_dir`` that hold a playlist."""
    return sorted(p.parent.name for p in videos_dir.glob(f"*/{PLAYLIST}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flight", nargs="?", help="flight folder to serve as the web root, e.g. ift-12")
    parser.add_argument("--port", type=int, default=8001, help="port to listen on (default: 8001)")
    parser.add_argument("--host", default="127.0.0.1", help="address to bind (default: 127.0.0.1)")
    parser.add_argument("--dir", type=Path, default=VIDEOS_DIR, help=f"videos root (default: {VIDEOS_DIR})")
    args = parser.parse_args()

    videos_dir = args.dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"videos directory not found: {videos_dir}")
    available = flights(videos_dir)
    listing = ", ".join(available) or "none"

    if args.flight:
        web_root = videos_dir / args.flight
        if not (web_root / PLAYLIST).is_file():
            sys.exit(f"{web_root / PLAYLIST} not found (available flights: {listing})")
        paths = [PLAYLIST]
    else:
        if not available:
            sys.exit(f"no <flight>/{PLAYLIST} found in {videos_dir}")
        web_root = videos_dir
        paths = [f"{name}/{PLAYLIST}" for name in available]

    handler = functools.partial(HLSRequestHandler, directory=str(web_root))
    with QuietServer((args.host, args.port), handler) as server:
        # 0.0.0.0 is not a usable address in a URL; localhost reaches it.
        host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
        print(f"Serving {web_root}")
        for path in paths:
            print(f"  http://{host}:{args.port}/{path}")
        print("Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
