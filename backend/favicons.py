"""Provider favicon extraction, processing, caching, and serving.

On startup/reload, fetches favicons and page titles from provider api_urls by
extracting the root domain. Always fetches the homepage for the page title,
then tries /favicon.ico first, falling back to <link rel="icon"> from the
homepage HTML. Skips providers that already have a cached file.

Raster icons are processed with Pillow: alpha-composited onto a white matte,
resized to 32×32, and saved as PNG. SVGs get a white matte background rect
injected. Logos are served inline as base64 data URIs via /api/providers.
Titles are cached in memory and exposed via /api/providers.
"""

import asyncio
import base64
import httpx
import re
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urljoin

import backend.state as st
import backend.db as db

FAVICON_DIR = st.DATA_DIR / "favicons"
FAVICON_DIR.mkdir(exist_ok=True)

_MAX_ICON_BYTES = 500_000
_MAX_HTML_BYTES = 500_000
_FETCH_TIMEOUT = 15
_CONCURRENCY = 2
_LOGO_SIZE = 32
_MATTE_COLOR = (255, 255, 255)
_VALID_EXTS = frozenset({".svg", ".png"})


def _provider_slug(provider_name: str) -> str:
    return provider_name.replace(" ", "_")

_favicon_task: asyncio.Task | None = None

_ICON_LINK_RE = re.compile(r'<link\s[^>]*?rel\s*=\s*["\'](?:shortcut\s+)?icon["\'][^>]*?>', re.IGNORECASE)
_HREF_RE = re.compile(r'href\s*=\s*(?:["\']([^"\']*)["\']|([^\s>]+))', re.IGNORECASE)
_SIZES_RE = re.compile(r'sizes\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_SVG_SIGNATURES = (b"<?xm", b"<svg")
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)


def root_url(base_url: str) -> str:
    """Extract scheme + registered domain from a URL, stripping subdomains and path.

    Takes the last 2 netloc parts (domain + TLD), which works for all
    common domain structures but fails on multi-part TLDs (e.g. .co.uk).
    No current provider uses a multi-part TLD.

    Examples: https://api.deepseek.com/v1 → https://deepseek.com
              https://nano-gpt.com/api/v1 → https://nano-gpt.com
              https://inference.api.novita.ai/v3 → https://novita.ai
    """
    # Defense-in-depth: config.py normalizes scheme at load, but root_url()
    # may be called from other paths that skip that normalization.
    if "://" not in base_url:
        base_url = f"https://{base_url}"
    parsed = urlparse(base_url)
    parts = parsed.netloc.split(".")
    domain = ".".join(parts[-2:]) if len(parts) > 2 else parsed.netloc
    return f"{parsed.scheme}://{domain}"


def _is_svg(content: bytes) -> bool:
    return any(content[:len(s)] == s for s in _SVG_SIGNATURES)


def _process_raster(content: bytes) -> bytes | None:
    """Composite a raster icon onto white matte, resize to 32×32, export PNG.

    Returns None if Pillow is unavailable or processing fails.
    """
    if not st.pillow_available:
        return None
    try:
        img = st.Image.open(BytesIO(content))
        img = img.convert("RGBA")
        bg = st.Image.new("RGBA", img.size, (*_MATTE_COLOR, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
        if img.size != (_LOGO_SIZE, _LOGO_SIZE):
            img = img.resize((_LOGO_SIZE, _LOGO_SIZE), st.Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        st.log_error("Favicon: raster processing failed", e)
        return None


def _process_svg(content: bytes) -> bytes:
    """Inject a white matte background rect into an SVG.

    Finds the opening <svg> tag and inserts a <rect> right after it,
    ensuring it paints behind all other elements (SVG paints in document order).
    Handles XML declarations and self-closing SVGs.
    """
    svg = content.decode("utf-8", errors="replace")
    svg_start = svg.find("<svg")
    if svg_start < 0:
        return content
    tag_end = svg.find(">", svg_start)
    if tag_end < 0:
        return content
    r, g, b = _MATTE_COLOR
    bg_rect = f'<rect width="100%" height="100%" fill="rgb({r},{g},{b})"/>'
    insert_pos = tag_end + 1
    if svg[tag_end - 1:tag_end] == "/":
        svg = svg[:tag_end - 1] + ">" + svg[tag_end + 1:]
        insert_pos = tag_end
    return (svg[:insert_pos] + bg_rect + svg[insert_pos:]).encode("utf-8")


def _icon_priority(tag: str, href: str) -> int:
    """Rank icon links: SVG (0, ideal at any size) < PNG near 32px < large PNG < ICO."""
    ext = Path(urlparse(href).path).suffix.lower()
    if ext == ".svg":
        return 0
    sizes = _SIZES_RE.search(tag)
    if sizes:
        try:
            w = int(sizes.group(1).split("x")[0])
            return abs(w - 32) + 1
        except (ValueError, IndexError):
            pass
    if ext in (".png", ".webp"):
        return 50
    return 100


def _extract_title(html: str) -> str | None:
    """Extract and clean the <title> text from HTML.

    Unescapes HTML entities and strips whitespace. Returns None if missing or empty.
    """
    m = _TITLE_RE.search(html)
    if not m:
        return None
    from html import unescape
    title = unescape(m.group(1)).strip()
    return title or None


def _extract_icon_url(html: str, base_url: str) -> str | None:
    """Extract the best favicon URL from <link rel="icon"> tags in the HTML head.

    Prefers SVG (resolution-independent), then the PNG closest to 32×32.
    Skips data: URIs. Returns None if no suitable link is found.
    """
    head_end = html.lower().find("</head>")
    head = html[:head_end] if head_end > 0 else html
    candidates: list[tuple[int, str]] = []
    for match in _ICON_LINK_RE.finditer(head):
        href = _HREF_RE.search(match.group(0))
        href_val = href.group(1) or href.group(2)
        if href_val and not href_val.startswith("data:"):
            url = urljoin(base_url, href_val)
            candidates.append((_icon_priority(match.group(0), url), url))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


async def _fetch_homepage(client: httpx.AsyncClient, domain: str, timeout: httpx.Timeout) -> tuple[str | None, str | None]:
    """Fetch the homepage HTML at domain. Returns (html, final_url).

    final_url is the URL after all redirects - may be on a different host than
    domain (e.g. umans.ai → app.umans.ai). Used by _fetch_icon to fetch the
    favicon from the redirected host instead of the original.

    When the page exceeds _MAX_HTML_BYTES, truncates to that limit rather than
    discarding entirely - the <head> section with favicon links and <title> is
    almost always near the top of the HTML.
    """
    try:
        resp = await client.get(domain, timeout=timeout, follow_redirects=True,
                                headers={"Accept": "text/html"})
        if resp.status_code != 200:
            return None, None
        ct = (resp.headers.get("content-type", "") or "").lower()
        if "html" not in ct:
            return None, None
        final_url = str(resp.url)
        if len(resp.content) > _MAX_HTML_BYTES:
            st.log.debug("Favicon: homepage too large (%d bytes), truncating to %d for head parse",
                         len(resp.content), _MAX_HTML_BYTES)
            return resp.content[:_MAX_HTML_BYTES].decode("utf-8", errors="replace"), final_url
        return resp.text, final_url
    except Exception as e:
        st.log_error(f"Favicon: homepage fetch failed for {domain}", e)
        return None, None


def _looks_like_icon(resp: httpx.Response) -> bool:
    """Check if a response is a valid icon image.

    Uses Content-Type first, falls back to magic-byte sniffing for servers
    that don't send a content-type header (e.g., kimi.com via Cloudflare).
    """
    ct = (resp.headers.get("content-type", "") or "").lower()
    if ct.startswith("image/"):
        return True
    if resp.status_code != 200 or not resp.content:
        return False
    content = resp.content
    if _is_svg(content):
        return True
    if content[:4] == b"\x00\x00\x01\x00":
        return True
    if content[:3] == b"\x89PN":
        return True
    if content[:2] == b"\xff\xd8":
        return True
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


async def _fetch_icon(client: httpx.AsyncClient, domain: str, timeout: httpx.Timeout,
                      provider_name: str) -> tuple[httpx.Response | None, str | None, str | None]:
    """Fetch homepage for title, then try HTML <link rel="icon">, then /favicon.ico.

    Returns (response, url, title). When no icon is found, response and url are None
    but title may still be present. The homepage is always fetched for the page title.

    If the homepage redirects to a different host (e.g. umans.ai → app.umans.ai),
    the favicon is fetched from the redirected host, not the original domain.

    Icon URLs are extracted from the homepage HTML first (no extra discovery
    request - we already have the HTML). /favicon.ico is tried as a fallback
    only when no <link rel="icon"> tags are found or the extracted URL fails.
    """
    homepage, final_url = await _fetch_homepage(client, domain, timeout)
    title = _extract_title(homepage) if homepage else None

    # Use the redirected host for favicon fetching - static assets live on the
    # final host, which may differ from the root_url() derived domain.
    if final_url:
        parsed_final = urlparse(final_url)
        base_domain = f"{parsed_final.scheme}://{parsed_final.netloc}"
    else:
        base_domain = domain

    # Try icon URLs discovered from the homepage HTML first (free - we already
    # have the HTML), then fall back to the /favicon.ico convention.
    tried: set[str] = set()
    if homepage:
        icon_url = _extract_icon_url(homepage, base_domain)
        if icon_url and icon_url not in tried:
            tried.add(icon_url)
            resp = await client.get(icon_url, timeout=timeout, follow_redirects=True)
            if _looks_like_icon(resp):
                return resp, icon_url, title
            st.log.debug("Favicon: %s - HTML <link rel=icon> %s returned %d ct=%s",
                         provider_name, icon_url, resp.status_code,
                         resp.headers.get("content-type", "") or "empty")

    # Fall back to the standard /favicon.ico convention
    icon_url = f"{base_domain}/favicon.ico"
    if icon_url not in tried:
        resp = await client.get(icon_url, timeout=timeout, follow_redirects=True)
        if _looks_like_icon(resp):
            return resp, icon_url, title
        st.log.debug("Favicon: %s - /favicon.ico returned %d ct=%s",
                     provider_name, resp.status_code, resp.headers.get("content-type", "") or "empty")

    st.log.debug("Favicon: %s - no favicon found", provider_name)
    return None, None, title


def _client_and_domain(base_url: str) -> tuple[httpx.AsyncClient, str, httpx.Timeout]:
    """Return (client, root_url, timeout) for a provider base URL."""
    return st.get_http_client(), root_url(base_url), httpx.Timeout(_FETCH_TIMEOUT, connect=5)


async def _save_title(provider_name: str, base_url: str, title: str | None, *, mark_fetched: bool = False) -> None:
    """Persist a page title to SQLite (fire-and-forget fallback when no icon found)."""
    if title or mark_fetched:
        await asyncio.to_thread(db.upsert_provider, provider_name, base_url,
                                page_title=title,
                                last_fetched_at=time.time() if mark_fetched else None)
        if title:
            st.invalidate_providers_cache()
            st.invalidate_model_info_response_cache()


async def fetch_provider_favicon(provider_name: str, base_url: str) -> str | None:
    """Fetch a provider favicon, process it, and cache it. Returns the local filename or None.

    Raster icons are composited onto a white matte, resized to 32×32, saved as PNG.
    SVGs get a white matte background rect injected. Tries HTML <link rel="icon">
    first, then /favicon.ico. Also extracts and caches the page title.
    """
    try:
        client, domain, timeout = _client_and_domain(base_url)

        resp, icon_url, title = await _fetch_icon(client, domain, timeout, provider_name)

        if resp is None:
            await _save_title(provider_name, base_url, title, mark_fetched=True)
            return None

        if len(resp.content) > _MAX_ICON_BYTES:
            st.log.info("Favicon: %s too large (%d bytes, limit %d)", provider_name, len(resp.content), _MAX_ICON_BYTES)
            await _save_title(provider_name, base_url, title, mark_fetched=True)
            return None

        content = resp.content
        is_svg = _is_svg(content)
        slug = _provider_slug(provider_name)

        for old in FAVICON_DIR.glob(f"{slug}.*"):
            old.unlink(missing_ok=True)

        filepath = None
        if is_svg:
            processed = _process_svg(content)
            filepath = FAVICON_DIR / f"{slug}.svg"
            filepath.write_bytes(processed)
            st.log.debug("Favicon: saved %s (svg, %d bytes)%s", provider_name, len(processed),
                         "" if icon_url == f"{domain}/favicon.ico" else f" [from {icon_url}]")
        else:
            processed = _process_raster(content)
            if processed:
                filepath = FAVICON_DIR / f"{slug}.png"
                filepath.write_bytes(processed)
                st.log.debug("Favicon: saved %s (png, %d→%d bytes)%s", provider_name, len(content), len(processed),
                             "" if icon_url == f"{domain}/favicon.ico" else f" [from {icon_url}]")
            else:
                st.log.warning("Favicon: %s - Pillow unavailable or processing failed, skipping", provider_name)
                await _save_title(provider_name, base_url, title, mark_fetched=True)
                return None

        await asyncio.to_thread(db.upsert_provider, provider_name, base_url,
                                page_title=title, logo_path=filepath.name if filepath else None,
                                last_fetched_at=time.time())
        st.invalidate_providers_cache()
        st.invalidate_model_info_response_cache()
        return filepath.name if filepath else None

    except Exception as e:
        st.log_error(f"Favicon: failed for {provider_name}", e)
        try:
            await asyncio.to_thread(db.upsert_provider, provider_name, last_fetched_at=time.time())
        except Exception as e2:
            st.log_error(f"Favicon: failed to update provider fetch time for {provider_name}", e2)
        return None


async def fetch_all_favicons():
    """Fetch favicons for providers with stale or missing metadata."""
    try:
        providers = st.models_cfg.get("providers", [])
        stale_providers = await asyncio.to_thread(db.providers_needing_fetch, st.c.provider_fetch_ttl)
        stale_set = set(stale_providers)
        favicon_tasks = []
        title_tasks = []
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _limited_favicon(name, url):
            async with sem:
                await fetch_provider_favicon(name, url)

        async def _limited_title(name, url):
            async with sem:
                await _fetch_provider_title(name, url)

        for p in providers:
            name = p.get("name", "")
            url = p.get("provider_url") or p.get("api_url", "")
            if not name or not url:
                continue
            if name in stale_set or not provider_logo_path(name):
                favicon_tasks.append(_limited_favicon(name, url))
            else:
                prov = db.get_provider(name)
                if not prov or not prov.get("page_title"):
                    title_tasks.append(_limited_title(name, url))

        if favicon_tasks:
            st.log.info("Favicon: fetching for %d providers", len(favicon_tasks))
            await asyncio.gather(*favicon_tasks)

        # Fetch titles for providers that already have cached favicons but no title
        for p in providers:
            name = p.get("name", "")
            url = p.get("provider_url") or p.get("api_url", "")
            if not name or not url:
                continue
            if name in stale_set or not provider_logo_path(name):
                continue  # already fetched above
            prov = db.get_provider(name)
            if not prov or not prov.get("page_title"):
                title_tasks.append(_limited_title(name, url))

        if title_tasks:
            st.log.info("Favicon: fetching titles for %d providers", len(title_tasks))
            await asyncio.gather(*title_tasks)
    except Exception as e:
        st.log_error("Favicon: fetch_all_favicons failed", e)


async def _fetch_provider_title(provider_name: str, base_url: str) -> None:
    """Fetch and cache the page title for a provider."""
    try:
        client, domain, timeout = _client_and_domain(base_url)
        homepage, _ = await _fetch_homepage(client, domain, timeout)
        title = _extract_title(homepage) if homepage else None
        await asyncio.to_thread(db.upsert_provider, provider_name,
                                page_title=title,
                                last_fetched_at=time.time())
        if title:
            st.invalidate_providers_cache()
            st.invalidate_model_info_response_cache()
    except Exception as e:
        st.log_error(f"Favicon: title fetch failed for {provider_name}", e)


def provider_logo_path(provider_name: str) -> Path | None:
    """Find the cached favicon file for a provider. Returns Path or None."""
    slug = provider_name.replace(" ", "_")
    for f in FAVICON_DIR.glob(f"{slug}.*"):
        if f.is_file() and f.stat().st_size > 0 and f.suffix in _VALID_EXTS:
            return f
    return None


_logo_data_cache: dict[str, tuple[float, str]] = {}


def provider_logo_data_uri(provider_name: str) -> str | None:
    """Return a base64 data URI for a provider's logo, cached by mtime.

    Returns None if no logo file exists. The data URI includes the full
    image content inline, eliminating separate HTTP requests for logos.
    """
    path = provider_logo_path(provider_name)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    cached = _logo_data_cache.get(provider_name)
    if cached and cached[0] == mtime:
        return cached[1]
    body = path.read_bytes()
    ct = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    b64 = base64.b64encode(body).decode()
    data_uri = f"data:{ct};base64,{b64}"
    _logo_data_cache[provider_name] = (mtime, data_uri)
    return data_uri


def start_favicon_fetch():
    """Start the favicon fetch as a fire-and-forget background task."""
    global _favicon_task
    if _favicon_task and not _favicon_task.done():
        _favicon_task.cancel()
    _favicon_task = st.create_task(fetch_all_favicons(), name="fetch_all_favicons")
