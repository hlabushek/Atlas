"""HTTPS smoke без LLM, аудио и записи пользовательских задач."""

import os
import asyncio
import httpx
import websockets
from sqlalchemy import select
from atlas.db import sessions
from atlas.models import Task, Assignment
from atlas.diary import retrieve


async def events(cookie):
    async with websockets.connect(
        "wss://atlas.prism-news.xyz/api/events",
        origin="https://atlas.prism-news.xyz",
        additional_headers={"Cookie": cookie},
    ) as socket:
        import json

        payload = json.loads(await asyncio.wait_for(socket.recv(), 20))
        assert isinstance(payload, dict)
        print("PASS: authenticated live WebSocket snapshot")


def main():
    with httpx.Client(base_url="https://atlas.prism-news.xyz", timeout=20) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/voice/audio-status").status_code == 401
        assert (
            client.get("/api/diary/search", params={"q": "проверка"}).status_code == 401
        )
        assert (
            client.post(
                "/api/login", json={"password": os.environ["ATLAS_OWNER_PASSWORD"]}
            ).status_code
            == 200
        )
        client.headers["X-Atlas-Client"] = "web"
        status = client.get("/api/voice/audio-status")
        assert status.status_code == 200
        assert status.json()["sample_rate"] == 16000
        print("Browser ASR ready:", status.json()["ready"])
        # Secret sentinel causes warm() to return before a paid embedding call.
        response = client.get("/api/diary/search", params={"q": "sk-" + "a" * 48})
        assert response.status_code == 200 and response.json()["method"] == "rrf"
        with sessions()() as session:
            task_id = session.scalar(select(Task.id).limit(1))
            assignment = session.scalar(select(Assignment).limit(1))
            if assignment:
                assert retrieve(session, assignment.title, limit=100)
                print(
                    "PASS: live saved diary lexical/RRF retrieval without provider calls"
                )
        if task_id:
            detail = client.get("/api/tasks/" + task_id)
            assert detail.status_code == 200 and isinstance(
                detail.json()["tool_events"], list
            )
        assert client.get("/voice-capture.worklet.js").status_code == 200
        cookie = "; ".join(name + "=" + value for name, value in client.cookies.items())
        asyncio.run(events(cookie))
        client.post("/api/logout")
        print(
            "PASS: HTTPS health, owner auth, protected search/audio, RRF API, tool metadata, worklet"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL: HTTPS smoke:", type(exc).__name__)
        raise SystemExit(1)
