import gzip
import io
import zlib
from types import SimpleNamespace
from urllib.error import HTTPError

from re_ass.arxiv_rate_limit import decode_html_response, describe_http_error


def _fake_response(body: bytes, *, content_encoding: str | None = None):
    return SimpleNamespace(
        read=lambda: body,
        headers=SimpleNamespace(
            get=lambda key, default=None: (
                content_encoding if key == "Content-Encoding" else default
            )
        ),
    )


def test_decode_html_response_passes_through_identity() -> None:
    response = _fake_response("<html>hello</html>".encode("utf-8"))

    assert decode_html_response(response) == "<html>hello</html>"


def test_decode_html_response_ungzips_when_content_encoding_is_gzip() -> None:
    html = "<html>gzipped</html>"
    response = _fake_response(
        gzip.compress(html.encode("utf-8")), content_encoding="gzip"
    )

    assert decode_html_response(response) == html


def test_decode_html_response_inflates_zlib_wrapped_deflate() -> None:
    html = "<html>deflated</html>"
    response = _fake_response(
        zlib.compress(html.encode("utf-8")), content_encoding="deflate"
    )

    assert decode_html_response(response) == html


def test_decode_html_response_inflates_raw_deflate() -> None:
    html = "<html>raw deflated</html>"
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw = compressor.compress(html.encode("utf-8")) + compressor.flush()
    response = _fake_response(raw, content_encoding="deflate")

    assert decode_html_response(response) == html


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
