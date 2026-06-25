"""Aurora Deep Link — aurora:// URL scheme router.

Parses and routes aurora:// URLs to their target destinations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, parse_qs


@dataclass
class DeepLinkResult:
    """Parsed result from a deep link URL."""
    scheme: str
    host: str
    path: str = ""
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "path": self.path,
            "params": self.params,
        }


@dataclass
class RoutedAction:
    """Action routing result from a deep link."""
    action: str          # e.g. "navigate", "open-settings", "open-plugin"
    target: str          # e.g. "/settings/general", "/plugin/marketplace"
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target": self.target,
            "params": self.params,
        }


class DeepLinkRouter:
    """Parses aurora:// scheme URLs and routes them to known handlers."""

    SCHEME = "aurora"

    def parse(self, url: str) -> DeepLinkResult:
        """Parse an aurora:// URL into its components.

        Example:
            aurora://settings/general -> scheme=aurora, host=settings, path=general
            aurora://plugin/marketplace -> scheme=aurora, host=plugin, path=marketplace
            aurora://thread/abc123 -> scheme=aurora, host=thread, path=abc123
        """
        if "://" not in url:
            url = f"{self.SCHEME}://{url}"

        parsed = urlparse(url)
        scheme = parsed.scheme or self.SCHEME

        if parsed.netloc:
            host = parsed.netloc
            path = parsed.path.lstrip("/")
        else:
            raw_path = parsed.path.lstrip("/")
            parts = raw_path.split("/", 1)
            host = parts[0] if parts else ""
            path = parts[1] if len(parts) > 1 else ""

        params: dict[str, str] = {}
        for k, v_list in parse_qs(parsed.query).items():
            params[k] = v_list[0] if v_list else ""

        return DeepLinkResult(scheme=scheme, host=host, path=path, params=params)

    def route(self, url: str) -> RoutedAction:
        """Route a parsed URL to the appropriate action.

        Mapping:
            aurora://settings[/section] -> open-settings
            aurora://skills            -> open-skills
            aurora://connector/oauth_callback -> oauth-callback
            aurora://automations       -> open-automations
            aurora://plugin/<id>       -> open-plugin
            aurora://thread/<id>       -> open-thread
        """
        result = self.parse(url)
        host = result.host
        path = result.path
        params = result.params

        route_map = {
            "settings": lambda: RoutedAction(
                action="open-settings",
                target=f"/settings/{path}" if path else "/settings",
                params=params,
            ),
            "skills": lambda: RoutedAction(
                action="open-skills", target="/skills", params=params,
            ),
            "connector": lambda: RoutedAction(
                action="oauth-callback",
                target="/connector/oauth_callback" if "oauth" in path else f"/connector/{path}",
                params=params,
            ),
            "automations": lambda: RoutedAction(
                action="open-automations", target="/automations", params=params,
            ),
            "plugin": lambda: RoutedAction(
                action="open-plugin",
                target=f"/plugin/{path}" if path else "/plugins",
                params=params,
            ),
            "thread": lambda: RoutedAction(
                action="open-thread",
                target=f"/thread/{path}" if path else "/threads",
                params=params,
            ),
        }

        handler = route_map.get(host)
        if handler:
            return handler()

        return RoutedAction(
            action="navigate",
            target=f"/{host}/{path}".rstrip("/") if path else f"/{host}",
            params=params,
        )

    def validate(self, url: str) -> bool:
        """Check whether a URL is a valid aurora:// deep link."""
        result = self.parse(url)
        if result.scheme != self.SCHEME:
            return False
        if not result.host:
            return False
        return True


# Singleton
deep_link_router = DeepLinkRouter()
