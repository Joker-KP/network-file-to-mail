from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scanmail.__main__ import main
from scanmail.config import load_config


class TestCLI(unittest.TestCase):
    def test_environment_paths_and_explicit_cli_overrides(self):
        config = load_config(Path("config/config.yaml"))
        cases = [
            ([], "/mounted/config.yaml", "/mounted/state"),
            (["--config", "/explicit/config.yaml", "--state-dir", "/explicit/state"],
             "/explicit/config.yaml", "/explicit/state"),
        ]
        for arguments, config_path, state_path in cases:
            with self.subTest(arguments=arguments):
                with patch.dict(os.environ, {"SCANMAIL_CONFIG": "/mounted/config.yaml", "SCANMAIL_STATE_DIR": "/mounted/state"}):
                    with patch.object(sys, "argv", ["scanmail", *arguments]):
                        with patch("scanmail.__main__.load_config", return_value=config) as load:
                            with patch("scanmail.__main__.ScanMailer") as worker:
                                with patch("scanmail.__main__.threading.Event") as event:
                                    event.return_value.is_set.return_value = True
                                    with patch("scanmail.__main__.signal.signal"):
                                        self.assertEqual(main(), 0)
                                load.assert_called_once_with(Path(config_path))
                                worker.assert_called_once_with(config, Path(state_path))
                                worker.return_value.close.assert_called_once()

    def test_config_check_uses_yaml_without_starting_worker_or_printing_password(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["scanmail", "--config", "config/config.yaml", "--check-config"]):
            with patch("scanmail.__main__.ScanMailer") as worker:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 0)
                worker.assert_not_called()
        self.assertIn("account1", output.getvalue())
        self.assertNotIn("REPLACE_WITH_SMTP_APP_PASSWORD", output.getvalue())


if __name__ == "__main__":
    unittest.main()
