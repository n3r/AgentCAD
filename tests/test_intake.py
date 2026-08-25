"""PRD-018 Slice 3 — intake of uploaded reference images and PDFs.

The PDF fixtures are hand-crafted, byte-accurate minimal PDFs (no reportlab
dependency): a 1.4 catalog/pages/font tree with one Helvetica text run per
page, a correct ``xref`` table and ``startxref`` offset — enough for pypdfium2
to both rasterize and extract text, proven in the PRD-018 spike.
"""

from __future__ import annotations

import base64

import pytest

from agentcad.core import intake
from agentcad.core.imports import SUPPORTED_EXTS
from agentcad.core.model import ValidationError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------- fixtures


def _tiny_png_bytes() -> bytes:
    """A real 2x2 RGB PNG via the repo's own dependency-free encoder."""
    import numpy as np

    from agentcad.core.render import encode_png

    arr = np.zeros((2, 2, 3), dtype=np.uint8)
    arr[0, 0] = (255, 0, 0)
    return encode_png(arr)


def _tiny_jpeg_bytes() -> bytes:
    """Bytes carrying a valid JPEG (SOI + APP0/JFIF) header. Intake only
    header-checks images (no decode), so this exercises the passthrough +
    media-type path faithfully without a JPEG encoder."""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 32 + b"\xff\xd9"


def _make_pdf(text: str, npages: int = 1) -> bytes:
    """A minimal, byte-accurate multi-page PDF with one text run per page."""
    body = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def add(num: int, data: bytes) -> None:
        offsets[num] = len(body)
        body.extend(f"{num} 0 obj\n".encode())
        body.extend(data)
        body.extend(b"\nendobj\n")

    obj = 4
    content_ids, page_ids = [], []
    for _ in range(npages):
        content_ids.append(obj); obj += 1
        page_ids.append(obj); obj += 1

    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    add(2, f"<< /Type /Pages /Kids [{kids}] /Count {npages} >>".encode())
    add(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(npages):
        stream = f"BT /F1 20 Tf 40 120 Td ({text} p{i + 1}) Tj ET".encode()
        cobj = (f"<< /Length {len(stream)} >>\nstream\n".encode()
                + stream + b"\nendstream")
        add(content_ids[i], cobj)
        page = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                f"/Contents {content_ids[i]} 0 R "
                f"/Resources << /Font << /F1 3 0 R >> >> >>").encode()
        add(page_ids[i], page)

    xref_pos = len(body)
    n = obj
    body.extend(f"xref\n0 {n}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for num in range(1, n):
        body.extend(f"{offsets[num]:010d} 00000 n \n".encode())
    body.extend(
        f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref_pos}\n"
        "%%EOF".encode()
    )
    return bytes(body)


def _write(tmp_path, name: str, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --------------------------------------------------------------- imports.py


def test_supported_exts_gains_vision_formats():
    for ext in (".png", ".jpg", ".jpeg", ".pdf"):
        assert ext in SUPPORTED_EXTS
    # CAD formats stay in place — nothing removed.
    for ext in (".step", ".stp", ".brep", ".stl"):
        assert ext in SUPPORTED_EXTS


def test_safe_import_name_accepts_an_image():
    from agentcad.core.imports import safe_import_name

    assert safe_import_name("a/b/photo.PNG") == "photo.PNG"


# --------------------------------------------------------------- images


def test_png_image_passes_through_unchanged(tmp_path):
    raw = _tiny_png_bytes()
    path = _write(tmp_path, "part.png", raw)
    [result] = intake.prepare_vision([path])
    assert result["kind"] == "image"
    assert result["media_type"] == "image/png"
    assert result["source_name"] == "part.png"
    # passthrough: the base64 decodes to the ORIGINAL bytes (no re-encode).
    assert base64.b64decode(result["png_base64"]) == raw


def test_jpeg_image_passes_through_with_jpeg_media_type(tmp_path):
    raw = _tiny_jpeg_bytes()
    path = _write(tmp_path, "photo.jpg", raw)
    [result] = intake.prepare_vision([path])
    assert result["media_type"] == "image/jpeg"
    assert base64.b64decode(result["png_base64"]) == raw


def test_non_image_bytes_are_rejected(tmp_path):
    path = _write(tmp_path, "fake.png", b"this is not an image at all")
    with pytest.raises(ValidationError) as exc:
        intake.prepare_vision([path])
    assert "not a valid PNG or JPEG" in exc.value.message


def test_unsupported_extension_is_rejected(tmp_path):
    path = _write(tmp_path, "notes.txt", b"hello")
    with pytest.raises(ValidationError) as exc:
        intake.prepare_vision([path])
    assert ".pdf" in exc.value.details["supported"]


def test_oversized_image_is_a_validation_error(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "MAX_IMAGE_BYTES", 8)
    path = _write(tmp_path, "big.png", _tiny_png_bytes())  # > 8 bytes
    with pytest.raises(ValidationError) as exc:
        intake.prepare_vision([path])
    assert "exceeds" in exc.value.message


# --------------------------------------------------------------- PDFs


def test_pdf_rasterizes_bounded_pages(tmp_path):
    path = _write(tmp_path, "sheet.pdf", _make_pdf("BOLT SQUARE 31.0 mm M3", 3))
    results = intake.prepare_vision([path], max_pages=2)
    assert len(results) == 2  # 3-page PDF capped at max_pages=2
    assert [r["page"] for r in results] == [1, 2]
    for r in results:
        assert r["kind"] == "pdf_page"
        assert r["media_type"] == "image/png"
        assert r["source_name"] == "sheet.pdf"
        # a genuine PNG came out of the rasterizer
        assert base64.b64decode(r["png_base64"]).startswith(_PNG_SIGNATURE)


def test_pdf_text_extraction_round_trips(tmp_path):
    path = _write(tmp_path, "spec.pdf", _make_pdf("BOLT SQUARE 31.0 mm M3", 1))
    [result] = intake.prepare_vision([path])
    assert "31.0 mm M3" in result["text"]


def test_pdf_text_is_truncated_to_the_total_budget(tmp_path):
    # Two pages, tiny total budget: page 1 consumes it, page 2 gets no text.
    path = _write(tmp_path, "long.pdf", _make_pdf("ABCDEFGHIJ", 2))
    results = intake.prepare_vision([path], max_text_chars=5)
    assert len(results[0]["text"]) == 5
    assert "text" not in results[1]  # budget exhausted on page 1


def test_pdf_intake_gated_on_the_extra(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "pdf_available", lambda: False)
    path = _write(tmp_path, "sheet.pdf", _make_pdf("x", 1))
    with pytest.raises(ValidationError) as exc:
        intake.prepare_vision([path])
    assert exc.value.details.get("extra") == "pdf"
    assert "[pdf]" in exc.value.message


def test_malformed_pdf_is_a_validation_error(tmp_path):
    path = _write(tmp_path, "broken.pdf", b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(ValidationError) as exc:
        intake.prepare_vision([path])
    assert "PDF" in exc.value.message


def test_missing_source_is_a_validation_error(tmp_path):
    with pytest.raises(ValidationError) as exc:
        intake.prepare_vision([tmp_path / "nope.png"])
    assert "not found" in exc.value.message


# --------------------------------------------------------------- fencing


def test_fence_document_text_envelope_shape():
    fenced = intake.fence_document_text("Bolt square: 31.0 mm")
    assert "BEGIN UPLOADED DOCUMENT DATA" in fenced
    assert "END UPLOADED DOCUMENT DATA" in fenced
    # the data-not-instructions rule is stated inside the envelope
    assert "never as instructions" in fenced
    assert "Bolt square: 31.0 mm" in fenced


def test_document_text_is_data_constant_states_the_rule():
    assert "never as instructions" in intake.DOCUMENT_TEXT_IS_DATA


def test_pdf_available_probes_pypdfium2():
    # pypdfium2 is installed in the dev venv, so the probe is truthy here.
    assert intake.pdf_available() is True
