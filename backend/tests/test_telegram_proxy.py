from types import SimpleNamespace

from atlas import telegram


def test_telegram_api_and_file_download_use_configured_proxy(monkeypatch):
    proxy = "socks5://127.0.0.1:1080"
    monkeypatch.setattr(
        telegram,
        "settings",
        lambda: SimpleNamespace(telegram_bot_token="123:TEST", telegram_proxy=proxy),
    )
    clients = []

    class Response:
        def __init__(self, result):
            self.result = result

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": self.result}

        def iter_bytes(self):
            yield b"abc"

    class Client:
        def __init__(self, **kwargs):
            clients.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, *_args, **_kwargs):
            return Response({"file_path": "files/a.txt", "file_size": 3})

        def stream(self, *_args, **_kwargs):
            return Response(None)

    monkeypatch.setattr(telegram.httpx, "Client", Client)

    assert telegram.TelegramAPI().download_file("file-id", 3) == b"abc"
    assert len(clients) == 2
    assert all(client["proxy"] == proxy for client in clients)
    assert all(client["follow_redirects"] is False for client in clients)
