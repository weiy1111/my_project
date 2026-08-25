import asyncio
import base64
import hashlib
import json
import time

import httpx
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from auto_agent.exceptions import ImChannelAuthError
from auto_agent.http import create_app
from auto_agent.im_channels import FeishuWebhookChannel
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


def message_event(*, token: str = "verify-token", mentions: bool = True) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "event-1",
            "event_type": "im.message.receive_v1",
            "token": token,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_user"},
                "sender_type": "user",
                "tenant_key": "tenant",
            },
            "message": {
                "message_id": "om_message",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 review this"}),
                "mentions": (
                    [{"key": "@_user_1", "id": {"open_id": "ou_bot"}}] if mentions else []
                ),
            },
        },
    }


def encrypted_body(payload: dict, encrypt_key: str) -> bytes:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(encrypt_key.encode())
    key = digest.finalize()
    iv = b"0123456789abcdef"
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    plaintext = json.dumps(payload).encode()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return json.dumps({"encrypt": base64.b64encode(iv + ciphertext).decode()}).encode()


def signed_headers(body: bytes, encrypt_key: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = "nonce-1"
    signature = hashlib.sha256((timestamp + nonce + encrypt_key).encode() + body).hexdigest()
    return {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


def test_feishu_url_verification_route() -> None:
    async def run() -> None:
        channel = FeishuWebhookChannel(
            app_id="app-id",
            app_secret="app-secret",
            verification_token="verify-token",
        )
        app = create_app(
            TaskManager(FakeAgentWorker()),
            webhook_channels=[channel],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                channel.webhook_path,
                json={
                    "type": "url_verification",
                    "token": "verify-token",
                    "challenge": "challenge-code",
                },
            )
            assert response.status_code == 200
            assert response.json() == {"challenge": "challenge-code"}

            denied = await client.post(
                channel.webhook_path,
                json={
                    "type": "url_verification",
                    "token": "wrong-token",
                    "challenge": "challenge-code",
                },
            )
            assert denied.status_code == 401
            assert denied.json()["detail"]["code"] == "im_channel_auth_error"

    asyncio.run(run())


def test_feishu_encrypted_message_is_verified_and_dispatched_in_background() -> None:
    async def run() -> None:
        encrypt_key = "encrypt-key"
        channel = FeishuWebhookChannel(
            app_id="app-id",
            app_secret="app-secret",
            verification_token="verify-token",
            encrypt_key=encrypt_key,
        )
        await channel.connect()
        started = asyncio.Event()
        release = asyncio.Event()
        messages = []

        async def handler(message) -> None:
            messages.append(message)
            started.set()
            await release.wait()

        channel.on_message(handler)
        body = encrypted_body(message_event(), encrypt_key)
        result = await channel.handle_webhook(body, signed_headers(body, encrypt_key))
        assert result == {"code": 0}
        await asyncio.wait_for(started.wait(), timeout=1)
        assert len(messages) == 1
        message = messages[0]
        assert message.content == "review this"
        assert message.session_id == "oc_chat:ou_user"
        assert message.reply_callback == "om_message"
        assert message.metadata["event_id"] == "event-1"
        release.set()
        await asyncio.sleep(0)
        await channel.close()

    asyncio.run(run())


def test_feishu_rejects_invalid_signature_and_ignores_unmentioned_group_message() -> None:
    async def run() -> None:
        channel = FeishuWebhookChannel(
            app_id="app-id",
            app_secret="app-secret",
            verification_token="verify-token",
            encrypt_key="encrypt-key",
        )
        await channel.connect()
        body = json.dumps(message_event()).encode()
        try:
            await channel.handle_webhook(
                body,
                {
                    "X-Lark-Request-Timestamp": str(int(time.time())),
                    "X-Lark-Request-Nonce": "nonce",
                    "X-Lark-Signature": "invalid",
                },
            )
        except ImChannelAuthError:
            pass
        else:
            raise AssertionError("invalid Feishu signature was accepted")

        received = []

        async def handler(message) -> None:
            received.append(message)

        channel.on_message(handler)
        body = json.dumps(message_event(mentions=False)).encode()
        result = await channel.handle_webhook(body, signed_headers(body, "encrypt-key"))
        await asyncio.sleep(0)
        assert result == {"code": 0}
        assert received == []
        await channel.close()

    asyncio.run(run())


def test_feishu_requires_signature_for_unencrypted_message_event() -> None:
    async def run() -> None:
        channel = FeishuWebhookChannel(
            app_id="app-id",
            app_secret="app-secret",
            verification_token="verify-token",
        )
        await channel.connect()
        body = json.dumps(message_event()).encode()
        try:
            await channel.handle_webhook(body, {})
        except ImChannelAuthError:
            pass
        else:
            raise AssertionError("unsigned Feishu message event was accepted")

        received = []

        async def handler(message) -> None:
            received.append(message)

        channel.on_message(handler)
        result = await channel.handle_webhook(body, signed_headers(body, ""))
        await asyncio.sleep(0)
        assert result == {"code": 0}
        assert len(received) == 1
        await channel.close()

    asyncio.run(run())


def test_feishu_reply_uses_cached_tenant_token_and_original_message() -> None:
    async def run() -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("tenant_access_token/internal"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "tenant_access_token": "tenant-token",
                        "expire": 7200,
                    },
                )
            assert request.headers["authorization"] == "Bearer tenant-token"
            return httpx.Response(200, json={"code": 0, "msg": "success"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = FeishuWebhookChannel(
            app_id="app-id",
            app_secret="app-secret",
            verification_token="verify-token",
            client=client,
        )
        await channel.connect()
        metadata = {"reply_callback": "om_original", "chat_id": "oc_chat"}
        await channel.send_reply("session-1", "first", metadata=metadata)
        await channel.send_reply("session-1", "second", metadata=metadata)

        token_requests = [
            request
            for request in requests
            if request.url.path.endswith("tenant_access_token/internal")
        ]
        reply_requests = [request for request in requests if request.url.path.endswith("/reply")]
        assert len(token_requests) == 1
        assert len(reply_requests) == 2
        assert reply_requests[0].url.path.endswith("/messages/om_original/reply")
        assert json.loads(reply_requests[0].content)["content"] == json.dumps(
            {"text": "first"}, ensure_ascii=False
        )
        await channel.close()
        await client.aclose()

    asyncio.run(run())
