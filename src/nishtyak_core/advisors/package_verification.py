from __future__ import annotations

import time
from dataclasses import asdict

from packaging.utils import canonicalize_name

from .. import package_safety
from ..providers import ProviderLookupError, PublishedDistributionInspector


class PackageVerificationError(ValueError):
    pass


class PublishedPackageVerifier:
    """Verify a proposed import provider using bounded published evidence.

    Metadata or a wheel directory can establish a top-level import provider.
    It does not establish author intent, a tested version, or successful execution.
    The inspector records whether it verified the whole artifact's hash.
    """

    def __init__(self, inspector: PublishedDistributionInspector | None = None):
        self.inspector = inspector if inspector is not None else PublishedDistributionInspector()

    def verify(self, module: str, package: str, *, timeout: float = 4) -> dict:
        if (
            not isinstance(module, str)
            or not module.isidentifier()
            or not isinstance(package, str)
            or not PublishedDistributionInspector.name_pattern.fullmatch(package)
            or not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= 15
        ):
            raise PackageVerificationError("package_mapping_invalid")
        deadline = time.monotonic() + timeout
        try:
            evidence = self.inspector.inspect(package, timeout=min(timeout, 10))
            if canonicalize_name(evidence.package) != canonicalize_name(
                package
            ) or evidence.method not in {"published_core_metadata", "published_wheel_directory"}:
                raise PackageVerificationError("package_mapping_unverified")
            if not evidence.provides(module):
                raise PackageVerificationError("package_does_not_provide_module")
            safety = (
                package_safety.get_package_safety_client()
                .check(
                    [package_safety.PackageVersion(evidence.package, evidence.observed_release)],
                    deadline=deadline,
                )
                .to_dict()
            )
            if safety["status"] != "checked":
                raise PackageVerificationError(
                    "package_known_malicious"
                    if safety["status"] == "blocked"
                    else "package_safety_unavailable"
                )
            return {
                **asdict(evidence),
                "module": module,
                "execution_verified": False,
                "package_safety": safety,
            }
        except PackageVerificationError:
            raise
        except ProviderLookupError as error:
            raise PackageVerificationError(str(error)) from error
        except Exception as error:
            raise PackageVerificationError("package_mapping_unverified") from error
