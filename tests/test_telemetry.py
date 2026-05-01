import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from lib import telemetry


class TestSendTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        self.redis_data: dict[str, str] = {}

        def fake_get(key: str) -> str | None:
            return self.redis_data.get(key)

        def fake_set(key: str, value: str) -> None:
            self.redis_data[key] = value

        def fake_expire(key: str, _ttl: int) -> None:
            return None

        self.fake_redis = MagicMock()
        self.fake_redis.get.side_effect = fake_get
        self.fake_redis.set.side_effect = fake_set
        self.fake_redis.expire.side_effect = fake_expire

        self.patches = [
            patch.object(telemetry, 'r', self.fake_redis),
            patch.object(telemetry, 'is_ci', return_value=False),
            patch.object(telemetry, 'is_balena_app', return_value=False),
            patch.object(telemetry, 'is_docker', return_value=True),
            patch.object(telemetry, 'get_git_branch', return_value='master'),
            patch.object(
                telemetry, 'get_git_short_hash', return_value='abc1234'
            ),
            patch.object(
                telemetry, 'parse_cpu_info', return_value={'model': 'Pi 4'}
            ),
        ]
        for p in self.patches:
            p.start()

        self.settings_data: dict[str, Any] = {
            'analytics_opt_out': False,
            'resolution': '1920x1080',
            'audio_output': 'hdmi',
            'use_ssl': False,
        }
        self.settings_patch = patch.object(telemetry, 'settings')
        self.mock_settings = self.settings_patch.start()
        self.mock_settings.__getitem__.side_effect = (
            self.settings_data.__getitem__
        )

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.settings_patch.stop()

    @patch.object(telemetry, 'requests_post')
    def test_sends_event_when_no_cooldown(self, mock_post: Any) -> None:
        with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
            self.assertTrue(telemetry.send_telemetry())

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        url = call_args.args[0]
        self.assertIn('measurement_id=', url)
        self.assertIn('api_secret=', url)

        payload = json.loads(call_args.kwargs['data'])
        self.assertEqual(len(payload['client_id']), 15)
        event = payload['events'][0]
        self.assertEqual(event['name'], 'device_active')
        params = event['params']
        self.assertEqual(params['branch'], 'master')
        self.assertEqual(params['commit_short'], 'abc1234')
        self.assertEqual(params['device_type'], 'pi4-64')
        self.assertEqual(params['hardware_model'], 'Pi 4')
        # NOOBS is gone.
        self.assertNotIn('is_noobs', params)
        self.assertNotIn('NOOBS', params)
        # Asset counts always present even when DB is empty.
        self.assertIn('asset_count', params)
        self.assertIn('asset_image_count', params)
        self.assertIn('asset_video_count', params)
        self.assertIn('asset_webpage_count', params)
        # Display + TLS adoption.
        self.assertEqual(params['resolution'], '1920x1080')
        self.assertEqual(params['audio_output'], 'hdmi')
        self.assertEqual(params['tls_enabled'], False)

    @patch.object(telemetry, 'requests_post')
    def test_passes_timeout(self, mock_post: Any) -> None:
        telemetry.send_telemetry()
        self.assertEqual(
            mock_post.call_args.kwargs['timeout'],
            telemetry.ANALYTICS_TIMEOUT,
        )

    @patch.object(telemetry, 'requests_post')
    def test_skips_when_cooldown_set(self, mock_post: Any) -> None:
        self.redis_data[telemetry.TELEMETRY_COOLDOWN_KEY] = '1'
        self.assertFalse(telemetry.send_telemetry())
        mock_post.assert_not_called()

    @patch.object(telemetry, 'requests_post')
    def test_skips_when_opted_out(self, mock_post: Any) -> None:
        self.settings_data['analytics_opt_out'] = True
        self.assertFalse(telemetry.send_telemetry())
        mock_post.assert_not_called()

    @patch.object(telemetry, 'requests_post')
    def test_skips_in_ci(self, mock_post: Any) -> None:
        with patch.object(telemetry, 'is_ci', return_value=True):
            self.assertFalse(telemetry.send_telemetry())
        mock_post.assert_not_called()

    @patch.object(
        telemetry, 'requests_post', side_effect=RequestsConnectionError
    )
    def test_swallows_connection_error_and_skips_cooldown(
        self, _mock_post: Any
    ) -> None:
        self.assertFalse(telemetry.send_telemetry())
        # No cooldown set — next tick retries.
        self.assertNotIn(telemetry.TELEMETRY_COOLDOWN_KEY, self.redis_data)

    @patch.object(telemetry, 'requests_post', side_effect=Timeout)
    def test_swallows_timeout(self, _mock_post: Any) -> None:
        self.assertFalse(telemetry.send_telemetry())
        self.assertNotIn(telemetry.TELEMETRY_COOLDOWN_KEY, self.redis_data)

    @patch.object(telemetry, 'requests_post')
    def test_sets_cooldown_after_success(self, _mock_post: Any) -> None:
        telemetry.send_telemetry()
        self.assertIn(telemetry.TELEMETRY_COOLDOWN_KEY, self.redis_data)
        self.fake_redis.expire.assert_any_call(
            telemetry.TELEMETRY_COOLDOWN_KEY,
            telemetry.TELEMETRY_COOLDOWN_TTL,
        )

    @patch.object(telemetry, 'requests_post')
    def test_reuses_persisted_device_id(self, _mock_post: Any) -> None:
        self.redis_data[telemetry.DEVICE_ID_KEY] = 'persisted-id-123'
        telemetry.send_telemetry()
        payload = json.loads(_mock_post.call_args.kwargs['data'])
        self.assertEqual(payload['client_id'], 'persisted-id-123')

    @patch.object(telemetry, 'requests_post')
    def test_generates_and_persists_new_device_id(
        self, _mock_post: Any
    ) -> None:
        self.assertNotIn(telemetry.DEVICE_ID_KEY, self.redis_data)
        telemetry.send_telemetry()
        self.assertIn(telemetry.DEVICE_ID_KEY, self.redis_data)
        self.assertEqual(
            len(self.redis_data[telemetry.DEVICE_ID_KEY]),
            telemetry.DEVICE_ID_LENGTH,
        )


if __name__ == '__main__':
    unittest.main()
