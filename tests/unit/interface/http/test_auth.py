from pathlib import Path

import pytest

from monkeyocr.interface.http.auth import BearerTokenVerifier


def test_token_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unavailable"):
        BearerTokenVerifier.from_file(tmp_path / "missing")


def test_token_requires_at_least_32_bytes() -> None:
    with pytest.raises(ValueError, match="32"):
        BearerTokenVerifier("short")


def test_token_file_rejects_trailing_newline(tmp_path: Path) -> None:
    secret = tmp_path / "token"
    secret.write_text("a" * 32 + "\n", encoding="ascii")

    with pytest.raises(ValueError, match="without whitespace"):
        BearerTokenVerifier.from_file(secret)


def test_token_verifier_accepts_only_exact_bearer_value() -> None:
    verifier = BearerTokenVerifier("a" * 32)

    assert verifier.accepts("Bearer " + "a" * 32)
    assert not verifier.accepts("Bearer " + "b" * 32)
    assert not verifier.accepts("Basic " + "a" * 32)
