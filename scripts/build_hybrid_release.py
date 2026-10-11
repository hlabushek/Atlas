"""Build a reviewed allowlist-only release; never package .env or local secrets."""

import hashlib
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".sessions" / "atlas-hybrid-20261011.tar.gz"


def files():
    for folder, suffix in [
        ("backend/atlas", ".py"),
        ("backend/migrations/versions", ".py"),
        ("frontend/src", ""),
        ("frontend/dist", ""),
        ("frontend/public", ""),
    ]:
        root = ROOT / folder
        for path in root.rglob("*"):
            if path.is_file() and (not suffix or path.suffix == suffix):
                yield path
    for name in (
        "backend/alembic.ini",
        "backend/pyproject.toml",
        "backend/requirements.lock",
        "backend/Dockerfile",
        "backend/Browser.Dockerfile",
        "backend/migrations/env.py",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/nginx.conf",
        "frontend/index.html",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
        "compose.browser.yaml",
        "ops/backup/atlas-restore-drill.sh",
    ):
        yield ROOT / name
    for name in ("ops/voice-rollout/backend.Dockerfile", "ops/voice-rollout/frontend.Dockerfile"):
        yield ROOT / name
    yield from (
        path for path in (ROOT / "ops/hybrid-rollout").glob("*") if path.is_file()
    )


def main():
    paths = sorted(set(files()))
    for path in paths:
        relative = path.relative_to(ROOT)
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > 8_000_000
            or any(
                part.startswith(".") or part == "__pycache__" for part in relative.parts
            )
        ):
            raise ValueError("Недопустимый файл в пакете: " + str(relative))
    OUT.parent.mkdir(exist_ok=True)
    with tarfile.open(OUT, "w:gz") as tar:
        for path in paths:
            tar.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"{OUT} {len(paths)} files {OUT.stat().st_size} bytes sha256={digest}")


if __name__ == "__main__":
    main()
