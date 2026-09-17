"""Package this limited Demo with full source and independently verified hashes.

Never copies originals, .env, SDK repositories, .venv or Docker volumes.
The caller's stage status is preserved even when packaging succeeds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import zipfile

DENY = {".DS_Store", ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".demo-runtime"}
TEXT_SUFFIXES = {".py", ".sql", ".md", ".txt", ".json", ".jsonl", ".log", ".toml",
                 ".lock", ".html", ".js", ".css", ".w1-test", ".demo"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe evidence/source path: " + str(path))
    if any(part in DENY or part.endswith(".egg-info") for part in path.relative_to(root).parts):
        return False
    if path.name.startswith(".env") or path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def command(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    return {"argv": argv, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--stage-status", choices=["SUCCESS", "FAIL", "PARTIAL", "BLOCKED"], required=True)
    parser.add_argument("--execution-report", type=Path,
                        help="Optional current-stage execution matrix; never rewrites sealed evidence")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    evidence = args.evidence.resolve(strict=True)
    execution = {}
    if args.execution_report:
        report_path = args.execution_report.resolve(strict=True)
        if not report_path.is_relative_to(evidence) or report_path.is_symlink():
            raise ValueError("Execution report must be a regular evidence file")
        execution = json.loads(report_path.read_text())
        if not isinstance(execution, dict):
            raise ValueError("Execution report must be an object")
    baseline = json.loads((evidence / "baseline.json").read_text())
    if baseline["project"] != str(project):
        raise ValueError("Baseline project differs")
    stage, delivery = evidence / "bundle_stage", evidence / "delivery"
    stage.mkdir(exist_ok=False)
    delivery.mkdir(exist_ok=False)
    fw = project / "sanzo_cost_table_framework_v1_1_0"
    candidates = [project / name for name in ["README.md", ".gitignore", "pyproject.toml",
                                             "requirements.lock", "requirements-demo.lock",
                                             "requirements-demo-test.lock"]]
    for folder in ["backend", "tests", "tools", "docs", "sanzo_cost_table_framework_v1_1_0", "demo_materials"]:
        candidates.extend(p for p in (project / folder).rglob("*") if p.is_file())
    selected = {}
    for path in sorted(set(candidates)):
        if not path.is_file() or not safe(path, project):
            continue
        # User-supplied original samples are hashes only, even though their
        # older attached instruction permits bundling them.
        if path.relative_to(project).parts[0] == "demo_materials" and path.suffix.lower() in {".pdf", ".xlsx"}:
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {".gitignore", "SHA256SUMS"}:
            raise ValueError("Unexpected source type: " + str(path))
        selected[path.relative_to(project).as_posix()] = path
    baseline_files = baseline["files"]
    changed = [{"path": name, "change": "MODIFIED" if name in baseline_files else "ADDED",
                "before_sha256": baseline_files.get(name, {}).get("sha256"), "after_sha256": digest(path),
                "origin": "IMPLEMENTATION_RAW_FIXTURE" if name.startswith("demo_materials/fixtures/") else "USER_SUPPLIED_DURING_STAGE" if name.startswith("demo_materials/") else "IMPLEMENTATION"}
               for name, path in selected.items()
               if name not in baseline_files or digest(path) != baseline_files[name]["sha256"]]
    deleted = [name for name in baseline_files if not (project / name).exists()]
    framework_changed = [x["path"] for x in changed if x["path"].startswith(fw.name + "/")]
    if framework_changed:
        raise RuntimeError("Original framework changed: " + repr(framework_changed))
    git = [command(["git", "--no-optional-locks", "-c", "core.quotepath=false", *tail], project)
           for tail in [["status", "--porcelain=v1", "-uall"], ["rev-parse", "HEAD"],
                        ["diff", "--check"], ["diff", "--binary"], ["diff", "--cached", "--binary"]]]
    (evidence / "FINAL_BASELINE.json").write_text(json.dumps({
        "project": str(project), "head": git[1]["stdout"].strip() if git[1]["exit_code"] == 0 else None,
        "git": git, "files": {name: digest(path) for name, path in selected.items()},
        "framework_changed": framework_changed, "note": "No HEAD: full source is included; empty git diff is not the implementation."
    }, ensure_ascii=False, indent=2) + "\n")
    (evidence / "CHANGED_PATHS.json").write_text(json.dumps({
        "changed": changed, "deleted": deleted, "runtime_excluded": [".venv", ".demo-runtime", "project-labeled Docker volumes", "demo_inputs/ (copied original samples; hashes in supplemental baseline)"],
        "evidence_root": str(evidence), "source_full_text": True,
    }, ensure_ascii=False, indent=2) + "\n")
    (evidence / "PATCH.txt").write_text(git[3]["stdout"] or
        "HEAD is unborn and sources are untracked. No usable git patch exists; source/ includes every selected file in full.\n")
    source_paths = {}
    for name, path in selected.items():
        # The framework verifier reserves any SHA256SUMS basename. Preserve its
        # original bytes under a nonreserved name and record the exact mapping.
        bundle_name = name + ".original.txt" if path.name == "SHA256SUMS" else name
        dest = stage / "source" / bundle_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        source_paths[name] = {"bundle_path": "source/" + bundle_name, "sha256": digest(path)}
    (evidence / "SOURCE_PATH_MAPPING.json").write_text(json.dumps(source_paths, ensure_ascii=False, indent=2) + "\n")
    evidence_inventory = []
    for path in sorted(evidence.rglob("*")):
        relative = path.relative_to(evidence)
        if relative.parts[0] in {"bundle_stage", "delivery"} or not path.is_file():
            continue
        if not safe(path, evidence):
            continue
        # Only generated browser screenshots are included as binary evidence.
        is_screenshot = path.suffix == ".png" and any(part.startswith("browser_") for part in relative.parts)
        if path.suffix not in TEXT_SUFFIXES and not is_screenshot:
            continue
        # Baseline source snapshots may contain the framework's original
        # manifests; preserve their bytes without colliding with bundle hashes.
        bundle_relative = relative.with_name(relative.name + ".original.txt") if path.name == "SHA256SUMS" else relative
        dest = stage / "evidence" / bundle_relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        evidence_inventory.append(str(relative))
    members = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    allowlist = evidence / "bundle_allowlist.json"
    allowlist.write_text(json.dumps(members, ensure_ascii=False, indent=2) + "\n")
    archive = delivery / "ReviewBundle.zip"
    sidecar = delivery / "ReviewBundle.zip.sha256"
    packaging = command([sys.executable, "-B", str(fw / "tools/pack_review.py"),
                         "--root", str(stage), "--allowlist", str(allowlist), "--output", str(archive),
                         "--stage-status", args.stage_status], project)
    checks, error = {}, None
    try:
        if packaging["exit_code"]:
            raise RuntimeError("Framework packer failed")
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            expected = {"ReviewBundle/" + n for n in members}
            expected |= {"ReviewBundle/SHA256SUMS", "ReviewBundle/PACKAGING_STATUS.json"}
            if set(names) != expected or len(names) != len(set(n.casefold() for n in names)):
                raise ValueError("Exact allowlist / duplicate path check failed")
            for info in z.infolist():
                name = info.filename
                if (PurePosixPath(name).as_posix() != name or PurePosixPath(name).is_absolute()
                    or any(p in {"", ".", ".."} for p in name.split("/"))
                    or any(c in name for c in ["\\", ":", "\n", "\r", "\x00"])
                    or not stat.S_ISREG(info.external_attr >> 16)):
                    raise ValueError("Unsafe member: " + name)
            if z.testzip() is not None:
                raise ValueError("ZIP CRC failed")
        if sidecar.read_text() != digest(archive) + "  " + archive.name + "\n":
            raise ValueError("External digest differs")
        independent = command([sys.executable, "-B", str(fw / "tools/verify_bundle.py"),
                               str(archive), str(sidecar)], project)
        if independent["exit_code"]:
            raise ValueError("Independent internal/external hash verification failed")
        checks = {"crc": "PASS", "member_paths": "PASS", "exact_allowlist": "PASS",
                  "internal_sha256": "PASS", "external_sha256": "PASS", "independent": independent}
    except Exception as exc:
        error = repr(exc)
    verification = {"stage_status": args.stage_status, "w0_sdk_contract": "INCOMPLETE",
                    "real_sdk_ai_rounds": execution.get("real_sdk_ai_rounds", "NOT_RUN"),
                    "execution_report": execution, "packaging": "FAIL" if error else "SUCCESS",
                    "zip": str(archive) if archive.exists() else None,
                    "zip_sha256": digest(archive) if archive.exists() else None,
                    "packaging_command": packaging, "checks": checks, "error": error,
                    "notice": "Packaging success does not change implementation or test status."}
    (delivery / "VERIFICATION.txt").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"stage_status": args.stage_status, "packaging": verification["packaging"],
                      "zip": verification["zip"], "sha256": str(sidecar) if sidecar.exists() else None,
                      "verification": str(delivery / "VERIFICATION.txt"),
                      "changed_paths": len(changed)}, ensure_ascii=False, indent=2))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
