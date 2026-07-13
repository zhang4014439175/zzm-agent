from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class SandboxViolation(PermissionError):
    """A deterministic policy denial that may only be bypassed by controlled escalation."""


@dataclass(frozen=True)
class SandboxProfile:
    workspace_roots: tuple[Path, ...]
    deny_paths: tuple[Path, ...] = ()
    sensitive_names: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {".env", ".ssh", ".aws", ".gnupg", "id_rsa", "id_ed25519", "credentials"}
        )
    )
    allow_sensitive_read: bool = False
    network_enabled: bool = False
    network_allow_domains: tuple[str, ...] = ()
    network_deny_domains: tuple[str, ...] = ()
    allow_localhost: bool = False
    allow_private_network: bool = False

    @classmethod
    def from_environment(cls) -> "SandboxProfile":
        root_text = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT", os.getcwd())
        roots_text = os.environ.get("ZZM_AGENT_WORKSPACE_ROOTS", root_text)
        roots = tuple(
            Path(item).expanduser().resolve(strict=False)
            for item in roots_text.split(os.pathsep)
            if item.strip()
        )
        deny = tuple(
            Path(item).expanduser().resolve(strict=False)
            for item in os.environ.get("ZZM_AGENT_DENY_PATHS", "").split(os.pathsep)
            if item.strip()
        )
        return cls(
            workspace_roots=roots or (Path(root_text).resolve(strict=False),),
            deny_paths=deny,
            allow_sensitive_read=_env_bool("ZZM_AGENT_ALLOW_SENSITIVE_READ"),
            network_enabled=_env_bool("ZZM_AGENT_NETWORK_ENABLED"),
            network_allow_domains=_env_list("ZZM_AGENT_NETWORK_ALLOW_DOMAINS"),
            network_deny_domains=_env_list("ZZM_AGENT_NETWORK_DENY_DOMAINS"),
            allow_localhost=_env_bool("ZZM_AGENT_ALLOW_LOCALHOST"),
            allow_private_network=_env_bool("ZZM_AGENT_ALLOW_PRIVATE_NETWORK"),
        )

    def authorize_path(self, path: str | Path, *, access: str = "read") -> Path:
        if access not in {"read", "write"}:
            raise ValueError(f"Unsupported filesystem access: {access}")
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else self.workspace_roots[0] / raw
        candidate = candidate.resolve(strict=False)
        real = candidate.resolve(strict=True) if candidate.exists() else self._real_parent(candidate) / candidate.name
        root = next((item for item in self.workspace_roots if real.is_relative_to(item)), None)
        if root is None:
            raise SandboxViolation(f"Path escapes workspace root(s): {candidate}")
        if any(real == denied or real.is_relative_to(denied) for denied in self.deny_paths):
            raise SandboxViolation(f"Path is explicitly denied: {candidate}")
        if access == "read" and not self.allow_sensitive_read:
            names = {part.casefold() for part in real.parts}
            blocked = sorted(names & {item.casefold() for item in self.sensitive_names})
            if blocked:
                raise SandboxViolation(f"Sensitive path cannot be read: {blocked[0]}")
        return candidate

    def authorize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SandboxViolation("Only HTTP(S) network targets are supported")
        if not self.network_enabled:
            raise SandboxViolation("Network access is disabled by the sandbox profile")
        host = parsed.hostname.rstrip(".").casefold()
        if _domain_matches(host, self.network_deny_domains):
            raise SandboxViolation(f"Network domain is denied: {host}")
        if host == "localhost" and not self.allow_localhost:
            raise SandboxViolation("Localhost access is disabled")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if address.is_loopback and not self.allow_localhost:
                raise SandboxViolation("Loopback access is disabled")
            if (address.is_private or address.is_link_local) and not self.allow_private_network:
                raise SandboxViolation("Private network access is disabled")
        if self.network_allow_domains and not _domain_matches(host, self.network_allow_domains):
            raise SandboxViolation(f"Network domain is not allowlisted: {host}")
        return url

    def _real_parent(self, candidate: Path) -> Path:
        parent = candidate.parent
        suffix = []
        while not parent.exists() and parent != parent.parent:
            suffix.append(parent.name)
            parent = parent.parent
        real = parent.resolve(strict=True) if parent.exists() else parent.resolve(strict=False)
        for name in reversed(suffix):
            real /= name
        return real


def get_sandbox_profile() -> SandboxProfile:
    return SandboxProfile.from_environment()


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(item.strip().casefold() for item in os.environ.get(name, "").split(",") if item.strip())


def _domain_matches(host: str, rules: tuple[str, ...]) -> bool:
    return any(host == rule or host.endswith("." + rule) for rule in rules)
