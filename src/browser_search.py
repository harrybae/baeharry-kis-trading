"""Headless browser search helpers for JS-rendered pages (Naver, Daum Finance).

Playwright is used because requests/BeautifulSoup cannot parse JS-rendered
ranking tables. This module is intentionally separate from telegram_bot.py
to keep browser logic isolated and easy to test.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Shared browser instance for reuse within the process.
_browser: Optional[Browser] = None
_shared_context: Optional[BrowserContext] = None
_browser_lock = asyncio.Lock()
_browser_semaphore = asyncio.Semaphore(2)


async def _get_browser() -> Browser:
    """Return a shared async browser instance, launching one if needed."""
    global _browser
    async with _browser_lock:
        if _browser is None or not _browser.is_connected():
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(headless=True)
        return _browser


async def _get_context() -> BrowserContext:
    """Return a shared browser context, creating one if needed."""
    global _shared_context
    if _shared_context is None:
        browser = await _get_browser()
        _shared_context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
    return _shared_context


@asynccontextmanager
async def _new_page():
    """Create a new page in the shared context and close it when done."""
    context = await _get_context()
    page: Optional[Page] = None
    try:
        page = await context.new_page()
        yield page
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def close_browser() -> None:
    """Close the shared browser instance and context."""
    global _browser, _shared_context
    async with _browser_lock:
        if _shared_context:
            try:
                await _shared_context.close()
            except Exception:
                pass
            _shared_context = None
        if _browser and _browser.is_connected():
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None


def _extract_ai_briefing_table(html: str) -> List[Dict[str, Any]]:
    """Parse Naver AI briefing area for ranking tables or lists.

    Looks for text blocks that contain a ranking pattern such as
    "1 삼성전자 15,668,027 2 SK하이닉스 ..." and returns structured rows.
    Falls back to generic snippet collection if no table is found.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []

    # Strategy 1: find markdown-style table cells used in AI briefing.
    # Naver AI briefing renders flattened cells in groups of 3:
    #   rank | name | value  (e.g. '1', '삼성전자', '15,668,027')
    cells = soup.find_all("div", class_=re.compile(r"fds-markdown-td"))
    if cells and len(cells) >= 6:
        texts = [c.get_text(strip=True) for c in cells if c.get_text(strip=True)]
        # Group into chunks of 3; skip header rows where first token is not a rank.
        for i in range(0, len(texts) - 2, 3):
            rank_text, name, value = texts[i], texts[i + 1], texts[i + 2]
            rank_match = re.match(r"^\d+$", rank_text)
            if not rank_match:
                continue
            # AI briefing value may sometimes be a sector/tag (e.g. "2차전지")
            # instead of a market-cap number. Keep only numeric-looking values
            # as market cap; otherwise leave it empty so Daum can fill it.
            numeric_value = re.sub(r"[^0-9,.]", "", value)
            results.append(
                {
                    "source": "naver_ai_briefing_table",
                    "rank": int(rank_text),
                    "name": name,
                    "value": numeric_value if numeric_value else "",
                    "raw": f"{rank_text} | {name} | {value}",
                }
            )
        if results:
            return results

    # Strategy 2: parse AI briefing prose that embeds a numbered list.
    # e.g. "1 삼성전자 15,668,027 2 SK하이닉스 11,636,743 ..."
    for tag in soup.find_all(
        ["div", "span"], class_=re.compile(r"fds-markdown-p|sds-comps-text-type-body")
    ):
        text = tag.get_text(strip=True)
        if not text or "삼성전자" not in text and "코스피" not in text:
            continue
        # Find sequences like: 1 name number 2 name number ...
        pattern = re.compile(
            r"(?:(\d+)\s*([가-힣A-Za-z\.\-]+(?:우)?)\s*([0-9,\.]+)\s*)+"
        )
        for match in pattern.finditer(text):
            # Extract tokens from the matched region.
            region = match.group(0)
            tokens = re.findall(
                r"(\d+)\s+([가-힣A-Za-z\.\-]+(?:우)?)\s+([0-9,\.]+)", region
            )
            for rank, name, value in tokens:
                results.append(
                    {
                        "source": "naver_ai_briefing_text",
                        "rank": int(rank),
                        "name": name.strip(),
                        "value": value.strip(),
                        "raw": f"{rank} {name} {value}",
                    }
                )
        if results:
            return results

    # Strategy 3: collect any visible snippet containing stock-like numbers.
    seen: set = set()
    for tag in soup.find_all(["span", "div"]):
        text = tag.get_text(strip=True)
        if len(text) < 10 or len(text) > 300:
            continue
        if "삼성전자" in text or re.search(r"코스피.*순위", text):
            key = text[:80]
            if key not in seen:
                seen.add(key)
                results.append(
                    {
                        "source": "naver_ai_briefing_snippet",
                        "rank": None,
                        "name": "",
                        "value": "",
                        "raw": text,
                    }
                )
    return results


def _extract_naver_place_text(html: str) -> List[Dict[str, Any]]:
    """Parse Naver local-place embedded text from search.naver results.

    Naver's place SPA renders the full result as a flattened text blob under
    ``place-app-root``.  We split the blob by candidate markers and pull out
    blocks that contain a place name plus address/phone/hours clues.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    root = soup.find("div", id="place-app-root") or soup.find(class_=re.compile(r"place-app-root"))
    if not root:
        return results

    text = root.get_text(" ", strip=True)
    if not text or len(text) < 20:
        return results

    # Clean global UI noise first.
    text = re.sub(r"©\s*NAVER\s*Corp\.", "", text)
    text = re.sub(r"/OpenStreetMap", "", text)
    text = re.sub(r"지도\s*(?:보기|확대|축소|데이터|×|x)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()

    # Common Korean address/phone/hours markers.
    addr_markers = re.compile(r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|전라|경상|제주)[가-힣\s\d\-]+")
    phone_marker = re.compile(r"\d{2,4}-\d{3,4}-\d{4}")
    hours_markers = re.compile(r"(?:영업\s*(?:중|종료|시작|시간)|오(?:전|후)\s*\d{1,2}:\d{2}|\d{1,2}:\d{2}\s*에\s*영업|\d{1,2}:\d{2}\s*영업|24시간|휴무)")

    # Split into candidate chunks using distance markers.
    chunks = re.split(r"(?=\b(?:\d{1,3}(?:\.\d+)?km|\d+m)\b)", text)
    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 15:
            continue
        if not (phone_marker.search(chunk) or hours_markers.search(chunk) or addr_markers.search(chunk)):
            continue

        # Structured extraction.
        distance = ""
        distance_match = re.match(r"^(\d+m|\d+\.?\d*km)\b", chunk)
        if distance_match:
            distance = distance_match.group(1)

        address = ""
        addr_match = re.search(r"((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|전라|경상|제주)\s+[가-힣]+(?:구|군)\s+[가-힣]+(?:동|읍|면|리))", chunk)
        if addr_match:
            address = addr_match.group(1)

        phone = ""
        phone_match = phone_marker.search(chunk)
        if phone_match:
            phone = phone_match.group(0)

        # Category list (common Naver local categories).
        category_match = re.search(r"\b(슈퍼,마트|편의점|카페|식당|병원|약국|주유소|세탁소|은행|ATM|맛집)\b", chunk)
        category = category_match.group(1) if category_match else ""

        # Business hours / status.
        status = ""
        closing = ""
        status_match = re.search(r"(영업\s*중|곧\s*영업\s*종료|영업\s*종료|준비\s*중|24시간)", chunk)
        if status_match:
            status = status_match.group(1).replace(" ", "")
        closing_match = re.search(r"(\d{1,2}:\d{2})\s*에\s*영업\s*종료", chunk)
        if closing_match:
            closing = closing_match.group(1)

        holiday = ""
        holiday_match = re.search(r"(\d{1,2}/\d{1,2}\([^)]*\)\s*\d+(?:,\s*\d+)*번째\s*[^\s]+요일\s*휴무)", chunk)
        if holiday_match:
            holiday = holiday_match.group(1)

        # Build a snippet that preserves Naver's original text as much as possible
        # while only removing obvious UI noise. Do not synthesize a pipe table.
        snippet = chunk
        snippet = re.sub(r"©\s*NAVER\s*Corp\.", "", snippet)
        snippet = re.sub(r"/OpenStreetMap", "", snippet)
        snippet = re.sub(r"지도\s*(?:보기|확대|축소|데이터|×|x)", "", snippet, flags=re.IGNORECASE)
        snippet = re.sub(r"더보기|×|x\b|\bx", "", snippet, flags=re.IGNORECASE)
        snippet = re.sub(r"상세주소\s*열기|길찾기|거리뷰|공유|지금배달|안내|휠체어\s*출입\s*가능|현재\s*위치에서", "", snippet)
        snippet = re.sub(r"\s{2,}", " ", snippet).strip()

        # Name extraction: remove the same noise tokens, then take the first
        # Korean business-name-like token sequence.
        name = ""
        cleaned = snippet
        cleaned = re.sub(r"^(\d+m|\d+\.?\d*km)\s*", "", cleaned)
        cleaned = re.sub(re.escape(address) if address else r"$^", "", cleaned)
        cleaned = re.sub(re.escape(phone) if phone else r"$^", "", cleaned)
        cleaned = re.sub(re.escape(category) if category else r"$^", "", cleaned)
        cleaned = re.sub(re.escape(status) if status else r"$^", "", cleaned)
        cleaned = re.sub(r"\d{1,2}:\d{2}\s*에\s*영업\s*종료", "", cleaned)
        cleaned = re.sub(holiday if holiday else r"$^", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        name_match = re.search(r"([가-힣\sA-Za-z0-9]+?(?:점|마트|식자재|편의점|카페|병원|약국|주유소|은행))", cleaned)
        if name_match:
            name = name_match.group(1).strip()

        if not name:
            name = cleaned[:60]

        # Drop pagination-only noise blocks lacking concrete place attributes.
        if re.search(r"\b(이전|전체|다음)\b", chunk) and not (phone and category and status):
            continue

        # Keep the original Naver text (minus UI noise) as the snippet. Structured
        # fields are still returned for consumers that want them, but we no
        # longer synthesize a pipe-delimited summary or an "영업시간:" label.
        if not snippet:
            continue
        results.append(
            {
                "source": "naver_place_text",
                "title": name,
                "snippet": snippet,
                "distance": distance,
                "address": address,
                "phone": phone,
                "category": category,
                "status": status,
                "closing": closing,
                "holiday": holiday,
                "raw": chunk,
            }
        )
    return results


def _extract_naver_web_results(html: str) -> List[Dict[str, Any]]:
    """Collect general Naver web/local search snippets.

    Targets the local/place result module and ordinary organic result blocks.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    seen: set = set()

    # 1. Local/place module blocks (api_subject_bx contains place + blog cards).
    for block in soup.find_all("div", class_=re.compile(r"api_subject_bx|total_wrap|group_news|lst_total")):
        # Skip huge unrelated blobs.
        text = block.get_text(" ", strip=True)
        if len(text) > 600:
            text = text[:600]
        # Extract title from heading-like tags inside the block.
        title_tag = block.find(["a", "strong", "span"], class_=re.compile(r"title|tit|total_tit"))
        title = title_tag.get_text(strip=True)[:120] if title_tag else ""
        # Prefer a description paragraph.
        desc_tag = block.find(["div", "span", "p"], class_=re.compile(r"dsc|desc|txt|detail|answer_text"))
        snippet = desc_tag.get_text(strip=True)[:250] if desc_tag else text[:250]
        if not title:
            title = snippet[:80]
        key = (title[:60], snippet[:80])
        if key in seen or not snippet:
            continue
        seen.add(key)
        results.append(
            {
                "source": "naver_web_result",
                "title": title,
                "snippet": snippet,
                "raw": f"{title}\n{snippet}",
            }
        )

    if not results:
        for li in soup.find_all("li", class_=re.compile(r"bx|lst")):
            a = li.find("a", class_=re.compile(r"title|tit|link"))
            if not a:
                continue
            title = a.get_text(strip=True)[:120]
            desc = li.find(class_=re.compile(r"dsc|desc|txt"))
            snippet = desc.get_text(strip=True)[:250] if desc else ""
            key = (title[:60], snippet[:80])
            if key in seen or not (title or snippet):
                continue
            seen.add(key)
            results.append(
                {
                    "source": "naver_web_result",
                    "title": title,
                    "snippet": snippet,
                    "raw": f"{title}\n{snippet}",
                }
            )
    return results


async def search_naver_browser(
    query: str, max_results: int = 5, location: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Run a Naver search in a headless browser and extract usable data.

    For market-ranking queries without a location, the AI briefing table is used.
    For location-aware queries, Naver Map search is tried first, then the
    general Naver search page is parsed for local/web snippets.

    Args:
        query: Search query string.
        max_results: Maximum number of result items to return.
        location: Optional location context (e.g. district/dong).  When provided,
            browser search is performed in dong -> gu -> broader fallback order
            by the caller; this function simply uses the supplied location.

    Returns:
        List of dicts.  Ranking rows have keys: source, rank, name, value, raw.
        Local/web rows have keys: source, title, snippet, raw.
    """
    async with _browser_semaphore:
        try:
            async with _new_page() as page:
                search_query = f"{location} {query}".strip() if location else query
                encoded_query = search_query.replace(" ", "+")

                # Use the regular Naver search page; it embeds a place-app-root
                # SPA for local queries, which _extract_naver_place_text handles.
                url = f"https://search.naver.com/search.naver?query={encoded_query}"
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(1.5)
                html = await page.content()

                # Market-ranking queries: prefer AI briefing table.
                if not location:
                    rows = _extract_ai_briefing_table(html)
                    ranked = [r for r in rows if r.get("rank") is not None]
                    ranked.sort(key=lambda x: x["rank"])
                    if ranked:
                        return ranked[:max_results]
                    if rows:
                        return rows[:max_results]

                # Location-aware or fallback queries: parse place/web snippets.
                rows = _extract_naver_place_text(html)
                if not rows:
                    rows = _extract_naver_web_results(html)
                return rows[:max_results]
        except Exception as exc:
            return [{"source": "naver_browser_error", "error": str(exc)}]


def _extract_daum_market_cap_table(html: str, market: str = "kospi") -> List[Dict[str, Any]]:
    """Parse Daum Finance market-cap page for top ranked stocks.

    The page renders a plain HTML table with rows:
      rank | name | price | change | change_rate | market_cap | shares | foreign

    Args:
        html: Rendered page HTML.
        market: 'kospi' or 'kosdaq'.

    Returns:
        List of dicts with rank, name, price, change_rate, market_cap.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    results: List[Dict[str, Any]] = []
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_text = rows[0].get_text(strip=True)
        if "종목명" not in header_text or "시가총액" not in header_text:
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            texts = [c.get_text(strip=True) for c in cells]
            if len(texts) < 5:
                continue
            # texts layout: [rank, name, price, change, change_rate, market_cap, ...]
            rank_match = re.match(r"^\d+$", texts[0])
            if not rank_match:
                continue
            results.append(
                {
                    "source": f"daum_{market}_market_cap",
                    "rank": int(texts[0]),
                    "name": texts[1],
                    "price": texts[2] if len(texts) > 2 else "",
                    "change_rate": texts[4] if len(texts) > 4 else "",
                    "market_cap": texts[5] if len(texts) > 5 else "",
                    "raw": " | ".join(texts),
                }
            )
    return results


async def search_daum_market_cap_browser(
    market: str = "kospi", max_results: int = 5
) -> List[Dict[str, Any]]:
    """Open finance.daum.net market-cap page and extract top rankings.

    Args:
        market: 'kospi' or 'kosdaq'.
        max_results: Maximum rows to return.

    Returns:
        List of dicts with rank, name, price, change_rate, market_cap.
    """
    async with _browser_semaphore:
        try:
            async with _new_page() as page:
                url = f"https://finance.daum.net/domestic/market_cap?market={market.upper()}"
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(1.5)
                html = await page.content()
                rows = _extract_daum_market_cap_table(html, market=market)
                rows.sort(key=lambda x: x["rank"])
                return rows[:max_results]
        except Exception as exc:
            return [{"source": "daum_browser_error", "error": str(exc)}]


def evaluate_search_quality(query: str, results: List[Dict[str, Any]]) -> bool:
    """Check whether existing search results are good enough to answer the query.

    For market ranking queries, we require at least a few results and some
    evidence that ranking/stock data is present (Korean stock names + numbers).
    """
    if not results or len(results) < 2:
        return False

    query_lower = query.lower()
    is_ranking_query = any(
        k in query_lower
        for k in ["코스피", "코스닥", "시가총액", "거래대금", "거래량", "순위", "상위"]
    )

    if not is_ranking_query:
        # General query: require non-empty title/snippet text.
        for r in results:
            if any(str(r.get(k, "")).strip() for k in ("title", "snippet", "hours")):
                return True
        return False

    # Ranking query: require stock-like names AND market-sized numbers.
    # We avoid hardcoding stock names; instead we look for multi-character
    # Korean tokens paired with values that have stock units or large comma
    # separated numbers.
    name_pattern = re.compile(r"[가-힣A-Za-z]{2,}(?:[\._\-]?[가-힣A-Za-z0-9]+)*")
    large_number_pattern = re.compile(r"\d{1,3}(?:,\d{3}){2,}")
    unit_pattern = re.compile(r"(?:원|억|조|만|천|백|%)")

    hits = 0
    for r in results:
        text = " ".join(str(r.get(k, "")) for k in ("name", "title", "snippet", "hours"))
        names = name_pattern.findall(text)
        if not names:
            continue
        # Require at least one value that looks like a market metric.
        has_metric = bool(
            large_number_pattern.search(text) or unit_pattern.search(text)
        )
        if has_metric:
            hits += 1
        if hits >= 2:
            return True
    return False


async def search_market_ranking_browser(
    query: str, market: str = "kospi", max_results: int = 5
) -> List[Dict[str, Any]]:
    """Parallel browser search for market ranking queries.

    Runs Naver AI briefing and Daum Finance market-cap search concurrently.
    Returns a merged, deduplicated list of ranking rows.
    """
    naver_task = search_naver_browser(query, max_results=max_results)
    daum_task = search_daum_market_cap_browser(market, max_results=max_results)
    naver_rows, daum_rows = await asyncio.gather(naver_task, daum_task)

    # Filter out error-only payloads.
    naver_rows = [r for r in naver_rows if "error" not in r]
    daum_rows = [r for r in daum_rows if "error" not in r]

    merged: List[Dict[str, Any]] = []
    seen: set = set()
    # Prefer Daum rows (more fields: price, change_rate, market_cap).
    for r in daum_rows:
        name = r.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        merged.append(r)
    # Fill in missing names from Naver only when Daum is incomplete.
    for r in naver_rows:
        name = r.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        merged.append(r)

    # Re-rank by numeric market cap (largest first). Daum provides market_cap;
    # Naver rows use value. Rows without a usable market metric sink to the end.
    def _market_cap_numeric(r: Dict[str, Any]) -> int:
        cap = str(r.get("market_cap", "") or r.get("value", "")).replace(",", "")
        try:
            return int(cap)
        except ValueError:
            return 0

    merged.sort(key=lambda x: (-_market_cap_numeric(x), x.get("rank", 999)))
    for i, r in enumerate(merged, start=1):
        r["rank"] = i
    return merged[:max_results]


def format_market_ranking(rows: List[Dict[str, Any]]) -> str:
    """Convert browser-extracted ranking rows to a Telegram-friendly string.

    Values are emitted as returned by the source page, without injecting
    labels such as "현재가", "등락" or "시총".
    """
    if not rows:
        return ""
    lines = ["📊 시장 순위 (브라우저 검색)"]
    for r in rows:
        rank = r.get("rank")
        name = r.get("name", "")
        price = r.get("price", "")
        change_rate = r.get("change_rate", "")
        market_cap = r.get("market_cap", "")
        value = r.get("value", "")
        parts = [f"{rank}. {name}"]
        if price:
            parts.append(str(price))
        if change_rate:
            parts.append(str(change_rate))
        numeric_cap = re.sub(r"[^0-9,]", "", str(market_cap or value))
        if numeric_cap:
            parts.append(numeric_cap)
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_local_results(rows: List[Dict[str, Any]]) -> str:
    """Convert browser-extracted local/web rows to a Telegram-friendly string.

    The snippet is kept close to Naver's original text; we do not synthesize
    labels like "영업시간:" or force a pipe-delimited table.
    """
    if not rows:
        return ""
    lines = ["📍 근처 정보 (브라우저 검색)"]
    for i, r in enumerate(rows, start=1):
        title = str(r.get("title", "")).strip()
        snippet = str(r.get("snippet", "")).strip()
        # Avoid repeating the title at the very start of the snippet, but only
        # when it is an exact natural prefix; this prevents visual duplication
        # without rewriting Naver's data.
        if title and snippet.startswith(title):
            snippet = snippet[len(title):].strip()
        parts = [f"{i}. {title}"] if title else [f"{i}."]
        if snippet:
            parts.append(snippet)
        parts = [p.strip() for p in parts if p.strip()]
        line = " | ".join(parts)
        line = re.sub(r"\s*\|\s*\|\s*", " | ", line)
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    q = "지금 코스피 순위 5개"
    print("=== Naver ===")
    print(asyncio.run(search_naver_browser(q)))
    print("\n=== Daum KOSPI ===")
    print(asyncio.run(search_daum_market_cap_browser("kospi")))
    print("\n=== Daum KOSDAQ ===")
    print(asyncio.run(search_daum_market_cap_browser("kosdaq")))
    print("\n=== Naver local ===")
    rows = asyncio.run(search_naver_browser("대형마트 영업시간", location="염창동"))
    print(rows)
    print(format_local_results(rows))
