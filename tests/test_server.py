"""WebSocket-Ende-zu-Ende gegen den echten FastAPI-Server (mit Fake-Providern)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.config.settings import Profile, Secrets, Settings
from jarvis.core.errors import AuthenticationError
from jarvis.interfaces.auth import RateLimiter, TokenAuth
from jarvis.interfaces.protocol import decode_audio_frame
from jarvis.interfaces.server import create_app

TOKEN = "test-token-mit-genug-laenge"
SPEECH = b"\x11\x00" * 160
SILENCE = b"\x00" * 320


@pytest.fixture
def app_settings() -> Settings:
    profile = Profile.model_validate(
        {
            "profile": {"language": "de", "persona": "Du bist JARVIS."},
            "providers": {"stt": "fake", "tts": "fake", "llm": "fake"},
        }
    )
    return Settings(
        profile=profile,
        secrets=Secrets(_env_file=None, jarvis_auth_token=TOKEN),
    )


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    # Als Kontextmanager, sonst läuft der Lifespan nicht und der geteilte
    # LLM-Client wird nie gebaut.
    with TestClient(create_app(app_settings)) as test_client:
        yield test_client


def test_health_needs_no_auth(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_websocket_without_token_is_rejected(client: TestClient) -> None:
    # Der Core kann später Mails senden und Browser steuern. Er darf nie
    # ohne Nachweis erreichbar sein.
    with pytest.raises(Exception):  # noqa: B017 - Starlette schließt hart
        with client.websocket_connect("/ws") as socket:
            socket.receive_text()


def test_websocket_with_wrong_token_is_rejected(client: TestClient) -> None:
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect("/ws?token=falsch") as socket:
            socket.receive_text()


def test_session_ready_configures_the_client(client: TestClient) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        ready = json.loads(socket.receive_text())

    assert ready["type"] == "session.ready"
    assert ready["input_sample_rate"] == 16000
    assert ready["input_frame_ms"] == 20
    assert ready["barge_in_min_speech_ms"] == 200
    assert ready["session_id"]


def test_text_turn_streams_deltas_and_completes(client: TestClient) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()  # session.ready
        socket.send_text(json.dumps({"type": "user.text", "text": "Hallo JARVIS"}))

        deltas: list[str] = []
        completed: dict | None = None
        while completed is None:
            message = json.loads(socket.receive_text())
            if message["type"] == "text.delta":
                deltas.append(message["text"])
            elif message["type"] == "reply.completed":
                completed = message

    assert "".join(deltas)
    assert completed["spoken"] == "".join(deltas).strip()
    assert completed["interrupted"] is False


def test_audio_upstream_produces_transcript_and_audio_downstream(
    client: TestClient,
) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()  # session.ready
        socket.send_text(json.dumps({"type": "mic.toggle", "open": True}))
        for _ in range(10):
            socket.send_bytes(SPEECH)
        for _ in range(10):
            socket.send_bytes(SILENCE)

        final_text: str | None = None
        audio_frames = 0
        for _ in range(200):
            message = socket.receive()
            if "bytes" in message and message["bytes"] is not None:
                _seq, sample_rate, pcm = decode_audio_frame(message["bytes"])
                assert sample_rate == 24000
                assert len(pcm) > 0
                audio_frames += 1
                if final_text is not None and audio_frames >= 3:
                    break
                continue
            payload = json.loads(message["text"])
            if payload["type"] == "transcript.final":
                final_text = payload["text"]

        assert final_text == "Hallo JARVIS, wie geht es dir?"
        assert audio_frames >= 3


def test_unknown_message_type_returns_error_without_closing(client: TestClient) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()  # session.ready
        socket.send_text(json.dumps({"type": "tool.execute", "name": "rm -rf /"}))

        error = json.loads(socket.receive_text())
        assert error["type"] == "error"

        # Verbindung lebt weiter.
        socket.send_text(json.dumps({"type": "ping"}))
        socket.send_text(json.dumps({"type": "user.text", "text": "Noch da?"}))
        assert json.loads(socket.receive_text())["type"] in {"state.changed", "text.delta"}


def test_token_auth_rejects_short_and_missing_tokens() -> None:
    with pytest.raises(AuthenticationError, match="nicht gesetzt"):
        TokenAuth("")
    with pytest.raises(AuthenticationError, match="zu kurz"):
        TokenAuth("kurz")

    auth = TokenAuth(TOKEN)
    auth.verify(TOKEN)
    with pytest.raises(AuthenticationError):
        auth.verify(TOKEN + "x")


def test_rate_limiter_blocks_after_burst() -> None:
    limiter = RateLimiter(max_events=3, window_s=60)

    assert [limiter.allow("1.2.3.4") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("5.6.7.8") is True
