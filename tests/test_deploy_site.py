import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, MagicMock

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('deploy', root / 'scripts/deploy-site.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class DeployTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(deploy.os.environ, {'AWS_REGION': 'us-east-1', 'SITE_BUCKET': 'test-site', 'DISTRIBUTION_ID': 'TEST', 'SITE_URL': 'https://example.com'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def response(self, request, **kwargs):
        name = request.full_url.rsplit('/', 1)[1] or 'index.html'
        response = MagicMock()
        response.headers = {'Cache-Control': 'no-cache'}
        response.read.return_value = (root / 'site' / name).read_bytes()
        response.__enter__.return_value = response
        return response

    def test_publish_then_invalidate_then_verify(self):
        with patch.object(deploy, 'aws', return_value='INV') as aws, patch.object(deploy, 'urlopen', side_effect=self.response) as live:
            deploy.main()
        calls = [c.args for c in aws.call_args_list]
        uploads = [c for c in calls if c[:2] == ('s3', 'cp')]
        self.assertTrue(uploads[-1][2].endswith('project_upload'))
        self.assertTrue(uploads[-2][2].endswith('index.html'))
        self.assertIn('text/html', uploads[-1])
        self.assertEqual(calls[-1][:3], ('cloudfront', 'wait', 'invalidation-completed'))
        self.assertEqual(live.call_count, 6)

    def test_project_upload_page_has_external_script_and_unlimited_picker(self):
        page = (root / 'site' / 'project_upload').read_text(encoding='utf-8')
        self.assertIn('src="/project-upload.js"', page)
        self.assertNotIn('<script>', page)
        self.assertIn('type="file" multiple', page)
        self.assertIn('up to 250 MiB per file', page)

    def test_failed_upload_stops_deployment(self):
        with patch.object(deploy, 'aws', side_effect=subprocess.CalledProcessError(1, 'aws')) as aws, patch.object(deploy, 'urlopen') as live:
            with self.assertRaises(subprocess.CalledProcessError):
                deploy.main()
        self.assertEqual(aws.call_count, 1)
        live.assert_not_called()

    def test_stale_live_content_fails_release(self):
        def stale(*args, **kwargs):
            response = self.response(*args, **kwargs)
            response.read.return_value = b'old deployed content'
            return response
        with patch.object(deploy, 'aws', return_value='INV'), patch.object(deploy, 'urlopen', side_effect=stale) as live, patch.object(deploy.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                deploy.main()
        self.assertEqual(live.call_count, 6)

if __name__ == '__main__':
    unittest.main()
