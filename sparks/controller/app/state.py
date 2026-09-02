"""Read-only views: residency, profiles from the git checkout, endpoint health."""

import asyncio
from pathlib import Path

import httpx
import yaml


def read_resident(state_dir: Path) -> str:
    resident_file = state_dir / "resident"
    try:
        return resident_file.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def read_switch_log_tail(state_dir: Path, lines: int = 10) -> list[str]:
    log_file = state_dir / "switch.log"
    try:
        return log_file.read_text().splitlines()[-lines:]
    except OSError:
        return []


def read_profiles(ansible_dir: Path) -> dict[str, dict]:
    profiles = {}
    for profile_file in sorted((ansible_dir / "profiles").glob("*.yaml")):
        profiles[profile_file.stem] = yaml.safe_load(profile_file.read_text())
    return profiles


def read_default_profile(ansible_dir: Path) -> str | None:
    group_vars = ansible_dir / "group_vars" / "all.yaml"
    try:
        return yaml.safe_load(group_vars.read_text()).get("default_profile")
    except OSError:
        return None


async def _probe(client: httpx.AsyncClient, url: str) -> dict:
    try:
        response = await client.get(url)
        return {"ok": response.status_code == 200, "status": response.status_code}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__}


async def probe_endpoints(profiles: dict[str, dict]) -> dict[str, dict]:
    urls = {"stable": "http://localhost:80/v1/models"}
    for profile in profiles.values():
        port = profile.get("serve_port")
        if port:
            urls[f"port_{port}"] = f"http://localhost:{port}/health"
    async with httpx.AsyncClient(timeout=3.0) as client:
        results = await asyncio.gather(*(_probe(client, url) for url in urls.values()))
    return dict(zip(urls, results))
