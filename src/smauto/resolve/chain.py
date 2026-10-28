"""Try resolvers in order until one produces a usable post."""

from __future__ import annotations

from dataclasses import dataclass, field

from smauto.config import get_settings
from smauto.logging import get_logger
from smauto.resolve.base import (
    ProtectedContentError,
    ResolvedPost,
    ResolveError,
    Resolver,
)
from smauto.resolve.fxtwitter import FxTwitterResolver
from smauto.resolve.gallerydl import GalleryDlResolver
from smauto.resolve.ytdlp import YtDlpResolver

log = get_logger(__name__)


class ChainExhaustedError(ResolveError):
    """No resolver could handle the URL — the item falls back to a manual upload."""

    def __init__(self, attempts: list[Attempt]) -> None:
        detail = "; ".join(f"{a.resolver}: {a.error}" for a in attempts if a.error) or "no resolver"
        super().__init__(detail)
        self.attempts = attempts


@dataclass(slots=True)
class Attempt:
    resolver: str
    ok: bool
    error: str | None = None


@dataclass(slots=True)
class ChainResult:
    post: ResolvedPost
    attempts: list[Attempt] = field(default_factory=list)


def build_resolvers(names: tuple[str, ...] | None = None) -> list[Resolver]:
    """Instantiate the configured resolver chain.

    `manual` is not a resolver — it is the absence of one — so it is skipped here
    and handled by the caller when the chain is exhausted.
    """
    settings = get_settings()
    wanted = names if names is not None else settings.resolver_names

    registry: dict[str, type[Resolver]] = {
        "fxtwitter": FxTwitterResolver,
        "ytdlp": YtDlpResolver,
        "gallerydl": GalleryDlResolver,
    }

    resolvers: list[Resolver] = []
    for name in wanted:
        if name == "manual":
            continue
        factory = registry.get(name)
        if factory is None:
            log.warning("unknown resolver in RESOLVER_CHAIN", resolver=name)
            continue
        resolvers.append(factory())
    return resolvers


def resolve(url: str, *, resolvers: list[Resolver] | None = None) -> ChainResult:
    """Resolve `url`, falling through the chain on failure.

    Raises ProtectedContentError immediately (the item must be rejected, not
    retried) and ChainExhaustedError when nothing worked.
    """
    chain = resolvers if resolvers is not None else build_resolvers()
    attempts: list[Attempt] = []

    for resolver in chain:
        name = resolver.name
        try:
            if not resolver.supports(url):
                attempts.append(Attempt(name, ok=False, error="unsupported url"))
                continue
        except Exception as exc:
            attempts.append(Attempt(name, ok=False, error=f"supports() raised: {exc}"))
            continue

        try:
            post = resolver.resolve(url)
        except ProtectedContentError:
            raise
        except Exception as exc:
            attempts.append(Attempt(name, ok=False, error=str(exc)[:300]))
            log.info("resolver failed", resolver=name, url=url, error=str(exc)[:200])
            continue

        if post.is_protected:
            raise ProtectedContentError(f"{name}: source is protected")

        problem = _usability_problem(post)
        if problem:
            attempts.append(Attempt(name, ok=False, error=problem))
            log.info("resolver produced unusable post", resolver=name, reason=problem)
            continue

        post.resolver = name
        attempts.append(Attempt(name, ok=True))
        log.info("resolved", resolver=name, url=url, media=len(post.media))
        return ChainResult(post=post, attempts=attempts)

    raise ChainExhaustedError(attempts)


def _usability_problem(post: ResolvedPost) -> str | None:
    """A post needs either media or enough text to stand on its own as a card."""
    if post.media:
        return None
    if post.text and len(post.text.strip()) >= 2:
        return None
    return "no media and no text"


__all__ = ["Attempt", "ChainExhaustedError", "ChainResult", "build_resolvers", "resolve"]
