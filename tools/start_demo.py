#!/usr/bin/env python3
"""Start the existing SANZO Demo with a verified fixed SDK wheel or explicit Mock reader.

Usage: .venv/bin/python tools/start_demo.py [start|stop|status|test]
Only this project's labeled Docker resources are managed. stop preserves data.
No existing environment files, SDK repositories or host databases are read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

PROJECT = Path(__file__).resolve().parents[1]
OWNER_KEY = "org.sanzo.demo-owner"
OWNER = hashlib.sha256(str(PROJECT).encode()).hexdigest()[:16]
PREFIX = "sanzo-demo-" + OWNER
NETWORK = PREFIX + "-net"
WEB_NETWORK = PREFIX + "-web-net"
PG = PREFIX + "-pg"
APP = PREFIX + "-web"
PG_VOLUME = PREFIX + "-postgres"
UPLOAD_VOLUME = PREFIX + "-uploads"
PG_IMAGE = "postgres:16"
PYTHON_IMAGE = "python:3.12.13-slim"
SDK_WHEEL = PROJECT / "document_processing_sdk-1.8.0-py3-none-any.whl"
SDK_WHEEL_SHA256 = "781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0"


def verified_sdk_wheel():
    """Read only the explicitly approved artifact, never a SDK checkout."""
    if SDK_WHEEL.is_symlink() or not SDK_WHEEL.is_file():
        raise ValueError("FIXED_SDK_WHEEL_UNAVAILABLE")
    if hashlib.sha256(SDK_WHEEL.read_bytes()).hexdigest() != SDK_WHEEL_SHA256:
        raise ValueError("FIXED_SDK_WHEEL_SHA256_MISMATCH")
    return SDK_WHEEL


BROWSER_URL = "http://127.0.0.1:8765"
SOURCE_SUFFIXES = {".py", ".sql", ".html", ".js", ".css", ".svg"}


class Runtime:
    def __init__(self, evidence: Path, *, reader_mode="REAL_SDK", authorization=None):
        self.reader_mode = reader_mode
        self.authorization = authorization
        self.evidence = evidence
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.seq = 0
        self.records = []
        self.docker = shutil.which("docker") or "/usr/local/bin/docker"

    def command(self, label, argv, *, required=True, timeout=600):
        self.seq += 1
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = subprocess.run(argv, cwd=PROJECT, text=True, capture_output=True,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            result = subprocess.CompletedProcess(argv, 124, decoded(exc.stdout),
                                                  decoded(exc.stderr) + "\nTIMEOUT")
        except OSError as exc:
            result = subprocess.CompletedProcess(argv, 127, "", str(exc))
        log = self.evidence / f"{self.run_id}_{self.seq:03d}_{label}.txt"
        log.write_text("COMMAND: " + json.dumps(argv) + "\nEXIT_CODE: " + str(result.returncode)
                       + "\nSTDOUT:\n" + result.stdout + "\nSTDERR:\n" + result.stderr)
        self.records.append({"command": argv, "label": label, "exit_code": result.returncode,
                             "started_at": started, "log": str(log),
                             "sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
        (self.evidence / (self.run_id + "_commands.json")).write_text(
            json.dumps(self.records, indent=2))
        print(f"{label}: exit={result.returncode}", flush=True)
        if result.returncode and required:
            raise RuntimeError(f"{label} failed ({result.returncode}); see {log}")
        return result

    def dk(self, label, *args, **kwargs):
        return self.command(label, [self.docker, *args], **kwargs)

    def owned(self, kind, name):
        # Never dump Docker Config.Env: an explicitly approved backend key
        # may be present. Ownership checks need only these nonsecret fields.
        inspect_args = ["inspect", name]
        if kind == "container":
            inspect_args += ["--format",
                '[{"Config":{"Labels":{{json .Config.Labels}}},"State":{{json .State}},'
                '"HostConfig":{"PortBindings":{{json .HostConfig.PortBindings}}}}]']
        result = self.dk("inspect_" + name, kind, *inspect_args, required=False)
        if result.returncode:
            if "no such" in result.stderr.lower() or "not found" in result.stderr.lower():
                return None
            raise RuntimeError(f"Cannot safely inspect {kind} {name}")
        data = json.loads(result.stdout)[0]
        labels = data.get("Config", {}).get("Labels") if kind == "container" else data.get("Labels")
        if (labels or {}).get(OWNER_KEY) != OWNER:
            raise RuntimeError(f"Refusing to manage differently owned resource: {name}")
        return data

    def remove_owned_container(self, name):
        if self.owned("container", name):
            self.dk("remove_" + name, "rm", "--force", name)

    def build(self):
        members = {"Dockerfile": PROJECT / "backend/Dockerfile.demo",
                   "requirements-demo.lock": PROJECT / "requirements-demo.lock",
                   "sdk/" + SDK_WHEEL.name: verified_sdk_wheel()}
        for root, dest in [(PROJECT / "backend", "backend"),
                           (PROJECT / "sanzo_cost_table_framework_v1_1_0/reference_core", "reference_core")]:
            for source in sorted(root.rglob("*")):
                if source.is_file() and not source.is_symlink() and source.suffix in SOURCE_SUFFIXES:
                    if "__pycache__" not in source.parts:
                        members[dest + "/" + source.relative_to(root).as_posix()] = source
        fixture_root = PROJECT / "demo_materials/fixtures"
        for source in sorted(fixture_root.glob("*.json")):
            if source.is_symlink():
                raise ValueError("Fixture symlinks are not permitted")
            members["demo_materials/fixtures/" + source.name] = source
        fingerprints = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for name, path in members.items()}
        digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
        image = "sanzo-cost-table-demo:" + digest[:20]
        (self.evidence / (self.run_id + "_build_members.json")).write_text(
            json.dumps(fingerprints, indent=2))
        self.dk("docker_version", "version", "--format", "{{json .Server.Version}}")
        for base in [PG_IMAGE, PYTHON_IMAGE]:
            if self.dk("image_available", "image", "inspect", base, required=False).returncode:
                self.dk("pull_required_image", "pull", base)
        if self.dk("demo_image_available", "image", "inspect", image, required=False).returncode:
            with tempfile.TemporaryDirectory(prefix="sanzo_demo_build_") as folder:
                context = Path(folder)
                for name, source in members.items():
                    target = context / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                self.dk("build_demo", "build", "--pull=false", "--tag", image, str(context))
        self.dk("demo_image_identity", "image", "inspect", image, "--format",
                "{{json .Id}} {{json .RepoDigests}} {{json .Architecture}}")
        return image

    def wait_pg(self, name):
        for attempt in range(40):
            check = self.dk("postgres_ready", "exec", name, "pg_isready", "-h", "127.0.0.1",
                            "-U", "sanzo_demo", "-d", "sanzo_demo", required=False, timeout=10)
            if check.returncode == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("Project PostgreSQL did not become ready")

    def start(self):
        image = self.build()
        net = self.owned("network", NETWORK)
        if net is None:
            self.dk("create_network", "network", "create", "--internal",
                    "--label", OWNER_KEY + "=" + OWNER, NETWORK)
        elif not net.get("Internal"):
            raise RuntimeError("Project network unexpectedly permits external access")
        # Docker Desktop does not publish ports on an internal-only network.
        # PostgreSQL remains internal; only the web container joins this bridge.
        if self.owned("network", WEB_NETWORK) is None:
            self.dk("create_web_network", "network", "create",
                    "--label", OWNER_KEY + "=" + OWNER, WEB_NETWORK)
        for volume in [PG_VOLUME, UPLOAD_VOLUME]:
            if self.owned("volume", volume) is None:
                self.dk("create_volume", "volume", "create", "--label", OWNER_KEY + "=" + OWNER, volume)
        existing = self.owned("container", PG)
        if existing is None:
            self.dk("create_postgres", "create", "--pull=never", "--name", PG,
                    "--label", OWNER_KEY + "=" + OWNER, "--network", NETWORK,
                    "--mount", f"type=volume,source={PG_VOLUME},target=/var/lib/postgresql/data",
                    "-e", "POSTGRES_USER=sanzo_demo", "-e", "POSTGRES_DB=sanzo_demo",
                    "-e", "POSTGRES_HOST_AUTH_METHOD=trust", PG_IMAGE)
        elif existing["HostConfig"].get("PortBindings"):
            raise RuntimeError("Refusing PostgreSQL container with published ports")
        self.dk("start_postgres", "start", PG)
        self.wait_pg(PG)
        # Rebuild/recreate only our web process; volume and database history survive.
        self.remove_owned_container(APP)
        configuration_args = ["-e", "SANZO_READER_MODE=" + self.reader_mode]
        if self.authorization is not None:
            config = self.authorization.public_config()
            source = config["credential_source"]
            if source["kind"] == "environment":
                # Docker forwards this explicitly named variable from its backend
                # parent environment. argv/logs contain the name, never its value.
                configuration_args += ["-e", source["name"]]
            else:
                key_path = Path(source["path"])
                if not key_path.is_file():
                    raise ValueError("Designated credential file is unavailable")
                target = "/run/secrets/sanzo_openai_key"
                configuration_args += ["--mount", f"type=bind,source={key_path},target={target},readonly"]
                config["credential_source"] = {"kind": "file", "path": target}
            configuration_args += ["-e", "SANZO_AI_AUTHORIZATION_JSON=" + json.dumps(config)]
        self.dk("create_web", "create", "--pull=never", "--name", APP,
                "--label", OWNER_KEY + "=" + OWNER, "--network", WEB_NETWORK,
                "--publish", "127.0.0.1:8765:8765", "--read-only", "--cap-drop=ALL",
                "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,size=64m",
                "--mount", f"type=volume,source={UPLOAD_VOLUME},target=/data/uploads",
                "-e", f"SANZO_DATABASE_DSN=postgresql://sanzo_demo@{PG}:5432/sanzo_demo",
                "-e", "SANZO_UPLOAD_DIR=/data/uploads", *configuration_args, image)
        self.dk("connect_web_to_database", "network", "connect", NETWORK, APP)
        self.dk("start_web", "start", APP)
        for attempt in range(60):
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(BROWSER_URL + "/api/status", timeout=2) as response:
                    if response.status == 200:
                        self.dk("network_isolation", "network", "inspect", NETWORK, "--format",
                                "{{json .Internal}}")
                        self.dk("postgres_ports", "inspect", PG, "--format",
                                "{{json .HostConfig.PortBindings}}")
                        self.dk("web_ports", "inspect", APP, "--format",
                                "{{json .HostConfig.PortBindings}}")
                        print("BROWSER_URL: " + BROWSER_URL, flush=True)
                        return {"status": "RUNNING", "browser_url": BROWSER_URL,
                                "reader_mode": self.reader_mode, "sdk": "NOT_RUN",
                                "ai": "CONFIGURED_NOT_CALLED" if self.authorization else "NOT_RUN",
                                "database": "PostgreSQL"}
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        self.dk("web_failure_log", "logs", APP, required=False)
        raise RuntimeError("Local web process did not become healthy")

    def stop(self):
        for name in [APP, PG]:
            if self.owned("container", name):
                self.dk("stop_" + name, "stop", "--time", "10", name)
        return {"status": "STOPPED", "data": "PRESERVED"}

    def status(self):
        states = {}
        for name in [APP, PG]:
            data = self.owned("container", name)
            states[name] = data["State"]["Status"] if data else "ABSENT"
        return {"containers": states, "browser_url": BROWSER_URL}

    def test(self):
        image = self.build()
        token = uuid.uuid4().hex[:12]
        network, pg, runner = [PREFIX + "-test-" + token + suffix for suffix in ["-net", "-pg", "-runner"]]
        created = []
        net_created = False
        outcome = {"status": "FAIL", "real_sdk_ai_rounds": "NOT_RUN"}
        try:
            self.dk("test_network", "network", "create", "--internal",
                    "--label", OWNER_KEY + "=" + OWNER, network)
            net_created = True
            self.dk("test_postgres_create", "create", "--pull=never", "--name", pg,
                    "--label", OWNER_KEY + "=" + OWNER, "--network", network,
                    "--tmpfs", "/var/lib/postgresql/data:rw,nosuid,size=512m",
                    "-e", "POSTGRES_USER=sanzo_demo", "-e", "POSTGRES_DB=sanzo_demo",
                    "-e", "POSTGRES_HOST_AUTH_METHOD=trust", PG_IMAGE)
            created.append(pg)
            self.dk("test_postgres_start", "start", pg)
            self.wait_pg(pg)
            self.dk("test_runner_create", "create", "--pull=never", "--name", runner,
                    "--label", OWNER_KEY + "=" + OWNER, "--network", network, "--read-only",
                    "--tmpfs", "/tmp:rw,nosuid,size=128m", "--cap-drop=ALL",
                    "--security-opt", "no-new-privileges",
                    "--mount", f"type=bind,source={PROJECT / 'tests'},target=/workspace/tests,readonly",
                    "--mount", f"type=bind,source={PROJECT / 'demo_materials/samples'},target=/workspace/demo_materials/samples,readonly",
                    "-e", f"SANZO_TEST_DSN=postgresql://sanzo_demo@{pg}:5432/sanzo_demo",
                    image, "python", "-B", "-m", "unittest", "discover", "-s", "tests",
                    "-p", "test_demo*.py", "-v")
            created.append(runner)
            attached = self.dk("demo_tests", "start", "--attach", runner, required=False)
            actual = self.dk("test_exit_code", "inspect", runner, "--format", "{{json .State}}")
            state = json.loads(actual.stdout)
            if state["Running"] or state["Status"] != "exited":
                raise RuntimeError("Test container has not terminated normally")
            code = state["ExitCode"] or attached.returncode
            import re
            match = re.search(r"Ran (\d+) tests?", attached.stdout + attached.stderr)
            skipped = "skipped=" in attached.stdout + attached.stderr
            outcome.update(exit_code=code, tests=int(match.group(1)) if match else 0,
                           skipped=skipped, database="REAL_POSTGRESQL")
            outcome["status"] = "PASS" if code == 0 and match and not skipped else "FAIL"
            return outcome
        finally:
            if pg in created:
                self.dk("test_pg_log", "logs", pg, required=False)
            for name in reversed(created):
                self.remove_owned_container(name)
            if net_created and self.owned("network", network):
                self.dk("test_network_cleanup", "network", "rm", network)
            (self.evidence / (self.run_id + "_test_result.json")).write_text(json.dumps(outcome, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["start", "stop", "status", "test"], default="start")
    parser.add_argument("--evidence-dir", type=Path, default=PROJECT / ".demo-runtime" / "logs")
    parser.add_argument("--reader-mode", choices=["REAL_SDK", "MOCK_SDK"], default="REAL_SDK")
    parser.add_argument("--ai-authorization", type=Path,
                        help="Explicit nonsecret authorization JSON; never a key or .env")
    args = parser.parse_args()
    authorization = None
    if args.ai_authorization is not None:
        if args.action != "start":
            parser.error("AI authorization is only accepted for start")
        sys.path[:0] = [str(PROJECT / "backend"), str(PROJECT / "sanzo_cost_table_framework_v1_1_0/reference_core")]
        from sanzo_demo.configuration import AIAuthorization
        with args.ai_authorization.open(encoding="utf-8") as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise ValueError("Authorization configuration exceeds limit")
        authorization = AIAuthorization.parse(raw)
    runtime = Runtime(args.evidence_dir.resolve(), reader_mode=args.reader_mode, authorization=authorization)
    output = {"status": "FAIL", "action": args.action}
    code = 1
    try:
        output = getattr(runtime, args.action)()
        code = 0 if output.get("status") != "FAIL" else 1
    except Exception as exc:
        output["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    finally:
        output["exit_code"] = code
        target = runtime.evidence / (runtime.run_id + "_result.json")
        target.write_text(json.dumps(output, indent=2))
        print(json.dumps(output, indent=2))
        print("EVIDENCE: " + str(target))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
