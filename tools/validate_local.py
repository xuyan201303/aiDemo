"""Local offline verification. No SDK, AI, files, Docker or database access."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("domain", "reference", "contracts", "all"), default="all")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fw = root / "sanzo_cost_table_framework_v1_1_0"
    args.evidence.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suites = {
        "reference": (fw / "reference_core", ["-m", "unittest", "discover", "-s", "tests", "-v"]),
        "domain": (root, ["-m", "unittest", "discover", "-s", "tests", "-p", "test_domain.py", "-v"]),
        "contracts": (fw, ["tools/check_contracts.py"]),
    }
    failed = False
    for name in (suites if args.suite == "all" else [args.suite]):
        cwd, tail = suites[name]
        cmd = [sys.executable, "-B"] + tail
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = str(root / "backend") + os.pathsep + str(fw / "reference_core")
        result = subprocess.run(cmd, cwd=cwd, env=environment, capture_output=True, text=True)
        log = args.evidence / (stamp + "_" + name + ".txt")
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        record = {"argv": cmd, "cwd": str(cwd), "exit_code": result.returncode,
                  "log": str(log), "nature": "offline synthetic domain/reference validation"}
        with (args.evidence / "local_commands.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        print(json.dumps(record))
        print(result.stdout + result.stderr)
        failed = failed or result.returncode != 0
    return int(failed)

if __name__ == "__main__":
    raise SystemExit(main())
