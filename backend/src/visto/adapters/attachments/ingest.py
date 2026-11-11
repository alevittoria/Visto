"""Turn an uploaded file into page images; the type comes from its bytes, never its name.

PDF pages are rendered locally with pypdfium2 (docs/references.md) so every provider
receives the same images and a finding can cite an exact page.
"""

import io
from dataclasses import dataclass, field

import pypdfium2
from PIL import Image, UnidentifiedImageError

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RUN_PAGES = 40
# Rendered PDF pages aim for the largest provider limit (Claude high resolution)
# without enlarging a small page more than this factor.
RENDER_LONG_SIDE = 2576
MAX_RENDER_SCALE = 4.0
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_PDF = b"%PDF-"


class AttachmentError(ValueError):
    """A rejected attachment; the message is safe to show and never quotes content."""


@dataclass(frozen=True, slots=True)
class RenderedPage:
    media_type: str
    content: bytes = field(repr=False)
    width: int
    height: int


def _image(content: bytes, media_type: str) -> RenderedPage:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise AttachmentError("The image file is damaged or unreadable") from None
    return RenderedPage(media_type, content, width, height)


def _pdf(content: bytes) -> tuple[RenderedPage, ...]:
    try:
        document = pypdfium2.PdfDocument(content)
    except pypdfium2.PdfiumError:
        raise AttachmentError("The PDF is damaged or password-protected") from None
    try:
        count = len(document)
        if count == 0:
            raise AttachmentError("The PDF has no pages")
        if count > MAX_RUN_PAGES:
            raise AttachmentError(f"The PDF has more than {MAX_RUN_PAGES} pages")
        pages = []
        for index in range(count):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = min(RENDER_LONG_SIDE / max(width, height), MAX_RENDER_SCALE)
                image = page.render(scale=scale).to_pil()
            finally:
                page.close()
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "PNG")
            pages.append(RenderedPage("image/png", buffer.getvalue(), *image.size))
        return tuple(pages)
    except pypdfium2.PdfiumError:
        raise AttachmentError("The PDF is damaged or password-protected") from None
    finally:
        document.close()


def render(name: str, content: bytes) -> tuple[RenderedPage, ...]:
    """PNG and JPEG pass through unchanged; PDF pages become PNG images."""
    if type(name) is not str or not name.strip() or type(content) is not bytes:
        raise AttachmentError("An attachment needs a name and content")
    if not content:
        raise AttachmentError("The file is empty")
    if len(content) > MAX_FILE_BYTES:
        raise AttachmentError("The file is larger than 20 MB")
    if content.startswith(_PNG):
        return (_image(content, "image/png"),)
    if content.startswith(_JPEG):
        return (_image(content, "image/jpeg"),)
    if content.startswith(_PDF):
        return _pdf(content)
    raise AttachmentError("Only PNG, JPG and PDF files can be attached")
