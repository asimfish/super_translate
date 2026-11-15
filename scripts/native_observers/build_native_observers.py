"""Build the two exact-version MuPDF observers for the current Linux machine.

Run inside the compiler image with the MuPDF 1.25.2 include directory. This
builds external artifacts; it never modifies or installs a frozen candidate.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path


def build_native_observers(include_dir: Path, output_dir: Path, compiler: str) -> dict:
    if sys.platform != "linux" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ValueError("A_64_BIT_LINUX_BUILD_PROCESS_IS_REQUIRED")
    machine = {"aarch64": 183, "x86_64": 62}.get(platform.machine())
    if machine is None:
        raise ValueError("UNSUPPORTED_NATIVE_MACHINE")
    source_dir = Path(__file__).resolve().parent
    headers = json.loads((source_dir / "headers.sha256.json").read_text())
    for name, expected in headers.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("INVALID_HEADER_PATH")
        path = include_dir / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"EXACT_MUPDF_1_25_2_HEADER_REQUIRED: {name}")
    executable = shutil.which(compiler)
    if executable is None:
        raise ValueError("COMPILER_NOT_FOUND")
    # Refuse replacement of any prior build, including a partially failed one.
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "scope": "NATIVE_OBSERVER_BUILD_ONLY_NOT_TRANSLATION_ACCEPTANCE",
        "machine": platform.machine(),
        "ELF_machine": machine,
        "headers_verified": len(headers),
        "compiler": executable,
        "compiler_version": subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout,
        "artifacts": [],
    }
    for stem in ("c1025_native_icc_buffer", "c1240_current_linear_shade"):
        source = source_dir / f"{stem}.c"
        library = output_dir / f"{stem}.so"
        command = [
            executable,
            "-shared",
            "-fPIC",
            "-O2",
            "-ffp-contract=off",
            "-I",
            str(include_dir.resolve()),
            str(source),
            "-o",
            str(library),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        data = library.read_bytes()
        if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != machine:
            raise ValueError("COMPILER_OUTPUT_MACHINE_MISMATCH")
        symbol = "c1025_version" if stem.startswith("c1025") else "c1240_version"
        probe = (
            "import ctypes,sys; library=ctypes.PyDLL(sys.argv[1]); "
            "version=getattr(library,sys.argv[2]); version.restype=ctypes.c_char_p; "
            "assert version()==b'1.25.2'"
        )
        subprocess.run(
            [sys.executable, "-I", "-S", "-c", probe, str(library.resolve()), symbol],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        report["artifacts"].append(
            {
                "name": library.name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "command": command,
                "loaded_and_exact_version": True,
            }
        )
    (output_dir / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    print(json.dumps(build_native_observers(args.include_dir, args.output_dir, args.compiler)))
