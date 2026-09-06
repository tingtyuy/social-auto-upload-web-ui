"""
SMTP 邮件发送工具 — 供定时图集发布失败通知使用。

settings 中的 `smtp` 配置为 dict:
{
  "host": "smtp.example.com",
  "port": 465,
  "user": "noreply@example.com",
  "password": "xxxx",
  "from": "noreply@example.com",        # 可选，默认同 user
  "to": ["a@example.com"],              # 收件人列表（或逗号分隔字符串）
  "ssl": true,                          # true=SMTP_SSL，false=SMTP(+可选 starttls)
  "starttls": false
}
"""

from __future__ import annotations

import smtplib
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from util._logger import get_channel_logger  # noqa: E402

logger = get_channel_logger("mail")


def _smtp_cfg() -> dict:
    from impl.settings import read_settings
    cfg = read_settings().get("smtp")
    return cfg if isinstance(cfg, dict) else {}


def _normalize_to(to) -> list:
    if not to:
        return []
    if isinstance(to, str):
        return [x.strip() for x in to.split(",") if x.strip()]
    if isinstance(to, (list, tuple)):
        return [str(x).strip() for x in to if str(x).strip()]
    return [str(to).strip()]


def send_mail(subject: str, body: str, html: bool = False) -> bool:
    """发送一封邮件。成功返回 True，失败返回 False（不抛异常，便于后台线程安全）。"""
    cfg = _smtp_cfg()
    host = cfg.get("host")
    user = cfg.get("user")
    password = cfg.get("password")
    to_list = _normalize_to(cfg.get("to"))
    try:
        port = int(cfg.get("port") or 25)
    except (ValueError, TypeError):
        port = 25
    use_ssl = bool(cfg.get("ssl", False))
    from_addr = cfg.get("from") or user

    if not host or not to_list:
        logger.warning(f"[mail] SMTP 未完整配置 host={host} to={to_list}")
        return False

    sender = (from_addr or user) or "noreply@local"
    msg = MIMEText(body, "html" if html else "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")  # type: ignore[index]
    msg["From"] = formataddr((str(Header("QianFan Sync", "utf-8")), sender))
    msg["To"] = ", ".join(to_list)

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            if cfg.get("starttls"):
                server.starttls()
        try:
            if user and password:
                server.login(user, password)
            server.sendmail(sender, to_list, msg.as_string())
        finally:
            server.quit()
        logger.info(f"[mail] 邮件已发送 subject={subject} to={to_list}")
        return True
    except Exception as e:
        logger.error(f"[mail] 邮件发送失败: {e}", exc_info=True)
        return False
