#!/usr/bin/env python3
"""Read-only receipt check for the paused 1,893-race handoff ZIP."""

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import stat
import unicodedata
import zipfile


EXPECTED_NAME = "horse-myu-to-heroines-1893r-v1.zip"
EXPECTED_BYTES = 182_952_831
EXPECTED_SHA256 = "db52e0fec372aa0635ffc2457984f19da140b2d6fce29ac2bc2ba833643f5337"
EXPECTED_PAYLOAD_FILES = 13_985
EXPECTED_ZIP_FILES = EXPECTED_PAYLOAD_FILES + 1  # The file hash manifest lists the payload.
EXPECTED_ORIGINAL_PDFS = 192
from _paths import collect_root
RECEIPT_DIRECTORY = collect_root() / "みゅう引継ぎ受領"


def inspect_zip(archive):
    files = []
    explicit_paths = set()
    paths = {}
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.orig_filename
            directory = member.is_dir()
            parts = (name[:-1] if directory else name).split("/")
            if ("\x00" in name or "\\" in name or PureWindowsPath(name).drive
                    or any(part in {"", ".", ".."} for part in parts)):
                raise ValueError(f"Invalid ZIP entry path: {name!r}")
            if member.flag_bits & 0x1:
                raise ValueError(f"Encrypted ZIP entry: {name!r}")
            if member.create_system == 3:
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in (0, stat.S_IFDIR if directory else stat.S_IFREG):
                    raise ValueError(f"ZIP entry is a link, special file or has a conflicting type: {name!r}")

            normalized = [unicodedata.normalize("NFD", part.casefold()) for part in parts]
            for depth in range(1, len(parts) + 1):
                key = tuple(normalized[:depth])
                entry = (tuple(parts[:depth]), directory or depth < len(parts))
                if paths.setdefault(key, entry) != entry:
                    raise ValueError(f"Conflicting ZIP entry paths: {name!r}")

            if key in explicit_paths:
                raise ValueError(f"Duplicate ZIP entry: {name!r}")
            explicit_paths.add(key)
            if not directory:
                files.append(name)
    return files


def check_package(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular file at the receiving path")
    with path.open("rb") as archive:
        archive_size = archive.seek(0, 2)
        if archive_size != EXPECTED_BYTES:
            raise ValueError(f"Expected {EXPECTED_BYTES} bytes, found {archive_size}")
        archive.seek(0)
        digest = hashlib.file_digest(archive, "sha256").hexdigest()
        if digest != EXPECTED_SHA256:
            raise ValueError(f"ZIP SHA-256 mismatch: {digest}")
        files = inspect_zip(archive)

    if len(files) != EXPECTED_ZIP_FILES:
        raise ValueError(f"Expected {EXPECTED_ZIP_FILES} ZIP files including the manifest, found {len(files)}")
    pdf_files = sum(name.casefold().endswith(".pdf") for name in files)
    if pdf_files < EXPECTED_ORIGINAL_PDFS:
        raise ValueError(f"Expected at least {EXPECTED_ORIGINAL_PDFS} PDFs, found {pdf_files}")
    return {
        "zip_bytes": archive_size,
        "zip_sha256": digest,
        "zip_files": len(files),
        "expected_payload_files": EXPECTED_PAYLOAD_FILES,
        "zip_pdf_entries": pdf_files,
        "extraction_performed": False,
        "file_manifest_validated": False,
        "paused_data_validated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path, default=RECEIPT_DIRECTORY / EXPECTED_NAME)
    args = parser.parse_args()
    try:
        report = check_package(args.archive)
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f"ZIP preflight pending or failed: {error}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
