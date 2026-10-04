"""Process-wide pacing and retry policy for requests to the arxiv.org family
of domains.

All arxiv-host HTTP -- the `rss.arxiv.org` announcement feed, `arxiv.org/list`
gap fill, `export.arxiv.org` abstract pages, and `export.arxiv.org` PDFs --
shares one crawl-delay clock here rather than each call site keeping an
independent timer that could still race the others inside the 15s window.
The RSS feed, `/list`, and PDF downloads also retry on a transient HTTP
status per RETRY_DELAYS_SECONDS; the abstract-page fetch makes a single
attempt, since it is itself a fallback reached only after the primary
metadata query has already hit a transient error.

Transport: every one of those requests goes through `fetch_arxiv_url`, which
shells out to `curl` rather than using Python's `urllib`. From about 17-18 Sep
2026 arXiv's CDN answers stdlib `urllib` with HTTP 406 (empty body, no
`Server` header) for any request that misses its caches. The refusal is keyed
on the TLS client fingerprint rather than on headers: `curl` sending
byte-identical headers from the same machine gets 200. Cached responses still
succeed for any client, which made the failures look intermittent. Because
curl's behaviour here is not a passing CDN window, a 406 is not retried (see
`is_transient_http_status`). If curl is not installed, `fetch_arxiv_url` falls
back to `urllib` and warns that uncached fetches will likely fail with 406.
"""

from __future__ import annotations

from collections.abc import Mapping
import email.message
import gzip
import http.client
import io
from http import HTTPStatus
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zlib

LOGGER = logging.getLogger(__name__)

CRAWL_DELAY_SECONDS = 15
RETRY_DELAYS_SECONDS = (15, 30, 90)

# arxiv.org/robots.txt asks operators to contact arXiv in advance if an
# application needs relaxed limits; a bare version string gives their abuse
# tooling nothing to go on if it ever needs to tell this client apart from an
# anonymous bot, so it carries a contact address.
USER_AGENT = "re-ass/1.0 (+mailto:dcroton@swin.edu.au)"

# Accept/Accept-Language/Accept-Encoding for text responses; decode_response_text()
# un-gzips or inflates the compressed bodies this invites.
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def is_transient_http_status(status_code: int) -> bool:
    """Whether an HTTP status is worth retrying on RETRY_DELAYS_SECONDS.

    406 is deliberately excluded: with the curl transport a 406 from arXiv is
    not a passing CDN window that waiting out would clear, so retrying it only
    burns the whole retry schedule (about 135 s) before failing anyway.
    """
    return status_code == 429 or status_code >= 500


# curl exit codes the callers distinguish (see `man curl`, EXIT CODES).
CURL_EXIT_PARTIAL_FILE = 18
CURL_EXIT_MAXFILESIZE_EXCEEDED = 63

# Hard cap on a whole transfer, as a multiple of the caller's idle timeout; curl
# gets _CURL_TIMEOUT_GRACE_SECONDS beyond it before subprocess kills it.
_CURL_MAX_TIME_FACTOR = 10
_CURL_TIMEOUT_GRACE_SECONDS = 10


class ArxivTransferError(URLError):
    """A transfer failed below the HTTP-status level (connect, TLS, timeout,
    short read, size cap, or curl itself timing out).

    `curl_exit_code` is curl's exit status, or None when the subprocess was
    killed for overrunning its timeout. It is a URLError so every existing
    `except URLError` / `_ARXIV_SOURCE_ERRORS` handler already covers it.
    """

    def __init__(self, message: str, curl_exit_code: int | None = None) -> None:
        super().__init__(message)
        self.curl_exit_code = curl_exit_code


class ArxivResponse:
    """A completed arxiv-host response, shaped for `decode_response_text`.

    `headers` is a Message so `.get()` is case-insensitive (curl reports
    lower-case names over HTTP/2).
    """

    def __init__(self, status: int, headers: email.message.Message, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def read(self) -> bytes:
        return self.body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)


def _parse_last_header_block(dump: bytes) -> tuple[str, email.message.Message]:
    """Reason phrase and headers of the final response in a curl header dump.

    With --location (and proxies or 100-continue) the dump holds one block per
    response, separated by blank lines; the last one is the response whose
    body curl saved. curl also writes HTTP trailer fields (chunked responses)
    to the dump as a further block after the headers; it has no status line,
    so only blocks starting with `HTTP/` count and trailers are ignored rather
    than mistaken for the final response's headers.
    """
    text = dump.replace(b"\r\n", b"\n")
    blocks = [
        block.strip()
        for block in text.split(b"\n\n")
        if block.strip().startswith(b"HTTP/")
    ]
    if not blocks:
        raise ArxivTransferError("curl produced no response headers.")
    status_line, _, header_lines = blocks[-1].partition(b"\n")
    parts = status_line.decode("iso-8859-1").split(None, 2)
    reason = parts[2] if len(parts) > 2 else ""
    return reason, http.client.parse_headers(io.BytesIO(header_lines + b"\n\n"))


def _raise_for_status(url: str, status: int, reason: str, headers, body: bytes) -> None:
    if not 200 <= status < 300:
        raise HTTPError(url, status, reason or _status_phrase(status), headers, io.BytesIO(body))


def _status_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def _read_body(body_path: Path, max_bytes: int | None) -> bytes:
    """The saved body, refusing an oversize one before reading it into memory.

    Older curl (< 8.4) downloads a streaming body in full despite
    --max-filesize, so the cap is re-checked here against the file's size.
    """
    if not body_path.exists():
        return b""
    if max_bytes is not None and body_path.stat().st_size > max_bytes:
        raise ArxivTransferError(
            f"response exceeds the {max_bytes}-byte limit", CURL_EXIT_MAXFILESIZE_EXCEEDED
        )
    return body_path.read_bytes()


def _raise_for_error_status_despite_exit(
    url: str,
    completed: subprocess.CompletedProcess,
    header_path: Path,
    body_path: Path,
    max_bytes: int | None,
) -> None:
    """Raise HTTPError when a failed curl run still reported a non-2xx status.

    An error response whose body was cut short makes curl exit non-zero (e.g.
    18) after `--write-out` has already printed the status. Surfacing it as
    HTTPError keeps the callers' retry loops, which retry only HTTPError with
    a transient status, working. A 2xx, 000 (no response) or unparsable
    status returns without raising so the caller reports the transfer failure
    (a truncated 200 PDF must stay exit 18). Headers and body are whatever
    curl managed to save, possibly none.
    """
    try:
        status = int(completed.stdout.decode("ascii").strip())
    except ValueError:
        return
    if status == 0 or 200 <= status < 300:
        return
    reason = ""
    response_headers = email.message.Message()
    try:
        reason, response_headers = _parse_last_header_block(header_path.read_bytes())
    except (OSError, ArxivTransferError):
        pass
    try:
        body = _read_body(body_path, max_bytes)
    except (OSError, ArxivTransferError):
        body = b""
    _raise_for_status(url, status, reason, response_headers, body)


_warned_curl_missing = False


def _fetch_with_urllib(
    url: str, headers: Mapping[str, str], timeout: float, max_bytes: int | None
) -> ArxivResponse:
    """urllib fallback for hosts without curl; same result and error shapes."""
    global _warned_curl_missing
    if not _warned_curl_missing:
        _warned_curl_missing = True
        LOGGER.warning(
            "curl was not found on PATH. arXiv's CDN refuses Python's urllib with HTTP 406 on "
            "requests it has not cached, so arXiv fetches will likely fail until curl is installed."
        )
    try:
        with urlopen(Request(url, headers=dict(headers)), timeout=timeout) as response:
            body = response.read() if max_bytes is None else response.read(max_bytes + 1)
            status, response_headers = response.status, response.headers
    except http.client.IncompleteRead as error:
        raise ArxivTransferError(
            f"transfer closed with {len(error.partial)} bytes read", CURL_EXIT_PARTIAL_FILE
        ) from error
    if max_bytes is not None and len(body) > max_bytes:
        raise ArxivTransferError(
            f"response exceeds the {max_bytes}-byte limit", CURL_EXIT_MAXFILESIZE_EXCEEDED
        )
    _raise_for_status(url, status, "", response_headers, body)
    return ArxivResponse(status, response_headers, body)


def fetch_arxiv_url(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int | None = None,
) -> ArxivResponse:
    """GET `url` with curl (see the module docstring for why not urllib).

    `headers` are sent verbatim, one -H each. curl is not given --compressed:
    decode_response_text() already un-gzips what an Accept-Encoding header in
    `headers` invites. `timeout` keeps urllib's meaning, an idle timeout: the
    connect must finish within it and the transfer fails once it has moved
    under 1 byte/s for that long, so a large PDF on a slow link is not cut off
    merely for taking a while. A hard cap of _CURL_MAX_TIME_FACTOR x `timeout`
    still bounds a transfer that trickles forever. `max_bytes`, when given, is
    curl's --max-filesize (exit 63 when exceeded), but curl enforces it
    mid-stream only from 8.4; older curl aborts only when the server states a
    length up front. So the saved body's size is checked against `max_bytes`
    before it is read into memory and an oversize body raises the same exit-63
    ArxivTransferError (the whole body may still have been downloaded to disk).
    Callers' own size checks remain as a backstop.

    Raises HTTPError for a non-2xx final status (with that response's headers
    and body, so describe_http_error and the retry loops work unchanged),
    including when curl exited non-zero after reporting such a status (an
    error body cut short, e.g. exit 18 on a 503), so a transient status is
    still retried; and ArxivTransferError for any other curl failure or
    timeout.
    """
    if shutil.which("curl") is None:
        return _fetch_with_urllib(url, headers, timeout, max_bytes)

    max_time = timeout * _CURL_MAX_TIME_FACTOR
    with tempfile.TemporaryDirectory(prefix="re-ass-curl-") as tmp:
        header_path = Path(tmp) / "headers"
        body_path = Path(tmp) / "body"
        # --disable must come first so a user's ~/.curlrc cannot alter the request.
        command = [
            "curl", "--disable", "--silent", "--show-error", "--location",
            "--connect-timeout", str(timeout),
            "--speed-limit", "1",
            "--speed-time", str(timeout),
            "--max-time", str(max_time),
            "--dump-header", str(header_path),
            "--output", str(body_path),
            "--write-out", "%{http_code}",
        ]
        if max_bytes is not None:
            command += ["--max-filesize", str(max_bytes)]
        for name, value in headers.items():
            command += ["-H", f"{name}: {value}"]
        command.append(url)

        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=max_time + _CURL_TIMEOUT_GRACE_SECONDS, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise ArxivTransferError(f"curl timed out fetching {url} after {max_time}s") from error
        if completed.returncode != 0:
            _raise_for_error_status_despite_exit(url, completed, header_path, body_path, max_bytes)
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ArxivTransferError(
                f"curl exited {completed.returncode} fetching {url}: {stderr}", completed.returncode
            )
        try:
            status = int(completed.stdout.decode("ascii").strip())
        except ValueError as error:
            raise ArxivTransferError(
                f"curl reported no HTTP status fetching {url}: {completed.stdout!r}", completed.returncode
            ) from error
        reason, response_headers = _parse_last_header_block(header_path.read_bytes())
        body = _read_body(body_path, max_bytes)

    _raise_for_status(url, status, reason, response_headers, body)
    return ArxivResponse(status, response_headers, body)


def _decompress(body: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or "").lower()
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def decode_response_text(response) -> str:
    """Read and decode an HTML or XML text body, un-gzipping or inflating it
    when Content-Encoding says so."""
    body = _decompress(response.read(), response.headers.get("Content-Encoding"))
    return body.decode("utf-8")


_ERROR_BODY_SNIPPET_BYTES = 2048


def describe_http_error(exc: HTTPError) -> str:
    """Best-effort summary of an HTTPError's Retry-After header and body
    snippet, for diagnosing a transient status."""
    parts = []
    headers = getattr(exc, "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after:
        parts.append(f"Retry-After={retry_after}")
    try:
        # Bounded read: an error page is normally tiny, but nothing in HTTP
        # guarantees that, and this must never be the thing that turns a
        # transient-status retry into a slow, memory-heavy detour.
        raw = exc.read(_ERROR_BODY_SNIPPET_BYTES)
        content_encoding = (
            headers.get("Content-Encoding") if headers is not None else None
        )
        if content_encoding:
            raw = _decompress(raw, content_encoding)
    except Exception:
        raw = b""
    if raw:
        snippet = raw.decode("utf-8", errors="replace").strip()
        if snippet:
            parts.append(f"body={snippet!r}")
    return "; ".join(parts) if parts else "(no additional detail in response)"


class ArxivRateLimiter:
    """Enforces a minimum gap between requests to the arxiv.org family of hosts."""

    def __init__(self) -> None:
        self._last_request_at: float | None = None

    def wait_for_crawl_delay(self) -> None:
        if self._last_request_at is None:
            return
        remaining = CRAWL_DELAY_SECONDS - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def mark_request_completed(self) -> None:
        self._last_request_at = time.monotonic()


_shared_limiter = ArxivRateLimiter()


def get_shared_limiter() -> ArxivRateLimiter:
    """Return the process-wide limiter shared by all arxiv.org call sites."""
    return _shared_limiter
