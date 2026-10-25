import re
import time

from playwright.sync_api import Error as PlaywrightError, TimeoutError

from .. import config, job_state

print = job_state.safe_print

ASSISTANT_MESSAGE_SELECTORS = (
    "[data-message-author-role='assistant']",
    "[data-testid^='conversation-turn-'] .markdown",
    ".agent-turn .markdown",
    "main article",
    "main .markdown",
)


def log(message, part_number=None):
    label = f"[PART-{part_number}] " if part_number is not None else ""
    print(f"[ChatGPT] {label}{message}")


def launch_persistent_context(
    playwright,
    profile_dir=None,
    fallback_profile_dir=None,
    headless=None,
    channel="chrome",
):
    profile_dir = profile_dir or config.CHATGPT_PROFILE_DIR
    fallback_profile_dir = fallback_profile_dir or config.CHATGPT_FALLBACK_PROFILE_DIR
    headless = config.PLAYWRIGHT_HEADLESS if headless is None else headless
    log(f"Starting browser profile: {profile_dir}")
    args = ["--disable-blink-features=AutomationControlled"]
    if config.PLAYWRIGHT_CHROME_PROFILE:
        args.append(f"--profile-directory={config.PLAYWRIGHT_CHROME_PROFILE}")
    launch_options = {
        "user_data_dir": profile_dir,
        "headless": headless,
        "args": args,
    }
    if channel:
        launch_options["channel"] = channel
    try:
        return playwright.chromium.launch_persistent_context(**launch_options)
    except PlaywrightError as error:
        message = str(error).lower()
        profile_in_use = any(
            marker in message
            for marker in (
                "existing browser session",
                "user data directory is already in use",
                "processsingleton",
                "profile appears to be in use",
            )
        )
        if not profile_in_use:
            raise
        log(
            "Shared Chrome profile is locked by another browser window. "
            f"Using fallback profile: {fallback_profile_dir}"
        )
        log("Log in to ChatGPT manually in the fallback profile once.")
        launch_options["user_data_dir"] = fallback_profile_dir
        return playwright.chromium.launch_persistent_context(**launch_options)


def find_chat_input(page):
    candidates = [
        lambda: page.locator("#prompt-textarea"),
        lambda: page.locator("[data-testid='prompt-textarea']"),
        lambda: page.locator("[contenteditable='true'][role='textbox']"),
        lambda: page.locator("textarea").first,
        lambda: page.locator("[contenteditable='true']").last,
        lambda: page.get_by_role("textbox", name=re.compile("message|ask anything|prompt", re.IGNORECASE)).last,
        lambda: page.get_by_placeholder(re.compile("message|prompt", re.IGNORECASE)).last,
    ]
    for get_candidate in candidates:
        try:
            candidate = get_candidate()
            if candidate.is_visible():
                return candidate
        except PlaywrightError:
            continue
    raise TimeoutError("ChatGPT message input was not found")


def open_chatgpt(page, part_number=None, chatgpt_url=None):
    chatgpt_url = chatgpt_url or config.CHATGPT_URL
    log(f"Opening ChatGPT: {chatgpt_url}", part_number)
    page.goto(chatgpt_url, wait_until="commit", timeout=60000)


def wait_for_chat_input(page, part_number=None, timeout_ms=120000):
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            chat_input = find_chat_input(page)
            log("ChatGPT message input is ready", part_number)
            return chat_input
        except TimeoutError as error:
            if time.monotonic() >= deadline:
                log(f"ChatGPT composer not available at {page.url}", part_number)
                raise TimeoutError(
                    "ChatGPT message input was not found. Confirm that this browser profile is signed in "
                    "and that ChatGPT has finished loading, then retry."
                ) from error
            page.wait_for_timeout(500)


def start_new_chat(page, part_number=None, chatgpt_url=None, login_timeout_ms=120000):
    open_chatgpt(page, part_number, chatgpt_url)
    try:
        page.get_by_role("button", name=re.compile("new chat", re.IGNORECASE)).first.click(timeout=5000)
    except TimeoutError:
        pass
    wait_for_chat_input(page, part_number, login_timeout_ms)
    log("ChatGPT session detected", part_number)


def assistant_messages(page):
    return page.locator("[data-message-author-role='assistant']")


def extract_latest_response(page, previous_count):
    messages = assistant_messages(page)
    if messages.count() <= previous_count:
        return ""
    return messages.last.inner_text().strip()


def wait_for_response(page, previous_count, timeout_seconds=None):
    timeout_seconds = timeout_seconds or config.CHATGPT_RESPONSE_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    stable_text = ""
    stable_count = 0

    while time.monotonic() < deadline:
        current_text = extract_latest_response(page, previous_count)
        stop_button = page.get_by_role("button", name=re.compile("stop generating|stop", re.IGNORECASE))
        generating = stop_button.count() > 0 and stop_button.last.is_visible()

        if current_text and current_text == stable_text and not generating:
            stable_count += 1
        else:
            stable_count = 0
        stable_text = current_text

        if stable_count >= 2:
            return current_text
        time.sleep(3)

    raise TimeoutError("ChatGPT response timed out")


def _is_response_generating(page):
    candidates = [
        lambda: page.locator("button[data-testid='stop-button']"),
        lambda: page.locator("button[data-testid*='stop' i]"),
        lambda: page.locator("button[aria-label*='Stop' i]"),
        lambda: page.locator("button[title*='Stop' i]"),
        lambda: page.get_by_role("button", name=re.compile("stop generating|stop", re.IGNORECASE)),
    ]
    for get_candidate in candidates:
        try:
            if get_candidate().last.is_visible():
                return True
        except PlaywrightError:
            continue
    return False


def _response_snapshot(page):
    snapshot = {}
    for selector in ASSISTANT_MESSAGE_SELECTORS:
        try:
            messages = page.locator(selector)
            count = messages.count()
            text = messages.last.inner_text().strip() if count else ""
            snapshot[selector] = (count, text)
        except PlaywrightError:
            snapshot[selector] = (0, "")
    return snapshot


def _response_after_prompt(page, prompt):
    if not prompt.strip():
        return ""
    prompt_words = prompt.split()
    prompt_patterns = [
        re.compile(
            r"\s+".join(re.escape(part) for part in prompt_words[-word_count:]),
            re.DOTALL,
        )
        for word_count in dict.fromkeys(
            (len(prompt_words), min(120, len(prompt_words)),
             min(60, len(prompt_words)), min(30, len(prompt_words)),
             min(12, len(prompt_words)))
        )
    ]
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
    for selector in ("main", "body"):
        try:
            text = page.locator(selector).inner_text()
        except PlaywrightError:
            continue
        prompt_match = None
        for prompt_pattern in prompt_patterns:
            matches = list(prompt_pattern.finditer(text))
            if matches:
                prompt_match = matches[-1]
                break
        if prompt_match is None:
            continue
        response = text[prompt_match.end():].strip()
        lines = response.splitlines()
        while lines and lines[-1].strip().lower() in trailing_controls:
            lines.pop()
        response = "\n".join(lines).strip()
        if response:
            return response
    return ""


def _latest_response_for_prompt(page, baseline, prompt):
    for selector in ASSISTANT_MESSAGE_SELECTORS:
        try:
            messages = page.locator(selector)
            count = messages.count()
            current_text = messages.last.inner_text().strip() if count else ""
            baseline_count, baseline_text = baseline.get(selector, (0, ""))
            if current_text and (count > baseline_count or current_text != baseline_text):
                return current_text
        except PlaywrightError:
            continue
    return _response_after_prompt(page, prompt)


def wait_for_prompt_response(page, prompt, baseline, part_number=None):
    deadline = time.monotonic() + config.CHATGPT_RESPONSE_TIMEOUT_SECONDS
    previous_text = ""
    stable_polls = 0
    poll_count = 0

    while time.monotonic() < deadline:
        poll_count += 1
        current_text = _latest_response_for_prompt(page, baseline, prompt)
        generating = _is_response_generating(page)
        if current_text and current_text == previous_text and not generating:
            stable_polls += 1
        else:
            stable_polls = 0
        if current_text and not generating and stable_polls >= 2:
            log(f"ChatGPT response received ({len(current_text)} characters)", part_number)
            return current_text
        if poll_count % 4 == 0:
            status = (
                f"Waiting for response to finish ({len(current_text)} characters detected)."
                if current_text
                else "Waiting for ChatGPT response text."
            )
            log(status, part_number)
        previous_text = current_text
        time.sleep(3)

    raise TimeoutError("ChatGPT response timed out")


def send_prompt_for_translation(page, prompt, part_number=None):
    baseline = _response_snapshot(page)
    chat_input = find_chat_input(page)
    chat_input.fill(prompt)
    send_buttons = [
        page.locator("button[data-testid='send-button']"),
        page.locator("button[aria-label*='Send' i]"),
        page.get_by_role("button", name=re.compile("send", re.IGNORECASE)),
    ]
    send_button = None
    for candidate in send_buttons:
        try:
            if candidate.last.is_visible():
                send_button = candidate.last
                break
        except PlaywrightError:
            continue
    if send_button is not None:
        send_button.click(timeout=15000)
    else:
        chat_input.press("Enter")
    log("Waiting for response", part_number)
    return wait_for_prompt_response(page, prompt, baseline, part_number)


def wait_for_completed_response(
    page,
    previous_count,
    timeout_seconds=None,
    on_wait=None,
    stable_polls=3,
):
    timeout_seconds = timeout_seconds or config.CHATGPT_RESPONSE_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    previous_text = ""
    stable_count = 0
    poll_count = 0
    current_text = ""
    generating = False
    message_count = 0

    while time.monotonic() < deadline:
        poll_count += 1
        if on_wait:
            on_wait()
        messages = assistant_messages(page)
        message_count = messages.count()
        current_text = messages.last.inner_text().strip() if message_count > previous_count else ""
        generating = _is_response_generating(page)

        if current_text and current_text == previous_text and not generating:
            stable_count += 1
        else:
            stable_count = 0
        previous_text = current_text

        if current_text and not generating and stable_count >= stable_polls:
            log(
                f"ChatGPT response received ({len(current_text)} characters)",
                "YouTube-English",
            )
            return current_text
        if poll_count % 15 == 0:
            log(
                f"Still waiting: assistant messages={message_count}, "
                f"latest response characters={len(current_text)}, "
                f"generation indicator={'visible' if generating else 'not visible'}",
                "YouTube-English",
            )
        page.wait_for_timeout(1000)

    raise TimeoutError(
        f"ChatGPT response did not finish within {timeout_seconds} seconds "
        f"(assistant messages={message_count}, "
        f"latest response characters={len(current_text)}, "
        f"generation indicator={'visible' if generating else 'not visible'})."
    )


def send_prompt(
    page,
    prompt,
    previous_count,
    part_number=None,
    timeout_seconds=None,
    *,
    require_completed_response=False,
    on_wait=None,
):
    chat_input = find_chat_input(page)
    chat_input.fill(prompt)
    send_buttons = [
        page.locator("button[data-testid='send-button']"),
        page.locator("button[aria-label*='Send' i]"),
        page.get_by_role("button", name=re.compile("send", re.IGNORECASE)),
    ]
    send_button = None
    for candidate in send_buttons:
        try:
            if candidate.last.is_visible():
                send_button = candidate.last
                break
        except PlaywrightError:
            continue
    if send_button is not None:
        send_button.click(timeout=15000)
    else:
        chat_input.press("Enter")
    log("Waiting for response", part_number)
    if require_completed_response:
        return wait_for_completed_response(
            page,
            previous_count,
            timeout_seconds=timeout_seconds,
            on_wait=on_wait,
        )
    return wait_for_response(page, previous_count, timeout_seconds)
