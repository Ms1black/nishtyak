#!/usr/bin/env python3
"""Run on the Linux worker host after installing the package and building its image.

No image is built or pulled. Only containers with this invocation's random names
are inspected or removed. Probe code is fixed here, never taken from a project.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from nishtyak_core.jobs import JobPolicy
from nishtyak_core.models import AnalysisOptions
from nishtyak_service.execution import DockerExecutor

LABEL = "io.nishtyak.runtime-verification"
PROBE = r"""
import errno
import json
import os
import socket
from pathlib import Path

def denied_write():
    try:
        with open("/app/nishtyak-runtime-probe", "x") as output:
            output.write("unexpected")
    except OSError as error:
        return error.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
    return False

def tmp_writable():
    path = Path("/tmp/nishtyak-runtime-probe")
    try:
        path.write_text("probe")
        return path.read_text() == "probe"
    finally:
        path.unlink(missing_ok=True)

def tcp_denied():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        try:
            connection.connect(("198.51.100.1", 443))
        except OSError:
            return True
    return False

def dns_denied():
    # A bounded UDP DNS probe to a documentation-only address, not a real resolver.
    query = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    query += b"\x0druntime-probe\x07invalid\x00\x00\x01\x00\x01"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(0.5)
        try:
            connection.sendto(query, ("192.0.2.53", 53))
            connection.recvfrom(512)
        except OSError:
            return True
    return False

def cgroup_values():
    root = Path("/sys/fs/cgroup")
    if (root / "cgroup.controllers").exists():
        memory = (root / "memory.max").read_text().strip()
        pids = (root / "pids.max").read_text().strip()
        quota, period = (root / "cpu.max").read_text().split()
    else:
        memory = (root / "memory/memory.limit_in_bytes").read_text().strip()
        pids = (root / "pids/pids.max").read_text().strip()
        quota = (root / "cpu/cpu.cfs_quota_us").read_text().strip()
        period = (root / "cpu/cpu.cfs_period_us").read_text().strip()
    return {
        "cgroup_memory": memory == "536870912",
        "cgroup_pids": pids == "32",
        "cgroup_cpu": quota not in ("max", "-1") and int(quota) == int(period),
    }

status = {}
for line in Path("/proc/self/status").read_text().splitlines():
    key, _, value = line.partition(":")
    status[key] = value.strip()
result = {
    "uid": os.getuid() == 65532 and os.getgid() == 65532,
    "root_write_denied": denied_write(),
    "tmp_writable": tmp_writable(),
    "tcp_denied": tcp_denied(),
    "dns_denied": dns_denied(),
    "loopback_only": {name for _, name in socket.if_nameindex()} == {"lo"},
    "no_new_privileges": status.get("NoNewPrivs") == "1",
    "capabilities_empty": int(status.get("CapEff", "-1"), 16) == 0,
}
try:
    result.update(cgroup_values())
except (OSError, ValueError):
    result.update(cgroup_memory=False, cgroup_pids=False, cgroup_cpu=False)
print(json.dumps(result))
"""


class VerificationFailed(Exception):
    pass


@dataclass(frozen=True)
class ProbeJob:
    job_id: str
    options: AnalysisOptions = field(default_factory=lambda: AnalysisOptions(python_version="3.11"))


class TrustedProbeExecutor(DockerExecutor):
    """Exercise the real executor timeout with fixed operator-owned sleep code."""

    def __init__(self, policy: JobPolicy, image: str, run_id: str):
        super().__init__(policy, image)
        self.run_id = run_id

    def _command(self, job_id: str, options: AnalysisOptions) -> list[str]:
        command = super()._command(job_id, options)
        prefix = command[: command.index("--archive") - 1]
        return prefix + [
            "--label",
            f"{LABEL}={self.run_id}",
            "--entrypoint",
            "python",
            self.image,
            "-I",
            "-c",
            "import time; time.sleep(120)",
        ]


class RuntimeVerifier:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.executor = DockerExecutor(
            image=os.environ.get("NISHTYAK_ANALYZER_IMAGE", "nishtyak-analyzer:0.1.0")
        )
        self.owned_names: set[str] = set()
        self.owned_jobs: list[tuple[DockerExecutor, str]] = []
        self.checks: dict[str, bool] = {}
        self.failures: list[str] = []

    def require(self, name: str, passed: bool) -> None:
        self.checks[name] = bool(passed)
        if not passed:
            self.failures.append(name)
            raise VerificationFailed(name)

    def docker(self, *arguments: str, timeout: float = 8) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )

    def run(self) -> dict:
        try:
            self.require("linux_host", sys.platform == "linux")
            self.require("docker_and_image_ready", self.executor.ready())
            with tempfile.TemporaryDirectory(prefix="nishtyak-runtime-check-") as directory:
                archive = Path(directory) / "trusted-fixture.zip"
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                    output.writestr("main.py", "import math\nprint(math.sqrt(16))\n")
                self.verify_analysis(archive)
                self.verify_profile()
                self.verify_timeout(archive)
        except VerificationFailed:
            pass
        except (KeyboardInterrupt, SystemExit):
            self.failures.append("interrupted")
        except Exception:
            # Exception details may contain host paths or operator configuration.
            self.failures.append("verification_error")
        finally:
            self.cleanup()
        return {
            "schema_version": 1,
            "status": "passed" if not self.failures else "failed",
            "checks": self.checks,
            "failures": self.failures,
        }

    def verify_analysis(self, archive: Path) -> None:
        job = ProbeJob(f"smoke-{self.run_id}-analysis")
        self.owned_names.add(self.executor.container_name(job.job_id))
        self.owned_jobs.append((self.executor, job.job_id))
        result = self.executor.analyze(job, archive, threading.Event())
        self.require(
            "synthetic_analysis", result.status == "succeeded" and result.report is not None
        )
        report = result.report
        self.require(
            "synthetic_report",
            report is not None
            and any(
                item.module == "math" and item.category == "stdlib" for item in report.dependencies
            )
            and {item.path for item in report.files} == {"main.py"},
        )

    def verify_profile(self) -> None:
        job = ProbeJob(f"smoke-{self.run_id}-profile")
        name = self.executor.container_name(job.job_id)
        self.owned_names.add(name)
        self.owned_jobs.append((self.executor, job.job_id))
        command = self.executor._command(job.job_id, job.options)
        prefix = command[1 : command.index("--archive") - 1]
        created = self.docker(
            *prefix,
            "--label",
            f"{LABEL}={self.run_id}",
            "--entrypoint",
            "python",
            self.executor.image,
            "-I",
            "-c",
            "import time; time.sleep(120)",
        )
        self.require("profile_created", created.returncode == 0)
        started = self.docker("start", name)
        self.require("profile_started", started.returncode == 0)
        inspected = self.docker("inspect", name)
        self.require("profile_inspected", inspected.returncode == 0)
        document = json.loads(inspected.stdout)[0]
        host = document["HostConfig"]
        security = host.get("SecurityOpt") or []
        profile = {
            "inspect_memory": host.get("Memory") == 512 * 1024**2,
            "inspect_swap": host.get("MemorySwap") == 512 * 1024**2,
            "inspect_cpu": host.get("NanoCpus") == 1_000_000_000,
            "inspect_pids": host.get("PidsLimit") == 32,
            "inspect_network": host.get("NetworkMode") == "none",
            "inspect_rootfs": host.get("ReadonlyRootfs") is True,
            "inspect_user": document["Config"].get("User") == "65532:65532",
            "inspect_no_new_privileges": any(
                value in {"no-new-privileges", "no-new-privileges:true"} for value in security
            ),
            "inspect_cap_drop": "ALL" in (host.get("CapDrop") or []),
            "inspect_unprivileged": host.get("Privileged") is False,
            "inspect_no_binds": not host.get("Binds"),
        }
        self.checks.update(profile)
        self.require("container_profile", all(profile.values()))
        probe = self.docker("exec", name, "python", "-I", "-c", PROBE, timeout=10)
        self.require("isolation_probe_completed", probe.returncode == 0)
        checks = json.loads(probe.stdout)
        expected = {
            "uid",
            "root_write_denied",
            "tmp_writable",
            "tcp_denied",
            "dns_denied",
            "loopback_only",
            "no_new_privileges",
            "capabilities_empty",
            "cgroup_memory",
            "cgroup_pids",
            "cgroup_cpu",
        }
        self.require(
            "isolation_probe_schema",
            isinstance(checks, dict)
            and set(checks) == expected
            and all(type(value) is bool for value in checks.values()),
        )
        self.checks.update(checks)
        self.require("container_isolation", all(checks.values()))
        self.require("profile_removed", self.executor.cleanup(job.job_id))

    def verify_timeout(self, archive: Path) -> None:
        policy = JobPolicy(wall_time_seconds=2)
        executor = TrustedProbeExecutor(policy, self.executor.image, self.run_id)
        job = ProbeJob(f"smoke-{self.run_id}-timeout")
        self.owned_names.add(executor.container_name(job.job_id))
        self.owned_jobs.append((executor, job.job_id))
        start = time.monotonic()
        result = executor.analyze(job, archive, threading.Event())
        elapsed = time.monotonic() - start
        self.require(
            "hard_timeout", result.status == "timed_out" and result.code == "WALL_TIME_LIMIT"
        )
        self.require("timeout_bounded", elapsed < policy.wall_time_seconds + 10)
        self.require("timeout_removed", executor.cleanup(job.job_id))

    def cleanup(self) -> None:
        if not self.owned_names:
            return
        for executor, job_id in self.owned_jobs:
            try:
                executor.cleanup(job_id)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            remaining = self.docker("ps", "--all", "--format", "{{.Names}}", timeout=5)
            passed = remaining.returncode == 0 and not (
                self.owned_names & set(remaining.stdout.decode("utf-8").splitlines())
            )
        except (OSError, subprocess.TimeoutExpired):
            passed = False
        self.checks["no_container_leaks"] = passed
        if not passed:
            self.failures.append("no_container_leaks")


def main() -> int:
    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    try:
        summary = RuntimeVerifier().run()
    except Exception:
        summary = {
            "schema_version": 1,
            "status": "failed",
            "checks": {},
            "failures": ["configuration_error"],
        }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
