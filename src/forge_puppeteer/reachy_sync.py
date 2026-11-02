"""Install an operator-pinned official Reachy Mini recorded-motion snapshot.

No download occurs on import or at robot startup. Run the explicit installer
with a trusted, source-controlled manifest (dataset commit plus SHA-256 for
every byte). Failed installs leave no usable partial catalog. Existing
inventories are never changed in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .deployment import verify_reachy_snapshot

OFFICIAL_REACHY_DATASET = (
    "https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library"
)
MAX_SINGLE_FILE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILES = 512


def recording_url(manifest: dict, filename: str) -> str:
    """Construct a fixed-official-source, pinned-revision download URL."""
    if manifest.get("source_url") != OFFICIAL_REACHY_DATASET:
        raise ValueError("only the pinned official Reachy dataset may be imported")
    revision = manifest.get("dataset_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("dataset revision must be an exact 40-hex commit")
    files = manifest.get("files")
    if not isinstance(files, dict) or filename not in files:
        raise ValueError("file must appear in the reviewed manifest")
    path = Path(filename)
    if (path.is_absolute() or not filename or
            any(part in {"", ".", ".."} for part in path.parts) or
            "\\" in filename or path.as_posix() != filename):
        raise ValueError("invalid dataset relative path")
    return (
        f"{OFFICIAL_REACHY_DATASET}/resolve/{revision}/"
        f"{urllib.parse.quote(filename, safe='/')}?download=true"
    )


def _fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:  # fixed official host and commit
        data = response.read(MAX_SINGLE_FILE_BYTES + 1)
    if len(data) > MAX_SINGLE_FILE_BYTES:
        raise ValueError("Reachy dataset file exceeds download size bound")
    return data


def sync_reachy_recordings(
    manifest: dict,
    destination: str | Path,
    *,
    fetch: Callable[[str], bytes] | None = None,
) -> str:
    """Copy/check every manifest-listed byte to an immutable local directory.

    All paths are prevalidated before the first network request. A destination
    which already exists is verified, never modified or implicitly refreshed.
    """
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("invalid or excessive pinned recording inventory")
    # This also pins the exact SDK compatibility declared by the repository.
    from .devices.reachy import PINNED_REACHY_SDK_VERSION

    if manifest.get("sdk_version") != PINNED_REACHY_SDK_VERSION:
        raise ValueError("incompatible Reachy SDK version in dataset lock")
    urls: dict[str, str] = {}
    for name, digest in files.items():
        if (not isinstance(digest, str) or
                not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("invalid expected recording SHA-256")
        urls[name] = recording_url(manifest, name)
    target = Path(destination)
    if not target.name or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("destination must have an existing safe parent directory")
    if target.exists():
        if not target.is_dir():
            raise ValueError("destination is not a recording directory")
        return verify_reachy_snapshot(target, manifest)

    # O_EXCL guards independent install processes, including on most ordinary
    # local filesystems. Keep the staging tree beside its destination for an
    # atomic rename. Never put the manifest inside the catalog inventory.
    lock = target.with_name(f".{target.name}.reachy-install.lock")
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("pinned Reachy recording import\n")
        if target.exists() or target.is_symlink():
            raise ValueError("recording target appeared during import")
        temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.partial-", dir=target.parent))
        try:
            total = 0
            for name, url in sorted(urls.items()):
                data = (fetch or _fetch_bytes)(url)
                if not isinstance(data, bytes) or len(data) > MAX_SINGLE_FILE_BYTES:
                    raise ValueError("invalid or oversized downloaded recording bytes")
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Reachy recording snapshot exceeds total size bound")
                if hashlib.sha256(data).hexdigest() != files[name]:
                    raise ValueError(f"Reachy recording SHA-256 mismatch: {name}")
                output = temp.joinpath(*Path(name).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            revision = verify_reachy_snapshot(temp, manifest)
            if target.exists() or target.is_symlink():
                raise ValueError("recording target appeared during import")
            os.rename(temp, target)
            return revision
        finally:
            if temp.is_dir():
                shutil.rmtree(temp)
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage pinned Reachy Mini recordings")
    parser.add_argument("--manifest", type=Path, required=True, help="Reviewed JSON lock file")
    parser.add_argument("--target", type=Path, required=True, help="New immutable snapshot directory")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    revision = sync_reachy_recordings(manifest, args.target)
    print(json.dumps({"status": "verified", "revision": revision,
                      "target": str(args.target), "files": len(manifest["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
