"""Notification service for translation completion alerts."""

from __future__ import annotations

import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def send_feishu_notification(
    webhook_url: str,
    title: str,
    message: str,
) -> bool:
    """Send a notification to Feishu/Lark via webhook.

    Args:
        webhook_url: Feishu webhook URL
        title: Notification title
        message: Notification body text

    Returns:
        True if sent successfully, False otherwise
    """
    if not webhook_url:
        return False

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "green",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": message,
                },
            ],
        },
    }

    try:
        import json
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Feishu notification sent: %s", title)
                return True
            logger.warning("Feishu notification failed: HTTP %d", resp.status)
            return False
    except (URLError, OSError) as e:
        logger.warning("Feishu notification error: %s", e)
        return False


def notify_translation_complete(
    webhook_url: str,
    paper_title: str,
    paper_id: str,
    success: bool,
    error: str | None = None,
    base_url: str = "http://localhost:8001",
) -> None:
    """Send translation completion notification.

    Args:
        webhook_url: Feishu webhook URL
        paper_title: Title of the translated paper
        paper_id: Paper ID
        success: Whether translation succeeded
        error: Error message if translation failed
        base_url: Base URL for notification links
    """
    if not webhook_url:
        return

    link = f"{base_url.rstrip('/')}"
    if success:
        title = "翻译完成"
        message = (
            f"**论文**: {paper_title}\n"
            f"**状态**: 翻译成功\n"
            f"**ID**: {paper_id}\n\n"
            f"[查看翻译结果]({link})"
        )
    else:
        title = "翻译失败"
        message = (
            f"**论文**: {paper_title}\n"
            f"**状态**: 翻译失败\n"
            f"**错误**: {error or '未知错误'}\n"
            f"**ID**: {paper_id}\n\n"
            f"[查看详情]({link})"
        )

    send_feishu_notification(webhook_url, title, message)
