import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from auto_agent.exceptions import (
    ImChannelAuthError,
    ImChannelError,
    ImChannelProtocolError,
)
from auto_agent.im_channels.base import BaseWebhookImChannel
from auto_agent.im_channels.feishu_common import FeishuApiChannel


class FeishuWebhookChannel(FeishuApiChannel, BaseWebhookImChannel):
    """Feishu Open Platform event webhook transport.

    Inbound events arrive as HTTP POSTs on :attr:`webhook_path`; outbound
    replies reuse the shared Feishu API logic from :class:`FeishuApiChannel`.
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str | None = None,
        channel_id: str = "feishu_webhook",
        webhook_path: str = "/v1/im/feishu/events",
        max_clock_skew_seconds: int = 300,
        **api_kwargs: Any,
    ) -> None:
        if not app_id or not app_secret or not verification_token:
            raise ValueError("Feishu app_id, app_secret and verification_token are required")
        # BaseWebhookImChannel.__init__ chains into BaseImChannel.__init__; run it
        # first so FeishuApiChannel.__init__ performs the final state setup.
        BaseWebhookImChannel.__init__(self, channel_id, webhook_path)
        FeishuApiChannel.__init__(
            self, app_id=app_id, app_secret=app_secret, channel_id=channel_id, **api_kwargs
        )
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key or ""
        self.max_clock_skew_seconds = max(0, max_clock_skew_seconds)

    async def handle_webhook(self, body: bytes, headers: Mapping[str, str]) -> dict:
        payload = self._decode_payload(body)
        event_type = self._event_type(payload)
        self._verify_token(payload)

        if event_type == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise ImChannelProtocolError("Feishu URL verification has no challenge")
            return {"challenge": challenge}

        self._verify_signature(body, headers)
        if event_type == "card.action.trigger" or payload.get("type") == "card_action_trigger":
            action = self._to_im_action(payload)
            if action is not None:
                self._start_dispatch_action(action)
            return {"code": 0}
        if event_type != "im.message.receive_v1":
            return {"code": 0}

        message = self._to_im_message(payload)
        if message is None:
            return {"code": 0}
        self._start_dispatch(message)
        return {"code": 0}

    # -------------------------------------------------------- auth/decoding

    def _decode_payload(self, body: bytes) -> dict[str, Any]:
        try:
            envelope = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ImChannelProtocolError("Feishu webhook body is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise ImChannelProtocolError("Feishu webhook body must be a JSON object")
        encrypted = envelope.get("encrypt")
        if not encrypted:
            return envelope
        if not self.encrypt_key:
            raise ImChannelAuthError("Feishu encrypted event received without encrypt_key")
        try:
            plaintext = self._decrypt(str(encrypted))
            payload = json.loads(plaintext)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ImChannelAuthError("Feishu event decryption failed") from exc
        if not isinstance(payload, dict):
            raise ImChannelProtocolError("Feishu decrypted event must be a JSON object")
        return payload

    def _decrypt(self, encrypted: str) -> bytes:
        try:
            from cryptography.hazmat.primitives import hashes, padding
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            raw = base64.b64decode(encrypted, validate=True)
            if len(raw) <= 16 or len(raw) % 16:
                raise ValueError("invalid encrypted payload length")
            digest = hashes.Hash(hashes.SHA256())
            digest.update(self.encrypt_key.encode())
            key = digest.finalize()
            decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
            padded = decryptor.update(raw[16:]) + decryptor.finalize()
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except ImportError as exc:
            raise ImChannelError("cryptography is required for encrypted Feishu events") from exc

    def _verify_signature(self, body: bytes, headers: Mapping[str, str]) -> None:
        normalized = {key.lower(): value for key, value in headers.items()}
        timestamp = normalized.get("x-lark-request-timestamp", "")
        nonce = normalized.get("x-lark-request-nonce", "")
        signature = normalized.get("x-lark-signature", "")
        if not timestamp or not nonce or not signature:
            raise ImChannelAuthError("Feishu signature headers are missing")
        try:
            request_time = int(timestamp)
        except ValueError as exc:
            raise ImChannelAuthError("Feishu request timestamp is invalid") from exc
        if (
            self.max_clock_skew_seconds
            and abs(time.time() - request_time) > self.max_clock_skew_seconds
        ):
            raise ImChannelAuthError("Feishu request timestamp is outside the allowed window")
        expected = hashlib.sha256(
            (timestamp + nonce + self.encrypt_key).encode() + body
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ImChannelAuthError("Feishu request signature is invalid")

    def _verify_token(self, payload: dict[str, Any]) -> None:
        header = payload.get("header") or {}
        token = header.get("token") if isinstance(header, dict) else None
        token = token or payload.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, self.verification_token):
            raise ImChannelAuthError("Feishu verification token is invalid")

    def _event_type(self, payload: dict[str, Any]) -> str:
        header = payload.get("header") or {}
        if isinstance(header, dict) and header.get("event_type"):
            return str(header["event_type"])
        return str(payload.get("type") or "")
