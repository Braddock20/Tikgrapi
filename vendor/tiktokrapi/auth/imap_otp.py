"""IMAP OTP poller. Polls Gmail/Outlook/etc inbox for TikTok's 6-digit code.

Usage:
    poller = IMAPOTPPoller(
        email="me@gmail.com",
        password="<gmail app password>",  # NOT main pw
        host="imap.gmail.com",
    )
    code = poller.wait_for_code(sender="noreply@tiktok.com", timeout=60)
"""
from __future__ import annotations

import imaplib
import re
import time
from email import message_from_bytes
from typing import Optional


CODE_PATTERN = re.compile(r"\b(\d{6})\b")


class IMAPOTPPoller:
    def __init__(
        self,
        email: str,
        password: str,
        *,
        host: str = "imap.gmail.com",
        port: int = 993,
    ):
        self.email = email
        self.password = password
        self.host = host
        self.port = port

    def wait_for_code(
        self,
        *,
        sender: str = "noreply@tiktok.com",
        subject_contains: str = "verification",
        timeout: float = 90.0,
        poll_interval: float = 3.0,
        since_ts: Optional[float] = None,
    ) -> str:
        """Poll inbox until TikTok's verification email arrives. Return 6-digit code."""
        deadline = time.time() + timeout
        # Only check messages newer than the moment we started polling
        cutoff = since_ts if since_ts is not None else time.time()

        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                code = self._check_once(sender, subject_contains, cutoff)
                if code:
                    return code
            except Exception:
                continue
        raise TimeoutError(f"no OTP from {sender} within {timeout}s")

    def _check_once(self, sender: str, subject_contains: str, cutoff: float) -> Optional[str]:
        with imaplib.IMAP4_SSL(self.host, self.port) as imap:
            imap.login(self.email, self.password)
            imap.select("INBOX")
            typ, data = imap.search(None, f'(FROM "{sender}")')
            if typ != "OK":
                return None
            ids = data[0].split()
            # walk newest first
            for msg_id in reversed(ids[-10:]):
                typ, msg_data = imap.fetch(msg_id, "(RFC822)")
                if typ != "OK":
                    continue
                raw = msg_data[0][1]
                msg = message_from_bytes(raw)
                subject = msg.get("Subject", "")
                if subject_contains and subject_contains.lower() not in subject.lower():
                    continue
                # Parse message date — only take if newer than cutoff
                from email.utils import parsedate_to_datetime
                try:
                    msg_dt = parsedate_to_datetime(msg.get("Date", ""))
                    if msg_dt.timestamp() < cutoff:
                        continue
                except Exception:
                    pass
                # Find 6-digit code in body
                body = self._extract_body(msg)
                m = CODE_PATTERN.search(body)
                if m:
                    return m.group(1)
        return None

    @staticmethod
    def _extract_body(msg) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode(errors="ignore")
                if part.get_content_type() == "text/html":
                    return part.get_payload(decode=True).decode(errors="ignore")
            return ""
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode(errors="ignore")
        return str(payload or "")
