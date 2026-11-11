"""User settings and the provider keys, stored as JSON files in the Visto home.

The secrets file holds one `<provider>_api_key` field per AI provider. A file
written before more providers existed holds only `openai_api_key` and stays valid
unchanged; saving or removing one provider's key never touches another's.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from visto.adapters.ai.providers import DEFAULT_PROVIDER, PROVIDERS
from visto.adapters.ios.signing import IOSSigning
from visto.home import VistoHome, make_private_dir

FORMAT = 1
MAX_KEY_LENGTH = 8192
_IOS_TEXT = ("team_id", "signing_id", "wda_bundle_id", "wda_derived_data")


class SettingsError(ValueError):
    """A settings or secrets problem; messages never contain file or key content."""


@dataclass(frozen=True)
class Settings:
    price_refresh: bool = True
    ios: IOSSigning = IOSSigning()
    ios_saved_for_xcuitest: str | None = None
    # The active AI provider; absent from files written before providers were selectable.
    provider: str = DEFAULT_PROVIDER


@dataclass(frozen=True)
class KeyStatus:
    configured: bool
    last4: str | None


def _read(path: Path, label: str) -> dict | None:
    """The parsed object of a format-1 file, or None when the file is absent."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise SettingsError(f"{label} file is invalid") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise SettingsError(f"{label} file is invalid") from None
    version = data.get("format") if isinstance(data, dict) else None
    if type(version) is not int:
        raise SettingsError(f"{label} file is invalid")
    if version > FORMAT:
        raise SettingsError(f"{label} file was written by a newer Visto")
    if version != FORMAT:
        raise SettingsError(f"{label} file is invalid")
    return data


def _write(path: Path, data: dict) -> None:
    """Atomic replace through an owner-only sibling temp file."""
    make_private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if os.name != "nt":
                os.chmod(handle.fileno(), 0o600)  # Before any content is written.
            json.dump(data, handle, indent=2)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class SettingsStore:
    def __init__(self, home: VistoHome):
        self._home = home

    def load(self) -> Settings:
        data = _read(self._home.settings_file, "settings")
        if data is None:
            return Settings()
        price_refresh = data.get("price_refresh", True)
        saved = data.get("ios_saved_for_xcuitest")
        ios = data.get("ios", {})
        active = data.get("provider", DEFAULT_PROVIDER)
        if (
            type(price_refresh) is not bool
            or not (isinstance(active, str) and active in PROVIDERS)
            or not (saved is None or isinstance(saved, str))
            or not isinstance(ios, dict)
        ):
            raise SettingsError("settings file is invalid")
        values: dict[str, object] = {name: ios.get(name, "") for name in _IOS_TEXT}
        values["prebuilt_wda"] = ios.get("prebuilt_wda", False)
        if type(values["prebuilt_wda"]) is not bool or not all(
            isinstance(values[name], str) for name in _IOS_TEXT
        ):
            raise SettingsError("settings file is invalid")
        return Settings(price_refresh, IOSSigning(**values), saved, active)

    def save(self, settings: Settings) -> None:
        ios = settings.ios
        _write(
            self._home.settings_file,
            {
                "format": FORMAT,
                "price_refresh": settings.price_refresh,
                "ios": {
                    "team_id": ios.team_id,
                    "signing_id": ios.signing_id,
                    "wda_bundle_id": ios.wda_bundle_id,
                    "prebuilt_wda": ios.prebuilt_wda,
                    "wda_derived_data": ios.wda_derived_data,
                },
                "ios_saved_for_xcuitest": settings.ios_saved_for_xcuitest,
                "provider": _known(settings.provider),
            },
        )

    def _secrets(self) -> dict[str, str]:
        """Stored non-empty keys by provider."""
        data = _read(self._home.secrets_file, "secrets")
        if data is None:
            return {}
        keys = {}
        for identifier in PROVIDERS:
            key = data.get(_field(identifier), "")
            if not isinstance(key, str):
                raise SettingsError("secrets file is invalid")
            if key:
                keys[identifier] = key
        return keys

    def _write_secrets(self, keys: dict[str, str]) -> None:
        if not keys:
            self._home.secrets_file.unlink(missing_ok=True)
            return
        fields = {
            _field(identifier): keys[identifier] for identifier in PROVIDERS if identifier in keys
        }
        _write(self._home.secrets_file, {"format": FORMAT, **fields})

    def api_key(self, provider: str = DEFAULT_PROVIDER) -> str:
        return self._secrets().get(_known(provider), "")

    def active(self) -> tuple[str, str]:
        """The active provider and its stored key (empty when none is stored)."""
        provider = self.load().provider
        return provider, self.api_key(provider)

    def set_api_key(self, key: str, provider: str = DEFAULT_PROVIDER) -> KeyStatus:
        provider = _known(provider)
        key = key.strip() if isinstance(key, str) else ""
        if not key:
            raise SettingsError("the API key is empty")
        if len(key) > MAX_KEY_LENGTH:
            raise SettingsError("the API key is too long")
        if not key.isprintable() or any(character.isspace() for character in key):
            raise SettingsError("the API key contains whitespace or control characters")
        keys = self._secrets()
        keys[provider] = key
        self._write_secrets(keys)
        return self.key_status(provider)

    def delete_api_key(self, provider: str = DEFAULT_PROVIDER) -> KeyStatus:
        provider = _known(provider)
        try:
            keys = self._secrets()
        except SettingsError:
            keys = {}  # An unreadable file holds no usable key; removing it recovers.
        if keys.pop(provider, None) is not None or not keys:
            self._write_secrets(keys)
        return self.key_status(provider)

    def key_status(self, provider: str = DEFAULT_PROVIDER) -> KeyStatus:
        key = self.api_key(provider)
        return KeyStatus(True, key[-4:]) if key else KeyStatus(False, None)


def _known(provider: str) -> str:
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise SettingsError("the AI provider is not supported")
    return provider


def _field(provider: str) -> str:
    return f"{provider}_api_key"
