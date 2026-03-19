from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from email.utils import parsedate_to_datetime

from app.mail.parser import parse_email_bytes


class ParserTests(unittest.TestCase):
    def test_missing_message_id_fallback_is_stable(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: Test\r\n"
            b"Date: Sun, 09 Mar 2026 09:00:00 +0900\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"body"
        )

        first = parse_email_bytes(raw_message)
        second = parse_email_bytes(raw_message)

        self.assertEqual(first.message_id, second.message_id)
        self.assertTrue(first.message_id.startswith("missing-"))

    def test_html_body_strips_script_tags(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: HTML\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b"<html><body><script>alert('x')</script><p>Hello</p><p>World</p></body></html>"
        )

        parsed = parse_email_bytes(raw_message)

        self.assertIn("Hello", parsed.raw_preview)
        self.assertIn("World", parsed.raw_preview)
        self.assertNotIn("alert", parsed.raw_preview)

    def test_reply_headers_are_parsed_for_threading(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: Re: Weekly Report\r\n"
            b"Message-ID: <child@example.com>\r\n"
            b"In-Reply-To: <root@example.com>\r\n"
            b"References: <root@example.com> <mid@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"reply body"
        )

        parsed = parse_email_bytes(raw_message)

        self.assertEqual(parsed.normalized_subject, "Weekly Report")
        self.assertEqual(parsed.in_reply_to, "<root@example.com>")
        self.assertEqual(parsed.references, ["<root@example.com>", "<mid@example.com>"])
        self.assertEqual(parsed.thread_key, "<child@example.com>")

    def test_aware_date_is_converted_to_local_naive_time(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: TZ\r\n"
            b"Message-ID: <tz@example.com>\r\n"
            b"Date: Sat, 07 Mar 2026 23:30:00 -0800\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"body"
        )

        parsed = parse_email_bytes(raw_message)
        expected = parsedate_to_datetime("Sat, 07 Mar 2026 23:30:00 -0800").astimezone().replace(tzinfo=None, microsecond=0)

        self.assertEqual(parsed.received_at, expected)

    def test_missing_message_id_can_use_prefetched_override(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: Override\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"body"
        )

        parsed = parse_email_bytes(raw_message, fallback_message_id="missing-header-123")

        self.assertEqual(parsed.message_id, "missing-header-123")

    def test_attachments_are_saved_into_relative_cache_paths(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: Attachment\r\n"
            b"Message-ID: <attachment@example.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=\"BOUNDARY\"\r\n"
            b"\r\n"
            b"--BOUNDARY\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"body\r\n"
            b"--BOUNDARY\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=\"report.pdf\"\r\n"
            b"Content-Transfer-Encoding: base64\r\n"
            b"\r\n"
            b"JVBERi0xLjQK\r\n"
            b"--BOUNDARY--\r\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            parsed = parse_email_bytes(
                raw_message,
                attachment_storage_root=root / "cache" / "mail_attachments",
                attachment_relative_root=root,
            )

            self.assertEqual(parsed.attachment_names, ["report.pdf"])
            self.assertEqual(len(parsed.attachment_paths), 1)
            attachment_path = root / parsed.attachment_paths[0]
            self.assertTrue(attachment_path.exists())
            self.assertEqual(attachment_path.name, "report.pdf")

    def test_attachment_free_message_does_not_create_empty_cache_directory(self) -> None:
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: user@example.com\r\n"
            b"Subject: No Attachment\r\n"
            b"Message-ID: <plain@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"body"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachment_root = root / "cache" / "mail_attachments"
            parsed = parse_email_bytes(
                raw_message,
                attachment_storage_root=attachment_root,
                attachment_relative_root=root,
            )

            self.assertEqual(parsed.attachment_names, [])
            self.assertEqual(parsed.attachment_paths, [])
            self.assertFalse(attachment_root.exists())


if __name__ == "__main__":
    unittest.main()
