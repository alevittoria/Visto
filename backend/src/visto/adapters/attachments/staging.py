"""Uploaded attachments wait here, owner-only, until a run claims them or they expire."""

import json
import re
import shutil
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from visto.adapters.storage import LocalArtifactStore
from visto.domain.models import AttachmentPage
from visto.home import make_private_dir

from .ingest import MAX_RUN_PAGES, AttachmentError, RenderedPage, render

_ID = re.compile(r"[0-9a-f]{32}")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}
MAX_NAME_CHARS = 255


@dataclass(frozen=True, slots=True)
class StagedFile:
    upload_id: str
    name: str
    pages: tuple[RenderedPage, ...] = field(repr=False)


def _clean_name(name: str) -> str:
    """Display name only: path parts are dropped, it never becomes a path."""
    base = re.split(r"[\\/]", name.strip())[-1].strip()
    base = "".join(c for c in base if c.isprintable())[:MAX_NAME_CHARS]
    if not base:
        raise AttachmentError("An attachment needs a file name")
    return base


def _read_meta(directory: Path) -> dict:
    return json.loads((directory / "meta.json").read_text(encoding="utf-8"))


class AttachmentStaging:
    def __init__(self, root: Path, ttl_s: int = 3600):
        self.root = Path(root)
        self.ttl_s = ttl_s

    def _directory(self, upload_id: str) -> Path | None:
        if type(upload_id) is not str or _ID.fullmatch(upload_id) is None:
            return None
        return self.root / upload_id

    def stage(self, name: str, content: bytes) -> StagedFile:
        name = _clean_name(name) if isinstance(name, str) else ""
        return self.stage_pages(name, render(name, content))

    def stage_pages(self, name: str, pages: tuple[RenderedPage, ...]) -> StagedFile:
        """Stage pages already validated, such as those of an earlier run."""
        name = _clean_name(name)
        if not pages or len(pages) > MAX_RUN_PAGES:
            raise AttachmentError(f"A file must have 1 to {MAX_RUN_PAGES} pages")
        upload_id = uuid.uuid4().hex
        make_private_dir(self.root)
        directory = self.root / upload_id
        make_private_dir(directory)
        for index, page in enumerate(pages, start=1):
            (directory / f"{index}.{_EXTENSIONS[page.media_type]}").write_bytes(page.content)
        meta = {
            "name": name,
            "pages": [[p.media_type, p.width, p.height] for p in pages],
            "staged_at": time.time(),
        }
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return StagedFile(upload_id, name, pages)

    def get(self, upload_id: str) -> StagedFile:
        directory = self._directory(upload_id)
        try:
            if directory is None or directory.is_symlink():
                raise ValueError
            meta = _read_meta(directory)
            if time.time() - meta["staged_at"] > self.ttl_s:
                raise ValueError
            pages = tuple(
                RenderedPage(
                    media_type,
                    (directory / f"{index}.{_EXTENSIONS[media_type]}").read_bytes(),
                    width,
                    height,
                )
                for index, (media_type, width, height) in enumerate(meta["pages"], start=1)
            )
            return StagedFile(upload_id, meta["name"], pages)
        except (OSError, ValueError, KeyError, TypeError):
            raise AttachmentError("The attachment upload is missing or expired") from None

    def discard(self, upload_id: str) -> None:
        directory = self._directory(upload_id)
        if directory is not None and directory.is_dir() and not directory.is_symlink():
            shutil.rmtree(directory, ignore_errors=True)

    def sweep(self, now_s: float | None = None) -> int:
        """Remove expired or unreadable uploads; returns how many were removed."""
        now_s = time.time() if now_s is None else now_s
        removed = 0
        if not self.root.is_dir():
            return 0
        for directory in self.root.iterdir():
            if not _ID.fullmatch(directory.name) or directory.is_symlink():
                continue
            try:
                expired = now_s - _read_meta(directory)["staged_at"] > self.ttl_s
            except (OSError, ValueError, KeyError, TypeError):
                expired = True
            if expired:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        return removed


async def promote(
    files: Sequence[StagedFile], run_id: str, artifacts: LocalArtifactStore
) -> tuple[AttachmentPage, ...]:
    """Store every page as an owned artifact of the run, in upload order."""
    if sum(len(f.pages) for f in files) > MAX_RUN_PAGES:
        raise AttachmentError(f"A run can have at most {MAX_RUN_PAGES} attachment pages")
    result = []
    for staged in files:
        count = len(staged.pages)
        for number, page in enumerate(staged.pages, start=1):
            reference = await artifacts.put(run_id, page.content, page.media_type)
            result.append(
                AttachmentPage(
                    uuid.uuid4().hex,
                    staged.name,
                    number,
                    count,
                    page.width,
                    page.height,
                    reference,
                )
            )
    return tuple(result)
