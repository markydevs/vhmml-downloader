# Contributing

Bug reports and focused pull requests are welcome. Describe the concrete problem,
the affected platform, and the behavior after the change.

Run the offline suite before submitting:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
```

Changes to cookie handling should preserve token bytes, scope cookies to their intended
host, and keep secrets out of diagnostics. Changes to downloading should preserve page
order, resume behavior, and atomic replacement of completed files.

Use small synthetic manifests and generated images for tests. Do not commit real
cookies, browser profiles, downloaded manuscripts, screenshots containing session
headers, or full copies of catalog pages containing account details.

For GUI changes, run `python tests/gui_smoke.py` in a desktop session. This uses simulated
browser/network responses. State separately if you tested a live authenticated download.

Contributions to this project are provided under its AGPL-3.0-or-later license.
