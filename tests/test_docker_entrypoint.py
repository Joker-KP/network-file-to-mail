from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import call, patch

from scanmail import docker_entrypoint


class TestDockerEntrypoint(unittest.TestCase):
    def test_drops_groups_then_gid_then_uid_before_exec(self):
        with patch.dict(os.environ, {"PUID": "1026", "PGID": "100"}):
            with patch.object(docker_entrypoint, "os") as system:
                system.environ = os.environ
                system.geteuid.return_value = 0
                with patch.object(sys, "argv", ["entrypoint", "--check-config"]):
                    self.assertEqual(docker_entrypoint.main(), 0)
        self.assertEqual(system.method_calls, [
            call.geteuid(), call.setgroups([]), call.setgid(100), call.setuid(1026),
            call.execv(sys.executable, [sys.executable, "-m", "scanmail", "--check-config"]),
        ])

    def test_invalid_identity_and_failed_privilege_drop_do_not_launch_worker(self):
        cases = [("0", "100"), ("-1", "100"), ("abc", "100"), ("1026", "-1"), ("1026", "")]
        for uid, gid in cases:
            with self.subTest(uid=uid, gid=gid):
                with patch.dict(os.environ, {"PUID": uid, "PGID": gid}):
                    with patch.object(docker_entrypoint.os, "execv") as launch:
                        with contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(docker_entrypoint.main(), 2)
                        launch.assert_not_called()
        with patch.dict(os.environ, {"PUID": "1026", "PGID": "100"}):
            with patch.object(docker_entrypoint, "os") as system:
                system.environ = os.environ
                system.geteuid.return_value = 0
                system.setgroups.side_effect = PermissionError("cannot drop groups")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(docker_entrypoint.main(), 2)
                system.setuid.assert_not_called()
                system.execv.assert_not_called()

    def test_nonroot_user_must_match_configured_identity(self):
        with patch.dict(os.environ, {"PUID": "1026", "PGID": "100"}):
            with patch.object(docker_entrypoint, "os") as system:
                system.environ = os.environ
                system.geteuid.return_value = 1000
                system.getegid.return_value = 1000
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(docker_entrypoint.main(), 2)
                system.execv.assert_not_called()

    def test_real_entrypoint_executes_config_check_with_environment_paths(self):
        environment = dict(os.environ)
        environment.update(
            PUID=str(os.geteuid() or 1026), PGID=str(os.getegid() or 100),
            SCANMAIL_CONFIG=str(Path("config/config.yaml").resolve()),
        )
        environment.pop("SMTP_USERNAME", None)
        environment.pop("SMTP_PASSWORD", None)
        result = subprocess.run(
            [sys.executable, "-m", "scanmail.docker_entrypoint", "--check-config"],
            env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("account1", result.stdout)
        self.assertIn("account2", result.stdout)
        self.assertNotIn("REPLACE_WITH_SMTP_APP_PASSWORD", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
