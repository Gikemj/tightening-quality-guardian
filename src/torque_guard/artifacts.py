from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable


def canonical_json_bytes(payload: object) -> bytes:
    """Serialize public JSON deterministically as UTF-8 with LF and one final newline."""

    text = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    return f"{text}\n".encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Replace one file atomically after its complete content reaches disk."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_json(path: Path, payload: object) -> None:
    """Write one public JSON artifact using the repository's canonical format."""

    atomic_write_bytes(path, canonical_json_bytes(payload))


def _safe_relative_path(value: Path | str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"artifact path must be a safe relative path: {value}")
    return relative


def commit_staged_files(
    staged_root: Path,
    destination_root: Path,
    relative_paths: Iterable[Path | str],
) -> None:
    """Commit a complete staged artifact set and roll back if any replacement fails.

    Cross-directory updates cannot be one filesystem transaction. This function
    therefore validates and reads every staged file first, then keeps in-memory
    originals so an I/O failure cannot leave a known half-updated build behind.
    """

    staged_root = staged_root.resolve()
    destination_root = destination_root.resolve()
    relative_files = sorted({_safe_relative_path(value) for value in relative_paths})
    staged_payloads: dict[Path, bytes] = {}
    originals: dict[Path, bytes | None] = {}

    for relative in relative_files:
        staged_path = (staged_root / relative).resolve()
        try:
            staged_path.relative_to(staged_root)
        except ValueError as error:
            raise ValueError(f"staged artifact escapes build root: {relative}") from error
        if not staged_path.is_file():
            raise FileNotFoundError(f"staged artifact is missing: {relative}")
        staged_payloads[relative] = staged_path.read_bytes()

        destination = (destination_root / relative).resolve()
        try:
            destination.relative_to(destination_root)
        except ValueError as error:
            raise ValueError(f"artifact destination escapes repository: {relative}") from error
        if destination.exists() and not destination.is_file():
            raise IsADirectoryError(f"artifact destination is not a file: {relative}")
        originals[relative] = destination.read_bytes() if destination.exists() else None

    committed: list[Path] = []
    try:
        for relative in relative_files:
            atomic_write_bytes(destination_root / relative, staged_payloads[relative])
            committed.append(relative)
    except BaseException as commit_error:
        rollback_errors: list[str] = []
        for relative in reversed(committed):
            destination = destination_root / relative
            try:
                original = originals[relative]
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(destination, original)
            except OSError as rollback_error:
                rollback_errors.append(f"{relative}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                "artifact commit failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from commit_error
        raise
