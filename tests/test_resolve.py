from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from smauto.resolve.base import (
    ProtectedContentError,
    ResolvedPost,
    ResolveError,
    clean_text,
    detect_platform,
    parse_x_url,
)
from smauto.resolve.chain import ChainExhaustedError, resolve
from smauto.resolve.fxtwitter import FxTwitterResolver

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ----------------------------------------------------------------------
# URL parsing / text cleaning
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/user/status/12345", ("user", "12345")),
        ("https://twitter.com/user/status/12345?s=20", ("user", "12345")),
        ("https://x.com/i/web/status/999", (None, "999")),
        ("https://x.com/user", None),
        ("https://instagram.com/p/abc", None),
    ],
)
def test_parse_x_url(url: str, expected) -> None:
    assert parse_x_url(url) == expected


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://x.com/a/status/1", "x"),
        ("https://twitter.com/a/status/1", "x"),
        ("https://www.instagram.com/reel/abc/", "instagram"),
        ("https://vm.tiktok.com/xyz/", "tiktok"),
        ("https://example.com/", None),
    ],
)
def test_detect_platform(url: str, platform) -> None:
    assert detect_platform(url) == platform


def test_clean_text_strips_tco_links() -> None:
    assert clean_text("komik şey https://t.co/abc123") == "komik şey"


def test_clean_text_keeps_inline_links() -> None:
    text = clean_text("bak şuna https://ornek.com/x sonra devam")
    assert "https://ornek.com/x" in (text or "")


def test_clean_text_decodes_entities() -> None:
    assert clean_text("kahve &amp; çay") == "kahve & çay"


def test_clean_text_collapses_blank_lines() -> None:
    assert clean_text("bir\n\n\n\niki") == "bir\n\niki"


def test_clean_text_returns_none_when_empty() -> None:
    assert clean_text("   https://t.co/x  ") is None


# ----------------------------------------------------------------------
# fxtwitter resolver
# ----------------------------------------------------------------------
def _client_returning(payload: dict, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fxtwitter_parses_a_photo_tweet() -> None:
    resolver = FxTwitterResolver(client=_client_returning(load("fxtwitter_photo.json")))
    post = resolver.resolve("https://x.com/ornek/status/1800000000000000001")

    assert post.author_handle == "ornek"
    assert post.author_name == "Örnek Hesap"
    assert post.text is not None and "kedi" in post.text
    assert len(post.media) == 1
    assert post.media[0].kind == "image"
    assert post.has_video is False
    assert post.created_at is not None


def test_fxtwitter_parses_a_video_tweet() -> None:
    resolver = FxTwitterResolver(client=_client_returning(load("fxtwitter_video.json")))
    post = resolver.resolve("https://x.com/ornek/status/1800000000000000002")

    assert post.has_video is True
    primary = post.primary_media
    assert primary is not None
    assert primary.kind == "video"
    assert primary.duration_ms == 12300


def test_fxtwitter_parses_a_text_only_tweet() -> None:
    resolver = FxTwitterResolver(client=_client_returning(load("fxtwitter_text.json")))
    post = resolver.resolve("https://x.com/ornek/status/1800000000000000003")

    assert post.media == []
    assert post.text is not None


def test_fxtwitter_parses_a_quote_tweet() -> None:
    resolver = FxTwitterResolver(client=_client_returning(load("fxtwitter_quote.json")))
    post = resolver.resolve("https://x.com/ornek/status/1800000000000000004")

    assert post.quoted is not None
    assert post.quoted.author_handle == "alintilanan"
    assert post.quoted.text is not None


def test_fxtwitter_rejects_protected_accounts() -> None:
    resolver = FxTwitterResolver(client=_client_returning({}, status=401))
    with pytest.raises(ProtectedContentError):
        resolver.resolve("https://x.com/ornek/status/1")


def test_fxtwitter_raises_on_persistent_failure() -> None:
    resolver = FxTwitterResolver(client=_client_returning({}, status=500))
    with pytest.raises(ResolveError):
        resolver.resolve("https://x.com/ornek/status/1")


def test_fxtwitter_only_supports_status_urls() -> None:
    resolver = FxTwitterResolver()
    assert resolver.supports("https://x.com/a/status/1") is True
    assert resolver.supports("https://instagram.com/p/x") is False


# ----------------------------------------------------------------------
# chain
# ----------------------------------------------------------------------
class _Boom:
    name = "boom"

    def supports(self, url: str) -> bool:
        return True

    def resolve(self, url: str) -> ResolvedPost:
        raise ResolveError("nope")


class _Works:
    name = "works"

    def supports(self, url: str) -> bool:
        return True

    def resolve(self, url: str) -> ResolvedPost:
        return ResolvedPost(
            platform="x", source_id="1", source_url=url, text="tamam", author_handle="a"
        )


class _Protected:
    name = "protected"

    def supports(self, url: str) -> bool:
        return True

    def resolve(self, url: str) -> ResolvedPost:
        raise ProtectedContentError("private")


class _Empty:
    name = "empty"

    def supports(self, url: str) -> bool:
        return True

    def resolve(self, url: str) -> ResolvedPost:
        return ResolvedPost(platform="x", source_id="1", source_url=url)


def test_chain_falls_through_to_the_next_resolver() -> None:
    result = resolve("https://x.com/a/status/1", resolvers=[_Boom(), _Works()])
    assert result.post.resolver == "works"
    assert [a.resolver for a in result.attempts] == ["boom", "works"]


def test_chain_raises_when_everything_fails() -> None:
    with pytest.raises(ChainExhaustedError):
        resolve("https://x.com/a/status/1", resolvers=[_Boom(), _Boom()])


def test_chain_stops_immediately_on_protected_content() -> None:
    with pytest.raises(ProtectedContentError):
        resolve("https://x.com/a/status/1", resolvers=[_Protected(), _Works()])


def test_chain_skips_results_with_neither_media_nor_text() -> None:
    result = resolve("https://x.com/a/status/1", resolvers=[_Empty(), _Works()])
    assert result.post.resolver == "works"
