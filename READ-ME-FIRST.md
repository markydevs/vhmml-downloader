# Manuscript Downloader for vHMML (v4)

Paste manuscript links, preview their names, and save images plus one PDF per manuscript.
Supports vHMML Reading Room and IIIF manifest links for manuscripts your account can access.

## Start on Windows

1. Extract the ZIP completely. Keep all its files together in a writable folder, such as Downloads.
2. Install **Python 3.11 or newer** from <https://www.python.org/downloads/> if needed.
   Python 3.13 is a suitable choice. Include Tcl/Tk; select “Add Python to PATH” if offered.
3. Double-click **Start-Windows.bat**.

The first launch installs the required packages into a `.venv` folder next to the script.
You do not need to type installation commands. Internet access is needed for setup.

## Start on Mac

1. Extract the ZIP completely into a writable folder, such as Downloads.
2. Install **Python 3.11 or newer** from <https://www.python.org/downloads/> if needed.
   The python.org installer includes the window toolkit. Python 3.13 is a suitable choice.
3. Double-click **Start-Mac.command**.

If macOS opens the file as text, open it with Terminal. If it reports “permission denied,”
open Terminal, type `bash ` (including the space), drag **Start-Mac.command** into Terminal,
and press Return. This also works if your ZIP extractor dropped executable permissions.

If macOS refuses to open a downloaded script, use Finder’s **Open** or the specific
**Open Anyway** option in Privacy & Security after inspecting/trusting this package.
Do not disable system security globally.

For Python certificate errors, run the **Install Certificates.command** supplied with
the python.org installation, then try again.

## Use the window

1. Click **Sign in (normal browser)** and sign in to vHMML on the Reading Room home page.
   You can do this before entering any manuscript links. No individual viewers need to be opened.
2. Click **Import login** once. Open your normal browser's Developer Tools → Network, clear
   any filters, refresh the Reading Room home page, and select the `readingRoom/` request
   (type Document/Doc). Copy **Request Headers → Cookie** into the app's hidden field.
   You may also paste copied request headers; the app extracts Cookie and User-Agent.
   The home-page check is advisory. Your imported session is kept even if that page cannot
   confirm sign-in. Preview checks the actual requested manifests; no viewer is needed.
3. Paste one or more **Reading Room links** or **IIIF manifest links**, one per line.
   Click **Preview manuscripts** and check the names and page counts. The example button
   inserts three public catalog links as examples (images may require login).
4. Choose a save folder, then click **Download all previewed manuscripts**.

The imported login is reused across previews, downloads, and later batches until it expires
or the app is closed. The app retains session cookies updated by the server during requests.
You do not need to open each manuscript in the browser. Public manuscripts can also be tried
without signing in. Your account must still have permission for each requested manuscript.

The one-time **Import login** step remains necessary with your normal browser; the app cannot
automatically read that browser's session. This is not a promise of automatic normal-browser login.

**Separate browser (optional)** lets you sign in, click Use this login, and then paste links
without a cookie copy. It also makes an advisory check on the catalog home page and does not require
opening any viewers. It retains the earlier automated sign-in option. Cloudflare
can reject automated browsers even when you interact with them yourself. If it shows
“Verification failed,” click Stop and use the normal-browser steps above. This is not
evidence of an incorrect password. The app does not solve or bypass verification.
The optional browser tries Chrome or Edge, then Chromium, downloading it if necessary.
No existing browser profile is read. Your password is entered only on the vHMML website.
The downloader does not save cookies to a file; sign in again after closing it.

The default save folder is **Downloads → vHMML Downloads**. Each manuscript has:

```text
GAMS_01022__501579/
    GAMS_01022.pdf
    images/
        000001_....jpg
        000002_....jpg
        ...
    manifest.json
    source.json
    download-state.json
```

Names and extensions depend on the manuscript. The numeric prefix preserves page order,
even when original labels repeat. The ID in the folder name distinguishes records with similar titles.
The PDF retains the downloaded images' resolution; it is not OCR searchable.
“Images only” saves images and metadata without building a PDF.

**Stop and resume:** click Stop, or rerun after an interrupted connection. Use the same links
and save folder. Completed images are checked against their saved hashes and decoded before reuse.
Incomplete images are downloaded again. A PDF is replaced only after its new version is complete.
Stopping may wait for an active network request to finish or time out.

## Example links

- GAMS 01022: <https://w3id.org/vhmml/readingRoom/view/501579>
- GAMS 01021: <https://w3id.org/vhmml/readingRoom/view/501578>
- USJ 00586: <https://w3id.org/vhmml/readingRoom/view/504977>
- Direct manifest example: <https://www.vhmml.org/image/manifest/125659>

Use whole links, not bare numbers. Reading Room and manifest links are resolved using the
site's published record/template; the script does not try adjacent IDs. Preview checks
the manuscript's project identifier when a Reading Room record provides one.

## Troubleshooting

**Import says the home page did not confirm login:** your session is still imported. Paste your
links and choose Preview to test the requested manuscripts. This avoids rejecting an import
solely because a public, cached, or differently rendered home page has an anonymous login flag.

**Preview is denied:** use **Copy diagnostics**. The report contains cookie counts, whether a
JSESSIONID is present, the home-page check result, and HTTP status codes. It contains no cookie
values, account details, passwords, or full URLs. It can help distinguish a missing session
cookie from an HTTP denial without sharing credentials. Being signed in in the browser does
not guarantee that the website will accept that session from this downloader.
If the website itself denies your account access, the downloader cannot grant access.

**Import login:** in your normal signed-in browser,
open Developer Tools → Network, clear any filters, refresh the Reading Room home page, select
the `readingRoom/` document request, and copy its Request Headers → Cookie value. Paste that value into the hidden input.
You can also paste copied request headers (Cookie and User-Agent are extracted; no commands
are executed). The importer preserves quoted cookie values and avoids shadowing a refreshed
www.vhmml.org session with a second imported JSESSIONID.
Cookies stay in memory for this run. Do not send them to anyone or put them in shared logs.
The original `VHMM_COOKIE` environment variable is also supported, as is `VHMML_COOKIE`.

**Verification also fails in your normal browser:** update the browser, check that JavaScript
is enabled, and temporarily test without extensions or a VPN. Try another browser or network.
If it still fails, contact vHMML with the displayed error code. A successful browser login does
not guarantee that the image server will accept requests from this downloader.
See [Cloudflare's troubleshooting guide](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/).

**A Reading Room link cannot be resolved:** open the record in vHMML and copy the viewer's
IIIF Link from its information button. Paste the full `https://www.vhmml.org/image/manifest/NUMBER` link.

**Moved this application folder:** delete only its `.venv` folder and open the launcher again.
Your separately saved manuscripts are unaffected.

**Setup fails:** check internet access, available disk space, and write access to the extracted folder.
Use a supported Python release with packages available for your computer's architecture.

## Linux and terminal use

Linux needs Python 3.11+, venv, and Tkinter (often `python3-venv` and `python3-tk` distribution packages).
Run `sh Start-Linux.sh`. Browser sign-in may also need Playwright's Linux system dependencies:
<https://playwright.dev/python/docs/browsers#install-system-dependencies>.

For terminal use, these examples assume the folder is already set up by a launcher.

Mac/Linux:

```sh
.venv/bin/python vhmml_downloader.py --cli --browser-login "https://w3id.org/vhmml/readingRoom/view/501579"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\vhmml_downloader.py --cli --browser-login "https://w3id.org/vhmml/readingRoom/view/501579"
```

Alternatively run `python3 bootstrap.py --cli URL` on Mac/Linux or `py -3 bootstrap.py --cli URL`
on Windows; this sets up the private environment before running.

Additional options (use with `--cli`):

- Multiple quoted links after the script name: download manuscripts in sequence.
- `--list`: show titles/page counts without saving images or metadata.
- `--images-only`: skip PDF creation.
- `--output "PATH"`: choose the destination.
- `--ask-cookie`: prompt for a hidden cookie value instead of browser sign-in.
- `--delay 1`: wait one second between images (default 0.5; minimum 0.25).

You can also run the single Python script directly in your own environment after installing
`requests`, `pymupdf`, and `Pillow`; `playwright` is needed only for browser sign-in.

## Coverage and verification

Designed for macOS, Windows, and Linux desktops with Python and Tkinter. This package is source
code with launchers, not a signed `.app` or a standalone `.exe`. It does not run on phones.

Supports common IIIF Presentation 2 and 3 manifests with one complete image per canvas,
IIIF Image API services, and direct image URLs. Multiple layers, image choices, and external
annotation-page documents stop with an explanation rather than silently omitting pages.
Images are requested at full/server-allowed size; server restrictions still apply.

Verified locally with offline cookie-import, session-refresh, download/PDF/resume tests,
GUI login-import and multiple-batch tests, and small catalog-page fixtures. A user has reported a successful signed-in run.
Native Windows execution and live account access are not covered by the automated tests.
No claim of universal site compatibility or screen-reader certification is made.
A site update may require a resolver change.

Reference documentation: [vHMML login help](https://www.vhmml.org/login/help),
[IIIF Presentation 3](https://iiif.io/api/presentation/3.0/),
[IIIF Image 3](https://iiif.io/api/image/3.0/),
[Playwright browsers](https://playwright.dev/python/docs/browsers).
