"""Private file storage addressed only by generated opaque keys.

No user- or model-supplied path ever reaches the filesystem: keys are generated
here, validated against a strict pattern, and resolved under the storage root.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path

_KEY = re.compile(r"^(originals|derived)/[0-9a-f]{2}/[0-9a-f]{40}$")


class StorageError(Exception):
    pass


class FileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        for category in ("originals", "derived"):
            (self.root / category).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_key(category: str = "originals") -> str:
        if category not in ("originals", "derived"):
            raise StorageError("invalid storage category")
        token = secrets.token_hex(20)
        return f"{category}/{token[:2]}/{token}"

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise StorageError("invalid storage key")
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("storage key escapes root")
        return path

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def delete_derived_prefix(self, document_key: str) -> None:
        """Remove derived artifacts stored under the document's derived directory."""
        token = document_key.rsplit("/", 1)[-1]
        derived = self.root / "derived" / "by-document" / token
        if derived.exists():
            shutil.rmtree(derived)
