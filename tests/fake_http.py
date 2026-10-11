"""A fake aiohttp session, injected at OUR seam instead of inside aiohttp.

WHY THIS EXISTS. `aioresponses` patches aiohttp's internals, so when aiohttp changed
them the whole suite died on HA 2026.9 with `ClientResponse.__init__() missing 1
required keyword-only argument: 'stream_writer'` — and 0.7.9 is the newest release
there is, so there was nothing to upgrade to. This fake depends only on the surface
*our* code uses (`coordinator._get_session()` hands back whatever `_session` holds),
which is stable because we own it. That makes it version-proof by construction.

🔴 WHY THIS FILE IS DANGEROUS, AND THE RULE THAT FOLLOWS. A fake can be wrong in the
direction that makes tests pass, and a green suite built on a dishonest fake is worse
than an honest red one. `test_fake_http_fidelity.py` drives real integration code
against this fake AND against a real aiohttp server and requires identical observable
behavior. **Do not add or change behavior here without a fidelity case that pins it.**

The two details a naive fake gets wrong, both pinned by that control:
  * `raise_for_status()` must carry the real reason phrase ("Bad Request"), because
    `coordinator.async_send` formats it into `f"HTTP {err.status} {err.message}"`.
  * `json()` checks the content type and raises `ContentTypeError`; `json(content_type=None)`
    skips that check but still raises `ValueError` on a malformed body. `async_send`
    depends on the second to read a refusal payload off a 400 BEFORE raising.
"""
from __future__ import annotations

import asyncio
import json as jsonlib
from http import HTTPStatus

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL


def _reason(status: int) -> str:
    """The reason phrase aiohttp would report, e.g. 400 -> 'Bad Request'."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


class FakeResponse:
    """Mirrors the aiohttp.ClientResponse surface our code touches."""

    def __init__(self, status=200, payload=None, body=None,
                 content_type="application/json", url="http://fake/"):
        self.status = status
        self._url = URL(url)
        self.content_type = content_type
        if body is not None:
            self._body = body
        elif payload is not None:
            self._body = jsonlib.dumps(payload)
        else:
            self._body = ""
        self.headers = CIMultiDictProxy(CIMultiDict({"Content-Type": content_type}))
        self.reason = _reason(status)

    @property
    def _request_info(self) -> aiohttp.RequestInfo:
        empty = CIMultiDictProxy(CIMultiDict())
        return aiohttp.RequestInfo(self._url, "GET", empty, self._url)

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._body.encode()

    async def json(self, content_type="application/json"):
        # aiohttp checks the content type unless explicitly told not to.
        if content_type is not None and self.content_type != content_type:
            raise aiohttp.ContentTypeError(
                self._request_info, (),
                message=f"Attempt to decode JSON with unexpected mimetype: {self.content_type}",
                headers=self.headers,
            )
        # Malformed bodies raise JSONDecodeError, which IS a ValueError — the
        # distinction our callers rely on.
        return jsonlib.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                self._request_info, (), status=self.status,
                message=self.reason, headers=self.headers,
            )


class _Ctx:
    """The async context manager `async with session.get(...)` expects."""

    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for `coordinator._session`.

    `routes` maps a substring of the request URL to either a FakeResponse or an
    exception instance to raise. `default` answers anything unmatched; leaving it
    None makes an unmatched URL raise loudly rather than silently returning 200 —
    a silent default is how a fake starts passing tests it never exercised.
    """

    def __init__(self, routes=None, default=None):
        self._routes = routes or {}
        self._default = default
        self.closed = False
        self.requests: list[str] = []

    def _match(self, url: str):
        for key, outcome in self._routes.items():
            if key in url:
                return outcome
        if self._default is not None:
            return self._default
        raise AssertionError(
            f"FakeSession got an unrouted request: {url}\n"
            f"routes: {list(self._routes)}\n"
            "Add a route, or pass default= deliberately."
        )

    def get(self, url, params=None, **kwargs):
        full = str(URL(url).with_query(params)) if params else str(url)
        self.requests.append(full)
        return _Ctx(self._match(full))

    def post(self, url, **kwargs):
        self.requests.append(str(url))
        return _Ctx(self._match(str(url)))

    async def close(self) -> None:
        self.closed = True
