import unittest
from unittest.mock import Mock, patch

from youtube_automation.gemini import browser as gemini_browser


class GeminiNewChatTests(unittest.TestCase):
    def test_start_new_gemini_chat_goes_to_home_and_starts_fresh_conversation(self):
        page = Mock()
        page.url = gemini_browser.config.GEMINI_URL
        page.get_by_role.return_value.count.return_value = 0
        gemini_browser.start_new_gemini_chat(page, 7)

        page.goto.assert_called_once_with(
            gemini_browser.config.GEMINI_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.get_by_role.assert_any_call(
            "button",
            name=gemini_browser.re.compile("new chat|new conversation", gemini_browser.re.IGNORECASE),
        )

    def test_waits_for_manual_sign_in_when_google_login_is_open(self):
        page = Mock()
        page.url = "https://accounts.google.com/signin"
        upload_button = Mock()

        def get_by_role(role, name):
            if role == "button" and name is gemini_browser.UPLOAD_BUTTON_NAME:
                return upload_button
            return Mock()

        page.get_by_role.side_effect = get_by_role

        with patch.object(gemini_browser, "log") as log:
            gemini_browser.start_new_gemini_chat(page, 7)

        upload_button.wait_for.assert_called_once_with(
            state="visible",
            timeout=gemini_browser.SIGN_IN_WAIT_TIMEOUT_MS,
        )
        log.assert_any_call(
            "Gemini sign-in required. Sign in in the opened Chrome window; "
            "waiting up to 120 seconds for the upload controls.",
            7,
        )

    def test_fails_fast_when_upload_controls_are_missing_without_sign_in_prompt(self):
        page = Mock()
        page.url = "https://gemini.google.com/app"
        upload_button = Mock()
        upload_button.wait_for.side_effect = gemini_browser.TimeoutError("not found")

        def get_by_role(role, name):
            if role == "button" and name is gemini_browser.UPLOAD_BUTTON_NAME:
                return upload_button
            locator = Mock()
            locator.count.return_value = 0
            return locator

        page.get_by_role.side_effect = get_by_role

        with self.assertRaisesRegex(RuntimeError, "no sign-in prompt was detected"):
            gemini_browser.start_new_gemini_chat(page, 7)

        upload_button.wait_for.assert_called_once_with(
            state="visible",
            timeout=15000,
        )


if __name__ == "__main__":
    unittest.main()
