"""Build a W1 review from an explicit source/evidence allowlist; never include .venv or .git."""
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--stage-status", required=True, choices=("SUCCESS", "FAIL", "PARTIAL", "BLOCKED"))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    evidence = args.evidence.resolve()
    baseline = json.loads((evidence / "BASELINE.json").read_text())
    fw = project / "sanzo_cost_table_framework_v1_1_0"
    stage = evidence / "bundle_stage"
    stage.mkdir(exist_ok=False)
    delivery = evidence / "delivery"
    delivery.mkdir(exist_ok=False)
    deny = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}
    originals = {entry["path"]: entry["sha256"] for entry in baseline["preexisting_files"]}
    unchanged = {}
    for name, digest in originals.items():
        path = project / name
        unchanged[name] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    if not all(unchanged.values()):
        raise RuntimeError("Imported framework was modified: " + repr([n for n, ok in unchanged.items() if not ok]))

    # Source roots are explicit. Framework original evidence/HTML/env files are not copied.
    candidates = [project / n for n in ("README.md", ".gitignore", "pyproject.toml", "requirements.lock")]
    for folder in ("backend", "tests", "tools", "docs"):
        candidates.extend(p for p in (project / folder).rglob("*") if p.is_file())
    for folder in ("contracts", "reference_core", "docs", "tools"):
        candidates.extend(p for p in (fw / folder).rglob("*") if p.is_file())
    candidates.extend(fw / n for n in ("AGENTS.md", "README.md", "CHANGELOG.md"))
    selected = []
    for path in sorted(set(candidates)):
        relative = path.relative_to(project)
        if any(part in deny or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.name.startswith(".env") or path.suffix in (".pyc", ".pyo"):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(project.resolve()):
            raise ValueError("Unsafe source")
        if path.suffix not in (".py", ".sql", ".md", ".toml", ".lock", ".json", ".txt", ".w1-test") and path.name != ".gitignore":
            raise ValueError("Unexpected source file: " + str(relative))
        selected.append(path)
    source_inventory = []
    for path in selected:
        relative = path.relative_to(project).as_posix()
        dest = stage / "source" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        source_inventory.append({"path": relative, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                                 "origin": "imported_framework" if relative in originals else "W1_created"})
    changed = [x["path"] for x in source_inventory if x["origin"] == "W1_created"]
    final_commands = []
    for tail in (["status", "--porcelain=v1", "--branch", "--untracked-files=all"],
                 ["rev-parse", "HEAD"], ["diff", "--check"], ["diff", "--name-status"], ["diff", "--cached", "--name-status"]):
        cmd = ["git", "--no-optional-locks", "-c", "core.quotepath=false", "-C", str(project), *tail]
        p = subprocess.run(cmd, capture_output=True, text=True)
        final_commands.append({"argv": cmd, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
    (evidence / "FINAL_BASELINE.json").write_text(json.dumps({
        "project": str(project), "framework_originals_unchanged": unchanged,
        "commands": final_commands, "source_inventory": source_inventory,
        "business_mutations": changed, "head": (final_commands[1]["stdout"].strip() if final_commands[1]["exit_code"] == 0 else None), "head_command_exit_code": final_commands[1]["exit_code"],
        "note": "No HEAD exists: source files are included in full, not an empty git diff."
    }, ensure_ascii=False, indent=2) + "\n")
    (evidence / "CHANGED_PATHS.json").write_text(json.dumps({
        "project_created_files": [str(project / n) for n in changed],
        "framework_modified_files": [],
        "runtime_environment": str(project / ".venv"),
        "excluded_generated_metadata": "*.egg-info / __pycache__",
        "evidence_directory": str(evidence),
        "delivery_files": [str(delivery / n) for n in ("ReviewBundle.zip", "ReviewBundle.zip.sha256", "VERIFICATION.txt", "ReviewBundle_VERIFICATION.txt")]
    }, ensure_ascii=False, indent=2) + "\n")
    # Evidence only: bounded root records and synthetic logs, never databases or raw customer files.
    evidence_files = [p for p in evidence.iterdir() if p.is_file() and p.suffix in (".json", ".jsonl", ".md", ".txt")]
    evidence_files.extend(p for p in (evidence / "logs").iterdir() if p.is_file() and p.suffix in (".json", ".jsonl", ".txt"))
    for path in evidence_files:
        if path.is_symlink():
            raise ValueError("Symlink evidence")
        dest = stage / "evidence" / path.relative_to(evidence)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    members = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    allowlist = evidence / "bundle_allowlist.json"
    allowlist.write_text(json.dumps(members, ensure_ascii=False, indent=2) + "\n")
    archive = delivery / "ReviewBundle.zip"
    sidecar = delivery / "ReviewBundle.zip.sha256"
    cmd = [sys.executable, "-B", str(fw / "tools/pack_review.py"), "--root", str(stage),
           "--allowlist", str(allowlist), "--output", str(archive), "--stage-status", args.stage_status]
    p = subprocess.run(cmd, capture_output=True, text=True)
    record = {"argv": cmd, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    verified = False
    checks = {}
    error = None
    if p.returncode == 0:
        try:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            assert sidecar.read_text() == digest + "  " + archive.name + "\n", "sidecar exact name/format"
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                assert len(names) == len(set(n.casefold() for n in names)), "case collision"
                expected = {"ReviewBundle/" + n for n in members}
                expected |= {"ReviewBundle/SHA256SUMS", "ReviewBundle/PACKAGING_STATUS.json"}
                assert set(names) == expected, "exact member allowlist mismatch"
                for info in z.infolist():
                    name = info.filename
                    assert PurePosixPath(name).as_posix() == name
                    assert all(part not in ("", ".", "..") for part in name.split("/"))
                    assert not any(char in name for char in ("\\", ":", "\n", "\r", "\x00"))
                    assert not PurePosixPath(name).is_absolute()
                    assert stat.S_ISREG(info.external_attr >> 16)
                assert z.testzip() is None
            # Run the framework verifier again as a separate process; record real exit.
            verify_cmd = [sys.executable, "-B", str(fw / "tools/verify_bundle.py"), str(archive), str(sidecar)]
            vp = subprocess.run(verify_cmd, capture_output=True, text=True)
            checks = {"argv": verify_cmd, "exit_code": vp.returncode, "stdout": vp.stdout, "stderr": vp.stderr,
                      "exact_member_allowlist": "PASS", "sidecar_filename": "PASS", "canonical_paths_regular_files": "PASS"}
            assert vp.returncode == 0, "independent verification failed"
            verified = True
        except Exception as exc:
            error = repr(exc)
    else:
        error = "framework pack_review failed"
    verification = {
        "stage_status": args.stage_status, "w0_sdk_contract": "INCOMPLETE",
        "packaging": "SUCCESS" if verified else "FAIL",
        "packaging_command": record, "verification": checks, "error": error,
        "notice": "Packaging success does not change business/test/W0 status. Final packaging exits are external to avoid self-reference.",
    }
    (delivery / "VERIFICATION.txt").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"zip": str(archive) if archive.exists() else None,
                      "sha256": str(sidecar) if sidecar.exists() else None,
                      "verification": str(delivery / "VERIFICATION.txt"),
                      "packaging": verification["packaging"], "stage_status": args.stage_status}, ensure_ascii=False))
    return 0 if verified else 1

if __name__ == "__main__":
    raise SystemExit(main())
