from __future__ import annotations

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version


def select_python_target(constraint: str | None, preferred: str = "3.11") -> str | None:
    """Choose a supported target, without claiming to recover an author interpreter."""
    specifiers = SpecifierSet(constraint or "")
    minors = tuple(dict.fromkeys((preferred, "3.11", "3.12", "3.13", "3.14", "3.10", "3.9", "3.8")))
    for minor in minors:
        version = Version(minor)
        if version.major != 3 or not 8 <= version.minor <= 14:
            continue
        profile = f"3.{version.minor}"
        patches = {0, 1, 999}
        for specifier in specifiers:
            try:
                bound = Version(specifier.version.removesuffix(".*"))
            except InvalidVersion:
                continue
            if bound.release[:2] == version.release[:2]:
                patch = bound.release[2] if len(bound.release) > 2 else 0
                patches.update(
                    value for value in (patch - 1, patch, patch + 1) if 0 <= value <= 999
                )
        allowed = [patch for patch in sorted(patches) if specifiers.contains(f"{profile}.{patch}")]
        if len(allowed) == len(patches):
            return profile
        if allowed:
            return f"{profile}.{allowed[0]}"
    return None
