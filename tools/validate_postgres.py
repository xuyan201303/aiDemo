#!/usr/bin/env python3
"""Run synthetic W1 PostgreSQL tests in an isolated, disposable Docker network.

This is business-core validation, not real file / AI / Web Demo validation.
Only dependencies and test images use the network during --prepare-only.
--run uses the prepared images and does not pull any images.
No SDK, environment file, host database, or pre-existing container is accessed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


PROJECT = Path(__file__).resolve().parents[1]
PROJECT_LABEL = "sanzo-cost-table-w1"
PG_IMAGE = "postgres:16"
PY_IMAGE = "python:3.13-slim"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class StepFailed(RuntimeError):
    pass


class Audit:
    def __init__(self, evidence_dir: Path):
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.logs = evidence_dir / "logs"
        self.logs.mkdir(exist_ok=True)
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]
        self.commands_path = evidence_dir / ("pg_commands_" + self.run_id + ".jsonl")
        self.result_path = evidence_dir / ("pg_result_" + self.run_id + ".json")
        self.seq = 0
        self.commands = []
        self.docker = shutil.which("docker") or "/usr/local/bin/docker"

    def run(self, label: str, args: list[str], *, required: bool = True,
            timeout: int = 600, cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.seq += 1
        log = self.logs / f"pg_{self.run_id}_{self.seq:03d}_{label}.txt"
        start = now()
        try:
            result = subprocess.run(
                args, cwd=str(cwd or PROJECT), capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            result = subprocess.CompletedProcess(args, 124, decode(exc.stdout), decode(exc.stderr) + "\nTIMEOUT\n")
        except OSError as exc:
            result = subprocess.CompletedProcess(args, 127, "", str(exc) + "\n")
        body = "COMMAND: " + json.dumps(args, ensure_ascii=False) + "\n"
        body += "START: " + start + "\nEND: " + now() + "\nEXIT_CODE: " + str(result.returncode)
        body += "\n--- STDOUT ---\n" + result.stdout + "\n--- STDERR ---\n" + result.stderr
        log.write_text(body, encoding="utf-8")
        record = {
            "sequence": self.seq, "label": label, "argv": args,
            "cwd": str(cwd or PROJECT), "started_at": start, "finished_at": now(),
            "exit_code": result.returncode, "log": str(log),
            "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        }
        self.commands.append(record)
        with self.commands_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{label}: exit={result.returncode}; log={log}", flush=True)
        if required and result.returncode:
            raise StepFailed(f"{label} failed with exit code {result.returncode}")
        return result

    def docker_run(self, label: str, *args: str, **kwargs):
        return self.run(label, [self.docker, *args], **kwargs)

    def image_identity(self, image: str, label: str) -> dict:
        result = self.docker_run(label, "image", "inspect", image, "--format",
                                 "{{json .Id}} {{json .RepoDigests}} {{json .Architecture}}")
        return {"reference": image, "inspection": result.stdout.strip()}


def image_name() -> str:
    digest = hashlib.sha256((PROJECT / "backend" / "Dockerfile.w1-test").read_bytes()).hexdigest()
    return "sanzo-cost-table-w1-test:" + digest[:20]


def prepare(audit: Audit) -> dict:
    audit.docker_run("docker_version", "version", "--format", "{{json .Server}}")
    audit.docker_run("pull_postgres", "pull", PG_IMAGE)
    audit.docker_run("pull_python", "pull", PY_IMAGE)
    identities = [
        audit.image_identity(PG_IMAGE, "postgres_image_identity"),
        audit.image_identity(PY_IMAGE, "python_image_identity"),
    ]
    # Docker receives only this file: never .git, .env, customer data, or framework.
    with tempfile.TemporaryDirectory(prefix="sanzo_w1_build_") as folder:
        context = Path(folder)
        shutil.copyfile(PROJECT / "backend" / "Dockerfile.w1-test", context / "Dockerfile")
        manifest = {
            "build_context": str(context),
            "members": [{"path": "Dockerfile", "sha256": hashlib.sha256((context / "Dockerfile").read_bytes()).hexdigest()}],
        }
        (audit.logs / f"pg_{audit.run_id}_build_context.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        audit.docker_run("build_test_image", "build", "--pull=false", "--tag", image_name(), str(context))
    identities.append(audit.image_identity(image_name(), "test_image_identity"))
    return {"images": identities, "test_image": image_name()}


def execute_tests(audit: Audit) -> dict:
    # --pull=never ensures no hidden network dependency or runtime image change.
    identities = [
        audit.image_identity(PG_IMAGE, "postgres_image_identity"),
        audit.image_identity(image_name(), "test_image_identity"),
    ]
    token = uuid.uuid4().hex
    label = "org.sanzo.w1-run=" + token
    network = "sanzo-w1-net-" + token
    postgres = "sanzo-w1-pg-" + token
    app = "sanzo-w1-app-" + token
    cli = "sanzo-w1-cli-" + token
    dsn = f"postgresql://sanzo_w1@{postgres}:5432/sanzo_w1_test"
    network_created = False
    created_containers: list[str] = []
    results = {"images": identities, "network": network, "postgres": postgres,
               "test_image": image_name(), "dsn": dsn, "synthetic_only": True,
               "postgres_tests": "NOT_RUN", "cli": "NOT_RUN", "cleanup": "NOT_RUN"}
    try:
        audit.docker_run("create_network", "network", "create", "--internal", "--label", label, network)
        network_created = True
        audit.docker_run(
            "create_postgres", "create", "--pull=never", "--name", postgres, "--label", label,
            "--network", network, "--tmpfs", "/var/lib/postgresql/data:rw,nosuid,size=512m",
            "-e", "POSTGRES_USER=sanzo_w1", "-e", "POSTGRES_DB=sanzo_w1_test",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", PG_IMAGE,
        )
        created_containers.append(postgres)
        audit.docker_run("start_postgres", "start", postgres)
        for attempt in range(1, 31):
            ready = audit.docker_run(
                f"postgres_ready_{attempt:02d}", "exec", postgres,
                "pg_isready", "-h", "127.0.0.1", "-U", "sanzo_w1", "-d", "sanzo_w1_test",
                required=False, timeout=15,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise StepFailed("PostgreSQL did not become ready")
        # Inspect only resources created by this invocation, recording no host ports.
        audit.docker_run("network_isolation", "network", "inspect", network, "--format",
                         "{{json .Internal}} {{json .Containers}}")
        audit.docker_run("postgres_ports_mounts", "inspect", postgres, "--format",
                         "{{json .HostConfig.PortBindings}} {{json .HostConfig.Tmpfs}} {{json .Mounts}}")

        def app_container(name: str, command: list[str], step: str) -> subprocess.CompletedProcess:
            audit.docker_run(
                step + "_create", "create", "--pull=never", "--name", name, "--label", label,
                "--network", network, "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m",
                "--mount", f"type=bind,source={PROJECT},target=/workspace,readonly",
                "-e", "SANZO_TEST_DSN=" + dsn, image_name(), *command,
            )
            created_containers.append(name)
            attached = audit.docker_run(step, "start", "--attach", name, required=False)
            state = audit.docker_run(step + "_exit_state", "inspect", name, "--format",
                                     "{{json .State}}")
            actual = json.loads(state.stdout)
            results[step + "_container_state"] = actual
            actual_code = int(actual["ExitCode"])
            if actual.get("Running") or actual.get("Status") != "exited":
                raise StepFailed(step + " did not exit normally")
            # The container state is the application exit code; preserve attach errors too.
            code = actual_code if actual_code != 0 else attached.returncode
            return subprocess.CompletedProcess(attached.args, code, attached.stdout, attached.stderr)

        test = app_container(app, [
            "python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_postgres.py", "-v",
        ], "postgres_tests")
        test_text = test.stdout + "\n" + test.stderr
        test_count = re.search(r"Ran (\d+) tests? in", test_text)
        results["postgres_test_count"] = int(test_count.group(1)) if test_count else 0
        skip_count = re.search(r"skipped=(\d+)", test_text)
        results["postgres_skip_count"] = int(skip_count.group(1)) if skip_count else 0
        valid_test_run = results["postgres_test_count"] > 0 and results["postgres_skip_count"] == 0
        results["postgres_tests"] = "PASS" if test.returncode == 0 and valid_test_run else "FAIL"
        cli_result = app_container(cli, [
            "python", "-m", "sanzo_core.cli", "--dsn", dsn,
        ], "synthetic_cli")
        results["cli"] = "PASS" if cli_result.returncode == 0 else "FAIL"
    finally:
        if postgres in created_containers:
            audit.docker_run("postgres_server_log", "logs", postgres, required=False)
        cleanup_ok = True
        # Names come only from successful creates above. No prune or broad selectors.
        for name in reversed(created_containers):
            cleaned = audit.docker_run("cleanup_" + name.split("-")[2], "rm", "--force", name, required=False)
            cleanup_ok = cleanup_ok and cleaned.returncode == 0
        if network_created:
            cleaned = audit.docker_run("cleanup_network", "network", "rm", network, required=False)
            cleanup_ok = cleanup_ok and cleaned.returncode == 0
        results["cleanup"] = "PASS" if cleanup_ok else "FAIL"
        (audit.logs / f"pg_{audit.run_id}_execution.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    evidence = args.evidence_dir or Path(tempfile.mkdtemp(prefix="sanzo_w1_validation_"))
    audit = Audit(evidence.resolve())
    output = {
        "started_at": now(), "project": str(PROJECT), "phase": "W1-INDEPENDENT",
        "mode": "prepare-only" if args.prepare_only else "run",
        "status": "FAIL", "w0_sdk_contract": "INCOMPLETE",
        "commands": str(audit.commands_path), "synthetic_only": True,
    }
    code = 1
    try:
        output.update(prepare(audit) if args.prepare_only else execute_tests(audit))
        passed = args.prepare_only or all(output.get(k) == "PASS" for k in ("postgres_tests", "cli", "cleanup"))
        output["status"] = "SUCCESS" if passed else "FAIL"
        code = 0 if passed else 1
    except (StepFailed, OSError, ValueError) as exc:
        output["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    finally:
        output["finished_at"] = now()
        output["exit_code"] = code
        audit.result_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print("RESULT: " + str(audit.result_path), flush=True)
        print("This is synthetic business-core validation, not a real file / AI / Web Demo.", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
