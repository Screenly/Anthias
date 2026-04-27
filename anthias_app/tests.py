from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.http import Http404
from django.test import RequestFactory, TestCase

from anthias_app import views_files


class SafeJoinTest(TestCase):
    def test_resolves_simple_relative(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.txt').write_text('hi')
            self.assertEqual(
                views_files._safe_join(root, 'a.txt'),
                (root / 'a.txt').resolve(),
            )

    def test_rejects_parent_traversal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'sub'
            root.mkdir()
            with self.assertRaises(Http404):
                views_files._safe_join(root, '../../../etc/passwd')

    def test_rejects_symlink_escape(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'sub'
            root.mkdir()
            outside = Path(tmp) / 'outside.txt'
            outside.write_text('x')
            (root / 'link').symlink_to(outside)
            with self.assertRaises(Http404):
                views_files._safe_join(root, 'link')


class AnthiasAssetsViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'hello.txt').write_text('hello')
        self.root_patch = mock.patch.object(
            views_files, 'ANTHIAS_ASSETS_ROOT', self.root
        )
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def _get(self, path, remote_addr):
        request = self.factory.get(path, REMOTE_ADDR=remote_addr)
        # views_files.anthias_assets is wrapped by require_client_in.
        filename = path.removeprefix('/anthias_assets/')
        return views_files.anthias_assets(request, filename=filename)

    def test_allows_docker_bridge_client(self):
        response = self._get('/anthias_assets/hello.txt', '172.18.0.1')
        self.assertEqual(response.status_code, 200)

    def test_blocks_public_ip(self):
        response = self._get('/anthias_assets/hello.txt', '8.8.8.8')
        self.assertEqual(response.status_code, 403)

    def test_blocks_lan_ip(self):
        # 192.168/16 is intentionally excluded from the asset allowlist.
        response = self._get('/anthias_assets/hello.txt', '192.168.1.50')
        self.assertEqual(response.status_code, 403)

    def test_missing_file_404(self):
        request = self.factory.get(
            '/anthias_assets/missing.txt', REMOTE_ADDR='172.18.0.1'
        )
        with self.assertRaises(Http404):
            views_files.anthias_assets(request, filename='missing.txt')

    def test_traversal_404(self):
        request = self.factory.get(
            '/anthias_assets/whatever', REMOTE_ADDR='172.18.0.1'
        )
        with self.assertRaises(Http404):
            views_files.anthias_assets(
                request, filename='../../../etc/passwd'
            )

    def test_malformed_remote_addr_403(self):
        response = self._get('/anthias_assets/hello.txt', 'not-an-ip')
        self.assertEqual(response.status_code, 403)


class StaticWithMimeViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'app.css').write_text('body{}')
        self.root_patch = mock.patch.object(
            views_files, 'STATIC_FILES_ROOT', self.root
        )
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def _call(self, filename, remote_addr, **extra):
        request = self.factory.get(
            f'/static_with_mime/{filename}', REMOTE_ADDR=remote_addr, **extra
        )
        return views_files.static_with_mime(request, filename=filename)

    def test_allows_rfc1918_clients(self):
        for ip in ('10.0.0.5', '172.18.0.1', '192.168.1.10'):
            self.assertEqual(
                self._call('app.css', ip).status_code,
                200,
                msg=f'expected 200 for {ip}',
            )

    def test_blocks_public_ip(self):
        self.assertEqual(self._call('app.css', '8.8.8.8').status_code, 403)

    def test_mime_override_via_query(self):
        request = self.factory.get(
            '/static_with_mime/app.css',
            data={'mime': 'application/x-tgz'},
            REMOTE_ADDR='10.0.0.5',
        )
        response = views_files.static_with_mime(request, filename='app.css')
        self.assertEqual(response['Content-Type'], 'application/x-tgz')

    def test_default_mime_from_extension(self):
        request = self.factory.get(
            '/static_with_mime/app.css', REMOTE_ADDR='10.0.0.5'
        )
        response = views_files.static_with_mime(request, filename='app.css')
        self.assertEqual(response['Content-Type'], 'text/css')


class HotspotViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.hotspot_file = base / 'hotspot.html'
        self.initialized_flag = base / 'initialized'
        self.hotspot_file.write_text('<html>hotspot</html>')
        self.patches = [
            mock.patch.object(
                views_files, 'HOTSPOT_FILE', self.hotspot_file
            ),
            mock.patch.object(
                views_files, 'INITIALIZED_FLAG', self.initialized_flag
            ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _get(self, remote_addr='172.18.0.1'):
        request = self.factory.get('/hotspot', REMOTE_ADDR=remote_addr)
        return views_files.hotspot(request, path='')

    def test_serves_when_uninitialized(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/html')

    def test_blocks_public_ip(self):
        self.assertEqual(self._get('8.8.8.8').status_code, 403)

    def test_404_after_initialization(self):
        self.initialized_flag.touch()
        request = self.factory.get('/hotspot', REMOTE_ADDR='172.18.0.1')
        with self.assertRaises(Http404):
            views_files.hotspot(request, path='')

    def test_404_when_file_missing(self):
        self.hotspot_file.unlink()
        request = self.factory.get('/hotspot', REMOTE_ADDR='172.18.0.1')
        with self.assertRaises(Http404):
            views_files.hotspot(request, path='')
