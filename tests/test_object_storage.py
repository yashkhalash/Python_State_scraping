"""
Tests for src/object_storage.py. download_document's HTTP call is mocked -
no real network access.
"""
import hashlib
from unittest.mock import MagicMock, patch

import src.object_storage as object_storage


def test_save_text_is_content_addressed_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_DIR", str(tmp_path))

    uri1, sha1 = object_storage.save_text("hello world", prefix="raw_captures")
    uri2, sha2 = object_storage.save_text("hello world", prefix="raw_captures")

    assert uri1 == uri2
    assert sha1 == sha2
    assert sha1 == hashlib.sha256(b"hello world").hexdigest()
    assert uri1.startswith("file://")


def test_save_text_different_content_different_uri(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_DIR", str(tmp_path))

    uri1, _ = object_storage.save_text("content A", prefix="raw_captures")
    uri2, _ = object_storage.save_text("content B", prefix="raw_captures")
    assert uri1 != uri2


def test_download_document_success(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_DIR", str(tmp_path))

    fake_response = MagicMock()
    fake_response.content = b"%PDF-1.4 fake pdf bytes"
    fake_response.headers = {"content-type": "application/pdf"}
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.get.return_value = fake_response
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False

    with patch.object(object_storage.httpx, "Client", return_value=fake_client):
        result = object_storage.download_document("https://example.com/spec.pdf")

    assert result["download_status"] == "downloaded"
    assert result["storage_uri"].startswith("file://")
    assert result["sha256"] == hashlib.sha256(b"%PDF-1.4 fake pdf bytes").hexdigest()
    assert result["byte_size"] == len(b"%PDF-1.4 fake pdf bytes")
    assert result["mime_type"] == "application/pdf"


def test_download_document_failure_returns_dict_not_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_DIR", str(tmp_path))

    fake_client = MagicMock()
    fake_client.get.side_effect = RuntimeError("connection refused")
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False

    with patch.object(object_storage.httpx, "Client", return_value=fake_client):
        result = object_storage.download_document("https://example.com/spec.pdf")

    assert result["download_status"] == "failed"
    assert "connection refused" in result["error"]


def test_guess_extension_from_url_path():
    assert object_storage._guess_extension("https://x.com/spec.pdf", None) == ".pdf"
    assert object_storage._guess_extension("https://x.com/spec.pdf?x=1", None) == ".pdf"


def test_guess_extension_falls_back_to_content_type():
    assert object_storage._guess_extension("https://x.com/download", "application/pdf") == ".pdf"
    assert object_storage._guess_extension("https://x.com/download", "text/html") == ".html"
    assert object_storage._guess_extension("https://x.com/download", None) == ".bin"
