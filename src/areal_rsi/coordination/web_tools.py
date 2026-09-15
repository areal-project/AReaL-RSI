"""Public web/paper retrieval for application agents, independent of Codex tools.

Sources: Brave and DuckDuckGo HTML search, Bing RSS, the arXiv Atom API, and Crossref's
public works API. Search snippets are leads, not verified findings. Returned web
content is untrusted input and must never override agent or tool instructions.

API references:
https://duckduckgo.com/duckduckgo-help-pages/features/non-javascript
https://info.arxiv.org/help/api/user-manual.html
https://www.crossref.org/documentation/retrieve-metadata/rest-api/

Each redirect receives a fresh DNS/public-address check. Connections are pinned
to a validated IP while retaining the original Host and TLS SNI. HTTP proxies
are intentionally not auto-selected: httpcore's CONNECT transport does not retain
the SNI override needed for this technique. Direct access and explicitly selected
SOCKS proxies keep TLS verification enabled. Proxy settings are operator-owned.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]
_USER_AGENT = "AReaL-RSI/0.1 (+https://github.com/areal-project/AReaL-RSI)"
_ARXIV_LOCK = threading.Lock()
_ARXIV_NEXT_REQUEST = 0.0
_BRAVE_LOCK = threading.Lock()
_BRAVE_NEXT_REQUEST = 0.0
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "wbr"}
_BLOCK_TAGS = {"p", "div", "article", "section", "main", "li", "h1", "h2", "h3", "h4", "tr", "pre"}
_ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?$")


class WebFetchError(ValueError):
    """An explicit retrieval failure, not empty evidence or a model finding."""


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Transition addresses may encode private IPv4 destinations.
        if address.ipv4_mapped is not None:
            return _public_address(str(address.ipv4_mapped))
        if address.sixtofour is not None or address.teredo is not None:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            return False
    return address.is_global and not address.is_multicast


def _url(value: str) -> httpx.URL:
    if not value or len(value) > 8192 or any(ord(char) < 32 for char in value) or "\\" in value:
        raise WebFetchError("Invalid public URL")
    try:
        parsed = httpx.URL(value)
    except (httpx.InvalidURL, ValueError) as error:
        raise WebFetchError("Invalid public URL") from error
    host = parsed.host.rstrip(".").lower()
    if parsed.scheme not in {"http", "https"} or not host or parsed.userinfo:
        raise WebFetchError("Only public HTTP(S) URLs without credentials are allowed")
    if "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise WebFetchError("Local and internal hostnames are not allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Single-label names can be resolved through local DNS search domains.
        if "." not in host:
            raise WebFetchError("A public, fully qualified hostname is required") from None
    else:
        if not _public_address(host):
            raise WebFetchError("Non-public IP addresses are not allowed")
    return parsed.copy_with(host=host, fragment=None)


async def _resolve(host: str, port: int) -> list[str]:
    values = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(value[4][0]) for value in values))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self.title_parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.stack) >= 256:
            raise WebFetchError("HTML nesting exceeds the extraction limit")
        attributes = dict(attrs)
        suppressed = (self.stack[-1][1] if self.stack else False) or tag in {
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "nav",
            "footer",
            "aside",
            "head",
        }
        suppressed = suppressed or "hidden" in attributes or attributes.get("aria-hidden") == "true"
        if re.search(r"display\s*:\s*none", attributes.get("style") or "", flags=re.I):
            suppressed = True
        if tag == "title":
            self.in_title = True
        if tag not in _VOID_TAGS:
            self.stack.append((tag, suppressed))
        if (tag in _BLOCK_TAGS or tag == "br") and not suppressed:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in _BLOCK_TAGS and not (self.stack and self.stack[-1][1]):
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        elif not (self.stack and self.stack[-1][1]):
            self._append(data)

    def _append(self, text: str) -> None:
        self.parts.append(text)
        if any(tag in {"main", "article"} for tag, _suppressed in self.stack):
            self.main_parts.append(text)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())

    @property
    def text(self) -> str:
        return "\n".join(
            line
            for part in "".join(self.main_parts or self.parts).splitlines()
            if (line := " ".join(part.split()))
        )


def _extract(markup: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.title, parser.text


class _DuckResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.depth = 0
        self.capture: tuple[str, int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID_TAGS:
            self.depth += 1
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "result__a" in classes:
            raw = urljoin("https://html.duckduckgo.com/", attributes.get("href") or "")
            if urlsplit(raw).hostname in {"duckduckgo.com", "html.duckduckgo.com"}:
                raw = parse_qs(urlsplit(raw).query).get("uddg", [raw])[0]
            try:
                target = str(_url(raw))
            except WebFetchError:
                self.capture = None
                return
            self.results.append({"title": "", "url": target, "snippet": ""})
            self.capture = ("title", self.depth)
        elif "result__snippet" in classes and self.results:
            self.capture = ("snippet", self.depth)

    def handle_endtag(self, tag: str) -> None:
        if self.capture is not None and self.depth == self.capture[1]:
            self.capture = None
        if tag not in _VOID_TAGS:
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str) -> None:
        if self.capture is not None and self.results:
            self.results[-1][self.capture[0]] += data


class _BraveResults(_DuckResults):
    """Extract ordinary linked results, excluding generated answers and widgets."""

    def __init__(self) -> None:
        super().__init__()
        self.result_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID_TAGS:
            self.depth += 1
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "snippet" in classes and attributes.get("data-type") == "web":
            self.result_depth = self.depth
            self.results.append({"title": "", "url": "", "snippet": ""})
        if self.result_depth is None:
            return
        if tag == "a" and "l1" in classes:
            with suppress(WebFetchError):
                self.results[-1]["url"] = str(_url(attributes.get("href") or ""))
        elif "search-snippet-title" in classes:
            self.capture = ("title", self.depth)
        elif "generic-snippet" in classes:
            self.capture = ("snippet", self.depth)

    def handle_endtag(self, tag: str) -> None:
        if self.depth == self.result_depth:
            self.result_depth = None
        super().handle_endtag(tag)


def _relevant(query: str, results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Reject obviously broadened fallback hits, not rank scientific merit.

    No domain-specific vocabulary: site filters and a small lexical overlap
    check guard against providers returning results for only the first word.
    """
    sites = re.findall(r"\bsite:([\w.-]+)", query.lower())
    text = re.sub(r"\bsite:[\w.-]+", "", query.lower())
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "with",
        "how",
        "what",
        "is",
        "are",
        "on",
        "by",
        "from",
    }
    terms = {term for term in re.findall(r"[\w-]+", text) if len(term) > 2 and term not in stop}
    selected = []
    for item in results:
        host = urlsplit(item["url"]).hostname or ""
        if sites and not any(host == site or host.endswith("." + site) for site in sites):
            continue
        content = " ".join(item.values()).lower()
        if len(terms) >= 3 and sum(term in content for term in terms) < math.ceil(len(terms) / 2):
            continue
        selected.append(item)
    return selected


@dataclass(frozen=True)
class _Page:
    url: str
    status: int
    content_type: str
    text: str
    download_truncated: bool = False


class ResearchWebClient:
    """Bounded read-only public fetcher; inject transport and resolver in tests."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        timeout_seconds: float = 15,
        max_bytes: int = 2_000_000,
        max_redirects: int = 5,
        proxy: str | None = None,
    ) -> None:
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or max_bytes < 1
            or max_redirects < 0
        ):
            raise ValueError("Invalid public web request limits")
        self.transport = transport
        self.resolver = resolver or _resolve
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        configured_proxy = proxy or os.environ.get("RSI_WEB_PROXY")
        if configured_proxy is None:
            # Lowercase proxy variables are a standard convention used by HTTP clients.
            possible = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")  # noqa: SIM112
            if possible and urlsplit(possible).scheme in {"socks5", "socks5h"}:
                configured_proxy = possible
        if configured_proxy and urlsplit(configured_proxy).scheme not in {"socks5", "socks5h"}:
            raise ValueError("Research web proxy must be SOCKS5 to preserve pinned TLS hostnames")
        self.proxy = configured_proxy if transport is None else None
        # Run-local client: repeated pages/queries reuse bounded retrievals for
        # five minutes. Each agent call still archives its own source reference.
        self._cache: dict[str, tuple[float, _Page]] = {}

    async def _request(self, value: str) -> _Page:
        cached = self._cache.get(value)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                page = await self._request_with_redirects(value)
                if page.status == 200:
                    self._cache[value] = (time.monotonic(), page)
                    while len(self._cache) > 8:
                        self._cache.pop(next(iter(self._cache)))
                return page
        except TimeoutError as error:
            raise WebFetchError("Public web request timed out") from error
        except (httpx.HTTPError, OSError) as error:
            # Do not copy proxy credentials or arbitrary response bodies into diagnostics.
            raise WebFetchError(f"Public web request failed ({type(error).__name__})") from error

    async def _request_with_redirects(self, value: str) -> _Page:
        current = _url(value)
        async with httpx.AsyncClient(
            transport=self.transport,
            proxy=self.proxy,
            trust_env=False,
            follow_redirects=False,
            timeout=self.timeout_seconds,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"},
        ) as client:
            for redirect in range(self.max_redirects + 1):
                addresses = await self.resolver(
                    current.host, current.port or (443 if current.scheme == "https" else 80)
                )
                if not addresses or any(not _public_address(address) for address in addresses):
                    raise WebFetchError("DNS resolved to a non-public or unavailable address")
                # IPv4 first avoids requiring IPv6 from a configured SOCKS gateway.
                address = sorted(addresses, key=lambda item: ":" in item)[0]
                headers = {"Host": current.netloc.decode("ascii")}
                async with client.stream(
                    "GET",
                    current.copy_with(host=address),
                    headers=headers,
                    extensions={"sni_hostname": current.host},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect == self.max_redirects:
                            raise WebFetchError("Too many redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise WebFetchError("Redirect omitted Location")
                        current = _url(urljoin(str(current), location))
                        continue
                    if response.status_code >= 400:
                        raise WebFetchError(f"Public server returned HTTP {response.status_code}")
                    content_type = (
                        response.headers.get("content-type", "text/plain").split(";", 1)[0].lower()
                    )
                    if content_type == "application/pdf":
                        return _Page(str(current), response.status_code, content_type, "")
                    if not (
                        content_type.startswith("text/")
                        or content_type
                        in {
                            "application/json",
                            "application/xml",
                            "application/atom+xml",
                            "application/xhtml+xml",
                        }
                    ):
                        raise WebFetchError(f"Unsupported content type: {content_type}")
                    body, truncated = await self._body(response)
                    encoding = response.encoding or "utf-8"
                    try:
                        text = body.decode(encoding, errors="replace")
                    except LookupError:
                        text = body.decode("utf-8", errors="replace")
                    return _Page(str(current), response.status_code, content_type, text, truncated)
        raise WebFetchError("Unable to retrieve public URL")

    async def _body(self, response: httpx.Response) -> tuple[bytes, bool]:
        raw = bytearray()
        truncated = False
        if response.is_stream_consumed:
            body = response.content
            return body[: self.max_bytes], len(body) > self.max_bytes
        async for chunk in response.aiter_raw():
            remaining = self.max_bytes + 1 - len(raw)
            raw.extend(chunk[:remaining])
            if len(raw) > self.max_bytes:
                truncated = True
                break
        encoding = response.headers.get("content-encoding", "identity").lower()
        if encoding == "identity":
            return bytes(raw[: self.max_bytes]), truncated
        if truncated:
            raise WebFetchError("Compressed response exceeds download limit")
        try:
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    body = stream.read(self.max_bytes + 1)
            elif encoding == "deflate":
                body = zlib.decompressobj().decompress(bytes(raw), self.max_bytes + 1)
            else:
                raise WebFetchError(f"Unsupported content encoding: {encoding}")
        except (OSError, EOFError, zlib.error) as error:
            raise WebFetchError("Invalid compressed response") from error
        return body[: self.max_bytes], len(body) > self.max_bytes

    async def fetch_url(self, url: str, max_chars: int = 12000, offset: int = 0) -> dict[str, Any]:
        if not 1 <= max_chars <= 50000 or not 0 <= offset <= self.max_bytes:
            raise ValueError("max_chars must be 1–50000; offset must fit the download bound")
        requested = _url(url)
        limitations: list[str] = []
        page: _Page | None = None
        # Prefer actual paper content when arXiv provides an HTML rendering.
        if requested.host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            match = re.match(r"^/(?:abs|pdf)/(.+?)(?:\.pdf)?$", requested.path)
            if match and _ARXIV_ID.fullmatch(match[1]):
                try:
                    page = await self._request(f"https://arxiv.org/html/{match[1]}")
                except WebFetchError:
                    limitations.append(
                        "arXiv HTML full text was unavailable; the original URL is used."
                    )
        if page is None:
            page = await self._request(str(requested))
        title = ""
        if page.content_type == "application/pdf":
            text = ""
            limitations.append(
                "PDF text was not extracted. Use an HTML full-text version when available."
            )
        elif page.content_type in {"text/html", "application/xhtml+xml"}:
            title, text = _extract(page.text)
        else:
            text = page.text
        if page.download_truncated:
            limitations.append(
                "The document exceeded the bounded download size; later content is unavailable."
            )
        if urlsplit(page.url).hostname in {
            "arxiv.org",
            "export.arxiv.org",
            "www.arxiv.org",
        } and urlsplit(page.url).path.startswith("/abs/"):
            limitations.append("This is the paper's abstract page, not its full text.")
        end = min(offset + max_chars, len(text))
        return {
            "requested_url": str(requested),
            "url": page.url,
            "title": title[:500],
            "text": text[offset:end],
            "content_type": page.content_type,
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_chars": len(text),
            "truncated": end < len(text) or page.download_truncated,
            "download_truncated": page.download_truncated,
            "limitations": limitations,
            "untrusted": True,
        }

    async def search_web(self, query: str, max_results: int = 5) -> dict[str, Any]:
        global _BRAVE_NEXT_REQUEST
        query = " ".join(query.split())
        if not query or len(query) > 600 or not 1 <= max_results <= 10:
            raise ValueError("Search needs a nonempty query of at most 600 chars and 1–10 results")
        limitations: list[str] = []
        try:
            # Share request pacing, not source content, across concurrent runs.
            with _BRAVE_LOCK:
                current_time = time.monotonic()
                delay = max(0, _BRAVE_NEXT_REQUEST - current_time)
                _BRAVE_NEXT_REQUEST = max(current_time, _BRAVE_NEXT_REQUEST) + 3
            await asyncio.sleep(delay)
            page = await self._request(
                str(
                    httpx.URL(
                        "https://search.brave.com/search", params={"q": query, "source": "web"}
                    )
                )
            )
            brave_parser = _BraveResults()
            brave_parser.feed(page.text)
            results = [
                {key: " ".join(value.split())[:1200] for key, value in item.items()}
                for item in brave_parser.results
                if item["title"] and item["url"]
            ]
            results = _relevant(query, results)[:max_results]
            if results:
                return {
                    "query": query,
                    "results": results,
                    "provider": "brave_html",
                    "limitations": [],
                    "untrusted": True,
                }
            limitations.append("Brave returned no relevant parseable results.")
        except WebFetchError as error:
            limitations.append(f"Brave unavailable: {error}")
        try:
            page = await self._request(
                str(httpx.URL("https://html.duckduckgo.com/html/").copy_add_param("q", query))
            )
            if "anomaly.js" in page.text or "challenge-form" in page.text or page.status == 202:
                raise WebFetchError("DuckDuckGo served a bot challenge")
            parser = _DuckResults()
            parser.feed(page.text)
            results = [
                {
                    "title": " ".join(result["title"].split())[:500],
                    "url": result["url"],
                    "snippet": " ".join(result["snippet"].split())[:1200],
                }
                for result in parser.results
                if result["title"].strip()
            ]
            results = _relevant(query, results)[:max_results]
            if results:
                return {
                    "query": query,
                    "results": results,
                    "provider": "duckduckgo_html",
                    "limitations": limitations,
                    "untrusted": True,
                }
            limitations.append("DuckDuckGo returned no parseable results.")
        except WebFetchError as error:
            limitations.append(f"DuckDuckGo unavailable: {error}")
        try:
            results = _relevant(query, await self._bing(query, 10))[:max_results]
            if results:
                limitations.append(
                    "Bing RSS may relax search terms or operators; check result relevance."
                )
                return {
                    "query": query,
                    "results": results,
                    "provider": "bing_rss",
                    "limitations": limitations,
                    "untrusted": True,
                }
            limitations.append("Bing returned no relevant results; broad first-word hits omitted.")
        except WebFetchError as error:
            limitations.append(f"Bing unavailable: {error}")
        limitations.append(
            "Fallback searches scholarly metadata only; these are not general web results."
        )
        alternatives = await asyncio.gather(
            self._arxiv(query, max_results),
            self._crossref(query, max_results),
            return_exceptions=True,
        )
        combined: list[dict[str, str]] = []
        providers: list[str] = []
        for name, alternative in zip(("arxiv", "crossref"), alternatives, strict=True):
            if isinstance(alternative, BaseException):
                limitations.append(
                    f"{name} unavailable: {type(alternative).__name__}: {alternative}"
                )
            else:
                providers.append(name)
                combined.extend(alternative)
        unique = {item["url"]: item for item in _relevant(query, combined)}
        return {
            "query": query,
            "results": list(unique.values())[:max_results],
            "provider": "+".join(providers) or "unavailable",
            "limitations": limitations,
            "untrusted": True,
        }

    async def _bing(self, query: str, count: int) -> list[dict[str, str]]:
        page = await self._request(
            str(httpx.URL("https://www.bing.com/search", params={"q": query, "format": "rss"}))
        )
        if page.download_truncated or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", page.text, re.I):
            raise WebFetchError("Unsafe or truncated Bing RSS")
        try:
            feed = ET.fromstring(page.text)
        except ET.ParseError as error:
            raise WebFetchError("Invalid Bing RSS response") from error
        results = []
        for item in feed.findall("./channel/item"):
            title = " ".join((item.findtext("title") or "").split())
            link = item.findtext("link") or ""
            if not title or not link:
                continue
            _, snippet = _extract(item.findtext("description") or "")
            results.append(
                {"title": title[:500], "url": str(_url(link)), "snippet": snippet[:1200]}
            )
        return results[:count]

    async def _arxiv(self, query: str, count: int) -> list[dict[str, str]]:
        global _ARXIV_NEXT_REQUEST
        with _ARXIV_LOCK:
            now = time.monotonic()
            delay = max(0, _ARXIV_NEXT_REQUEST - now)
            _ARXIV_NEXT_REQUEST = max(now, _ARXIV_NEXT_REQUEST) + 3
        await asyncio.sleep(delay)
        url = httpx.URL(
            "https://export.arxiv.org/api/query",
            params={
                # A single all: prefix followed by bare terms uses implicit OR
                # and may rank unrelated papers first. Apply every keyword explicitly.
                "search_query": " AND ".join(
                    f'all:"{term}"' for term in re.findall(r"[\w-]+", query)[:40]
                ),
                "max_results": count,
                "sortBy": "relevance",
            },
        )
        page = await self._request(str(url))
        if page.download_truncated or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", page.text, re.I):
            raise WebFetchError("Unsafe or truncated arXiv XML")
        try:
            feed = ET.fromstring(page.text)
        except ET.ParseError as error:
            raise WebFetchError("Invalid arXiv response") from error
        namespace = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in feed.findall("a:entry", namespace):
            link = entry.findtext("a:id", namespaces=namespace) or ""
            if "/api/errors" in link:
                raise WebFetchError("arXiv rejected the search query")
            if not link:
                continue
            results.append(
                {
                    "title": " ".join(
                        (entry.findtext("a:title", namespaces=namespace) or "").split()
                    )[:500],
                    "url": str(_url(link)),
                    "snippet": " ".join(
                        (entry.findtext("a:summary", namespaces=namespace) or "").split()
                    )[:1200],
                    "source": "arxiv",
                }
            )
        return results

    async def _crossref(self, query: str, count: int) -> list[dict[str, str]]:
        url = httpx.URL(
            "https://api.crossref.org/works",
            params={"query": query, "rows": count, "select": "title,URL,DOI,abstract,published"},
        )
        page = await self._request(str(url))
        try:
            response = json.loads(page.text)
            items = response["message"]["items"]
            results = []
            for item in items:
                title = (item.get("title") or [""])[0]
                link = item.get("URL")
                if not title or not link:
                    continue
                _, snippet = _extract(item.get("abstract") or "")
                if not snippet:
                    snippet = f"DOI: {item.get('DOI', '')}. Abstract not supplied by the publisher."
                results.append(
                    {
                        "title": title[:500],
                        "url": str(_url(link)),
                        "snippet": snippet[:1200],
                        "source": "crossref",
                    }
                )
            return results
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise WebFetchError("Invalid Crossref response") from error


async def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    return await ResearchWebClient().search_web(query, max_results)


async def fetch_url(url: str, max_chars: int = 12000, offset: int = 0) -> dict[str, Any]:
    return await ResearchWebClient().fetch_url(url, max_chars, offset)
