"""Share the installation log for bug reports through a paste service."""

from __future__ import annotations

import urllib.error
import urllib.request

PASTE_URL = "https://paste.rs/"
# paste.rs truncates large uploads; keep the end, where failures are.
MAX_UPLOAD_BYTES = 512 * 1024
TRUNCATION_NOTE = "[protogenos] log truncated to its last {size} KiB\n"


class LogShareError(RuntimeError):
    """Raised when the log could not be uploaded."""


def log_tail(lines: list[str], max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    data = ("\n".join(lines) + "\n").encode()
    if len(data) <= max_bytes:
        return data
    tail = data[-max_bytes:]
    # Start on a whole line.
    tail = tail[tail.find(b"\n") + 1 :]
    return TRUNCATION_NOTE.format(size=max_bytes // 1024).encode() + tail


def upload_log(data: bytes, *, url: str = PASTE_URL, timeout: float = 20.0) -> str:
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "text/plain; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            link = response.read().decode().strip()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LogShareError(f"could not upload the log: {error}") from error
    if not link.startswith("https://"):
        raise LogShareError("the paste service returned an unexpected response")
    return link
