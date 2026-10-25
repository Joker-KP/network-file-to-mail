from __future__ import annotations

import os
import tempfile
import traceback
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
  username: scanner@example.com
  password: "test-app-password"
  from_address: scanner@example.com
message:
  subject: "Scan {filename}"
  body: "File: {filename}"
routes:
  - id: account1
    inbox: /data/account1/inbox
    archive: /data/account1/archive
    to: [one@example.com]
  - id: account2
    inbox: /data/account2/inbox
    archive: /data/account2/archive
    to: [two@example.com]
"""


class TestConfig(unittest.TestCase):
    def load(self, document: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(document, encoding="utf-8")
            return load_config(path)

    def test_two_routes(self):
        with patch.dict(os.environ, {}, clear=True):
            config = self.load(BASE_CONFIG)
        self.assertEqual(len(config.routes), 2)
        self.assertEqual([route.id for route in config.routes], ["account1", "account2"])
        self.assertEqual(config.smtp.host, "smtp.fastmail.com")
        self.assertEqual(config.smtp.username, "scanner@example.com")
        self.assertEqual(config.smtp.password, "test-app-password")
        self.assertEqual(config.polling.min_file_age_seconds, 60)

    def test_smtp_environment_cannot_override_yaml(self):
        with patch.dict(os.environ, {"SMTP_USERNAME": "wrong", "SMTP_PASSWORD": "wrong"}):
            config = self.load(BASE_CONFIG)
        self.assertEqual(config.smtp.username, "scanner@example.com")
        self.assertEqual(config.smtp.password, "test-app-password")

    def test_missing_yaml_password_does_not_fall_back_to_environment(self):
        document = BASE_CONFIG.replace('  password: "test-app-password"\n', '')
        with patch.dict(os.environ, {"SMTP_PASSWORD": "environment-password"}):
            with self.assertRaisesRegex(ValueError, "smtp.password"):
                self.load(document)

    def test_invalid_yaml_credentials_fail(self):
        for key in ("username", "password"):
            for value in ('""', '"   "', 'null', '123', 'true'):
                with self.subTest(key=key, value=value):
                    old = 'scanner@example.com' if key == 'username' else '"test-app-password"'
                    document = BASE_CONFIG.replace(f'  {key}: {old}', f'  {key}: {value}')
                    with self.assertRaisesRegex(ValueError, f"smtp.{key}"):
                        self.load(document)

    def test_password_spaces_are_preserved_and_repr_hides_password(self):
        document = BASE_CONFIG.replace('"test-app-password"', '"  secret with spaces  "')
        config = self.load(document)
        self.assertEqual(config.smtp.password, "  secret with spaces  ")
        self.assertNotIn("secret with spaces", repr(config))

    def test_legacy_environment_keys_explain_migration(self):
        document = BASE_CONFIG.replace('  password: "test-app-password"', '  password_env: SMTP_PASSWORD')
        with self.assertRaisesRegex(ValueError, "smtp.username and smtp.password in YAML"):
            self.load(document)

    def test_yaml_syntax_error_does_not_expose_credentials(self):
        document = BASE_CONFIG.replace('"test-app-password"', '"private-secret" unexpected-token')
        try:
            self.load(document)
        except ValueError:
            diagnostic = traceback.format_exc()
            self.assertIn("Invalid YAML configuration", diagnostic)
            self.assertNotIn("private-secret", diagnostic)
        else:
            self.fail("Invalid YAML must fail")

    def test_duplicate_id_fails(self):
        document = BASE_CONFIG.replace("id: account2", "id: account1")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load(document)

    def test_duplicate_folder_fails(self):
        document = BASE_CONFIG.replace("/data/account2/inbox", "/data/account1/inbox")
        with self.assertRaisesRegex(ValueError, "already used"):
            self.load(document)

    def test_bad_subject_variable_fails(self):
        document = BASE_CONFIG.replace("Scan {filename}", "Scan {secret}")
        with self.assertRaisesRegex(ValueError, "allowed"):
            self.load(document)


if __name__ == "__main__":
    unittest.main()
