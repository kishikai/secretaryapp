"""Watch the local clipboard and post newly copied text to the backend.

Run this on your own desktop. It only sends text that you copy, and it never
logs message contents (only length, id and duplicate flag).
"""
import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

log = logging.getLogger("clipboard_collector")

DEFAULT_API_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_SECONDS = 10


def send_message(api_url: str, text: str, chat_id: Optional[str]) -> dict:
    """POST one message and return the parsed JSON response.

    urllib.error.HTTPError / URLError propagate so the caller can classify them.
    """
    body = json.dumps({"message_text": text, "source": "clipboard", "chat_id": chat_id}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/messages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def poll_once(
    read_clipboard: Callable[[], Optional[str]],
    send: Callable[[str], dict],
    last_text: Optional[str],
) -> Optional[str]:
    """Check the clipboard once and return the new last_text."""
    try:
        text = read_clipboard()
    except Exception as exc:
        log.warning("Could not read clipboard (%s)", type(exc).__name__)
        return last_text

    if text is None or not text.strip() or text == last_text:
        return last_text

    try:
        result = send(text)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            log.error("Backend error HTTP %s for %d chars; will retry", exc.code, len(text))
            return last_text
        log.error("Backend rejected %d chars with HTTP %s; not retrying", len(text), exc.code)
        return text
    except (OSError, TimeoutError) as exc:  # URLError and socket timeouts
        log.error("Could not reach backend (%s); will retry", type(exc).__name__)
        return last_text
    except ValueError:
        # Backend answered 2xx but the body was not JSON; the message was accepted.
        log.error("Backend response was not valid JSON for %d chars; not retrying", len(text))
        return text

    log.info("Sent %d chars: id=%s duplicate=%s", len(text), result.get("id"), result.get("duplicate"))
    return text


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}")
    if not number > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def _http_url(value: str) -> str:
    # Without a scheme urllib raises ValueError, which poll_once would treat as "accepted" and drop the message.
    if not value.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("must start with http:// or https://")
    return value


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send copied clipboard text to the Secretary backend.")
    parser.add_argument("--api-url", type=_http_url, default=DEFAULT_API_URL, help="backend base URL (default: %(default)s)")
    parser.add_argument("--chat-id", default=None, help="optional chat id to attach to every message")
    parser.add_argument("--interval", type=_positive_float, default=1.0, help="poll interval in seconds (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import pyperclip  # lazy so tests do not need it installed

    def send(text: str) -> dict:
        return send_message(args.api_url, text, args.chat_id)

    try:
        last_text = pyperclip.paste()
    except Exception as exc:
        log.warning("Could not read clipboard at start-up (%s)", type(exc).__name__)
        last_text = None
    log.info("Ignoring text already on the clipboard at start-up. Watching %s (Ctrl+C to stop)", args.api_url)

    try:
        while True:
            last_text = poll_once(pyperclip.paste, send, last_text)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Stopped")


if __name__ == "__main__":
    main()
