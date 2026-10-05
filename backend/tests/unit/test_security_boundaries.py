import asyncio
import io
import json
import logging
import sys
import zipfile

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from routeops.api.logging import JsonFormatter
from routeops.api.security import LocalRequestGuard, browser_write_origins
from routeops.application.import_upload import UploadError, receive_package
from routeops.application.import_validation import (
    ImportLimits,
    _CheckedXmlStream,
    _xml_root,
)
from routeops.infrastructure.logging import configure_logging
from routeops.infrastructure.storage import LocalObjectStorage


def guarded():
    writes = []

    async def create(request):
        writes.append(await request.body())
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/write", create, methods=["POST"])],
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=["testserver", "backend"]),
            Middleware(LocalRequestGuard, origins=("http://127.0.0.1:5173",), max_json_bytes=12),
        ],
    )
    return TestClient(app), writes


def test_historical_localhost_cors_still_accepts_the_documented_same_origin_proxy():
    origins = browser_write_origins(("http://localhost:5173",))
    assert origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert "http://127.0.0.1:9999" not in origins


@pytest.mark.parametrize("origin", ["https://untrusted.example", "null", "http://127.0.0.1:9999"])
def test_untrusted_simple_write_cannot_reach_endpoint(origin):
    client, writes = guarded()
    with client:
        response = client.post("/write", content=b"x", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert writes == []


def test_rebinding_host_rejected_and_trusted_origin_or_cli_work():
    client, writes = guarded()
    with client:
        assert client.post("/write", headers={"Host": "untrusted.example"}).status_code == 400
        assert client.post("/write", content=b"x").status_code == 200
        assert (
            client.post(
                "/write", content=b"x", headers={"Origin": "http://127.0.0.1:5173"}
            ).status_code
            == 200
        )
        assert client.post("/write", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert writes == [b"x", b"x"]


def test_json_declared_and_streamed_body_bound_before_side_effect():
    client, writes = guarded()
    with client:
        assert client.post("/write", content=b"x" * 12).status_code == 200
        assert client.post("/write", content=b"x" * 13).status_code == 413
        assert client.post("/write", content=iter([b"x" * 7, b"y" * 7])).status_code == 413
        assert client.post("/write", headers={"Content-Length": "9" * 5000}).status_code == 400
    assert writes == [b"x" * 12]


def test_multipart_oversized_headers_abort_even_after_first_file(tmp_path):
    storage = LocalObjectStorage(tmp_path)
    body = (
        b'--abc\r\nContent-Disposition: form-data; name="files"; filename="orders.csv"\r\n\r\nx\r\n'
        b'--abc\r\nContent-Disposition: form-data; name="files"; filename="inventory.csv"\r\n'
        b"X-Long: " + b"z" * 9000 + b"\r\n\r\nx\r\n--abc--\r\n"
    )
    chunks = [body[i : i + 100] for i in range(0, len(body), 100)]

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=abc")]},
        receive,
    )
    with pytest.raises(UploadError, match="MULTIPART_HEADER_LIMIT"):
        asyncio.run(receive_package(request, storage, ImportLimits()))
    assert list(storage.object_keys()) == list(storage.temporary_keys()) == []


def test_multipart_declared_limit_rejected_without_read(tmp_path):
    async def receive():
        pytest.fail("must reject before receiving the body")

    request = Request(
        {
            "type": "http",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=abc"),
                (b"content-length", b"104923137"),
            ],
        },
        receive,
    )
    with pytest.raises(UploadError, match="PACKAGE_LIMIT"):
        asyncio.run(receive_package(request, LocalObjectStorage(tmp_path), ImportLimits()))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_xml_entities_rejected_in_metadata_and_incremental_stream(encoding):
    xml = '<!DOCTYPE data [<!ENTITY secret "private">]><data>&secret;</data>'.encode(encoding)
    checked = _CheckedXmlStream(io.BytesIO(xml))
    with pytest.raises(ValueError, match="XLSX_INVALID"):
        while checked.read(3):
            pass
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as output:
        output.writestr("metadata.xml", xml)
    with (
        zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive,
        pytest.raises(ValueError, match="XLSX_INVALID"),
    ):
        _xml_root(archive, "metadata.xml", 10000)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("bom", [True, False])
@pytest.mark.parametrize("invalid", ["\ud800A", "\udc00", "\ud800"])
def test_malformed_utf16_is_rejected_before_xml_parser(encoding, bom, invalid):
    declaration = '<?xml version="1.0" encoding="UTF-16"?>'
    prefix = (b"\xff\xfe" if encoding.endswith("le") else b"\xfe\xff") if bom else b""
    xml = prefix + (declaration + "<data>" + invalid + "</data>").encode(
        encoding, errors="surrogatepass"
    )
    checked = _CheckedXmlStream(io.BytesIO(xml))
    with pytest.raises(ValueError, match="XLSX_INVALID"):
        while checked.read(3):
            pass
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as output:
        output.writestr("metadata.xml", xml)
    with (
        zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive,
        pytest.raises(ValueError, match="XLSX_INVALID"),
    ):
        _xml_root(archive, "metadata.xml", 10000)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-be", "utf-16-le"])
def test_valid_xml_unicode_and_split_surrogate_pairs_preserved(encoding):
    declaration = f'<?xml version="1.0" encoding="{encoding}"?>'
    xml = (declaration + "<data>0007 &amp; café 😀</data>").encode(encoding)
    checked = _CheckedXmlStream(io.BytesIO(xml))
    chunks = []
    while chunk := checked.read(3):
        chunks.append(chunk)
    assert b"".join(chunks) == xml


@pytest.mark.parametrize("suffix", [b"\x00", b"\x00\xd8"])
def test_truncated_utf16_stream_rejected_at_eof(suffix):
    checked = _CheckedXmlStream(io.BytesIO("<data/>".encode("utf-16") + suffix))
    checked.read()
    with pytest.raises(ValueError, match="XLSX_INVALID"):
        checked.read()


def test_logs_preserve_exception_type_and_frames_without_values_or_local_paths():
    try:
        raise ValueError("password=private-token SELECT customer-address")
    except ValueError:
        record = logging.LogRecord(
            "routeops", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    result = JsonFormatter().format(record)
    assert json.loads(result)["exception_type"] == "ValueError"
    assert json.loads(result)["frames"]
    assert "private-token" not in result and "customer-address" not in result
    assert __file__ not in result


def test_shared_logging_does_not_leave_access_or_exception_handler_bypasses(monkeypatch):
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        for attribute in ("handlers", "level", "propagate", "disabled"):
            monkeypatch.setattr(logger, attribute, getattr(logger, attribute))
    logging.getLogger("uvicorn.error").handlers = [logging.StreamHandler()]
    configure_logging("INFO")
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
    assert logging.getLogger("uvicorn.error").handlers == []
    assert logging.getLogger("uvicorn.error").propagate
    assert logging.getLogger("uvicorn.access").disabled
    assert logging.getLogger("httpx").level == logging.WARNING
