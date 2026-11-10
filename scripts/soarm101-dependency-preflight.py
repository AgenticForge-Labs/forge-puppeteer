#!/usr/bin/env python3
"""Check SO-ARM demo dependencies without contacting a broker or hardware."""
import importlib
import importlib.metadata
import shutil
import sys

requirements = {
    "soarm101_motion.choreography": ("JointTarget", "JointChoreography", "JointFrame", "ChoreographyPolicy"),
    "soarm101_motion.broker": ("load_trusted_choreography_catalog",),
    "forge_puppeteer.mcp.ensemble_v4": ("load_v4", "build_v4"),
    "mcp": ("ClientSession",),
}
failed = False
for module_name, symbols in requirements.items():
    try:
        module = importlib.import_module(module_name)
        missing = [name for name in symbols if not hasattr(module, name)]
        if missing:
            raise ImportError("missing " + ", ".join(missing))
        print(f"OK {module_name}: {module.__file__}")
    except ImportError as exc:
        failed = True
        print(f"MISSING {module_name}: {exc}", file=sys.stderr)
for executable in ("forge-puppeteer-ensemble", "soarm101-sim-daemon", "soarm101-physical-daemon"):
    path = shutil.which(executable)
    print(f"{'OK' if path else 'MISSING'} {executable}: {path or 'not on PATH'}")
    failed |= not bool(path)
for package in ("forge-puppeteer", "soarm101-motion-sdk", "mcp"):
    try:
        print(f"{package} version: {importlib.metadata.version(package)}")
    except importlib.metadata.PackageNotFoundError:
        failed = True
if failed:
    print("Install matching local SDK and Puppeteer wheels with the Puppeteer [mcp] extra; rerun in that environment.", file=sys.stderr)
    sys.exit(1)
print("Dependency check only: no broker connection, authority, calibration, or physical readiness checked.")
