#!/usr/bin/env python3
"""epub-speed — a local read-faster server for EPUB files.

  python3 server.py                 # serve data/books/*
  python3 server.py ~/book.epub ... # serve specific files too
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import epub_text  # noqa: E402

STATIC = os.path.join(HERE, "static")
DATA = os.path.join(HERE, "data")
BOOKS = os.path.join(DATA, "books")
PROGRESS = os.path.join(DATA, "progress")
SOURCES = os.path.join(DATA, "sources.json")
PORT = int(os.environ.get("EPUB_SPEED_PORT", "8756"))

_lock = threading.Lock()
_cache: dict[str, dict] = {}      # path -> parsed book
_meta: dict[str, tuple] = {}      # path -> (mtime, size)
_extra: list[str] = []


# -- storage ---------------------------------------------------------------


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _load_sources():
    global _extra
    _extra = [p for p in _read_json(SOURCES, []) if isinstance(p, str)]
    for arg in sys.argv[1:]:
        path = os.path.abspath(os.path.expanduser(arg))
        if os.path.isfile(path) and path not in _extra:
            _extra.append(path)
    _write_json(SOURCES, _extra)


def _book_paths() -> list[str]:
    paths: list[str] = []
    if os.path.isdir(BOOKS):
        for name in sorted(os.listdir(BOOKS)):
            if name.lower().endswith(".epub"):
                paths.append(os.path.join(BOOKS, name))
    for path in _extra:
        if path.lower().endswith(".epub") and os.path.isfile(path):
            paths.append(path)
    seen, unique = set(), []
    for path in paths:
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _get_book(path: str) -> dict:
    stat = os.stat(path)
    stamp = (stat.st_mtime, stat.st_size)
    with _lock:
        if _meta.get(path) == stamp and path in _cache:
            return _cache[path]
    book = epub_text.load(path)
    with _lock:
        _cache[path] = book
        _meta[path] = stamp
    return book


def _library() -> list[dict]:
    books, problems = [], []
    for path in _book_paths():
        try:
            summary = epub_text.summarize(_get_book(path))
        except Exception as exc:  # a broken file must not empty the shelf
            problems.append({"file": os.path.basename(path), "error": str(exc)})
            continue
        summary["progress"] = _read_json(os.path.join(PROGRESS, summary["id"] + ".json"), None)
        books.append(summary)
    books.sort(key=lambda b: b["title"].lower())
    if problems:
        books.append({"errors": problems})
    return books


# -- http ------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "epub-speed"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet: startup banner is enough
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, code: int, message: str):
        self._json({"error": message}, code)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if not parts or parts == ["index.html"]:
                return self._static("index.html")
            if parts[0] == "static":
                return self._static("/".join(parts[1:]))
            if parts[0] == "api":
                return self._api_get(parts[1:])
            if parts[0] == "favicon.ico":
                return self._send(204)
            return self._error(404, "not found")
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            self._error(500, "internal error")

    def do_POST(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            if parts == ["api", "upload"]:
                return self._upload(raw)
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                payload = {}
            if len(parts) == 3 and parts[:2] == ["api", "progress"]:
                return self._save_progress(parts[2], payload)
            if parts == ["api", "add"]:
                return self._add_source(payload)
            return self._error(404, "not found")
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            self._error(500, "internal error")

    # -- routes ---------------------------------------------------------

    def _static(self, rel: str):
        target = os.path.realpath(os.path.join(STATIC, rel))
        if not target.startswith(os.path.realpath(STATIC) + os.sep):
            return self._error(403, "forbidden")
        if not os.path.isfile(target):
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _api_get(self, parts: list[str]):
        if parts == ["library"]:
            return self._json({"books": _library()})
        if len(parts) >= 2 and parts[0] == "book":
            book_id = parts[1]
            book = self._book_by_id(book_id)
            if book is None:
                return self._error(404, "unknown book")
            if len(parts) == 2:
                payload = epub_text.summarize(book)
                payload["progress"] = _read_json(
                    os.path.join(PROGRESS, book_id + ".json"), None)
                return self._json(payload)
            if len(parts) == 4 and parts[2] == "chapter":
                try:
                    number = int(parts[3])
                except ValueError:
                    return self._error(400, "bad chapter")
                chapters = book["chapters"]
                if not 0 <= number < len(chapters):
                    return self._error(404, "no such chapter")
                chapter = chapters[number]
                return self._json({
                    "index": chapter["index"],
                    "title": chapter["title"],
                    "words": chapter["words"],
                    "blocks": chapter["blocks"],
                })
        if len(parts) == 2 and parts[0] == "progress":
            return self._json(_read_json(
                os.path.join(PROGRESS, parts[1] + ".json"), {}))
        return self._error(404, "not found")

    def _book_by_id(self, book_id: str) -> dict | None:
        for path in _book_paths():
            if epub_text.hash_id(path) == book_id:
                try:
                    return _get_book(path)
                except Exception:
                    return None
        return None

    def _save_progress(self, book_id: str, payload: dict):
        if book_id not in {epub_text.hash_id(p) for p in _book_paths()}:
            return self._error(404, "unknown book")
        clean = {
            "chapter": int(payload.get("chapter") or 0),
            "idx": max(0, int(payload.get("idx") or 0)),
            "wpm": max(100, min(1200, int(payload.get("wpm") or 300))),
            "mode": payload.get("mode") if payload.get("mode") in ("rsvp", "pacer", "plain") else "rsvp",
            "at": int(payload.get("at") or 0),
        }
        _write_json(os.path.join(PROGRESS, book_id + ".json"), clean)
        return self._json({"ok": True})

    def _upload(self, raw: bytes):
        name = os.path.basename(unquote(self.headers.get("X-Filename") or "")).strip()
        if not name.lower().endswith(".epub"):
            return self._error(400, "only .epub files are accepted")
        if not raw:
            return self._error(400, "empty upload")
        if len(raw) > 300 * 1024 * 1024:
            return self._error(413, "file too large")
        os.makedirs(BOOKS, exist_ok=True)
        target = os.path.join(BOOKS, name)
        stem, ext = os.path.splitext(target)
        n = 1
        while os.path.exists(target):
            target = f"{stem}-{n}{ext}"
            n += 1
        with open(target, "wb") as fh:
            fh.write(raw)
        try:
            _get_book(target)
        except Exception as exc:
            try:
                os.unlink(target)
            except OSError:
                pass
            return self._error(400, f"not a readable EPUB: {exc}")
        return self._json({"ok": True, "file": os.path.basename(target)})

    def _add_source(self, payload: dict):
        path = os.path.abspath(os.path.expanduser(str(payload.get("path") or "")))
        if not os.path.isfile(path):
            return self._error(400, "no such file")
        try:
            _get_book(path)
        except Exception as exc:
            return self._error(400, f"cannot read: {exc}")
        if path not in _extra:
            _extra.append(path)
            _write_json(SOURCES, _extra)
        return self._json({"ok": True, "id": epub_text.hash_id(path)})


def _lan_hints(port: int) -> list[str]:
    hints = [f"http://localhost:{port}"]
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                             text=True, timeout=3).stdout.split()
        if out:
            hints.append(f"http://{out[0]}:{port}")
    except Exception:
        pass
    return hints


def main():
    os.makedirs(BOOKS, exist_ok=True)
    os.makedirs(PROGRESS, exist_ok=True)
    _load_sources()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("epub-speed")
    for hint in _lan_hints(PORT):
        print(f"  {hint}")
    print(f"  books: {BOOKS}")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
