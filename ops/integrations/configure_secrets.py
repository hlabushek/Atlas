"""Create missing runtime secrets directly on VPS without printing or copying them."""

import base64
import os
import secrets
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1]).resolve()
    if str(path) != "/opt/atlas/app/.env":
        raise ValueError("Неверный файл окружения")
    lines = path.read_text(encoding="utf-8").splitlines()
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1] for line in lines if "=" in line
    }
    additions = []
    if not values.get("ATLAS_CONNECTOR_SESSION_KEY"):
        additions.append(
            "ATLAS_CONNECTOR_SESSION_KEY="
            + base64.urlsafe_b64encode(os.urandom(32)).decode()
        )
    if not values.get("ATLAS_BROWSER_SERVICE_TOKEN"):
        additions.append("ATLAS_BROWSER_SERVICE_TOKEN=" + secrets.token_urlsafe(48))
    if additions:
        updated = {line.split("=", 1)[0]: line for line in additions}
        output = []
        for line in lines:
            key = line.split("=", 1)[0] if "=" in line else ""
            if key in updated:
                output.append(updated.pop(key))
            else:
                output.append(line)
        output.extend(updated.values())
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print("Ключи сессий и браузерного сервиса настроены; значения не выводились")


if __name__ == "__main__":
    main()
