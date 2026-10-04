import gzip
import io
import subprocess
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import re_ass.arxiv_rate_limit as rate_limit
from re_ass.arxiv_rate_limit import (
    ArxivTransferError,
    decode_response_text,
    describe_http_error,
    fetch_arxiv_url,
    is_transient_http_status,
)


def _fake_response(body: bytes, *, content_encoding: str | None = None):
    return SimpleNamespace(
        read=lambda: body,
        headers=SimpleNamespace(
            get=lambda key, default=None: (
                content_encoding if key == "Content-Encoding" else default
            )
        ),
    )


def test_decode_response_text_passes_through_identity() -> None:
    response = _fake_response("<html>hello</html>".encode("utf-8"))

    assert decode_response_text(response) == "<html>hello</html>"


def test_decode_response_text_ungzips_when_content_encoding_is_gzip() -> None:
    html = "<html>gzipped</html>"
    response = _fake_response(
        gzip.compress(html.encode("utf-8")), content_encoding="gzip"
    )

    assert decode_response_text(response) == html


def test_decode_response_text_inflates_zlib_wrapped_deflate() -> None:
    html = "<html>deflated</html>"
    response = _fake_response(
        zlib.compress(html.encode("utf-8")), content_encoding="deflate"
    )

    assert decode_response_text(response) == html


def test_decode_response_text_inflates_raw_deflate() -> None:
    html = "<html>raw deflated</html>"
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw = compressor.compress(html.encode("utf-8")) + compressor.flush()
    response = _fake_response(raw, content_encoding="deflate")

    assert decode_response_text(response) == html


def test_describe_http_error_reports_retry_after_and_body() -> None:
    exc = HTTPError(
        "https://arxiv.org/list/cs.AI/pastweek",
        406,
        "Not Acceptable",
        {"Retry-After": "120"},
        io.BytesIO(b"Automated requests are not permitted."),
    )

    detail = describe_http_error(exc)

    assert "Retry-After=120" in detail
    assert "Automated requests are not permitted." in detail


def test_describe_http_error_tolerates_missing_headers_and_body() -> None:
    exc = HTTPError(
        "https://arxiv.org/list/cs.AI/pastweek", 406, "Not Acceptable", None, None
    )

    detail = describe_http_error(exc)

    assert detail == "(no additional detail in response)"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, True), (500, True), (503, True), (406, False), (404, False), (403, False), (200, False)],
)
def test_is_transient_http_status(status: int, expected: bool) -> None:
    assert is_transient_http_status(status) is expected


class _FakeCurl:
    """Stands in for subprocess.run, writing the header/body files real curl would.

    Records the command so tests can assert on the flags and -H headers sent.
    """

    def __init__(
        self,
        *,
        header_dump: str = "",
        body: bytes = b"",
        stdout: str = "200",
        returncode: int = 0,
        stderr: bytes = b"",
        raises: Exception | None = None,
        write_files: bool | None = None,
    ) -> None:
        self.header_dump = header_dump
        self.body = body
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.raises = raises
        # Real curl leaves partial files behind on some failures; by default
        # they are written only on success.
        self.write_files = returncode == 0 if write_files is None else write_files
        self.commands: list[list[str]] = []
        self.kwargs: dict = {}

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.kwargs = kwargs
        if self.raises is not None:
            raise self.raises
        if self.write_files:
            Path(command[command.index("--dump-header") + 1]).write_text(self.header_dump)
            Path(command[command.index("--output") + 1]).write_bytes(self.body)
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=self.stdout.encode(), stderr=self.stderr
        )


def _use_fake_curl(monkeypatch: pytest.MonkeyPatch, fake: _FakeCurl) -> _FakeCurl:
    monkeypatch.setattr(rate_limit.shutil, "which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr(rate_limit.subprocess, "run", fake)
    return fake


def test_fetch_arxiv_url_parses_status_headers_and_body(monkeypatch) -> None:
    fake = _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump="HTTP/2 200 \r\ncontent-type: text/html\r\nage: 42\r\n\r\n",
            body=b"<html>ok</html>",
        ),
    )

    response = fetch_arxiv_url(
        "https://export.arxiv.org/abs/2609.00001",
        headers={"User-Agent": "re-ass/1.0", "Accept-Encoding": "gzip, deflate"},
        timeout=60,
    )

    assert response.status == 200
    assert response.read() == b"<html>ok</html>"
    assert decode_response_text(response) == "<html>ok</html>"
    # Header lookup is case-insensitive whatever case curl reported.
    assert response.getheader("Age") == "42"
    assert response.headers.get("Content-Type") == "text/html"
    assert response.getheader("X-Missing") is None
    command = fake.commands[0]
    assert command[0] == "curl"
    # 60 s is an idle timeout (connect, then under 1 byte/s), with a 10x hard cap.
    assert command[command.index("--connect-timeout") + 1] == "60"
    assert command[command.index("--speed-limit") + 1] == "1"
    assert command[command.index("--speed-time") + 1] == "60"
    assert command[command.index("--max-time") + 1] == "600"
    assert "User-Agent: re-ass/1.0" in command
    assert "Accept-Encoding: gzip, deflate" in command
    assert "--compressed" not in command
    assert "--max-filesize" not in command
    assert command[-1] == "https://export.arxiv.org/abs/2609.00001"
    assert fake.kwargs["timeout"] == 610


def test_fetch_arxiv_url_uses_the_last_header_block_after_a_redirect(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump=(
                "HTTP/1.1 301 Moved Permanently\r\nLocation: https://arxiv.org/x\r\nServer: first\r\n\r\n"
                "HTTP/2 200 \r\nServer: second\r\nContent-Length: 4\r\n\r\n"
            ),
            body=b"body",
        ),
    )

    response = fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    assert response.status == 200
    assert response.getheader("Server") == "second"
    assert response.getheader("Location") is None


def test_fetch_arxiv_url_ignores_a_trailer_block_after_the_headers(monkeypatch) -> None:
    html = "<rss>trailer</rss>"
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump=(
                "HTTP/2 200 \r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n\r\n"
                "Digest: sha-256=abc\r\n\r\n"
            ),
            body=gzip.compress(html.encode("utf-8")),
        ),
    )

    response = fetch_arxiv_url("https://rss.arxiv.org/rss/cs.AI", headers={}, timeout=5)

    assert response.getheader("Content-Encoding") == "gzip"
    assert response.getheader("Digest") is None
    assert decode_response_text(response) == html


def test_fetch_arxiv_url_picks_the_final_response_over_redirect_and_trailer(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump=(
                "HTTP/1.1 301 Moved Permanently\r\nLocation: https://arxiv.org/x\r\nServer: first\r\n\r\n"
                "HTTP/2 200 \r\nServer: second\r\n\r\n"
                "Digest: sha-256=abc\r\n\r\n"
            ),
            body=b"body",
        ),
    )

    response = fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    assert response.status == 200
    assert response.getheader("Server") == "second"
    assert response.getheader("Location") is None
    assert response.getheader("Digest") is None


def test_fetch_arxiv_url_passes_max_bytes_as_max_filesize(monkeypatch) -> None:
    fake = _use_fake_curl(monkeypatch, _FakeCurl(header_dump="HTTP/2 200 \r\n\r\n", body=b"x"))

    fetch_arxiv_url("https://export.arxiv.org/pdf/1", headers={}, timeout=5, max_bytes=1234)

    command = fake.commands[0]
    assert command[command.index("--max-filesize") + 1] == "1234"


def test_fetch_arxiv_url_raises_http_error_readable_by_describe_http_error(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump="HTTP/1.1 429 Too Many Requests\r\nRetry-After: 120\r\n\r\n",
            body=b"slow down",
            stdout="429",
        ),
    )

    with pytest.raises(HTTPError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/list/cs.AI/pastweek", headers={}, timeout=5)

    error = excinfo.value
    assert error.code == 429
    assert error.reason == "Too Many Requests"
    assert error.headers.get("retry-after") == "120"
    assert is_transient_http_status(error.code)
    detail = describe_http_error(error)
    assert "Retry-After=120" in detail
    assert "slow down" in detail


def test_fetch_arxiv_url_raises_http_error_for_an_empty_406(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch, _FakeCurl(header_dump="HTTP/2 406 \r\n\r\n", body=b"", stdout="406")
    )

    with pytest.raises(HTTPError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    assert excinfo.value.code == 406
    assert excinfo.value.reason == "Not Acceptable"
    assert describe_http_error(excinfo.value) == "(no additional detail in response)"


def test_fetch_arxiv_url_raises_transfer_error_on_nonzero_curl_exit(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(returncode=18, stderr=b"curl: (18) transfer closed with 5 bytes remaining\n"),
    )

    with pytest.raises(ArxivTransferError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    assert excinfo.value.curl_exit_code == 18
    assert "transfer closed with 5 bytes remaining" in str(excinfo.value)
    assert "curl exited 18" in str(excinfo.value)


def test_fetch_arxiv_url_raises_http_error_for_error_status_with_interrupted_body(
    monkeypatch,
) -> None:
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump="HTTP/1.1 503 Service Unavailable\r\nRetry-After: 30\r\n\r\n",
            body=b"partial",
            stdout="503",
            returncode=18,
            stderr=b"curl: (18) transfer closed with 5 bytes remaining\n",
            write_files=True,
        ),
    )

    with pytest.raises(HTTPError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    error = excinfo.value
    assert error.code == 503
    assert is_transient_http_status(error.code)
    assert error.headers.get("retry-after") == "30"
    assert "partial" in describe_http_error(error)


def test_fetch_arxiv_url_raises_http_error_for_error_status_without_saved_files(
    monkeypatch,
) -> None:
    _use_fake_curl(monkeypatch, _FakeCurl(stdout="503", returncode=18))

    with pytest.raises(HTTPError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    assert excinfo.value.code == 503
    assert excinfo.value.reason == "Service Unavailable"
    assert describe_http_error(excinfo.value) == "(no additional detail in response)"


@pytest.mark.parametrize("stdout", ["200", "000", ""])
def test_fetch_arxiv_url_keeps_transfer_error_for_nonzero_exit_without_error_status(
    monkeypatch, stdout: str
) -> None:
    _use_fake_curl(
        monkeypatch,
        _FakeCurl(
            header_dump="HTTP/2 200 \r\n\r\n",
            body=b"truncated",
            stdout=stdout,
            returncode=18,
            write_files=True,
        ),
    )

    with pytest.raises(ArxivTransferError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5)

    assert excinfo.value.curl_exit_code == 18


def test_fetch_arxiv_url_rejects_an_oversize_body_curl_did_not_abort(monkeypatch) -> None:
    # curl < 8.4 downloads a streaming body in full despite --max-filesize.
    _use_fake_curl(
        monkeypatch, _FakeCurl(header_dump="HTTP/2 200 \r\n\r\n", body=b"x" * 11)
    )
    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(self):
        if self.name == "body":
            pytest.fail("an oversize body must not be read into memory")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(ArxivTransferError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5, max_bytes=10)

    assert excinfo.value.curl_exit_code == rate_limit.CURL_EXIT_MAXFILESIZE_EXCEEDED


def test_fetch_arxiv_url_accepts_a_body_exactly_at_max_bytes(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch, _FakeCurl(header_dump="HTTP/2 200 \r\n\r\n", body=b"x" * 10)
    )

    response = fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5, max_bytes=10)

    assert response.read() == b"x" * 10


def test_fetch_arxiv_url_raises_transfer_error_on_subprocess_timeout(monkeypatch) -> None:
    _use_fake_curl(
        monkeypatch, _FakeCurl(raises=subprocess.TimeoutExpired(cmd="curl", timeout=70))
    )

    with pytest.raises(ArxivTransferError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=60)

    assert excinfo.value.curl_exit_code is None
    assert "timed out" in str(excinfo.value)


def test_fetch_arxiv_url_falls_back_to_urllib_once_warning_when_curl_is_missing(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(rate_limit.shutil, "which", lambda _name: None)
    monkeypatch.setattr(rate_limit, "_warned_curl_missing", False)
    requests = []

    class _UrllibResponse:
        status = 200
        headers = SimpleNamespace(get=lambda key, default=None: default)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amt=None):
            return b"via urllib"

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _UrllibResponse()

    monkeypatch.setattr(rate_limit, "urlopen", fake_urlopen)

    with caplog.at_level("WARNING"):
        first = fetch_arxiv_url("https://arxiv.org/x", headers={"User-Agent": "ua"}, timeout=7)
        second = fetch_arxiv_url("https://arxiv.org/x", headers={"User-Agent": "ua"}, timeout=7)

    assert first.status == 200 and first.read() == b"via urllib"
    assert second.read() == b"via urllib"
    assert requests[0][0].full_url == "https://arxiv.org/x"
    assert requests[0][1] == 7
    warnings = [r for r in caplog.records if "curl was not found" in r.getMessage()]
    assert len(warnings) == 1
    assert "406" in warnings[0].getMessage()


def test_fetch_arxiv_url_urllib_fallback_enforces_max_bytes(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit.shutil, "which", lambda _name: None)
    monkeypatch.setattr(rate_limit, "_warned_curl_missing", True)

    class _UrllibResponse:
        status = 200
        headers = SimpleNamespace(get=lambda key, default=None: default)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amt=None):
            return b"x" * (amt if amt is not None else 100)

    monkeypatch.setattr(rate_limit, "urlopen", lambda request, timeout: _UrllibResponse())

    with pytest.raises(ArxivTransferError) as excinfo:
        fetch_arxiv_url("https://arxiv.org/x", headers={}, timeout=5, max_bytes=10)

    assert excinfo.value.curl_exit_code == rate_limit.CURL_EXIT_MAXFILESIZE_EXCEEDED
