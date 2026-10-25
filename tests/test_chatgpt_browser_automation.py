import os
import tempfile
import unittest
from itertools import count
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import chatgpt_browser_automation as automation
from playwright.sync_api import Error as PlaywrightError


class ChatGptBrowserAutomationTests(unittest.TestCase):
    def test_find_chat_input_uses_current_composer_selector(self):
        composer = Mock()
        composer.first = composer
        composer.last = composer
        composer.is_visible.return_value = True
        page = Mock()
        page.locator.return_value = composer

        self.assertIs(automation.find_chat_input(page), composer)
        self.assertEqual(page.locator.call_args.args[0], "#prompt-textarea")

    def test_detects_cloudflare_human_verification(self):
        challenge = Mock()
        challenge.count.return_value = 1
        page = Mock()
        page.locator.return_value = challenge

        self.assertTrue(automation._human_verification_visible(page))

    def test_waits_for_manual_verification_then_uses_chatgpt_composer(self):
        composer = Mock()
        statuses = []
        page = Mock()
        with patch.object(
            automation, "find_chat_input", side_effect=[None, composer]
        ), patch.object(
            automation, "_human_verification_visible", side_effect=[True, False]
        ):
            result = automation.wait_for_chat_input(
                page,
                timeout_ms=5_000,
                on_status=lambda stage, detail: statuses.append((stage, detail)),
            )

        self.assertIs(result, composer)
        self.assertEqual(statuses[0][0], "Waiting for ChatGPT verification")
        self.assertIn("manually", statuses[0][1])
        page.wait_for_timeout.assert_called_once_with(500)

    def test_headless_verification_fails_with_actionable_guidance(self):
        page = Mock()
        statuses = []
        with patch.object(
            automation, "find_chat_input", return_value=None
        ), patch.object(
            automation, "_human_verification_visible", return_value=True
        ), self.assertRaisesRegex(
            PlaywrightError, "headless.*cannot be interacted with"
        ):
            automation.wait_for_chat_input(
                page,
                timeout_ms=5_000,
                on_status=lambda stage, detail: statuses.append((stage, detail)),
                headless=True,
            )

        self.assertEqual(statuses[0][0], "Waiting for ChatGPT verification")
        self.assertIn("visible browser", statuses[0][1])
        page.wait_for_timeout.assert_not_called()

    def test_headless_composer_timeout_explains_visible_runtime(self):
        with self.assertRaisesRegex(
            PlaywrightError, "Windows server with a visible browser"
        ):
            automation.wait_for_chat_input(Mock(), timeout_ms=0, headless=True)

    def test_launch_falls_back_to_bundled_chromium_when_chrome_is_missing(self):
        playwright = Mock()
        context = Mock()
        playwright.chromium.launch_persistent_context.side_effect = [
            PlaywrightError("Chrome executable was not found"),
            context,
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "chrome-profile"
            fallback = Path(temp_dir) / "chromium-profile"
            launched = automation._launch_context(
                playwright,
                profile_dir=profile,
                fallback_profile_dir=fallback,
                headless=False,
                channel="chrome",
            )

        self.assertIs(launched, context)
        self.assertEqual(
            playwright.chromium.launch_persistent_context.call_args_list,
            [
                call(
                    user_data_dir=str(profile),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                    channel="chrome",
                ),
                call(
                    user_data_dir=str(fallback),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                ),
            ],
        )

    def test_session_launches_profile_navigates_and_reports_status(self):
        context = Mock()
        page = Mock()
        page.url = "https://chatgpt.com/"
        context.pages = [page]
        playwright_context = MagicMock()
        playwright_context.__enter__.return_value = Mock()
        statuses = []
        with patch.object(automation, "sync_playwright", return_value=playwright_context), patch.dict(
            os.environ, {"CUTROOM_CHATGPT_COMPOSER_TIMEOUT_SECONDS": "10"}
        ), patch.object(
            automation, "_launch_context", return_value=context
        ) as launch, patch.object(automation, "wait_for_chat_input") as wait_for_input:
            with automation.chatgpt_session(
                profile_dir=Path("chat-profile"),
                fallback_profile_dir=Path("chat-profile-fallback"),
                headless=False,
                channel=None,
                chatgpt_url="https://chatgpt.com/",
                on_status=lambda stage, detail: statuses.append((stage, detail)),
            ) as opened_page:
                self.assertIs(opened_page, page)

        self.assertEqual(launch.call_args.kwargs["profile_dir"], Path("chat-profile"))
        page.bring_to_front.assert_called_once_with()
        page.goto.assert_called_once_with(
            "https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000
        )
        self.assertEqual(wait_for_input.call_args.args, (page,))
        self.assertEqual(wait_for_input.call_args.kwargs["timeout_ms"], 10_000)
        self.assertFalse(wait_for_input.call_args.kwargs["headless"])
        self.assertTrue(callable(wait_for_input.call_args.kwargs["on_status"]))
        self.assertEqual(
            [stage for stage, _ in statuses],
            [
                "Starting ChatGPT browser",
                "Opening ChatGPT",
                "Waiting for ChatGPT input",
            ],
        )
        context.close.assert_called_once_with()

    def test_windows_defaults_to_a_visible_browser(self):
        context = Mock()
        context.pages = [Mock()]
        playwright_context = MagicMock()
        playwright_context.__enter__.return_value = Mock()
        with patch.dict(os.environ, {}, clear=True), patch.object(
            automation.sys, "platform", "win32"
        ), patch.object(
            automation, "sync_playwright", return_value=playwright_context
        ), patch.object(
            automation, "_launch_context", return_value=context
        ) as launch:
            with automation.chatgpt_session(wait_for_composer=False):
                pass

        self.assertFalse(launch.call_args.kwargs["headless"])

    def test_prompt_submission_waits_for_response(self):
        composer = Mock()
        composer.first = composer
        composer.last = composer
        composer.is_visible.return_value = True
        send_button = Mock()
        send_button.first = send_button
        send_button.last = send_button
        send_button.is_visible.return_value = True
        hidden = Mock()
        hidden.first = hidden
        hidden.last = hidden
        hidden.is_visible.return_value = False
        hidden.count.return_value = 0
        assistant_messages = Mock()
        assistant_messages.count.return_value = 0

        def locator(selector):
            if selector == "#prompt-textarea":
                return composer
            if selector == "[data-message-author-role='assistant']":
                return assistant_messages
            if selector == "button[data-testid='send-button']":
                return send_button
            return hidden

        page = Mock()
        page.locator.side_effect = locator
        page.get_by_role.return_value = hidden
        with patch.object(automation, "wait_for_response", return_value='{"clips":[]}') as wait:
            response = automation.send_prompt(page, "rank this transcript", timeout_seconds=45)

        self.assertEqual(response, '{"clips":[]}')
        composer.fill.assert_called_once_with("rank this transcript")
        send_button.click.assert_called_once_with(timeout=15_000)
        self.assertEqual(wait.call_args.args, (page, 0, 45))
        self.assertEqual(
            set(wait.call_args.kwargs),
            {"previous_snapshot", "on_status", "prompt"},
        )
        self.assertIsNone(wait.call_args.kwargs["on_status"])
        self.assertEqual(wait.call_args.kwargs["prompt"], "rank this transcript")

    def test_wait_for_response_detects_completed_markdown_turn(self):
        hidden = Mock()
        hidden.last = hidden
        hidden.count.return_value = 0
        hidden.is_visible.return_value = False
        markdown = Mock()
        markdown.last = markdown
        markdown.count.return_value = 1
        markdown.inner_text.return_value = '{"clips":[{"title":"A clip"}]}'
        page = Mock()

        def locator(selector):
            if selector == automation.ASSISTANT_MESSAGE_SELECTORS[1]:
                return markdown
            return hidden

        page.locator.side_effect = locator
        page.get_by_role.return_value = hidden
        baseline = {
            selector: (0, "")
            for selector in automation.ASSISTANT_MESSAGE_SELECTORS
        }
        with patch.object(automation.time, "monotonic", side_effect=count()):
            response = automation.wait_for_response(
                page,
                previous_count=0,
                timeout_seconds=60,
                previous_snapshot=baseline,
            )

        self.assertEqual(response, '{"clips":[{"title":"A clip"}]}')

    def test_wait_for_response_detects_completed_article_without_role_markup(self):
        article = Mock()
        article.last = article
        article.count.return_value = 1
        article.inner_text.return_value = '{"clips":[{"title":"A clip"}]}'
        hidden = Mock()
        hidden.last = hidden
        hidden.count.return_value = 0
        hidden.is_visible.return_value = False
        page = Mock()
        page.locator.side_effect = lambda selector: (
            article if selector == "main article" else hidden
        )
        page.get_by_role.return_value = hidden
        baseline = {
            selector: (0, "")
            for selector in automation.ASSISTANT_MESSAGE_SELECTORS
        }

        with patch.object(automation.time, "monotonic", side_effect=count()):
            response = automation.wait_for_response(
                page,
                previous_count=0,
                timeout_seconds=60,
                previous_snapshot=baseline,
                prompt="rank the transcript",
            )

        self.assertEqual(response, '{"clips":[{"title":"A clip"}]}')

    def test_wait_for_response_does_not_return_user_prompt_as_reply(self):
        article = Mock()
        article.last = article
        article.count.return_value = 1
        article.inner_text.return_value = "rank the transcript"
        hidden = Mock()
        hidden.last = hidden
        hidden.count.return_value = 0
        hidden.is_visible.return_value = False
        page = Mock()
        page.locator.side_effect = lambda selector: (
            article if selector == "main article" else hidden
        )
        page.get_by_role.return_value = hidden
        baseline = {
            selector: (0, "")
            for selector in automation.ASSISTANT_MESSAGE_SELECTORS
        }

        with patch.object(automation.time, "monotonic", side_effect=count()), self.assertRaisesRegex(
            PlaywrightError, "did not finish"
        ):
            automation.wait_for_response(
                page,
                previous_count=0,
                timeout_seconds=0,
                previous_snapshot=baseline,
                prompt="rank the transcript",
            )

    def test_latest_response_falls_back_to_visible_main_text_after_prompt(self):
        prompt = "Return only JSON.\nTranscript: example"
        response = '{\n  "clips": [{"title": "Share the moment"}]\n}'
        page_text = (
            "Old conversation\n"
            "Return only JSON. Transcript: example\n"
            + response
            + "\nCopy\nRead aloud\nChatGPT is AI and can make mistakes."
        )
        page = Mock()
        page.locator.return_value.inner_text.return_value = page_text

        actual = automation._latest_response_after_prompt(page, prompt)

        self.assertEqual(actual, response)
        page.locator.assert_called_once_with("main")

    def test_latest_response_fallback_uses_last_matching_prompt(self):
        prompt = "Return JSON only"
        page = Mock()
        page.locator.return_value.inner_text.return_value = (
            f"{prompt}\n{{\"clips\":[]}}\n{prompt}\n{{\"clips\":[{{\"title\":\"new\"}}]}}"
        )

        actual = automation._latest_response_after_prompt(page, prompt)

        self.assertEqual(actual, '{"clips":[{"title":"new"}]}')

    def test_latest_response_fallback_handles_collapsed_long_prompt(self):
        prompt = (
            "Return only a JSON array. Analyze the transcript and select "
            "the strongest moments without adding explanations. Transcript: "
            "a very long transcript that is collapsed by the chat interface"
        )
        visible_prompt = prompt[:60].rsplit(" ", 1)[0]
        response = '[{"start":25.0,"end":40.0,"title":"A clip"}]'
        page = Mock()
        page.locator.return_value.inner_text.return_value = (
            f"{visible_prompt}... Show more\n{response}\nCopy\nRead aloud"
        )

        actual = automation._latest_response_after_prompt(page, prompt)

        self.assertEqual(actual, response)

    def test_partial_prompt_without_collapse_control_is_not_treated_as_response(self):
        prompt = "Return only JSON. " + ("Analyze this transcript. " * 10)
        visible_prompt = prompt[:60].rsplit(" ", 1)[0]
        page = Mock()
        page.locator.return_value.inner_text.return_value = (
            f"{visible_prompt}... Loading"
        )

        self.assertEqual(automation._latest_response_after_prompt(page, prompt), "")

    def test_waiter_uses_visible_conversation_when_assistant_selectors_are_absent(self):
        prompt = (
            "Return JSON only. Analyze the transcript and select the "
            "strongest moments without adding explanations. Transcript: "
            "a long transcript collapsed by the interface"
        )
        visible_prompt = prompt[:60].rsplit(" ", 1)[0]
        response = '{"clips":[{"title":"new"}]}'
        main = Mock()
        main.inner_text.return_value = f"{visible_prompt}... Show more\n{response}"
        hidden = Mock()
        hidden.last = hidden
        hidden.is_visible.return_value = False
        page = Mock()
        page.locator.side_effect = lambda selector: main if selector == "main" else hidden
        page.get_by_role.return_value = hidden
        empty_snapshot = {
            selector: (0, "")
            for selector in automation.ASSISTANT_MESSAGE_SELECTORS
        }
        with (
            patch.object(
                automation,
                "_response_snapshot",
                return_value=empty_snapshot,
            ),
            patch.object(automation.time, "monotonic", side_effect=count()),
        ):
            actual = automation.wait_for_response(
                page,
                previous_count=0,
                timeout_seconds=60,
                previous_snapshot=empty_snapshot,
                prompt=prompt,
            )

        self.assertEqual(actual, response)


if __name__ == "__main__":
    unittest.main()
