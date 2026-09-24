"""
Desktop shell. Two modes:
  * standalone (default): starts the Flask server on a free localhost port, opens a window.
  * client:  --server-url http://192.168.1.10:8000  -> window only, for extra terminals / KDS.
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request

TITLE = "Restaurant ERP"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/v1/health", timeout=2):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument("--server-url", help="Connect to an existing server instead of starting one")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    try:
        import webview
    except ImportError:
        print("pywebview is not installed. Run: pip install -r requirements-desktop.txt")
        return 1

    url = args.server_url
    if not url:
        from waitress import serve

        from app import create_app

        port = args.port or _free_port()
        wsgi_app = create_app()
        threading.Thread(
            target=serve, args=(wsgi_app,), kwargs={"host": "127.0.0.1", "port": port}, daemon=True
        ).start()
        url = f"http://127.0.0.1:{port}"

    if not _wait_ready(url):
        print(f"Server at {url} did not become ready.")
        return 1

    webview.create_window(TITLE, url, width=1440, height=900, min_size=(1024, 700))
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
