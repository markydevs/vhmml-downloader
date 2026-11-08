"""Exercise the actual Tk window with synthetic network responses."""
import importlib.util
import copy
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('test_downloader', Path(__file__).with_name('test_downloader.py'))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
d = t.d
manifest = t.manifest_v2()
pages = d.manifest_pages(manifest)
url = 'https://www.vhmml.org/image/manifest/99'


class Session(t.FakeSession):
    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


session = Session({url: (200, json.dumps(manifest).encode(), 'application/json'),
                   d.BASE + '/readingRoom/': (200, b'var isAuthenticated = false;', 'text/html'),
                   pages[0].url: (200, t.jpeg('red'), 'image/jpeg'),
                   pages[1].url: (200, t.jpeg('blue'), 'image/jpeg')})
session.cookies.set('JSESSIONID', 'private-fake-session', domain='www.vhmml.org', path='/')
for ident in (100, 101):
    other = copy.deepcopy(manifest)
    other['label'] = f'TEST {ident}'
    session.data[f'https://www.vhmml.org/image/manifest/{ident}'] = (200, json.dumps(other).encode(), 'application/json')
real_Tk = tk.Tk
roots = []
messages = []
errors = []
opened = []
created = []
copied = []


def session_factory(*a, **kw):
    created.append(kw)
    return session


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


def make_root():
    root = real_Tk()
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    roots.append(root)
    phase = [0]
    tries = [0]

    def step():
        try:
            tries[0] += 1
            widgets = list(children(root))
            buttons = {str(w.cget('text')): w for w in widgets if isinstance(w, ttk.Button)}
            if phase[0] == 0:
                root.update_idletasks()
                root_bottom = root.winfo_rooty() + root.winfo_height()
                for widget in widgets:
                    if widget.winfo_ismapped() and widget.winfo_rooty() + widget.winfo_height() > root_bottom + 2:
                        errors.append('Widget extends below the window: ' + str(widget))
                buttons['1. Sign in (normal browser)'].invoke()
                buttons['Import login'].invoke()
                phase[0] = 1
            elif phase[0] == 1 and not buttons['3. Preview manuscripts'].instate(['disabled']):
                links = next(w for w in widgets if isinstance(w, ScrolledText) and int(w.cget('height')) == 5)
                assert not links.get('1.0', 'end').strip()  # Login happened before any links were entered.
                links.insert('1.0', url + '\nhttps://www.vhmml.org/image/manifest/100')
                buttons['3. Preview manuscripts'].invoke()
                phase[0] = 2
            elif phase[0] == 2 and not buttons['4. Download all previewed manuscripts'].instate(['disabled']):
                buttons['4. Download all previewed manuscripts'].invoke()
                phase[0] = 3
            elif phase[0] == 3 and len(messages) == 1 and not buttons['3. Preview manuscripts'].instate(['disabled']):
                links = next(w for w in widgets if isinstance(w, ScrolledText) and int(w.cget('height')) == 5)
                links.delete('1.0', 'end')
                links.insert('1.0', 'https://www.vhmml.org/image/manifest/101')
                buttons['3. Preview manuscripts'].invoke()
                phase[0] = 4
            elif phase[0] == 4 and not buttons['4. Download all previewed manuscripts'].instate(['disabled']):
                buttons['4. Download all previewed manuscripts'].invoke()
                phase[0] = 5
            elif phase[0] == 5 and len(messages) == 2:
                buttons['Copy diagnostics'].invoke()
                copied.append(root.clipboard_get())
                root.destroy()
                return
            if tries[0] > 150:
                errors.append('GUI test timed out')
                root.destroy()
                return
            root.after(100, step)
        except Exception as exc:
            errors.append(repr(exc))
            root.destroy()
    root.after(200, step)
    return root


with tempfile.TemporaryDirectory() as directory:
    with patch.object(tk, 'Tk', make_root), patch.object(d, 'make_session', session_factory), \
         patch.object(messagebox, 'showinfo', lambda *a: messages.append(a)), \
         patch.object(messagebox, 'showwarning', lambda *a: errors.append(a)), \
         patch.object(messagebox, 'showerror', lambda *a: errors.append(a)), \
         patch.object(simpledialog, 'askstring', lambda *a, **kw: 'JSESSIONID=fake-test-only'), \
         patch.object(d.webbrowser, 'open', lambda url, **kw: opened.append(url) or True):
        d.run_gui([], Path(directory))
    assert not errors, errors
    assert len(messages) == 2 and 'Saved 2 manuscript(s)' in messages[0][1] and 'Saved 1 manuscript(s)' in messages[1][1], messages
    assert opened == [d.BASE + '/readingRoom/'], opened
    assert len(created) == 1, created  # Same session for login check, preview, download, and the next batch.
    assert session.calls.count(d.BASE + '/readingRoom/') == 1, session.calls
    assert len(list(Path(directory).rglob('*.pdf'))) == 3
    assert copied and 'JSESSIONID present: yes' in copied[0], copied
    assert 'private-fake-session' not in copied[0]
    print('GUI inconclusive login import, multiple batches, downloads, safe diagnostic copy, and layout checks passed.')
