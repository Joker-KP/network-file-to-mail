from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanmail.config import load_config


BASE_CONFIG = """
version: 1
polling:
  interval_seconds: 30
  min_file_age_seconds: 60
  stable_observations: 2
  max_attachment_mb: 18
  extensions: [.pdf]
smtp:
  host: smtp.fastmail.com
  port: 465
  security: ssl
  username_env: SMTP_USERNAME
  password_env: SMTP_PASSWORD
  from_address: scanner@example.com
message:
  subject: "Scan {filename}"
  body: "File: {filename}"
routes:
  - id: konto1
    inbox: /data/konto1/inbox
    archive: /data/konto1/archive
    to: [one@example.com]
  - id: konto2
    inbox: /data/konto2/inbox
    archive: /data/konto2/archive
    to: [two@example.com]
"""


class TestConfig(unittest.TestCase):
    def load(self, document: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(document, encoding="utf-8")
            with patch.dict(os.environ, {"SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"}):
                return load_config(path)

    def test_two_routes(self):
        config = self.load(BASE_CONFIG)
        self.assertEqual(len(config.routes), 2)
        self.assertEqual(config.smtp.host, "smtp.fastmail.com")
        self.assertEqual(config.polling.min_file_age_seconds, 60)

    def test_duplicate_id_fails(self):
        document = BASE_CONFIG.replace("id: konto2", "id: konto1")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load(document)

    def test_duplicate_folder_fails(self):
        document = BASE_CONFIG.replace("/data/konto2/inbox", "/data/konto1/inbox")
        with self.assertRaisesRegex(ValueError, "already used"):
            self.load(document)

    def test_bad_subject_variable_fails(self):
        document = BASE_CONFIG.replace("Scan {filename}", "Scan {secret}")
        with self.assertRaisesRegex(ValueError, "allowed"):
            self.load(document)


if __name__ == "__main__":
    unittest.main()
