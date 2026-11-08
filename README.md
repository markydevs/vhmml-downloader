# Manuscript Downloader for vHMML

**Save manuscript images as a PDF.**

For researchers who can view manuscript images in vHMML Reading Room and want to
compile them into a local PDF. Paste a Reading Room or IIIF manifest link, preview
the manuscript, and save its images and PDF. No need to open each viewer manually.

Supports **Windows, macOS, and Linux**, with a desktop window and a terminal interface.
This tool is specific to **vHMML**; it is not a downloader for every image-viewer website.

## What it does

- Downloads multiple manuscripts in sequence, with one PDF per manuscript.
- Keeps the page order supplied by the manuscript's IIIF manifest.
- Preserves the downloaded image resolution and keeps the original image files.
- Resumes interrupted downloads and checks completed files before reusing them.
- Imports a login once and reuses it across batches until the session expires.
- Keeps session cookies in memory and offers diagnostics without cookie values.

## Quick start

1. Download the repository using **Code → Download ZIP**, then extract it completely.
2. Install [Python 3.11 or newer](https://www.python.org/downloads/), including Tcl/Tk.
   Python 3.13 is a suitable choice. On Windows, enable **Add Python to PATH** if offered.
3. Open the launcher for your computer:

| System | Launcher |
| --- | --- |
| Windows | Double-click `Start-Windows.bat` |
| macOS | Double-click `Start-Mac.command` |
| Linux | Run `sh Start-Linux.sh` |

The launcher installs dependencies in a private `.venv` folder on first use.
It needs internet access and a writable application folder. Keep the setup window open.
On Linux, install your distribution's Tkinter and venv packages if needed.

If the Mac launcher cannot execute, open Terminal, type `bash ` with a trailing space,
drag `Start-Mac.command` into Terminal, and press Return.

## Sign in once, then paste links

Publicly accessible images can be tried without signing in. For account-protected images:

1. Click **Sign in (normal browser)** and sign in on vHMML's Reading Room home page.
2. Click **Import login**. In your browser, open Developer Tools → Network, clear any filters,
   refresh the Reading Room home page, and select the `readingRoom/` document request.
   Copy **Request Headers → Cookie** into the app's hidden input.
   Copied request headers are also accepted; Cookie and User-Agent are extracted.
3. Paste one or more **Reading Room** or **IIIF manifest** links, one per line.
4. Choose **Preview manuscripts**, check the names and page counts, then **Download**.

You can import the login before entering any links. One import covers all batches for
the current session; individual manuscript viewers do not need to be opened.
Your normal browser's session cannot be copied automatically by this app.

An unconfirmed home-page login check does **not** reject your import. Preview tests the
actual requested manifests. The optional separate browser can copy its session after
sign-in, but Cloudflare may reject that automated browser; use the normal-browser route
if it shows “Verification failed.”

Treat session cookies like passwords. Paste them only into the app, never into a public
issue or repository. **Copy diagnostics** produces a report without cookie values,
account details, passwords, or full URLs.

## Supported links

```text
https://w3id.org/vhmml/readingRoom/view/501579
https://www.vhmml.org/readingRoom/view/501579
https://www.vhmml.org/image/manifest/125659
```

These are catalog/manifest examples; image access may require your own vHMML account.
Use the full link rather than a bare number. Manifest IDs are resolved from the site's
published record template, not guessed from neighboring records.

## Results and resume

By default, files are saved under **Downloads → vHMML Downloads**:

```text
GAMS_01022__501579/
├── GAMS_01022.pdf
├── images/
│   ├── 000001_....jpg
│   └── 000002_....jpg
├── manifest.json
├── source.json
└── download-state.json
```

Choose **Images only** to skip PDF creation. The PDF is image-based, without OCR.
To resume, use the same links and save folder. Completed images are checked using
their saved hashes and decoded before reuse; incomplete images are downloaded again.

## Terminal use

The bootstrap script sets up the same private environment used by the window:

```sh
# macOS / Linux
python3 bootstrap.py --cli --ask-cookie "https://w3id.org/vhmml/readingRoom/view/501579"
```

```powershell
# Windows
py -3 bootstrap.py --cli --ask-cookie "https://w3id.org/vhmml/readingRoom/view/501579"
```

`--ask-cookie` prompts for a hidden value. Omit it for public images or if the session
is supplied through `VHMML_COOKIE` (the older `VHMM_COOKIE` spelling also works).
Multiple quoted links can be supplied together.

| Option | Purpose |
| --- | --- |
| `--list` | Preview titles and page counts without downloading images |
| `--images-only` | Save images and metadata without a PDF |
| `--output "PATH"` | Select the save folder |
| `--browser-login` | Try signing in through the optional separate browser |
| `--delay 1` | Wait one second between images; default 0.5, minimum 0.25 |

## Limits and troubleshooting

The downloader supports common IIIF Presentation 2 and 3 manifests containing one
complete image per page. Multiple image layers, image choices, and linked annotation
pages stop with an explanation. No site access controls or verification challenges
are bypassed. Use only images you are permitted to download; manuscript rights remain
with the owning institutions. This project is not affiliated with vHMML or HMML.

For login failures, use **Copy diagnostics** and include that report in an issue.
If you move the application folder and setup stops working, delete its `.venv`
folder and relaunch. Your separately saved manuscript files are unaffected.

See [the detailed guide](READ-ME-FIRST.md) or open `START-HERE.html` for more help.

## Development

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
```

Tests use generated images and synthetic catalog fixtures. No vHMML credentials or
manuscript downloads are needed. A GitHub Actions workflow runs these offline checks
on Windows, macOS, and Linux. A desktop-only smoke test is available as
`python tests/gui_smoke.py`; its browser and network responses are simulated.

A user has reported a successful signed-in run. Automated tests do not establish live
account access or certify assistive-technology support. Website changes may require updates.

## License

Licensed under **GNU AGPL v3 or later**; see [LICENSE](LICENSE).
The PDF engine, [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright),
is available under AGPL or a separate commercial license. Other dependencies retain
their own licenses. This software license does not grant rights to manuscript images.
