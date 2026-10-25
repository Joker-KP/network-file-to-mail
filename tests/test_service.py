"""Regression tests: mail must precede archive, and retries avoid resending."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scanmail.config import Config, Polling, Route, SMTP
from scanmail.service import ScanMailer, build_message


class TestScanMailer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.routes = []
        for name in ("konto1", "konto2"):
            inbox = self.root / name / "inbox"
            inbox.mkdir(parents=True)
            archive = self.root / name / "archive"
            self.routes.append(Route(name, inbox, archive, (name + "@example.com",), None))
        self.config = Config(
            polling=Polling(1, 0, 2, 18 * 1024 * 1024, frozenset({".pdf"})),
            smtp=SMTP("smtp.example.com", 465, "ssl", "user", "password", "scanner@example.com", "Brother", 30),
            subject="Scan {route}: {filename}",
            body="File: {filename}, route: {route}",
            routes=tuple(self.routes),
        )
        self.worker = ScanMailer(self.config, self.root / "state")
        self.addCleanup(self.worker.close)

    def write_scan(self, route_idx: int, filename: str, content: bytes = b"%PDF-1.4 testing") -> Path:
        path = self.routes[route_idx].inbox / filename
        path.write_bytes(content)
        past = time.time() - 1000
        os.utime(path, (past, past))
        return path

    def ready_scan(self):
        self.worker.scan_once()
        self.worker.scan_once()

    def test_two_routes_send_to_separate_recipients_and_archive(self):
        self.write_scan(0, "scanA.pdf")
        self.write_scan(1, "scanB.pdf")
        with patch("scanmail.service.send_email") as mock_send:
            self.ready_scan()
            self.assertEqual(mock_send.call_count, 2)
            self.assertEqual(mock_send.call_args_list[0].args[2], ("konto1@example.com",))
            self.assertEqual(mock_send.call_args_list[1].args[2], ("konto2@example.com",))
        self.assertTrue((self.routes[0].archive / "scanA.pdf").exists())
        self.assertTrue((self.routes[1].archive / "scanB.pdf").exists())
        self.assertFalse((self.routes[0].inbox / "scanA.pdf").exists())
        self.assertFalse((self.routes[1].inbox / "scanB.pdf").exists())

    def test_error_from_smtp_leaves_file_for_retry(self):
        source = self.write_scan(0, "retry.pdf")
        with patch("scanmail.service.send_email", side_effect=OSError("SMTP offline")):
            self.ready_scan()
            self.assertTrue(source.exists())
            self.assertFalse((self.routes[0].archive / "retry.pdf").exists())
        with patch("scanmail.service.send_email") as sender:
            self.worker.scan_once()
            sender.assert_called_once()
        self.assertFalse(source.exists())

    def test_archive_failure_after_smtp_does_not_send_twice(self):
        source = self.write_scan(0, "archfail.pdf")
        with patch("scanmail.service.send_email") as sender:
            with patch("scanmail.service.archive_file", side_effect=PermissionError("NAS read-only")):
                self.ready_scan()
                self.assertTrue(source.exists())
            self.worker.scan_once()
            self.assertEqual(sender.call_count, 1)
        self.assertTrue((self.routes[0].archive / "archfail.pdf").exists())
        self.assertFalse(source.exists())

    def test_restarted_service_uses_persistent_ledger(self):
        source = self.write_scan(0, "restart.pdf")
        with patch("scanmail.service.send_email") as sender:
            with patch("scanmail.service.archive_file", side_effect=OSError("Disk unavailable")):
                self.ready_scan()
            self.worker.close()
            self.worker = ScanMailer(self.config, self.root / "state")
            self.addCleanup(self.worker.close)
            self.worker.scan_once()
            self.worker.scan_once()
            self.assertEqual(sender.call_count, 1)
        self.assertFalse(source.exists())

    def test_archive_collision_preserves_existing_file(self):
        existing = self.routes[0].archive
        existing.mkdir(parents=True, exist_ok=True)
        (existing / "file.pdf").write_bytes(b"preexisting")
        self.write_scan(0, "file.pdf", b"new-pdf")
        with patch("scanmail.service.send_email") as sender:
            self.ready_scan()
            sender.assert_called_once()
        self.assertEqual((existing / "file.pdf").read_bytes(), b"preexisting")
        archived = sorted(existing.glob("file__*.pdf"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), b"new-pdf")

    def test_touching_metadata_after_failed_archive_does_not_resend(self):
        source = self.write_scan(0, "chmod.pdf")
        with patch("scanmail.service.send_email") as sender:
            with patch("scanmail.service.archive_file", side_effect=OSError("No permission")):
                self.ready_scan()
            os.chmod(source, 0o600)  # Modifies ctime, but not the file identity.
            self.worker.scan_once()  # Fresh observation of modified ctime.
            self.worker.scan_once()
            sender.assert_called_once()
        self.assertFalse(source.exists())

    def test_unstable_file_and_wrong_extension_are_not_processed(self):
        source = self.write_scan(0, "new.pdf")
        self.write_scan(0, "ignore.txt")
        with patch("scanmail.service.send_email") as sender:
            self.worker.scan_once()
            sender.assert_not_called()
            source.write_bytes(b"changed")
            self.worker.scan_once()
            sender.assert_not_called()
            self.worker.scan_once()
            sender.assert_called_once()
        self.assertTrue((self.routes[0].inbox / "ignore.txt").exists())

    def test_message_has_correct_attachment_and_subject(self):
        msg = build_message(self.config, self.routes[0], "a.pdf", b"pdf-content")
        self.assertEqual(msg["Subject"], "Scan konto1: a.pdf")
        self.assertEqual(msg["To"], "konto1@example.com")
        self.assertEqual(msg.iter_attachments().__next__().get_content(), b"pdf-content")


if __name__ == "__main__":
    unittest.main()
