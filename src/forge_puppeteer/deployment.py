"""Validate explicitly staged, immutable Reachy motion snapshots offline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


def _path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (
        not name
        or relative.is_absolute()
        or any(p in (".", "..") for p in relative.parts)
        or "\\" in name
    ):
        raise ValueError(f"invalid snapshot path: {name!r}")
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe snapshot path: {name!r}")
    return path


def verify_reachy_snapshot(root: Path, manifest: dict[str, Any]) -> str:
    """Verify every staged file, metadata and inventory; return manifest hash.

    The manifest must be obtained through a trusted operator channel. Hashes
    detect drift; they do not authenticate a manifest supplied by an attacker.
    """
    required = {
        "version",
        "dataset_revision",
        "sdk_version",
        "source_url",
        "license",
        "notices",
        "files",
    }
    if set(manifest) != required or manifest["version"] != 1:
        raise ValueError("invalid snapshot manifest fields or version")
    for key in ("dataset_revision", "sdk_version", "source_url", "license", "notices"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError(f"missing {key}")
    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("snapshot has no files")
    expected = set()
    for name, digest in files.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("invalid file hash")
        path = _path(root, name)
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError(f"missing or mutated snapshot file: {name}")
        expected.add(name)
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("snapshot contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError("snapshot file inventory does not match manifest")
    encoded = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_reachy_manifest(
    root: Path,
    *,
    dataset_revision: str,
    sdk_version: str,
    source_url: str,
    license: str,
    notices: str,
) -> dict[str, Any]:
    """Create a lock from an operator-staged directory, without network access."""
    root = Path(root)
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("snapshot contains a symlink")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    manifest = {
        "version": 1,
        "dataset_revision": dataset_revision,
        "sdk_version": sdk_version,
        "source_url": source_url,
        "license": license,
        "notices": notices,
        "files": files,
    }
    verify_reachy_snapshot(root, manifest)
    return manifest
