"""Smoke an installed Reachy catalog launcher against staged snapshot files."""

import argparse
import json
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def exercise(asset_root: Path, manifest: Path, expected_assets: int) -> None:
    with tempfile.TemporaryDirectory(prefix="reachy-catalog-smoke-") as directory:
        config = Path(directory) / "server.json"
        config.write_text(json.dumps({
            "backend": "reachy_catalog",
            "device": "reachy",
            "instance_id": "reachy-staged-catalog",
            "name": "Reachy staged catalog smoke",
            "allowed_tools": ["device_info", "device_state", "list_assets", "action_lookup"],
            "asset_root": str(asset_root),
            "manifest": str(manifest),
        }), encoding="utf-8")
        params = StdioServerParameters(
            command="forge-puppeteer-mcp", args=["--config", str(config)],
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            info = (await session.call_tool("device_info", {})).structured_content
            state = (await session.call_tool("device_state", {})).structured_content
            assert info["sdk_version"] == "1.10.0rc5", info
            assert info["connected"] is False and info["ready"] is False, info
            assert state == {
                "backend": "reachy_catalog", "connected": False, "ready": False,
            }, state

            assets = []
            offset = 0
            while True:
                page = (await session.call_tool(
                    "list_assets", {"offset": offset, "limit": 100},
                )).structured_content
                assets.extend(page["assets"])
                next_offset = page["next_offset"]
                if next_offset is None:
                    break
                assert next_offset > offset, page
                offset = next_offset
            assert len(assets) == expected_assets, len(assets)
            assert len({asset["id"] for asset in assets}) == expected_assets
            assert all(asset["revision"].startswith("sha256:") for asset in assets)

            denied = await session.call_tool("play_move", {
                "action_id": "catalog-smoke-no-motion",
                "caller_id": "catalog-smoke",
                "session_id": "catalog-smoke",
                "asset_id": assets[0]["id"],
                "revision": assets[0]["revision"],
            })
            assert denied.is_error, denied
            print(
                f"Reachy catalog stdio passed: SDK {info['sdk_version']}, "
                f"{len(assets)} unique assets, read-only motion denial"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset_root", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-assets", type=int, default=85)
    args = parser.parse_args()
    anyio.run(exercise, args.asset_root, args.manifest, args.expected_assets)


if __name__ == "__main__":
    main()
