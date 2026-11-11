"""Fixed one-request HTTPS child. Never print credentials or raw exception details."""

import base64
import json
import re
import secrets
import socket
import ssl
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .providers import TRANSCRIPTION_PROVIDER
from .transport import (
    AUDIO_EXTENSIONS,
    MAX_AUDIO_BYTES,
    MAX_INPUT_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    PROBE_MODEL_PATTERN,
    TRANSCRIPTION_MODEL,
    safe_error,
)

RESPONSES_URL = "https://api.openai.com/v1/responses"
TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
MODELS_URL = "https://api.openai.com/v1/models/"
# https://platform.claude.com/docs/en/api/versioning
ANTHROPIC_VERSION = "2026-10-21"
# https://ai.google.dev/gemini-api/docs/interactions (Api-Revision header).
GEMINI_REVISION = "2026-05-20"


@dataclass(frozen=True)
class Endpoints:
    """Fixed per-provider URLs and authentication; nothing here comes from the caller."""

    inference: str
    models: str
    auth_header: str
    auth_prefix: str = ""
    headers: tuple[tuple[str, str], ...] = ()

    def auth(self, key: str) -> dict[str, str]:
        return {self.auth_header: self.auth_prefix + key, **dict(self.headers)}


ENDPOINTS = {
    "openai": Endpoints(RESPONSES_URL, MODELS_URL, "Authorization", "Bearer "),
    "anthropic": Endpoints(
        "https://api.anthropic.com/v1/messages",
        "https://api.anthropic.com/v1/models/",
        "x-api-key",
        headers=(("anthropic-version", ANTHROPIC_VERSION),),
    ),
    "gemini": Endpoints(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        "https://generativelanguage.googleapis.com/v1beta/models/",
        "x-goog-api-key",
        headers=(("Api-Revision", GEMINI_REVISION),),
    ),
}


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen(request, *, timeout):
    # A fixed endpoint cannot delegate credentials/images through a redirect.
    # build_opener retains standard ProxyHandler and TLS verification.
    return build_opener(RejectRedirects()).open(request, timeout=timeout)


def network_error(error: OSError) -> str:
    """Classify typed failures only; messages may contain credentials or proxy URLs.

    https://docs.python.org/3.12/library/urllib.error.html#urllib.error.URLError.reason
    """
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, TimeoutError):
        return "provider: timeout"
    if isinstance(reason, ssl.SSLError):
        return "provider: tls_error"
    if isinstance(reason, socket.gaierror):
        return "provider: dns_error"
    if isinstance(reason, ConnectionRefusedError):
        return "provider: connection_refused"
    return "provider: connection_error"


def transcription_body(audio: bytes, media_type: str) -> tuple[bytes, str]:
    """Fixed multipart form: only the audio bytes come from the caller.

    https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create
    """
    boundary = "visto-" + secrets.token_hex(16)
    head = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in (("model", TRANSCRIPTION_MODEL), ("response_format", "json"))
    )
    head += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="speech.{AUDIO_EXTENSIONS[media_type]}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n"
    )
    body = head.encode() + audio + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def handle_packet(raw: bytes) -> dict:
    try:
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Input limit")
        packet = json.loads(raw)
        probe = type(packet) is dict and "probe_model" in packet
        shapes = (
            ({"provider", "api_key", "probe_model", "timeout_ms"},)
            if probe
            else (
                {"provider", "api_key", "payload", "timeout_ms"},
                {"provider", "api_key", "payload", "timeout_ms", "media_type"},
            )
        )
        if type(packet) is not dict or set(packet) not in shapes:
            raise ValueError("Invalid packet")
        if packet["provider"] not in ENDPOINTS:
            raise ValueError("Invalid provider")
        endpoints = ENDPOINTS[packet["provider"]]
        key, timeout = packet["api_key"], packet["timeout_ms"]
        if (
            type(key) is not str
            or not key.strip()
            or len(key) > 8192
            or type(timeout) is not int
            or timeout < 1
        ):
            raise ValueError("Invalid fields")
        payload, method = None, "POST"
        if probe:
            # Key check: fixed GET, the model id is validated before any request.
            model = packet["probe_model"]
            if type(model) is not str or re.fullmatch(PROBE_MODEL_PATTERN, model) is None:
                raise ValueError("Invalid model")
            url, content_type, method = endpoints.models + model, None, "GET"
        else:
            encoded = packet["payload"]
            if type(encoded) is not str:
                raise ValueError("Invalid fields")
            payload = base64.b64decode(encoded, validate=True)
            if not payload or len(payload) > MAX_REQUEST_BYTES:
                raise ValueError("Invalid payload")
            if "media_type" in packet:
                # Speech transcription: the payload is audio; the form is fixed here.
                media_type = packet["media_type"]
                if (
                    packet["provider"] != TRANSCRIPTION_PROVIDER
                    or media_type not in AUDIO_EXTENSIONS
                    or len(payload) > MAX_AUDIO_BYTES
                ):
                    raise ValueError("Invalid audio")
                url = TRANSCRIPTIONS_URL
                payload, content_type = transcription_body(payload, media_type)
            else:
                # Validate before any request; endpoint/headers are never provided by AI.
                if type(json.loads(payload)) is not dict:
                    raise ValueError("Invalid request JSON")
                url, content_type = endpoints.inference, "application/json"
    except (OSError, ValueError, TypeError):
        return {"error": "provider: invalid_request"}
    try:
        headers = endpoints.auth(key)
        if content_type is not None:
            headers["Content-Type"] = content_type
        request = Request(url, data=payload, headers=headers, method=method)
        # urllib's normal TLS and proxy handling is retained. The parent owns the
        # total deadline and forcibly terminates this process if it expires.
        with urlopen(request, timeout=timeout / 1000) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            return {"error": "provider: response_size_limit"}
        value = json.loads(content)
        if type(value) is not dict:
            raise ValueError("Invalid provider response")
        return {"response": value}
    except HTTPError as error:
        code = f"provider: http_{error.code}"
        error.close()
        return {"error": code if safe_error(code) else "provider: transport_or_json_error"}
    except OSError as error:
        return {"error": network_error(error)}
    except (ValueError, TypeError):
        return {"error": "provider: invalid_json"}


def main() -> None:
    try:
        result = handle_packet(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        output = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(output) > MAX_RESPONSE_BYTES:
            output = b'{"error":"provider: response_size_limit"}'
    except Exception:
        output = b'{"error":"provider: transport_or_json_error"}'
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
