# Codex guidance

- Keep Stage and performer/Puppeteer responsibilities separate.
- Contracts are semantic; hardware-specific coordinates, motor commands and vendor SDK details belong in adapters.
- Physical execution must fail closed around safety/calibration/capability errors.
- Preserve stable shot/take IDs and provenance for Forge Studios and Forge Researcher.
- The repository must remain independently useful for live robot shows without Forge Worlds.
