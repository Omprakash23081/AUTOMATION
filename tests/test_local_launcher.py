import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import start_local


class LocalLauncherTests(unittest.TestCase):
    def test_loads_only_workspace_decryption_and_session_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "# Local settings\n"
                "SESSION_SECRET=local-session-secret\n"
                "CUTROOM_SECRET_ENCRYPTION_KEY=local-encryption-key\n"
                "SMTP_PASSWORD=must-not-be-loaded\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                start_local.load_local_secrets(env_file)
                self.assertEqual(os.environ["SESSION_SECRET"], "local-session-secret")
                self.assertEqual(
                    os.environ["CUTROOM_SECRET_ENCRYPTION_KEY"],
                    "local-encryption-key",
                )
                self.assertNotIn("SMTP_PASSWORD", os.environ)

    def test_existing_process_secrets_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "SESSION_SECRET=file-session-secret\n"
                "CUTROOM_SECRET_ENCRYPTION_KEY=file-encryption-key\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "SESSION_SECRET": "process-session-secret",
                    "CUTROOM_SECRET_ENCRYPTION_KEY": "process-encryption-key",
                },
                clear=True,
            ):
                start_local.load_local_secrets(env_file)
                self.assertEqual(
                    os.environ["SESSION_SECRET"], "process-session-secret"
                )
                self.assertEqual(
                    os.environ["CUTROOM_SECRET_ENCRYPTION_KEY"],
                    "process-encryption-key",
                )


if __name__ == "__main__":
    unittest.main()
