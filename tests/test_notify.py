"""Tests for app.services.notify module."""

import unittest
from unittest.mock import MagicMock, patch

from app.services.notify import notify_translation_complete, send_feishu_notification


class TestSendFeishuNotification(unittest.TestCase):
    """Test send_feishu_notification function."""

    def test_empty_url_returns_false(self):
        self.assertFalse(send_feishu_notification("", "title", "msg"))

    def test_success_response(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.services.notify.urlopen", return_value=mock_resp):
            result = send_feishu_notification("https://hook.example.com", "Test", "Hello")
        self.assertTrue(result)

    def test_non_200_returns_false(self):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.services.notify.urlopen", return_value=mock_resp):
            result = send_feishu_notification("https://hook.example.com", "Test", "Hello")
        self.assertFalse(result)

    def test_url_error_returns_false(self):
        from urllib.error import URLError
        with patch("app.services.notify.urlopen", side_effect=URLError("fail")):
            result = send_feishu_notification("https://hook.example.com", "Test", "Hello")
        self.assertFalse(result)

    def test_os_error_returns_false(self):
        with patch("app.services.notify.urlopen", side_effect=OSError("timeout")):
            result = send_feishu_notification("https://hook.example.com", "Test", "Hello")
        self.assertFalse(result)


class TestNotifyTranslationComplete(unittest.TestCase):
    """Test notify_translation_complete function."""

    def test_empty_url_does_nothing(self):
        with patch("app.services.notify.send_feishu_notification") as mock_send:
            notify_translation_complete("", "Paper", "id123", True)
        mock_send.assert_not_called()

    def test_success_notification(self):
        with patch("app.services.notify.send_feishu_notification") as mock_send:
            notify_translation_complete("https://hook.example.com", "My Paper", "abc123", True)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("翻译完成", args[1])
        self.assertIn("My Paper", args[2])

    def test_failure_notification(self):
        with patch("app.services.notify.send_feishu_notification") as mock_send:
            notify_translation_complete("https://hook.example.com", "My Paper", "abc123", False, "API error")
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("翻译失败", args[1])
        self.assertIn("API error", args[2])

    def test_failure_without_error_message(self):
        with patch("app.services.notify.send_feishu_notification") as mock_send:
            notify_translation_complete("https://hook.example.com", "My Paper", "abc123", False)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("未知错误", args[2])


if __name__ == "__main__":
    unittest.main()
