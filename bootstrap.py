# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small, standard-library-only launcher. Dependencies stay in this folder."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.version_info < (3, 11):
        print("Please install Python 3.11 or newer from https://www.python.org/downloads/")
        return 1
    if not any(arg in sys.argv for arg in ("--cli", "--help", "-h")):
        try:
            import tkinter
        except ImportError:
            print("This Python installation does not include the window toolkit (Tk).")
            print("Mac/Windows: install Python with Tcl/Tk from python.org.")
            print("Linux: install your distribution's python3-tk and python3-venv packages.")
            print("Or run this launcher with --cli and a manuscript URL.")
            return 1
    base = Path(__file__).resolve().parent
    env = base / ".venv"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Setting up vHMML Downloader for the first time...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    requirements = base / "requirements.txt"
    signature = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = env / ".vhmml-requirements"
    if not marker.exists() or marker.read_text().strip() != signature:
        print("Installing the required packages. This may take a few minutes...", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        marker.write_text(signature)
    return subprocess.call([str(python), str(base / "vhmml_downloader.py"), *sys.argv[1:]])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print("Setup could not finish. Check your internet connection and that this folder is writable.")
        print("If the folder was moved, delete its .venv folder and open the launcher again.")
        print(str(exc))
        raise SystemExit(1)
