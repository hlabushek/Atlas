"""Register one already-created read-only Companion certificate on the live Atlas."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import httpx

from atlas.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fingerprint", help="SHA-256 fingerprint of the device certificate")
    parser.add_argument("--name", default="JetGame Windows Companion")
    parser.add_argument("--root-alias", default="atlas_project")
    parser.add_argument("--days", type=int, default=360)
    parser.add_argument("--enable-write", action="store_true", help="Enable approved create_text/launch_file operations")
    args = parser.parse_args()
    if not 30 <= args.days <= 360:
        parser.error("--days must be between 30 and 360")
    if len(args.fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in args.fingerprint):
        parser.error("fingerprint must be a lowercase SHA-256 hex value")

    expiry = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
    with httpx.Client(
        base_url="https://atlas.prism-news.xyz",
        timeout=20,
        follow_redirects=False,
        trust_env=False,
        headers={"X-Atlas-Client": "web"},
    ) as client:
        login = client.post("/api/login", json={"password": settings().owner_password})
        login.raise_for_status()
        devices = client.get("/api/devices")
        devices.raise_for_status()
        matches = [item for item in devices.json() if item["fingerprint"] == args.fingerprint]
        if len(matches) > 1:
            raise RuntimeError("Duplicate device fingerprint found")
        if matches:
            device_id = matches[0]["id"]
        else:
            added = client.post("/api/devices", json={
                "name": args.name,
                "fingerprint": args.fingerprint,
                "expires_at": expiry,
            })
            added.raise_for_status()
            device_id = added.json()["id"]

        operations = ["read_text", "list"] + (["create_text", "launch_file"] if args.enable_write else [])
        policy = client.put(f"/api/devices/{device_id}", json={
            "enabled": True,
            "roots": [args.root_alias],
            "operations": operations,
            "expires_at": expiry,
        })
        policy.raise_for_status()

        tools = client.get("/api/tools")
        tools.raise_for_status()
        desktop_read = next((item for item in tools.json() if item["name"] == "desktop.read"), None)
        if desktop_read is None:
            raise RuntimeError("desktop.read tool is not available")
        if desktop_read["blocked"]:
            permission = client.put("/api/tools/desktop.read/permission", json={"allowed": True})
            permission.raise_for_status()
        if args.enable_write:
            desktop_write = next((item for item in tools.json() if item["name"] == "desktop.write"), None)
            if desktop_write is None:
                raise RuntimeError("desktop.write tool is not available")
            if desktop_write["blocked"]:
                permission = client.put("/api/tools/desktop.write/permission", json={"allowed": True})
                permission.raise_for_status()

    print("Companion registered for:", ", ".join(operations))
    print("Device ID:", device_id)


if __name__ == "__main__":
    main()
