"""Attachment ingest: local fixtures only, generated in the test; no device or provider."""

import asyncio
import io
import os
import time

import pypdfium2
import pytest
from PIL import Image

from visto.adapters.attachments import (
    MAX_FILE_BYTES,
    MAX_RUN_PAGES,
    AttachmentError,
    AttachmentStaging,
    promote,
    render,
)
from visto.adapters.storage import LocalArtifactStore


def png(size=(40, 80), color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def jpeg(size=(30, 60)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "gray").save(buffer, "JPEG")
    return buffer.getvalue()


def pdf(pages=2, size=(200, 400)):
    document = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        document.new_page(*size)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def test_png_and_jpeg_pass_through():
    (page,) = render("fictional-login.png", png())
    assert (page.media_type, page.width, page.height) == ("image/png", 40, 80)
    assert page.content == png()
    (photo,) = render("fictional-home.jpg", jpeg())
    assert (photo.media_type, photo.width, photo.height) == ("image/jpeg", 30, 60)


def test_pdf_pages_render_to_png_in_order():
    pages = render("fictional-flow.pdf", pdf(pages=3, size=(200, 400)))
    assert len(pages) == 3
    for page in pages:
        assert page.media_type == "image/png" and page.content.startswith(b"\x89PNG")
        # Long side scaled toward 2576 px, but never above 4x the natural size.
        assert (page.width, page.height) == (800, 1600)
    (large,) = render("fictional-poster.pdf", pdf(pages=1, size=(1000, 2000)))
    assert max(large.width, large.height) == 2576


def test_extension_does_not_decide_type():
    for name, content in (
        ("fictional.png", b"plain text pretending"),
        ("fictional.pdf", png()[:20]),
        ("fictional.gif", b"GIF89a" + b"\0" * 20),
    ):
        with pytest.raises(AttachmentError):
            render(name, content)
    # A PNG named .pdf is still a PNG.
    assert render("misnamed.pdf", png())[0].media_type == "image/png"


def test_corrupt_encrypted_or_empty_pdf_is_rejected():
    # A trailer that declares encryption; no encrypting library is a dependency, so
    # this exercises the refusal path, not a real password-protected document.
    encrypted = (
        b"%PDF-1.7\n1 0 obj<</Filter/Standard/V 2/R 3/O(x)/U(y)/P -4>>endobj\n"
        b"trailer<</Encrypt 1 0 R>>\n%%EOF"
    )
    for content in (b"%PDF-1.7\ngarbage", encrypted, pdf(pages=0), b""):
        with pytest.raises(AttachmentError):
            render("fictional.pdf", content)


def test_oversize_file_is_rejected():
    with pytest.raises(AttachmentError, match="20 MB"):
        render("fictional.png", png() + b"\0" * MAX_FILE_BYTES)


def test_page_limit_counts_across_files(tmp_path):
    with pytest.raises(AttachmentError):
        render("fictional-book.pdf", pdf(pages=MAX_RUN_PAGES + 1, size=(20, 20)))
    staging = AttachmentStaging(tmp_path / "staging")
    files = [staging.stage("a.pdf", pdf(pages=30, size=(20, 20)))]
    files.append(staging.stage("b.pdf", pdf(pages=11, size=(20, 20))))
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(AttachmentError, match="40"):
        asyncio.run(promote(files, "run-1", artifacts))


def test_staging_expires_and_sweeps(tmp_path):
    staging = AttachmentStaging(tmp_path / "staging", ttl_s=60)
    staged = staging.stage("fictional-login.png", png())
    assert staging.get(staged.upload_id).name == "fictional-login.png"
    assert staging.sweep(time.time() + 30) == 0
    assert staging.sweep(time.time() + 120) == 1
    with pytest.raises(AttachmentError):
        staging.get(staged.upload_id)
    other = staging.stage("fictional-home.png", png())
    staging.discard(other.upload_id)
    with pytest.raises(AttachmentError):
        staging.get(other.upload_id)


def test_staging_rejects_path_like_ids(tmp_path):
    staging = AttachmentStaging(tmp_path / "staging")
    for upload_id in ("../secrets", "a/b", "", "Z" * 32):
        with pytest.raises(AttachmentError):
            staging.get(upload_id)
        staging.discard(upload_id)  # Never raises, never touches outside the root.
    assert os.listdir(tmp_path) in ([], ["staging"])


def test_promote_stores_owned_artifacts(tmp_path):
    staging = AttachmentStaging(tmp_path / "staging")
    files = [staging.stage("fictional-flow.pdf", pdf(pages=2)), staging.stage("x.png", png())]
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    pages = asyncio.run(promote(files, "run-1", artifacts))
    assert [(p.name, p.page, p.page_count) for p in pages] == [
        ("fictional-flow.pdf", 1, 2),
        ("fictional-flow.pdf", 2, 2),
        ("x.png", 1, 1),
    ]
    assert len({p.attachment_id for p in pages}) == 3
    assert all(p.image.run_id == "run-1" for p in pages)
    assert asyncio.run(artifacts.read(pages[2].image)) == png()
