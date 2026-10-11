"""Две явные платные голосовые текстовые пробы; без микрофона, TTS и истории."""

import argparse
import json
import time
from atlas.local_voice import _send_to_atlas, _atlas_client
from atlas.voice_settings import config_path, effective_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Нужен явный --live: два запроса к модели с учётом бюджета")
    config = effective_config(config_path(None))
    config["store_transcripts"] = False
    config["_voice_context"] = []
    for label, prompt in [
        ("fast", "Что такое нейросеть?"),
        (
            "careful",
            "Подумай и сравни локальное и серверное распознавание речи. Ответь в двух предложениях.",
        ),
    ]:
        tasks = []
        fragments = []
        started = time.monotonic()
        config["_voice_task_callback"] = tasks.append

        def receive(_text):
            if not fragments:
                fragments.append(time.monotonic() - started)

        answer = _send_to_atlas(prompt, config, speaker=receive)
        with _atlas_client(config) as client:
            result = client.get("/voice-result/" + tasks[0])
            result.raise_for_status()
            assert result.json()["status"] == "COMPLETED", "Реальный ответ не завершён"
        print(
            json.dumps(
                {
                    "probe": label,
                    "task_id": tasks[0],
                    "first_fragment_seconds": round(fragments[0], 2)
                    if fragments
                    else None,
                    "total_seconds": round(time.monotonic() - started, 2),
                    "words": len(answer.split()),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
