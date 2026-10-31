"""Optional trusted-local MCP server integration."""

from .reachy import register_reachy_tools
from .server import DiscoveryProvider, Gateway, create_server
from .soarm101 import register_soarm101_tools

__all__ = ["DiscoveryProvider", "Gateway", "create_server", "register_reachy_tools", "register_soarm101_tools"]
