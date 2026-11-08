"""Offline integration checks: no credentials or real manuscript images required."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / 'vhmml_downloader.py'
spec = importlib.util.spec_from_file_location('vhmml_downloader', SCRIPT)
d = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = d
spec.loader.exec_module(d)


def jpeg(color):
    buffer = io.BytesIO()
    Image.new('RGB', (120, 180), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def manifest_v2():
    return {'@type': 'sc:Manifest', 'label': 'TEST 001', 'sequences': [{'canvases': [
        {'label': 'same.JPG', 'images': [{'resource': {'@id': 'https://images.example/one.jpg'}}]},
        {'label': 'same.JPG', 'images': [{'resource': {'service': {'@id': 'https://images.example/two',
             '@context': 'http://iiif.io/api/image/2/context.json', 'profile': 'http://iiif.io/api/image/2/level2.json'}}}]},
    ]}]}


class FakeSession:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, **kwargs):
        self.calls.append(url)
        result = self.data[url]
        if isinstance(result, list):
            result = result.pop(0)
        status, content, kind = result
        r = requests.Response()
        r.status_code = status
        r.url = url
        r._content = content
        r.raw = io.BytesIO(content)
        r.headers['Content-Type'] = kind
        r.headers['Content-Length'] = str(len(content))
        return r


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.cancel = threading.Event()

    def test_inputs_and_record_resolution(self):
        self.assertEqual(d.normalize_input('http://w3id.org/vhmml/readingRoom/view/501579'),
                         ('record', d.BASE + '/readingRoom/view/501579'))
        self.assertEqual(len(d.parse_inputs(d.EXAMPLES)), 3)
        for bad in ['125659', 'https://evil.example/readingRoom/view/1', 'https://w3id.org/readingRoom/view/1']:
            with self.assertRaises(d.DownloadError):
                d.normalize_input(bad)
        for i, ident, project in [(0, 501579, 'GAMS 01022'), (1, 501578, 'GAMS 01021'), (2, 504977, 'USJ 00586')]:
            source = Path(__file__).parent / f'fixtures/record-{ident}.html'
            html = source.read_bytes()
            url = d.BASE + f'/readingRoom/view/{ident}'
            session = FakeSession({url: (200, html, 'text/html')})
            manifest, info = d.resolve_record(session, url, self.cancel)
            self.assertEqual(manifest, d.BASE + f'/image/manifest/{ident}')
            self.assertEqual(info['hmmlProjectNumber'], project)

    def test_login_check_never_loads_a_viewer(self):
        url = d.BASE + '/readingRoom/'
        session = FakeSession({url: (200, b'<script>var isAuthenticated = false; isAuthenticated = true;</script>', 'text/html')})
        self.assertTrue(d.verify_login(session, self.cancel))
        self.assertEqual(session.calls, [url])
        source = Path(__file__).parent / 'fixtures/record-501579.html'
        session = FakeSession({url: (200, source.read_bytes(), 'text/html')})
        self.assertFalse(d.verify_login(session, self.cancel))
        self.assertIn('not yet tested', session.vhmml_login_hint)
        session = FakeSession({url: (200, b'<html>Unexpected page</html>', 'text/html')})
        self.assertFalse(d.verify_login(session, self.cancel))
        session = FakeSession({url: (403, b'Blocked', 'text/html')})
        self.assertIn('unavailable', d.import_login_check(session, self.cancel))

    def test_cookie_import_keeps_tokens_exact_and_replaces_rotated_session(self):
        value = 'JSESSIONID=old-session; prefs="a\\073b"; token=abc=='
        parsed, agent = d.imported_headers(value)
        self.assertEqual(parsed['prefs'], '"a\\073b"')
        with d.make_session(raw_cookie=value) as session:
            prepared = session.prepare_request(requests.Request('GET', d.BASE + '/readingRoom/'))
            self.assertIn('prefs="a\\073b"', prepared.headers['Cookie'])
            session.cookies.set('JSESSIONID', 'new-session', domain='www.vhmml.org', path='/', secure=True)
            prepared = session.prepare_request(requests.Request('GET', d.BASE + '/readingRoom/'))
            self.assertEqual(prepared.headers['Cookie'].count('JSESSIONID='), 1)
            self.assertIn('JSESSIONID=new-session', prepared.headers['Cookie'])
        headers = 'GET /readingRoom/ HTTP/1.1\nUser-Agent: Sample browser\nCookie: ' + value
        parsed, agent = d.imported_headers(headers)
        self.assertEqual(parsed['JSESSIONID'], 'old-session')
        self.assertEqual(agent, 'Sample browser')
        for bad in ['only-a-session-id', 'JSESSIONID=one; JSESSIONID=two', 'JSESSIONID=one; Path=/']:
            with self.assertRaises(d.DownloadError):
                d.imported_headers(bad)

    def test_diagnostic_report_does_not_contain_secrets(self):
        with d.make_session(raw_cookie='JSESSIONID=private-session-value; rememberMe=private-other-value') as session:
            session.vhmml_checks.append('manifest: HTTP 403')
            report = d.diagnostic_report(session)
            self.assertIn('JSESSIONID present: yes', report)
            self.assertIn('HTTP 403', report)
            self.assertNotIn('private-session-value', report)
            self.assertNotIn('private-other-value', report)

    def test_v3_and_direct_resources(self):
        manifest = {'label': {'en': ['Three']}, 'items': [{'label': {'en': ['Folio 1']}, 'items': [
            {'items': [{'motivation': 'painting', 'body': {'id': 'https://images.example/a.jpg',
              'service': [{'id': 'https://images.example/a', 'type': 'ImageService3', 'profile': 'level2'}]}}]}]}]}
        pages = d.manifest_pages(manifest)
        self.assertEqual(pages[0].url, 'https://images.example/a/full/max/0/default.jpg')
        self.assertEqual(pages[0].label, 'Folio 1')
        self.assertEqual(d.image_url({'@id': 'https://images.example/direct.JPG'}), 'https://images.example/direct.JPG')
        self.assertEqual(d.image_url({'service': [{'@id': 'https://images.example/a',
            'profile': 'http://library.stanford.edu/iiif/image-api/1.1/compliance.html#level2',
            '@context': 'http://library.stanford.edu/iiif/image-api/1.1/context.json'}]}),
            'https://images.example/a/full/full/0/native.jpg')

    def test_unsupported_layers_and_linked_pages(self):
        value = manifest_v2()
        value['sequences'][0]['canvases'][0]['images'] *= 2
        with self.assertRaises(d.DownloadError):
            d.manifest_pages(value)
        with self.assertRaises(d.DownloadError):
            d.manifest_pages({'items': [{'items': [{'id': 'https://example/page'}]}]})

    def test_download_pdf_order_resume_and_corrupt_recovery(self):
        value = manifest_v2()
        pages = d.manifest_pages(value)
        manuscript = d.Manuscript('https://www.vhmml.org/image/manifest/99',
                                  'https://www.vhmml.org/image/manifest/99', 'TEST 001', value, pages)
        session = FakeSession({pages[0].url: (200, jpeg('red'), 'image/jpeg'),
                               pages[1].url: (200, jpeg('blue'), 'image/jpeg')})
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            def download():
                return d.download_manuscript(session, manuscript, output, self.cancel, lambda _: None, delay=0)
            result = download()
            images = sorted((result / 'images').glob('*.jpg'))
            self.assertEqual(len(images), 2)
            self.assertNotEqual(images[0].name, images[1].name)
            self.assertLess(images[0].stat().st_size, 10000)  # No bogus 10 KB minimum.
            with d.fitz.open(result / 'TEST_001.pdf') as pdf:
                self.assertEqual(pdf.page_count, 2)
                colors = [p.get_pixmap().pixel(30, 30) for p in pdf]
                self.assertGreater(colors[0][0], colors[0][2])
                self.assertGreater(colors[1][2], colors[1][0])
            count = len(session.calls)
            download()
            self.assertEqual(len(session.calls), count)
            images[0].write_bytes(b'corrupt')
            download()
            self.assertEqual(len(session.calls), count + 1)
            self.assertTrue(d.valid_image(images[0]))
            self.assertEqual(json.loads((result / 'source.json').read_text())['title'], 'TEST 001')

    def test_auth_response_not_saved_and_pdf_not_replaced_on_cancel(self):
        page = d.Page(1, 'one', 'https://images.example/a.jpg')
        session = FakeSession({page.url: (200, b'<html>Sign in</html>', 'text/html')})
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'one.jpg'
            with self.assertRaises(d.SignInRequired):
                d.fetch_image(session, page, image, self.cancel)
            self.assertFalse(image.exists())
            self.assertFalse(image.with_name('one.jpg.part').exists())
            pdf = Path(folder) / 'existing.pdf'
            pdf.write_bytes(b'original')
            image.write_bytes(jpeg('green'))
            self.cancel.set()
            with self.assertRaises(d.Cancelled):
                d.make_pdf([image], pdf, 'Test', self.cancel, lambda _: None)
            self.assertEqual(pdf.read_bytes(), b'original')
            self.assertFalse((Path(folder) / 'existing.part.pdf').exists())

    def test_identity_mismatch_stops(self):
        record = d.BASE + '/readingRoom/view/123'
        source = 'selectedObjectJson = ' + json.dumps({'id': 123, 'hmmlProjectNumber': 'TEST 001'})
        source += '; <input value="https://www.vhmml.org/image/manifest/{{id}}">'
        value = manifest_v2()
        value['label'] = 'TEST 1001'
        session = FakeSession({record: (200, source.encode(), 'text/html'),
                               d.BASE + '/image/manifest/123': (200, json.dumps(value).encode(), 'application/json')})
        with self.assertRaisesRegex(d.DownloadError, 'does not identify TEST 001'):
            d.inspect_manuscript(session, 'record', record, self.cancel)

    def test_cookie_scoping(self):
        with d.make_session(raw_cookie='JSESSIONID=secret; other=value') as session:
            a = session.prepare_request(requests.Request('GET', 'https://www.vhmml.org/image/manifest/1'))
            b = session.prepare_request(requests.Request('GET', 'https://images.example/a.jpg'))
            c = session.prepare_request(requests.Request('GET', 'http://www.vhmml.org/'))
            self.assertIn('JSESSIONID=secret', a.headers['Cookie'])
            self.assertNotIn('Cookie', b.headers)
            self.assertNotIn('Cookie', c.headers)

    def test_rate_limit_retry(self):
        url = 'https://images.example/a.jpg'
        session = FakeSession({url: [(429, b'', 'text/html'), (200, jpeg('green'), 'image/jpeg')]})
        with patch.object(d, 'wait_cancel') as wait:
            with d.get_response(session, url, self.cancel) as response:
                self.assertEqual(response.status_code, 200)
            self.assertEqual(wait.call_count, 1)

    def test_webp_to_pdf_keeps_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'page.webp'
            Image.new('RGB', (120, 180), 'red').save(path)
            output = Path(folder) / 'test.pdf'
            d.make_pdf([path], output, 'WebP', self.cancel, lambda _: None)
            with d.fitz.open(output) as pdf:
                self.assertEqual(pdf.page_count, 1)
                image = pdf.extract_image(pdf[0].get_images()[0][0])
                self.assertEqual((image['width'], image['height']), (120, 180))

    def test_safe_windows_names(self):
        self.assertEqual(d.safe_name('CON'), '_CON')
        self.assertEqual(d.safe_name('???'), 'Manuscript')
        for value in [' name. ', 'A/B:C', 'COM1.jpg']:
            result = d.safe_name(value)
            self.assertNotRegex(result, r'[<>:"/\\|?*]')
            self.assertFalse(result.endswith(('.', ' ')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
