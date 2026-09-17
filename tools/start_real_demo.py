#!/usr/bin/env python3
"""Start the existing real-reader Demo using the explicitly approved local key source.

The user's designated .env is not evaluated as shell code. Only OPENAI_API_KEY
is imported into the backend child environment; it is never printed or stored.
Missing credentials still allow the application's local memory-only path.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APPROVED_SOURCE = ROOT / "sanzo_cost_table_framework_v1_1_0/.env"


def backend_environment():
    env = dict(os.environ)
    # The designated file is the only credential source used by this wrapper.
    env.pop("OPENAI_API_KEY", None)
    if APPROVED_SOURCE.exists():
        if APPROVED_SOURCE.is_symlink() or APPROVED_SOURCE.stat().st_size > 65536:
            raise ValueError("APPROVED_CREDENTIAL_SOURCE_UNAVAILABLE")
        found = []
        for line in APPROVED_SOURCE.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*", line)
            if match:
                try:
                    parts = shlex.split(match[1], comments=True, posix=True)
                except ValueError:
                    raise ValueError("APPROVED_CREDENTIAL_ASSIGNMENT_INVALID") from None
                if len(parts) != 1 or not parts[0]:
                    raise ValueError("APPROVED_CREDENTIAL_ASSIGNMENT_INVALID")
                found.append(parts[0])
        if len(found) > 1:
            raise ValueError("APPROVED_CREDENTIAL_ASSIGNMENT_AMBIGUOUS")
        if found:
            env["OPENAI_API_KEY"] = found[0]
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    try:
        env = backend_environment()
    except (OSError, UnicodeError, ValueError):
        print("Approved credential unavailable: starting the local reader; unknown AI work cannot run.", file=sys.stderr)
        env = dict(os.environ)
        env.pop("OPENAI_API_KEY", None)
    command = [sys.executable, "-B", str(ROOT / "tools/start_demo.py"), "start",
               "--reader-mode", "REAL_SDK", "--ai-authorization",
               str(ROOT / "demo_materials/config/ai-authorization.json")]
    if args.evidence_dir:
        command += ["--evidence-dir", str(args.evidence_dir)]
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
