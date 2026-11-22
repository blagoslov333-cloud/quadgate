"""Zero-dependency preflight environment check for QuadGate installation."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _check_python(version_info: tuple[int, int, int]) -> dict[str, str]:
    major, minor, micro = version_info
    ver_str = f"{major}.{minor}.{micro}"
    if (major, minor) in ((3, 11), (3, 12)):
        return {
            "name": "python",
            "status": "ok",
            "summary": f"Python {ver_str} ({sys.executable})",
            "hint": "Pre-built binary wheels are available; no C++ compiler is needed.",
        }
    if (major, minor) >= (3, 13):
        return {
            "name": "python",
            "status": "warn",
            "summary": f"Python {ver_str} ({sys.executable})",
            "hint": (
                "Supported, but on Windows/macOS some native packages may fall back to "
                "source builds requiring C/C++ Build Tools. Python 3.11 or 3.12 "
                "(or `uv venv --python 3.12`) is recommended."
            ),
        }
    return {
        "name": "python",
        "status": "fail",
        "summary": f"Python {ver_str} ({sys.executable}) is below 3.11",
        "hint": (
            "QuadGate requires Python >= 3.11. Install Python 3.12 from "
            "https://www.python.org/downloads/ (check 'Add python.exe to PATH' on Windows) "
            "or run `uv venv --python 3.12`."
        ),
    }


def _check_pip() -> dict[str, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "name": "pip",
            "status": "fail",
            "summary": f"Unable to invoke pip ({exc})",
            "hint": "Run `python -m ensurepip --upgrade` or install pip for this Python.",
        }

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        return {
            "name": "pip",
            "status": "fail",
            "summary": f"pip unavailable ({detail})",
            "hint": "Run `python -m ensurepip --upgrade` to bootstrap pip.",
        }

    out = (proc.stdout or "").strip()
    match = re.search(r"\bpip\s+(\d+)\.(\d+)(?:\.(\d+))?", out)
    if match:
        major = int(match.group(1))
        ver = ".".join(part for part in match.groups() if part is not None)
        if major >= 23:
            return {
                "name": "pip",
                "status": "ok",
                "summary": f"pip {ver}",
                "hint": "Modern wheel resolver available.",
            }
        return {
            "name": "pip",
            "status": "warn",
            "summary": f"pip {ver} (< 23.0)",
            "hint": "Upgrade pip first with `python -m pip install --upgrade pip`.",
        }

    return {
        "name": "pip",
        "status": "ok",
        "summary": out or "pip available",
        "hint": "Upgrade with `python -m pip install --upgrade pip` if wheel resolution fails.",
    }


def _check_git() -> dict[str, str]:
    git_path = shutil.which("git")
    if git_path:
        return {
            "name": "git",
            "status": "ok",
            "summary": f"git found ({git_path})",
            "hint": "Ready for git clone, Gate 1 co-change indexing, and Gate 4 hunk reverts.",
        }
    return {
        "name": "git",
        "status": "warn",
        "summary": "git not found on PATH",
        "hint": "Needed for git clone, Gate 1 co-change indexing, and Gate 4 clean hunk reverts.",
    }


def _check_uv() -> dict[str, str]:
    uv_path = shutil.which("uv")
    if uv_path:
        return {
            "name": "uv",
            "status": "ok",
            "summary": f"uv found ({uv_path})",
            "hint": "Can provision isolated Python 3.12 envs (`uv venv --python 3.12`).",
        }
    return {
        "name": "uv",
        "status": "info",
        "summary": "uv not installed (optional)",
        "hint": (
            "Optional: https://docs.astral.sh/uv/ manages Python 3.12 automatically "
            "for non-Python stacks."
        ),
    }


def _detect_windows_vs_build_tools() -> bool:
    if shutil.which("cl"):
        return True
    prog_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    vswhere = Path(prog_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return False
    try:
        proc = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def _has_windows_vcredist() -> bool:
    sys_root = os.environ.get("SystemRoot") or r"C:\Windows"
    return (Path(sys_root) / "System32" / "vcruntime140.dll").is_file()


def _check_build_tools(version_info: tuple[int, int, int]) -> dict[str, str]:
    py_pair = (version_info[0], version_info[1])
    needs_compiler_likely = py_pair >= (3, 13)

    if sys.platform == "win32":
        has_compiler = _detect_windows_vs_build_tools()
        has_vcredist = _has_windows_vcredist()
        redist_note = (
            "VC++ Redistributable (`vcruntime140.dll`) present."
            if has_vcredist
            else (
                "VC++ Redistributable (`vcruntime140.dll`) not found — only needed if "
                "installing the optional `[local]` ONNX runtime."
            )
        )
        if has_compiler:
            return {
                "name": "build_tools",
                "status": "ok",
                "summary": "Microsoft C++ Build Tools detected",
                "hint": redist_note,
            }
        if needs_compiler_likely:
            return {
                "name": "build_tools",
                "status": "warn",
                "summary": "Microsoft C++ Build Tools not detected (Python 3.13+)",
                "hint": (
                    "If `pip` tries to compile native wheels, switch to Python 3.11/3.12 "
                    "(or `uv venv --python 3.12`), or install Microsoft C++ Build Tools "
                    "('Desktop development with C++'): "
                    f"https://visualstudio.microsoft.com/visual-cpp-build-tools/ ({redist_note})"
                ),
            }
        return {
            "name": "build_tools",
            "status": "info",
            "summary": "Microsoft C++ Build Tools not detected",
            "hint": f"Not needed on Python 3.11/3.12 with pre-built wheels. {redist_note}",
        }

    if sys.platform == "darwin":
        clang_path = shutil.which("clang")
        if clang_path:
            return {
                "name": "build_tools",
                "status": "ok",
                "summary": f"Apple clang found ({clang_path})",
                "hint": "Xcode Command Line Tools are available if source builds occur.",
            }
        return {
            "name": "build_tools",
            "status": "warn" if needs_compiler_likely else "info",
            "summary": "Xcode Command Line Tools (`clang`) not detected",
            "hint": (
                "Run `xcode-select --install` if pip needs to compile a source package."
                if needs_compiler_likely
                else "Not needed on Python 3.11/3.12 with pre-built wheels "
                "(otherwise `xcode-select --install`)."
            ),
        }

    cc_path = shutil.which("gcc") or shutil.which("cc")
    if cc_path:
        return {
            "name": "build_tools",
            "status": "ok",
            "summary": f"C compiler found ({cc_path})",
            "hint": "System C compiler is available if source builds occur.",
        }
    return {
        "name": "build_tools",
        "status": "warn" if needs_compiler_likely else "info",
        "summary": "C/C++ compiler (`gcc`/`cc`) not detected",
        "hint": (
            "Install `build-essential python3-dev` (or switch to Python 3.11/3.12) "
            "if pip builds from source."
            if needs_compiler_likely
            else "Not needed on Python 3.11/3.12 with pre-built wheels "
            "(install `build-essential python3-dev` only for source builds)."
        ),
    }


def run_preflight() -> dict[str, Any]:
    ver_tuple = (sys.version_info[0], sys.version_info[1], sys.version_info[2])
    checks = [
        _check_python(ver_tuple),
        _check_pip(),
        _check_git(),
        _check_uv(),
        _check_build_tools(ver_tuple),
    ]

    statuses = {item["status"] for item in checks}
    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "ok"

    python_check = checks[0]
    pip_check = checks[1]
    if python_check["status"] == "fail":
        if shutil.which("uv"):
            next_step = "uv venv --python 3.12 && uv pip install -e ."
        else:
            next_step = (
                "Install Python 3.11 or 3.12 (or install uv from https://docs.astral.sh/uv/), "
                "then run: python -m pip install --upgrade pip && pip install -e ."
            )
    elif pip_check["status"] == "fail":
        next_step = (
            "python -m ensurepip --upgrade && "
            "python -m pip install --upgrade pip && pip install -e ."
        )
    else:
        next_step = "python -m pip install --upgrade pip && pip install -e ."

    return {
        "status": overall,
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})".strip(),
        "python": f"{ver_tuple[0]}.{ver_tuple[1]}.{ver_tuple[2]}",
        "checks": checks,
        "next_step": next_step,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"QuadGate Preflight Check — {report['os']} | Python {report['python']}")
    print("-" * 72)
    for item in report["checks"]:
        tag = item["status"].upper()
        print(f"[{tag:<4}] {item['name']}: {item['summary']}")
        if item.get("hint"):
            print(f"       {item['hint']}")
    print("-" * 72)
    print(f"Overall status : {report['status'].upper()}")
    print(f"Next step      : {report['next_step']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zero-dependency prerequisite checker for QuadGate installation.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON report.",
    )
    ns = parser.parse_args(argv)
    report = run_preflight()
    if ns.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
