"""Build the reviewbot-tsnet helper as a universal macOS binary and package it as a wheel.

Usage: uv run python tsnet/build_wheel.py VERSION [--out DIR]

The wheel holds only the binary, under `.data/scripts/`, so installing it places
`reviewbot-tsnet` in the environment's bin directory beside ReviewBot's own Python.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "reviewbot_tsnet"
TAG = "py3-none-macosx_11_0_universal2"
ARCHES = ("arm64", "amd64")


def build_binary(version: str, work: Path) -> Path:
    """Cross-compile both macOS architectures and join them with lipo."""
    parts = []
    for arch in ARCHES:
        out = work / f"reviewbot-tsnet-{arch}"
        env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "darwin", "GOARCH": arch}
        subprocess.run(
            ["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={version}",
             "-o", str(out), "."],
            cwd=HERE, env=env, check=True,
        )
        parts.append(str(out))
    universal = work / "reviewbot-tsnet"
    subprocess.run(["lipo", "-create", *parts, "-output", str(universal)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(universal)], check=True)
    return universal


def _record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def write_wheel(version: str, binary: Path, out: Path) -> Path:
    """Package the binary as an executable script in a platform wheel."""
    dist_info = f"{NAME}-{version}.dist-info"
    files: list[tuple[str, bytes, int]] = [
        (f"{NAME}-{version}.data/scripts/reviewbot-tsnet", binary.read_bytes(), 0o755),
        (f"{dist_info}/METADATA", (
            "Metadata-Version: 2.1\n"
            f"Name: reviewbot-tsnet\nVersion: {version}\n"
            "Summary: Tailnet device helper for ReviewBot (macOS)\n"
            "License: MIT\n"
        ).encode(), 0o644),
        (f"{dist_info}/WHEEL", (
            "Wheel-Version: 1.0\nGenerator: reviewbot build_wheel\n"
            f"Root-Is-Purelib: false\nTag: {TAG}\n"
        ).encode(), 0o644),
    ]
    out.mkdir(parents=True, exist_ok=True)
    wheel = out / f"{NAME}-{version}-{TAG}.whl"
    record = f"{dist_info}/RECORD"
    lines = [_record_line(path, data) for path, data, _ in files] + [f"{record},,"]
    files.append((record, ("\n".join(lines) + "\n").encode(), 0o644))
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data, mode in files:
            info = zipfile.ZipInfo(path, date_time=(2020, 2, 2, 0, 0, 0))
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as work:
        binary = build_binary(args.version, Path(work))
        print(write_wheel(args.version, binary, args.out))


if __name__ == "__main__":
    main()
