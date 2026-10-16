from __future__ import annotations

import os
import sys


def main() -> None:
    # This process receives only our requirements.in, never an uploaded pyproject/setup file.
    if len(sys.argv) != 6 or os.name != "posix":
        raise SystemExit(2)
    import resource

    limits = [
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_FSIZE, 8 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_CORE, 0),
    ]
    if sys.platform.startswith("linux"):
        limits.append((resource.RLIMIT_AS, 768 * 1024 * 1024))
    for kind, configured in limits:
        _, hard = resource.getrlimit(kind)
        limit = configured if hard == resource.RLIM_INFINITY else min(configured, hard)
        resource.setrlimit(kind, (limit, limit))
    executable, source, output, python, platform = sys.argv[1:]
    command = [
        executable,
        "--no-config",
        "pip",
        "compile",
        source,
        "--output-file",
        output,
        "--python-version",
        python,
        "--python-platform",
        platform,
        "--only-binary",
        ":all:",
        "--no-sources",
        "--generate-hashes",
        "--no-header",
        "--no-annotate",
        "--no-progress",
        "--no-python-downloads",
        "--index-url",
        "https://pypi.org/simple",
        "--keyring-provider",
        "disabled",
    ]
    os.execve(executable, command, os.environ)


if __name__ == "__main__":
    main()
