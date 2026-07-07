#!/usr/bin/env python3
"""Stream tar.zst extract with sha256 verify and atomic commit (batch-1)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - runtime guard
    zstd = None  # type: ignore[assignment,misc]


class ExtractError(Exception):
  """Structured extract failure for callers."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_safe_tar_path(name: str) -> bool:
    if not name:
        return False
    norm = name.replace("\\", "/")
    if norm.startswith("/"):
        return False
    parts = Path(norm).parts
    return ".." not in parts


def resolve_member_path(dest_root: Path, member_name: str) -> Path:
    """Resolve tar member path and ensure it stays under dest_root."""
    norm = member_name.replace("\\", "/")
    if not is_safe_tar_path(norm):
        raise ExtractError(f"unsafe tar path: {member_name}")
    rel = norm.lstrip("/")
    target = (dest_root / rel).resolve()
    root = dest_root.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ExtractError(f"unsafe tar path escapes dest: {member_name}") from None
    return target


def _require_zstd() -> None:
    if zstd is None:
        raise ExtractError("zstandard package not installed")


def peek_tar_member(archive: Path, member_name: str) -> bytes:
    _require_zstd()
    target = member_name.lstrip("./")
    dctx = zstd.ZstdDecompressor()
    with archive.open("rb") as raw:
        with dctx.stream_reader(raw) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    if member.isdir() or not member.isfile():
                        continue
                    name = member.name.lstrip("./")
                    if name != target and not name.endswith(f"/{target}"):
                        continue
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        raise ExtractError(f"cannot read tar member: {member.name}")
                    return extracted.read()
    raise ExtractError(f"tar member not found: {member_name}")


def extract_tar_zst(archive: Path, dest_dir: Path, *, expected_sha256: str | None = None) -> dict:
    _require_zstd()
    if not archive.is_file():
        raise ExtractError(f"archive missing: {archive}")

    actual_sha = sha256_file(archive)
    if expected_sha256 and actual_sha.lower() != expected_sha256.lower():
        raise ExtractError(
            f"sha256 mismatch expected={expected_sha256[:12]} actual={actual_sha[:12]}"
        )

    parent = dest_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = parent / f"{dest_dir.name}.tmp.{os.getpid()}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    file_count = 0
    try:
        dctx = zstd.ZstdDecompressor()
        with archive.open("rb") as raw:
            with dctx.stream_reader(raw) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    for member in tar:
                        if not member.isfile():
                            continue
                        target = resolve_member_path(tmp_dir, member.name)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            raise ExtractError(f"cannot read tar member: {member.name}")
                        with target.open("wb") as out:
                            shutil.copyfileobj(extracted, out)
                        file_count += 1

        if file_count <= 0:
            raise ExtractError("archive contained no regular files")

        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        tmp_dir.replace(dest_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return {
        "ok": True,
        "sha256": actual_sha,
        "files": file_count,
        "dest": str(dest_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream-extract tar.zst with atomic commit")
    parser.add_argument("archive", type=Path, help="Path to .tar.zst archive")
    parser.add_argument("dest_dir", nargs="?", type=Path, help="Destination directory")
    parser.add_argument("--peek", metavar="MEMBER", help="Extract single member to stdout")
    parser.add_argument("--expected-sha256", metavar="HEX", default=None)
    args = parser.parse_args()

    try:
        if args.peek:
            payload = peek_tar_member(args.archive, args.peek)
            sys.stdout.buffer.write(payload)
            return 0

        if args.dest_dir is None:
            print("dest_dir required unless --peek is used", file=sys.stderr)
            return 1

        result = extract_tar_zst(
            args.archive,
            args.dest_dir,
            expected_sha256=args.expected_sha256,
        )
        print(json.dumps(result))
        return 0
    except ExtractError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, tarfile.TarError, zstd.ZstdError) as exc:  # type: ignore[union-attr]
        print(json.dumps({"ok": False, "error": str(exc)[:500]}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
