"""Persistent Playwright automation for ChatGPT browser sessions."""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator

from playwright.sync_api import Error as PlaywrightError, Page, Playwright, sync_playwright


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CUTROOM_DATA", ROOT / "data")).resolve()
CHATGPT_URL = os.environ.get("CUTROOM_CHATGPT_URL", "https://chatgpt.com/")
LOCKED_PROFILE_MESSAGES = (
    "existing browser session",
    "user data directory is already in use",
    "processsingleton",
    "profile appears to be in use",
)
ASSISTANT_MESSAGE_SELECTORS = (
    "[data-message-author-role='assistant']",
    "[data-testid^='conversation-turn-']",
    "[data-testid^='conversation-turn-'] .markdown",
    ".agent-turn",
    ".agent-turn .markdown",
    "main article",
    "main .markdown",
    "article",
)


def _is_profile_locked(error: PlaywrightError) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in LOCKED_PROFILE_MESSAGES)


def _launch_context(
    playwright: Playwright,
    *,
    profile_dir: Path,
    fallback_profile_dir: Path,
    headless: bool,
    channel: str | None,
):
    profiles = [profile_dir]
    if fallback_profile_dir.resolve() != profile_dir.resolve():
        profiles.append(fallback_profile_dir)
    for profile_index, candidate_profile in enumerate(profiles):
        candidate_profile.mkdir(parents=True, exist_ok=True)
        options = {
            "user_data_dir": str(candidate_profile),
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if channel:
            options["channel"] = channel
        try:
            return playwright.chromium.launch_persistent_context(**options)
        except PlaywrightError as error:
            has_fallback_profile = profile_index + 1 < len(profiles)
            if _is_profile_locked(error) and candidate_profile == profile_dir and has_fallback_profile:
                print(
                    f"ChatGPT profile is already open; trying fallback profile: "
                    f"{fallback_profile_dir}",
                    flush=True,
                )
                continue
            if channel and candidate_profile == profile_dir and has_fallback_profile:
                print(
                    f"Could not launch Chrome channel ({error}); retrying with "
                    "Playwright Chromium and its separate fallback profile.",
                    flush=True,
                )
                fallback_profile_dir.mkdir(parents=True, exist_ok=True)
                fallback_options = {
                    "user_data_dir": str(fallback_profile_dir),
                    "headless": headless,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                return playwright.chromium.launch_persistent_context(**fallback_options)
            raise
    raise RuntimeError("Could not launch ChatGPT browser with either persistent profile.")


def find_chat_input(page: Page):
    candidates = (
        lambda: page.locator("#prompt-textarea"),
        lambda: page.locator("[data-testid='prompt-textarea']"),
        lambda: page.locator("[contenteditable='true'][role='textbox']"),
        lambda: page.locator("textarea").first,
        lambda: page.locator("[contenteditable='true']").last,
        lambda: page.get_by_role(
            "textbox", name=re.compile("message|ask anything|prompt", re.IGNORECASE)
        ).last,
        lambda: page.get_by_placeholder(re.compile("message|prompt", re.IGNORECASE)).last,
    )
    for get_candidate in candidates:
        try:
            candidate = get_candidate()
            if candidate.is_visible():
                return candidate
        except PlaywrightError:
            continue
    return None


def _human_verification_visible(page: Page) -> bool:
    challenge_selectors = (
        "iframe[src*='challenges.cloudflare.com']",
        "#challenge-running",
        "#challenge-stage",
        ".cf-turnstile",
    )
    try:
        if page.locator(", ".join(challenge_selectors)).count():
            return True
        title = page.title().lower()
        body = page.locator("body").inner_text(timeout=1_000).lower()
    except PlaywrightError:
        return False
    challenge_phrases = (
        "verify you are human",
        "checking your browser",
        "performing security verification",
        "complete the security check",
        "cloudflare",
        "captcha",
        "turnstile",
    )
    return any(phrase in title or phrase in body for phrase in challenge_phrases)


def wait_for_chat_input(
    page: Page,
    timeout_ms: int = 600_000,
    on_status: Callable[[str, str], None] | None = None,
    headless: bool = False,
):
    deadline = time.monotonic() + timeout_ms / 1000
    last_stage = None
    next_status_update = 0.0
    while time.monotonic() < deadline:
        chat_input = find_chat_input(page)
        if chat_input is not None:
            return chat_input
        now = time.monotonic()
        verification_visible = _human_verification_visible(page)
        stage = "Waiting for ChatGPT verification" if verification_visible else "Waiting for ChatGPT input"
        if headless and verification_visible:
            detail = (
                "ChatGPT is requesting human verification, but this pipeline browser is headless "
                "and cannot be interacted with. Run the Windows Cutroom server with a visible "
                "browser to complete sign-in or verification."
            )
            if on_status:
                on_status(stage, detail)
            raise PlaywrightError(detail)
        if on_status and (stage != last_stage or now >= next_status_update):
            detail = (
                "A human-verification page is visible. Complete its check manually in the opened "
                "browser; automation does not solve CAPTCHA or anti-bot challenges."
                if verification_visible
                else "Waiting for the ChatGPT composer. If sign-in, MFA, or a verification prompt "
                "is shown, complete it manually in the opened browser."
            )
            on_status(stage, detail)
            last_stage = stage
            next_status_update = now + 10
        page.wait_for_timeout(500)
    if headless:
        raise PlaywrightError(
            "ChatGPT's composer did not appear in the headless pipeline browser. This Docker "
            "worker cannot accept interactive sign-in or verification. Run Cutroom's Windows "
            "server with a visible browser; it uses the host's separate browser profile."
        )
    raise PlaywrightError(
        "ChatGPT message input was not found after waiting "
        f"{timeout_ms // 1000} seconds. Sign in or complete any verification manually "
        "in the opened browser, then retry."
    )


def _response_snapshot(
    page: Page,
    prompt: str = "",
) -> dict[str, tuple[int, str]]:
    snapshot = {}
    normalized_prompt = " ".join(prompt.split())
    for selector in ASSISTANT_MESSAGE_SELECTORS:
        try:
            messages = page.locator(selector)
            count = messages.count()
            text = messages.last.inner_text().strip() if count else ""
            if normalized_prompt and normalized_prompt in " ".join(text.split()):
                text = ""
            snapshot[selector] = (count, text)
        except PlaywrightError:
            snapshot[selector] = (0, "")
    return snapshot


def _latest_response_after_prompt(page: Page, prompt: str) -> str:
    if not prompt.strip():
        return ""

    def pattern_for(value: str) -> re.Pattern[str]:
        return re.compile(
            r"\s+".join(re.escape(part) for part in re.split(r"\s+", value.strip())),
            re.DOTALL,
        )

    normalized_prompt = " ".join(prompt.split())
    prompt_patterns = [(pattern_for(normalized_prompt), True)]
    prefix_length = min(240, len(normalized_prompt))
    while prefix_length >= 32:
        prefix = normalized_prompt[:prefix_length].rsplit(" ", 1)[0]
        if prefix and prefix != normalized_prompt:
            prompt_patterns.append((pattern_for(prefix), False))
        prefix_length //= 2

    for selector in ("main", "body"):
        try:
            text = page.locator(selector).inner_text()
        except PlaywrightError:
            continue
        for prompt_pattern, is_complete in prompt_patterns:
            matches = list(prompt_pattern.finditer(text))
            if not matches:
                continue
            response = text[matches[-1].end():].strip()
            if not is_complete:
                collapsed_prompt_end = re.search(r"show more", response, re.IGNORECASE)
                if collapsed_prompt_end is None:
                    continue
                response = response[collapsed_prompt_end.end():].strip()
            trailing_controls = {
                "copy",
                "read aloud",
                "good response",
                "bad response",
                "share",
                "regenerate",
                "retry",
                "edit",
                "chatgpt is ai and can make mistakes.",
                "chatgpt can make mistakes. check important info.",
            }
            lines = response.splitlines()
            while lines and lines[-1].strip().lower() in trailing_controls:
                lines.pop()
            response = "\n".join(lines).strip()
            if response:
                return response
    return ""


def wait_for_response(
    page: Page,
    previous_count: int,
    timeout_seconds: int,
    previous_snapshot: dict[str, tuple[int, str]] | None = None,
    on_status: Callable[[str, str], None] | None = None,
    prompt: str = "",
) -> str:
    deadline = time.monotonic() + timeout_seconds
    previous_text = ""
    stable_polls = 0
    next_status_update = 0.0
    has_previous_snapshot = previous_snapshot is not None
    previous_snapshot = previous_snapshot or {
        ASSISTANT_MESSAGE_SELECTORS[0]: (previous_count, "")
    }
    while time.monotonic() < deadline:
        snapshot = _response_snapshot(page, prompt=prompt)
        current_text = ""
        selectors = (
            ASSISTANT_MESSAGE_SELECTORS
            if has_previous_snapshot
            else ASSISTANT_MESSAGE_SELECTORS[:1]
        )
        for selector in selectors:
            count, text = snapshot[selector]
            if has_previous_snapshot:
                baseline_count, baseline_text = previous_snapshot.get(selector, (0, ""))
                if text and (count > baseline_count or text != baseline_text):
                    current_text = text
                    break
            elif selector == ASSISTANT_MESSAGE_SELECTORS[0] and count > previous_count:
                current_text = text
                break
        if not current_text and prompt:
            current_text = _latest_response_after_prompt(page, prompt)
        if not current_text and not has_previous_snapshot and snapshot:
            primary_count, primary_text = snapshot[ASSISTANT_MESSAGE_SELECTORS[0]]
            if primary_count > previous_count:
                current_text = primary_text
        stop_candidates = (
            page.locator("button[data-testid='stop-button']"),
            page.locator("button[data-testid*='stop' i]"),
            page.locator("button[aria-label*='Stop' i]"),
            page.locator("button[title*='Stop' i]"),
            page.get_by_role(
                "button",
                name=re.compile("stop generating|stop", re.IGNORECASE),
            ),
        )
        generating = False
        for candidate in stop_candidates:
            try:
                if candidate.last.is_visible():
                    generating = True
                    break
            except PlaywrightError:
                continue
        if current_text and current_text == previous_text and not generating:
            stable_polls += 1
        else:
            stable_polls = 0
        if current_text and not generating and stable_polls >= 3:
            return current_text
        previous_text = current_text
        now = time.monotonic()
        if on_status and now >= next_status_update:
            detail = (
                f"Waiting for ChatGPT to finish its reply ({len(current_text)} characters received)."
                if current_text
                else "Waiting for a new ChatGPT assistant reply; no response text has been detected yet."
            )
            on_status("Ranking moments with ChatGPT", detail)
            next_status_update = now + 10
        page.wait_for_timeout(1000)
    raise PlaywrightError(
        f"ChatGPT response did not finish within {timeout_seconds} seconds."
    )


def send_prompt(
    page: Page,
    prompt: str,
    timeout_seconds: int = 600,
    on_status: Callable[[str, str], None] | None = None,
) -> str:
    chat_input = find_chat_input(page)
    if chat_input is None:
        raise PlaywrightError("ChatGPT message input disappeared before the prompt could be sent.")
    previous_snapshot = _response_snapshot(page)
    previous_count = previous_snapshot[ASSISTANT_MESSAGE_SELECTORS[0]][0]
    chat_input.fill(prompt)
    send_candidates = (
        page.locator("button[data-testid='send-button']"),
        page.locator("button[aria-label*='Send' i]"),
        page.get_by_role("button", name=re.compile("send", re.IGNORECASE)),
    )
    send_button = None
    for candidate in send_candidates:
        try:
            if candidate.last.is_visible():
                send_button = candidate.last
                break
        except PlaywrightError:
            continue
    if send_button is None:
        chat_input.press("Enter")
    else:
        send_button.click(timeout=15_000)
    return wait_for_response(
        page,
        previous_count,
        timeout_seconds,
        previous_snapshot=previous_snapshot,
        on_status=on_status,
        prompt=prompt,
    )


@contextmanager
def chatgpt_session(
    *,
    profile_dir: Path | None = None,
    fallback_profile_dir: Path | None = None,
    headless: bool | None = None,
    channel: str | None = None,
    chatgpt_url: str = CHATGPT_URL,
    wait_for_composer: bool = True,
    on_status: Callable[[str, str], None] | None = None,
) -> Generator[Page, None, None]:
    profile_dir = profile_dir or Path(
        os.environ.get("CUTROOM_CHATGPT_PROFILE_DIR", DATA / "chatgpt_profile")
    ).expanduser()
    fallback_profile_dir = fallback_profile_dir or Path(
        os.environ.get(
            "CUTROOM_CHATGPT_FALLBACK_PROFILE_DIR",
            DATA / "chatgpt_fallback_profile",
        )
    ).expanduser()
    if headless is None:
        headless_value = os.environ.get(
            "CUTROOM_CHATGPT_HEADLESS", "false" if sys.platform == "win32" else "true"
        )
        headless = headless_value.strip().lower() in {"1", "true", "yes"}
    if channel is None:
        channel = os.environ.get("CUTROOM_CHATGPT_BROWSER_CHANNEL", "chrome").strip() or None

    with sync_playwright() as playwright:
        if on_status:
            on_status(
                "Starting ChatGPT browser",
                f"Launching a {'headless' if headless else 'visible'} persistent browser profile.",
            )
        context = _launch_context(
            playwright,
            profile_dir=profile_dir,
            fallback_profile_dir=fallback_profile_dir,
            headless=headless,
            channel=channel,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if not headless:
                page.bring_to_front()
            if on_status:
                on_status(
                    "Opening ChatGPT",
                    f"Browser launched (headless={headless}); navigating to ChatGPT.",
                )
            page.goto(chatgpt_url, wait_until="domcontentloaded", timeout=60_000)
            if on_status:
                on_status(
                    "Waiting for ChatGPT input",
                    f"ChatGPT page loaded at {page.url}; waiting for its message composer.",
                )
            if wait_for_composer:
                timeout_ms=int(
                    os.environ.get(
                        "CUTROOM_CHATGPT_COMPOSER_TIMEOUT_SECONDS",
                        "90" if headless else "600",
                    )
                ) * 1000
                wait_for_chat_input(
                    page,
                    timeout_ms=timeout_ms,
                    on_status=on_status,
                    headless=headless,
                )
            yield page
        finally:
            context.close()


def run_smoke_test(
    *,
    headless: bool = False,
    wait_for_composer: bool = False,
    keep_open: bool = False,
) -> str:
    with chatgpt_session(
        headless=headless,
        wait_for_composer=wait_for_composer,
        on_status=lambda stage, detail: print(f"{stage}: {detail}", flush=True),
    ) as page:
        print(f"Browser launched and navigated to: {page.url}", flush=True)
        title = page.title()
        print(f"Page title: {title}", flush=True)
        if wait_for_composer:
            print("ChatGPT composer is available.", flush=True)
        if keep_open:
            input("Browser smoke test passed. Press Enter to close the browser...")
        return page.url


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the persistent ChatGPT browser.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Launch the browser and navigate to ChatGPT without submitting a prompt.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser window.",
    )
    parser.add_argument(
        "--wait-for-composer",
        action="store_true",
        help="Also wait for a signed-in ChatGPT composer to become available.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep the browser visible until you press Enter.",
    )
    args = parser.parse_args()
    if not args.smoke_test:
        parser.error("Use --smoke-test to launch and verify the browser.")
    run_smoke_test(
        headless=args.headless,
        wait_for_composer=args.wait_for_composer,
        keep_open=args.keep_open,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
