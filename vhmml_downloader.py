#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""vHMML Downloader: a desktop window and a command-line interface.

Start with a platform launcher, or run this script after installing requirements.txt.
Only uses images made available to your own vHMML account. No ID guessing.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

try:
    import requests
    import pymupdf as fitz
    from PIL import Image
except ImportError:
    print("Please open Start-Mac.command or Start-Windows.bat to install the required packages.")
    print("Or: python -m pip install -r requirements.txt")
    raise SystemExit(1)

BASE = "https://www.vhmml.org"
DEFAULT_OUTPUT = Path.home() / "Downloads" / "vHMML Downloads"
EXAMPLES = "\n".join([
    "https://w3id.org/vhmml/readingRoom/view/501579",
    "https://w3id.org/vhmml/readingRoom/view/501578",
    "https://w3id.org/vhmml/readingRoom/view/504977",
])


class DownloadError(Exception):
    """An error whose text is safe to show without exposing session secrets."""


class SignInRequired(DownloadError):
    pass


class Cancelled(DownloadError):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("Stopped. Run the same download again to resume.")


def wait_cancel(cancel, seconds):
    if cancel.wait(seconds):
        check_cancel(cancel)


def text_value(value):
    if isinstance(value, dict):
        value = " ".join(text_value(v) for v in value.values())
    elif isinstance(value, list):
        value = " ".join(text_value(v) for v in value)
    return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def safe_name(value, fallback="Manuscript"):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text_value(value))
    name = re.sub(r"\s+", "_", name).strip(" ._")[:70].rstrip(". ")
    if not name:
        name = fallback
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I):
        name = "_" + name
    return name


def https_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise DownloadError("Only HTTPS links without a username or password are supported.")
    return quote(url, safe=":/?&=%+#,;@!$'()*[]~")


def normalize_input(value):
    """Return (kind, canonical URL). Bare numbers intentionally remain ambiguous."""
    value = value.strip().strip('<>"\'')
    if value.startswith(("www.vhmml.org/", "vhmml.org/", "w3id.org/")):
        value = "https://" + value
    p = urlsplit(value)
    host = (p.hostname or "").lower()
    path = unquote(p.path).rstrip("/")
    if p.username or p.password or p.port not in (None, 80, 443):
        raise DownloadError("Paste a normal vHMML Reading Room or IIIF manifest link.")
    if p.scheme not in ("http", "https"):
        raise DownloadError("Paste the whole Reading Room or IIIF manifest link, not just its number.")
    if host == "w3id.org" and path.startswith("/vhmml/"):
        path = path[len("/vhmml"):]
    elif host not in ("vhmml.org", "www.vhmml.org"):
        raise DownloadError("This downloader accepts vHMML links only.")
    if re.fullmatch(r"/readingRoom/view/\d+", path):
        return "record", BASE + path
    if re.fullmatch(r"/image/manifest/\d+", path):
        return "manifest", BASE + path
    raise DownloadError("Use a Reading Room /view/NUMBER or /image/manifest/NUMBER link.")


def parse_inputs(value):
    values = value.split() if isinstance(value, str) else value
    result = []
    for item in values:
        pair = normalize_input(item)
        if pair not in result:
            result.append(pair)
    if not result:
        raise DownloadError("Paste at least one manuscript link first.")
    return result


def imported_headers(value):
    """Accept a Cookie value or copied request headers, without decoding cookie tokens."""
    value = value.strip()
    agent = ""
    if re.search(r"(?im)^\s*cookie\s*:", value):
        lines = value.splitlines()
        cookie_lines = [line.split(":", 1)[1].strip() for line in lines
                        if re.match(r"^\s*cookie\s*:", line, re.I)]
        value = "; ".join(cookie_lines)
        for line in lines:
            if re.match(r"^\s*user-agent\s*:", line, re.I):
                agent = line.split(":", 1)[1].strip()
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise DownloadError("Paste the Cookie request-header value, or the copied request headers. "
                            "Do not paste Set-Cookie response headers or just the session number.")
    # A semicolon inside a quoted value is data; retain quotes and escapes byte-for-byte.
    parts, start, quoted, escaped = [], 0, False, False
    for i, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            parts.append(value[start:i].strip())
            start = i + 1
    parts.append(value[start:].strip())
    if quoted:
        raise DownloadError("The pasted Cookie header has an unfinished quote. Copy its complete value again.")
    result = {}
    for part in parts:
        if not part:
            continue
        name, sep, token = part.partition("=")
        if not sep or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name):
            raise DownloadError("The pasted text is not a complete Cookie request header. "
                                "Use Request Headers > Cookie, not Response Headers > Set-Cookie.")
        if name.lower() in ("path", "domain", "expires", "max-age", "samesite"):
            raise DownloadError("That looks like a Set-Cookie response header. Copy Request Headers > Cookie instead.")
        if name in result and result[name] != token:
            raise DownloadError("This request contains two different values for the same cookie. "
                                "Try copying the Cookie header from a fresh Reading Room home-page request.")
        result[name] = token
    if not result:
        raise DownloadError("No Cookie name=value pairs were found in the pasted text.")
    return result, agent


def make_session(cookies=(), raw_cookie="", user_agent=""):
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent or "vHMML-Downloader/1.0 (personal research)",
                            "Referer": BASE + "/"})
    # Never put Cookie in session.headers: that would leak it to external image hosts.
    for c in cookies:
        domain = c.get("domain", "")
        if domain.lstrip(".") not in ("vhmml.org", "www.vhmml.org"):
            continue
        expiry = c.get("expires", -1)
        session.cookies.set(c["name"], c["value"], domain=domain,
                            path=c.get("path", "/"), secure=c.get("secure", True),
                            expires=int(expiry) if expiry and expiry > 0 else None)
    if raw_cookie.strip():
        values, copied_agent = imported_headers(raw_cookie)
        if copied_agent:
            session.headers["User-Agent"] = copied_agent
        for name, token in values.items():
            # This Cookie header came from the canonical www host. Match that host so a
            # later Set-Cookie can replace it instead of creating a second JSESSIONID.
            session.cookies.set(name, token, domain="www.vhmml.org", path="/", secure=True)
    session.vhmml_checks = []
    session.vhmml_login_hint = "Not checked"
    return session


def get_response(session, url, cancel, *, stream=False, timeout=60, headers=None):
    """Bounded retries, including rate limits, without printing response bodies/cookies."""
    url = https_url(url)
    for attempt in range(4):
        check_cancel(cancel)
        try:
            response = session.get(url, timeout=(15, timeout), stream=stream, headers=headers)
        except requests.RequestException:
            if attempt == 3:
                raise DownloadError("The connection failed. Check your internet connection and try again.") from None
            wait_cancel(cancel, 2 ** attempt)
            continue
        if hasattr(session, "vhmml_checks"):
            path = urlsplit(url).path
            category = ("manifest" if "/image/manifest/" in path else "catalog record" if "/readingRoom/view/" in path
                        else "catalog home" if path.rstrip("/") == "/readingRoom" else "image/resource")
            session.vhmml_checks.append(f"{category}: HTTP {response.status_code}")
            session.vhmml_checks = session.vhmml_checks[-12:]
        if response.status_code in (401, 403):
            code = response.status_code
            response.close()
            raise SignInRequired(f"vHMML did not accept this request (HTTP {code}). Your browser may still be signed in. "
                                 "The imported session, account permissions, or the site's request checks may be responsible. "
                                 "Copy diagnostics for a report with no cookie values.")
        if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
            delay = response.headers.get("Retry-After", "")
            delay = min(120, max(1, int(delay))) if delay.isdigit() else 2 ** (attempt + 1)
            response.close()
            if attempt == 3:
                raise DownloadError("The server is busy or limiting downloads. Please try again later.")
            wait_cancel(cancel, delay)
            continue
        if not response.ok:
            code = response.status_code
            response.close()
            raise DownloadError(f"The server returned HTTP {code}. Check the manuscript link and try again.")
        if urlsplit(response.url).scheme != "https":
            response.close()
            raise DownloadError("The server redirected to an insecure link. Download stopped.")
        if re.search(r"/(?:login|signin)(?:/|$)", urlsplit(response.url).path, re.I):
            response.close()
            raise SignInRequired("Your login has expired. Sign in and try again.")
        return response
    raise DownloadError("Request failed.")


def verify_login(session, cancel):
    """Advisory hint only. The actual requested manifest decides whether access works."""
    with get_response(session, BASE + "/readingRoom/", cancel,
                      headers={"Cache-Control": "no-cache", "Pragma": "no-cache"}) as response:
        source = response.text
    # The page can initialize false, then assign true later. Do not read just the
    # first declaration or treat a public/cached home page as proof of image denial.
    values = re.findall(r"\bisAuthenticated\s*=\s*(true|false)\s*;", source)
    logout_link = re.search(r"href\s*=\s*['\"][^'\"]*/(?:logout|signout)(?:[/?'\"])", source, re.I)
    signed_in = bool(logout_link or (values and values[-1] == "true"))
    session.vhmml_login_hint = "Home page reports signed in" if signed_in else "Home page did not confirm login; access not yet tested"
    return signed_in


def import_login_check(session, cancel):
    """Keep a well-formed import even when the optional home-page check is inconclusive."""
    try:
        verify_login(session, cancel)
    except Cancelled:
        raise
    except DownloadError:
        session.vhmml_login_hint = "Home-page check unavailable; access not yet tested"
    return session.vhmml_login_hint


def diagnostic_report(session):
    lines = ["vHMML Downloader v4 diagnostic report", f"System: {sys.platform}; Python {sys.version.split()[0]}"]
    if session is None:
        lines.append("No imported session")
    else:
        lines += [f"Cookies loaded: {len(session.cookies)}",
                  "JSESSIONID present: " + ("yes" if any(c.name.upper() == "JSESSIONID" for c in session.cookies) else "no"),
                  "Login check: " + getattr(session, "vhmml_login_hint", "Not checked"),
                  "Requests: " + (", ".join(getattr(session, "vhmml_checks", [])) or "None")]
    lines.append("No cookie values, passwords, account details, or full URLs are included.")
    return "\n".join(lines)


def record_info(source):
    # Current Reading Room pages embed their catalog record as JSON, not executable code.
    match = re.search(r"\bselectedObjectJson\s*=\s*", source)
    if not match:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(source[match.end():])
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def resolve_record(session, url, cancel):
    with get_response(session, url, cancel) as response:
        source = response.text
    info = record_info(source)
    wanted = url.rsplit("/", 1)[1]
    if not info or str(info.get("id")) != wanted:
        raise DownloadError("Could not identify this Reading Room record. Open it in vHMML, "
                            "copy its IIIF Link from the information button, and paste that link instead.")
    if info.get("viewableOnline") is False:
        raise DownloadError(f"{info.get('hmmlProjectNumber', 'This record')} has no online images available.")
    # The site's own template supplies /image/manifest/{{id}} using this record's id.
    # Require that template or an explicit link; never infer IDs from neighboring records.
    pattern = r"/image/manifest/(?:\{\{\s*id\s*\}\}|" + re.escape(wanted) + r")(?!\d)"
    if not re.search(pattern, source):
        raise DownloadError("The Reading Room page format has changed. Paste its IIIF manifest link instead.")
    return BASE + "/image/manifest/" + wanted, info


def as_list(value):
    return value if isinstance(value, list) else [value] if value else []


def image_url(resource):
    if not isinstance(resource, dict):
        raise DownloadError("This page has an unsupported image description.")
    if resource.get("type") == "Choice" or resource.get("@type") == "oa:Choice":
        raise DownloadError("This page offers multiple image choices. It cannot be downloaded reliably by this version.")
    for service in as_list(resource.get("service")):
        if not isinstance(service, dict):
            continue
        sid = service.get("id") or service.get("@id")
        kind = str(service.get("type", "")) + str(service.get("@type", ""))
        context = json.dumps(service.get("@context", "")) + json.dumps(service.get("profile", ""))
        if not sid or "Auth" in kind or "auth/" in context:
            continue
        if "ImageService" not in kind and "image/" not in context and not service.get("profile"):
            continue
        version = 3 if "ImageService3" in kind or "image/3" in context else 2
        quality = "native" if "image/1" in context or "image-api/1" in context else "default"
        size = "max" if version == 3 else "full"
        return https_url(str(sid).rstrip("/") + f"/full/{size}/0/{quality}.jpg")
    direct = resource.get("id") or resource.get("@id")
    if not direct:
        raise DownloadError("This page does not provide a downloadable image.")
    return https_url(str(direct))


@dataclass
class Page:
    number: int
    label: str
    url: str


def manifest_pages(manifest):
    sequences = manifest.get("sequences") or []
    if sequences:
        canvases = sequences[0].get("canvases", [])
        v2 = True
    else:
        canvases = manifest.get("items", [])
        v2 = False
    if not canvases:
        raise DownloadError("This link has no manuscript pages. It may be a collection or metadata-only record.")
    pages = []
    for i, canvas in enumerate(canvases, 1):
        if v2:
            resources = [a.get("resource") for a in canvas.get("images", [])]
        else:
            resources = []
            for annotation_page in canvas.get("items", []):
                if "items" not in annotation_page:
                    raise DownloadError("This manifest uses linked annotation pages, which this version does not support.")
                for annotation in annotation_page.get("items", []):
                    if "painting" in as_list(annotation.get("motivation")):
                        resources.extend(as_list(annotation.get("body")))
        if len(resources) != 1:
            raise DownloadError(f"Page {i} has {len(resources)} image layers. "
                                "This version supports one complete image per page; no partial PDF was created.")
        pages.append(Page(i, text_value(canvas.get("label")) or f"Page {i}", image_url(resources[0])))
    return pages


@dataclass
class Manuscript:
    source: str
    url: str
    title: str
    manifest: dict
    pages: list[Page]

    @property
    def folder_name(self):
        identifier = self.url.rsplit("/", 1)[-1]
        return safe_name(self.title) + "__" + identifier


def inspect_manuscript(session, kind, url, cancel):
    source = url
    info = {}
    if kind == "record":
        url, info = resolve_record(session, url, cancel)
    with get_response(session, url, cancel) as response:
        try:
            manifest = response.json()
        except ValueError:
            raise SignInRequired("The manifest returned a web page instead of image information. "
                                 "Sign in and try again, or paste the IIIF Link from the viewer.") from None
    if not isinstance(manifest, dict):
        raise DownloadError("This link did not return a manuscript manifest.")
    label = text_value(manifest.get("label"))
    project = text_value(info.get("hmmlProjectNumber"))
    # Cross-check catalog identity when available. Require separate word/number tokens.
    if project:
        metadata = " ".join(text_value(m.get("value")) for m in manifest.get("metadata", []))
        tokenized = re.sub(r"[\W_]+", " ", label + " " + metadata).casefold().split()
        tokens = re.sub(r"[\W_]+", " ", project).casefold().split()
        if not all(t in tokenized for t in tokens):
            raise DownloadError(f"The manifest does not identify {project}. "
                                "Download stopped to avoid saving a different manuscript. Paste its IIIF Link instead.")
    return Manuscript(source, url, project or label or "Manuscript", manifest, manifest_pages(manifest))


def atomic_json(path, value):
    temp = path.with_name(path.name + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def valid_image(path):
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            return image.width > 0 and image.height > 0
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        return False


def fetch_image(session, page, path, cancel):
    partial = path.with_name(path.name + ".part")
    try:
        for attempt in range(3):
            check_cancel(cancel)
            try:
                with get_response(session, page.url, cancel, stream=True, timeout=60) as response:
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "html" in content_type or "json" in content_type:
                        raise SignInRequired("The image server returned a sign-in or error page. "
                                             "Sign in again, import the refreshed session, and retry your links.")
                    expected_size = response.headers.get("Content-Length", "")
                    with partial.open("wb") as file:
                        for chunk in response.iter_content(256 * 1024):
                            check_cancel(cancel)
                            if chunk:
                                file.write(chunk)
                    # Length is only comparable when requests has not decompressed the response.
                    if expected_size.isdigit() and not response.headers.get("Content-Encoding"):
                        if partial.stat().st_size != int(expected_size):
                            raise ValueError("Incomplete image")
                if not valid_image(partial):
                    raise ValueError("Invalid image")
                os.replace(partial, path)
                return
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise DownloadError(f"Page {page.number} was incomplete or unreadable. "
                                        "Try again to resume; completed pages are kept.") from None
                wait_cancel(cancel, 2 ** attempt)
    finally:
        partial.unlink(missing_ok=True)


def make_pdf(paths, output, title, cancel, log):
    temp = output.with_name(output.stem + ".part.pdf")
    try:
        with fitz.open() as pdf:
            for i, path in enumerate(paths, 1):
                check_cancel(cancel)
                try:
                    with fitz.open(path) as image:
                        data = image.convert_to_pdf()
                except (fitz.FileDataError, ValueError):
                    # Some direct resources (notably WebP) need a lossless format conversion.
                    # Pixel dimensions are kept; normal JPEGs use the direct path above.
                    buffer = io.BytesIO()
                    with Image.open(path) as image:
                        image.convert("RGB").save(buffer, format="PNG")
                    with fitz.open("png", buffer.getvalue()) as image:
                        data = image.convert_to_pdf()
                with fitz.open("pdf", data) as page_pdf:
                    pdf.insert_pdf(page_pdf)
                if i % 25 == 0 or i == len(paths):
                    log(f"Building PDF: {i}/{len(paths)} pages")
            pdf.set_metadata({"title": title, "creator": "vHMML Downloader"})
            pdf.save(temp, garbage=3, deflate=True)
        check_cancel(cancel)
        with fitz.open(temp) as check:
            if check.page_count != len(paths):
                raise DownloadError("The PDF page count did not match. Images have been kept.")
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def download_manuscript(session, manuscript, output, cancel, log=print,
                        progress=lambda a, b: None, images_only=False, delay=0.5):
    folder = output / manuscript.folder_name
    images = folder / "images"
    images.mkdir(parents=True, exist_ok=True)
    state_file = folder / "download-state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or not isinstance(state.get("pages", {}), dict):
        state = {}
    if state.get("manifest") != manuscript.url:
        state = {"manifest": manuscript.url, "pages": {}}
    atomic_json(folder / "manifest.json", manuscript.manifest)
    atomic_json(folder / "source.json", {"reading_room_or_manifest": manuscript.source,
                                       "manifest": manuscript.url, "title": manuscript.title})
    paths = []
    for page in manuscript.pages:
        check_cancel(cancel)
        # Sequence prefix preserves order and prevents duplicate labels overwriting each other.
        # .image is intentional: IIIF's direct resource can be JPEG, PNG, TIFF, or JP2.
        # The downloaded format is identified from its bytes; the extension is fixed below.
        key = f"{page.number:06d}"
        entry = state["pages"].get(key, {})
        if not isinstance(entry, dict):
            entry = {}
        filename = entry.get("file", "")
        path = images / filename if filename and Path(filename).name == filename else images / (key + ".image")
        reusable = (entry.get("url") == page.url and path.is_file()
                    and entry.get("sha256") == digest(path) and valid_image(path))
        if reusable:
            log(f"{manuscript.title}: {page.number}/{len(manuscript.pages)} — already saved")
        else:
            log(f"{manuscript.title}: {page.number}/{len(manuscript.pages)} — downloading {page.label}")
            fetch_image(session, page, path, cancel)
            with Image.open(path) as image:
                extension = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif", "JPEG2000": ".jp2",
                             "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp"}.get(image.format, ".image")
            named = images / (key + "_" + safe_name(Path(page.label).stem, "page") + extension)
            if named != path:
                os.replace(path, named)
            path = named
            state["pages"][key] = {"url": page.url, "file": path.name, "sha256": digest(path)}
            atomic_json(state_file, state)
            wait_cancel(cancel, delay)
        paths.append(path)
        progress(page.number, len(manuscript.pages))
    if not images_only:
        log(f"{manuscript.title}: creating PDF...")
        make_pdf(paths, folder / (safe_name(manuscript.title) + ".pdf"), manuscript.title, cancel, log)
    log(f"Saved: {folder}")
    return folder


def browser_login(start_url, finish, cancel, log, ready=lambda: None):
    """Run entirely on one worker thread; the GUI communicates using Events."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise DownloadError("Browser sign-in needs Playwright. Open the platform launcher to install it.") from None
    with sync_playwright() as playwright:
        browser = None
        # Reuse an installed browser binary, with an isolated session and no existing profile access.
        for channel in ("chrome", "msedge", None):
            check_cancel(cancel)
            try:
                browser = playwright.chromium.launch(headless=False, **({"channel": channel} if channel else {}))
                break
            except Exception:
                pass
        if browser is None:
            log("Installing the sign-in browser for first use. This may take a few minutes...")
            process = subprocess.Popen([sys.executable, "-m", "playwright", "install", "chromium"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if cancel.wait(0.2):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    check_cancel(cancel)
            if process.returncode:
                raise DownloadError("The sign-in browser could not be installed. Check your internet connection. "
                                    "On Linux, Playwright may also need system browser libraries.")
            check_cancel(cancel)
            try:
                browser = playwright.chromium.launch(headless=False)
            except Exception:
                raise DownloadError("The sign-in browser could not open. Try the advanced cookie option, "
                                    "or install the Playwright browser system dependencies on Linux.") from None
        try:
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                log("The page is still loading. You can navigate in the browser yourself.")
            log("In the opened browser: sign in to vHMML. No manuscript viewer is needed. "
                "Then return here and choose 'Use this login'. Keep the browser open until then.")
            ready()
            while not finish.is_set():
                check_cancel(cancel)
                if not browser.is_connected() or not context.pages:
                    raise DownloadError("The sign-in browser was closed before the login was copied. Please try again.")
                # Pump Playwright events, including page-close/disconnect events.
                try:
                    context.pages[0].wait_for_timeout(200)
                except Exception:
                    raise DownloadError("The sign-in browser closed. Please try again.") from None
            check_cancel(cancel)
            cookies = [c for c in context.cookies()
                       if c.get("domain", "").lstrip(".") in ("vhmml.org", "www.vhmml.org")]
            if not cookies:
                raise DownloadError("No vHMML session was found. Sign in to vHMML before continuing; no viewer is needed.")
            user_agent = context.pages[0].evaluate("navigator.userAgent")
            return cookies, user_agent
        finally:
            browser.close()


def open_folder(folder):
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(folder))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def run_gui(initial_urls=(), output=DEFAULT_OUTPUT):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError:
        raise DownloadError("The window needs Tkinter. Install Python with Tcl/Tk from python.org, "
                            "or run with --cli and a manuscript link.") from None

    class App:
        def __init__(self, root):
            self.root = root
            self.events = queue.Queue()
            self.cancel = threading.Event()
            self.finish_login = threading.Event()
            self.cookies = []
            self.user_agent = ""
            self.raw_cookie = os.environ.get("VHMML_COOKIE", os.environ.get("VHMM_COOKIE", ""))
            self.http_session = None
            self.manuscripts = []
            self.checked_input = ""
            self.busy = False
            root.title("Manuscript Downloader for vHMML (v4)")
            root.geometry("900x800")
            root.minsize(720, 650)
            root.protocol("WM_DELETE_WINDOW", self.close)
            root.columnconfigure(0, weight=1)
            root.rowconfigure(0, weight=1)
            panel = ttk.Frame(root, padding=18)
            panel.grid(sticky="nsew")
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(4, weight=1)
            panel.rowconfigure(9, weight=1)
            ttk.Label(panel, text="Manuscript Downloader for vHMML", font=("TkDefaultFont", 22, "bold")).grid(sticky="w")
            ttk.Label(panel, text="Sign in once • Paste your links • Save images and PDFs").grid(sticky="w", pady=(2, 12))
            auth = ttk.Frame(panel)
            auth.grid(sticky="ew", pady=(0, 10))
            self.login_button = ttk.Button(auth, text="1. Sign in (normal browser)", command=self.normal_login)
            self.login_button.pack(side="left")
            self.finish_button = ttk.Button(auth, text="Import login", command=self.paste_cookie)
            self.finish_button.pack(side="left", padx=6)
            self.cookie_button = ttk.Button(auth, text="Separate browser (optional)", command=self.login)
            self.cookie_button.pack(side="right")
            ttk.Label(panel, text="2. Paste Reading Room or IIIF manifest links, one per line:").grid(sticky="w")
            self.links = ScrolledText(panel, height=5, wrap="word", font=("TkDefaultFont", 12), undo=True)
            self.links.grid(sticky="nsew", pady=(5, 6))
            self.links.insert("1.0", "\n".join(initial_urls))
            link_actions = ttk.Frame(panel)
            link_actions.grid(sticky="ew")
            self.example_button = ttk.Button(link_actions, text="Load example links", command=self.examples)
            self.example_button.pack(side="left")
            self.preview_button = ttk.Button(link_actions, text="3. Preview manuscripts", command=self.preview)
            self.preview_button.pack(side="right")
            location = ttk.Frame(panel)
            location.grid(sticky="ew", pady=12)
            location.columnconfigure(1, weight=1)
            ttk.Label(location, text="Save to:").grid(row=0, column=0, padx=(0, 8))
            self.output = tk.StringVar(value=str(output))
            self.output_entry = ttk.Entry(location, textvariable=self.output)
            self.output_entry.grid(row=0, column=1, sticky="ew")
            self.browse_button = ttk.Button(location, text="Choose folder…", command=self.browse)
            self.browse_button.grid(row=0, column=2, padx=(8, 0))
            table_frame = ttk.Frame(panel)
            table_frame.grid(sticky="ew", pady=(0, 10))
            table_frame.columnconfigure(0, weight=1)
            self.table = ttk.Treeview(table_frame, columns=("name", "pages", "id"), show="headings", height=4)
            for col, title, width in [("name", "Manuscript", 400), ("pages", "Pages", 90), ("id", "Manifest ID", 140)]:
                self.table.heading(col, text=title)
                self.table.column(col, width=width, minwidth=70)
            self.table.grid(row=0, column=0, sticky="ew")
            bar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
            bar.grid(row=0, column=1, sticky="ns")
            self.table.configure(yscrollcommand=bar.set)
            controls = ttk.Frame(panel)
            controls.grid(sticky="ew", pady=(0, 10))
            self.images_only = tk.BooleanVar(value=False)
            self.images_check = ttk.Checkbutton(controls, text="Images only (skip PDF)", variable=self.images_only)
            self.images_check.pack(side="left")
            self.download_button = ttk.Button(controls, text="4. Download all previewed manuscripts", command=self.download, state="disabled")
            self.download_button.pack(side="right")
            self.stop_button = ttk.Button(controls, text="Stop", command=self.cancel.set, state="disabled")
            self.stop_button.pack(side="right", padx=6)
            self.log_area = ScrolledText(panel, height=10, wrap="word", font=("TkDefaultFont", 11), state="disabled")
            self.log_area.grid(sticky="nsew")
            self.progress = ttk.Progressbar(panel, mode="determinate")
            self.progress.grid(sticky="ew", pady=(10, 5))
            self.status = tk.StringVar(value="Ready. Sign in if needed, then paste a manuscript link.")
            ttk.Label(panel, textvariable=self.status, wraplength=820).grid(sticky="w")
            bottom = ttk.Frame(panel)
            bottom.grid(sticky="ew", pady=(8, 0))
            ttk.Label(bottom, text="Login stays in memory. Completed pages resume.").pack(side="left")
            ttk.Button(bottom, text="Open save folder", command=self.reveal).pack(side="right")
            ttk.Button(bottom, text="Copy diagnostics", command=self.copy_diagnostics).pack(side="right", padx=6)
            self.links.focus_set()
            root.after(100, self.poll)

        def log(self, message):
            self.events.put(("log", message))

        def append_log(self, message):
            self.log_area.configure(state="normal")
            self.log_area.insert("end", message + "\n")
            self.log_area.see("end")
            self.log_area.configure(state="disabled")

        def set_busy(self, busy):
            self.busy = busy
            for widget in (self.login_button, self.cookie_button, self.example_button,
                           self.preview_button, self.browse_button, self.images_check, self.output_entry):
                widget.configure(state="disabled" if busy else "normal")
            self.links.configure(state="disabled" if busy else "normal")
            self.download_button.configure(state="normal" if self.manuscripts and not busy else "disabled")
            self.stop_button.configure(state="normal" if busy else "disabled")
            self.finish_button.configure(state="disabled" if busy else "normal", text="Import login", command=self.paste_cookie)
            if busy:
                self.cancel.clear()
                self.progress["value"] = 0

        def launch(self, function):
            self.set_busy(True)
            def worker():
                try:
                    function()
                except Cancelled as exc:
                    self.events.put(("error", str(exc), False))
                except DownloadError as exc:
                    if self.http_session is not None:
                        self.log(diagnostic_report(self.http_session))
                    self.events.put(("error", str(exc), True))
                except OSError:
                    self.events.put(("error", "Could not read or write a file. Check free disk space and folder permissions.", True))
                except Exception as exc:
                    self.events.put(("error", f"An unexpected {type(exc).__name__} occurred. Your completed images are kept. "
                                     "Try again, or report the error type along with the manuscript link.", True))
                finally:
                    self.events.put(("idle",))
            threading.Thread(target=worker, daemon=True).start()

        def session(self):
            # Retain cookies refreshed by the server across preview, download, and new batches.
            # Workers run sequentially; this session is never used concurrently.
            if self.http_session is None:
                self.http_session = make_session(self.cookies, self.raw_cookie, self.user_agent)
            return self.http_session

        def install_session(self, session, raw_cookie="", cookies=(), agent=""):
            if self.http_session is not None:
                self.http_session.close()
            self.http_session = session
            self.raw_cookie = raw_cookie
            self.cookies = cookies
            self.user_agent = agent

        def examples(self):
            self.links.delete("1.0", "end")
            self.links.insert("1.0", EXAMPLES)

        def browse(self):
            selected = filedialog.askdirectory(initialdir=str(Path.home()), title="Choose a save folder")
            if selected:
                self.output.set(selected)

        def reveal(self):
            try:
                open_folder(Path(self.output.get()).expanduser())
            except OSError:
                messagebox.showerror("Could not open folder", "Choose a valid save folder first.")

        def copy_diagnostics(self):
            self.root.clipboard_clear()
            self.root.clipboard_append(diagnostic_report(self.http_session))
            self.status.set("Copied diagnostics. The report contains no cookie values, passwords, or full URLs.")

        def paste_cookie(self):
            value = simpledialog.askstring("Import your normal browser login",
                "Sign in ONCE on vHMML. No manuscript viewer is needed.\n\n"
                "1. Open Developer Tools: F12 (Windows) or Cmd+Option+I (Mac).\n"
                "2. Select Network, clear any filters, and refresh the Reading Room home page.\n"
                "3. Select the readingRoom/ request (type: document or Doc).\n"
                "4. Open Headers > Request Headers > Cookie.\n"
                "5. Copy everything AFTER Cookie: and paste it below.\n\n"
                "You may also paste copied request headers; the app extracts Cookie and User-Agent.\n"
                "Import once per run, then paste as many manuscript links as you like.\n"
                "The value is hidden. Do not share it.\n"
                "If normal browser verification also fails, resolve that on vHMML first.",
                show="*", parent=self.root)
            if value:
                try:
                    candidate = make_session(raw_cookie=value)
                except DownloadError as exc:
                    messagebox.showerror("Cookie not recognized", str(exc))
                    return
                self.status.set("Checking your login on the Reading Room home page…")
                def work():
                    try:
                        import_login_check(candidate, self.cancel)
                    except BaseException:
                        candidate.close()
                        raise
                    self.events.put(("session", candidate, value.strip(), [], ""))
                self.launch(work)

        def normal_login(self):
            url = BASE + "/readingRoom/"
            try:
                opened = webbrowser.open(url, new=2)
            except webbrowser.Error:
                opened = False
            if not opened:
                messagebox.showinfo("Open your browser", "Open this link in your normal browser:\n\n" + url)
            self.status.set("Sign in once in your normal browser, then click Import login. Paste your manuscript links afterward.")
            self.append_log("Sign in on the Reading Room home page, then click Import login. No individual viewers are needed. "
                            "A normal browser session cannot be copied automatically by this app.")

        def login(self):
            self.append_log("The optional separate browser is automated. If Cloudflare shows 'Verification failed', "
                            "click Stop and use 'Sign in (normal browser)' followed by 'Import login'.")
            start_url = BASE + "/readingRoom/"
            self.finish_login.clear()
            self.status.set("Opening a separate sign-in browser…")
            def work():
                cookies, agent = browser_login(start_url, self.finish_login, self.cancel, self.log,
                                               ready=lambda: self.events.put(("login-ready",)))
                candidate = make_session(cookies, user_agent=agent)
                try:
                    import_login_check(candidate, self.cancel)
                except BaseException:
                    candidate.close()
                    raise
                self.events.put(("session", candidate, "", cookies, agent))
            self.launch(work)

        def preview(self):
            source = self.links.get("1.0", "end").strip()
            try:
                inputs = parse_inputs(source)
            except (DownloadError, ValueError) as exc:
                messagebox.showerror("Check the links", str(exc))
                return
            self.manuscripts = []
            self.table.delete(*self.table.get_children())
            self.status.set("Reading manuscript information…")
            def work():
                found = []
                failed = 0
                session = self.session()
                for kind, url in inputs:
                    check_cancel(self.cancel)
                    try:
                        manuscript = inspect_manuscript(session, kind, url, self.cancel)
                        if manuscript.url not in [m.url for m in found]:
                            found.append(manuscript)
                            self.log(f"Preview: {manuscript.title} — {len(manuscript.pages)} pages")
                    except (SignInRequired, Cancelled):
                        raise
                    except DownloadError as exc:
                        failed += 1
                        self.log(f"Could not preview {url}: {exc}")
                self.events.put(("preview", found, source, failed))
            self.launch(work)

        def download(self):
            if self.links.get("1.0", "end").strip() != self.checked_input:
                messagebox.showinfo("Preview the new links", "The links have changed. Choose Preview manuscripts again first.")
                return
            output = Path(self.output.get()).expanduser()
            images_only = self.images_only.get()
            manuscripts = list(self.manuscripts)
            self.status.set("Downloading…")
            def work():
                saved = 0
                failed = 0
                session = self.session()
                for manuscript in manuscripts:
                    check_cancel(self.cancel)
                    try:
                        download_manuscript(session, manuscript, output, self.cancel, self.log,
                                            lambda a, b: self.events.put(("progress", a, b)), images_only)
                        saved += 1
                    except (Cancelled, SignInRequired):
                        raise
                    except DownloadError as exc:
                        failed += 1
                        self.log(f"{manuscript.title}: {exc}")
                self.events.put(("complete", saved, failed, str(output.resolve())))
            self.launch(work)

        def poll(self):
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                kind, *data = event
                if kind == "log":
                    self.append_log(data[0])
                elif kind == "login-ready":
                    self.finish_button.configure(state="normal", text="Use this login", command=self.finish_login.set)
                    self.status.set("Sign in to vHMML, then choose Use this login. No manuscript viewer is needed.")
                elif kind == "session":
                    self.install_session(*data)
                    self.status.set("Session imported. Paste your links and choose Preview to test access.")
                    self.append_log(getattr(self.http_session, "vhmml_login_hint", "Login not checked") + ". "
                                    "Import kept; Preview checks the actual manuscript. No individual viewers are required.")
                    if not any(c.name.upper() == "JSESSIONID" for c in self.http_session.cookies):
                        self.append_log("No JSESSIONID session cookie was found in the imported data. "
                                        "If Preview fails, use Copy diagnostics; do not share the cookie itself.")
                elif kind == "preview":
                    self.manuscripts, self.checked_input, failed = data
                    for manuscript in self.manuscripts:
                        self.table.insert("", "end", values=(manuscript.title, len(manuscript.pages), manuscript.url.rsplit("/", 1)[1]))
                    self.status.set(f"Previewed {len(self.manuscripts)} manuscript(s); {failed} link(s) failed. "
                                    "Review the list above, then download.")
                    if not self.manuscripts:
                        messagebox.showwarning("No manuscripts previewed", "See the messages for details. Sign in if required, or paste a IIIF manifest link.")
                elif kind == "progress":
                    self.progress["maximum"] = data[1]
                    self.progress["value"] = data[0]
                    self.status.set(f"Saved page {data[0]} of {data[1]} in the current manuscript.")
                elif kind == "complete":
                    saved, failed, folder = data
                    text = f"Saved {saved} manuscript(s); {failed} failed.\n\n{folder}"
                    self.status.set(text.replace("\n", " "))
                    (messagebox.showwarning if failed else messagebox.showinfo)("Download finished", text)
                elif kind == "error":
                    self.status.set(data[0])
                    self.append_log(data[0])
                    if data[1]:
                        messagebox.showerror("Download needs attention", data[0])
                elif kind == "idle":
                    self.set_busy(False)
            self.root.after(100, self.poll)

        def close(self):
            if self.busy:
                self.cancel.set()
                self.status.set("Stopping safely… The window will close when the current operation stops.")
                self.close_when_idle()
            else:
                if self.http_session is not None:
                    self.http_session.close()
                self.root.destroy()

        def close_when_idle(self):
            if self.busy:
                self.root.after(200, self.close_when_idle)
            else:
                if self.http_session is not None:
                    self.http_session.close()
                self.root.destroy()

    try:
        root = tk.Tk()
    except tk.TclError:
        raise DownloadError("A desktop display could not be opened. Run with --cli on this computer, "
                            "or use the launcher in a desktop session.") from None
    App(root)
    root.mainloop()


def cli(args):
    cancel = threading.Event()
    cookies, user_agent = [], ""
    if args.browser_login:
        finish = threading.Event()
        def ready():
            def prompt():
                try:
                    input("After signing in to vHMML (no manuscript viewer needed), press Enter here: ")
                    finish.set()
                except EOFError:
                    cancel.set()
            threading.Thread(target=prompt, daemon=True).start()
        cookies, user_agent = browser_login(BASE + "/readingRoom/", finish, cancel, print, ready)
    raw_cookie = os.environ.get("VHMML_COOKIE", os.environ.get("VHMM_COOKIE", ""))
    if args.ask_cookie:
        from getpass import getpass
        raw_cookie = getpass("Paste the Cookie header value (hidden): ")
    failed = 0
    seen = set()
    with make_session(cookies, raw_cookie, user_agent) as session:
        if args.browser_login or args.ask_cookie:
            print(import_login_check(session, cancel))
        for kind, url in parse_inputs(args.urls):
            try:
                manuscript = inspect_manuscript(session, kind, url, cancel)
                if manuscript.url in seen:
                    continue
                seen.add(manuscript.url)
                print(f"\n{manuscript.title}: {len(manuscript.pages)} pages\nManifest: {manuscript.url}")
                if not args.list:
                    download_manuscript(session, manuscript, args.output.expanduser(), cancel,
                                        images_only=args.images_only, delay=args.delay)
            except SignInRequired:
                print(diagnostic_report(session), file=sys.stderr)
                raise
            except DownloadError as exc:
                failed += 1
                print(f"Could not finish {url}: {exc}", file=sys.stderr)
    if failed:
        print(f"{failed} manuscript(s) failed. Completed images are kept; rerun to resume.", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download vHMML manuscripts. Opens a window by default.")
    parser.add_argument("urls", nargs="*", help="Reading Room or IIIF manifest links")
    parser.add_argument("--cli", action="store_true", help="Use the terminal instead of the window")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Save folder")
    parser.add_argument("--images-only", action="store_true", help="Skip PDF creation (CLI)")
    parser.add_argument("--list", action="store_true", help="Preview titles and page counts without saving (CLI)")
    parser.add_argument("--browser-login", action="store_true", help="Sign in in a separate browser (CLI)")
    parser.add_argument("--ask-cookie", action="store_true", help="Prompt for a hidden cookie value (CLI)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between images; minimum 0.25 (CLI)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.delay) or args.delay < 0.25:
        parser.error("--delay must be at least 0.25 seconds")
    if args.cli:
        if not args.urls:
            parser.error("With --cli, include at least one Reading Room or manifest link.")
        return cli(args)
    if any((args.images_only, args.list, args.browser_login, args.ask_cookie)):
        parser.error("These options require --cli. The window has its own sign-in and download controls.")
    run_gui(args.urls, args.output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped. Run the same download again to resume.", file=sys.stderr)
        raise SystemExit(130)
    except DownloadError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except OSError:
        print("Could not read or write a file. Check free disk space and folder permissions.", file=sys.stderr)
        raise SystemExit(1)
