import asyncio
import sys
import os
import json
import re
import time
import functools
from typing import Tuple

# 모듈 경로 추가 (src 내부에서 직접 실행할 때 상위 폴더의 config/kis_api 접근)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if __file__ else os.path.dirname(os.path.abspath("."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from src.nl_handlers import create_nl_conv_handler
from typing import Any, Dict, List, Optional, Tuple
import requests
import ollama
from bs4 import BeautifulSoup
import config
from src import kis_api
from src import browser_search as _browser_search

TELEGRAM_TOKEN = config.TELEGRAM_TOKEN

# ── Google Gemini (web search grounding) ──────────────────────────
try:
    from google import genai
    _GEMINI_AVAILABLE = True
except Exception as _gemini_import_err:
    _GEMINI_AVAILABLE = False
    print(f"[Gemini import skipped] {_gemini_import_err}")

_GEMINI_CLIENT: Any = None

def _get_gemini_client() -> Any:
    """GEMINI_API_KEY가 있을 때만 클라이언트를 초기화합니다."""
    global _GEMINI_CLIENT
    if not _GEMINI_AVAILABLE:
        return None
    if _GEMINI_CLIENT is None:
        key = getattr(config, "GEMINI_API_KEY", "")
        if not key:
            return None
        _GEMINI_CLIENT = genai.Client(api_key=key)
    return _GEMINI_CLIENT


async def _query_gemini_grounded(user_message: str) -> str:
    """Gemini web_search grounding으로 실시간 질문에 답변합니다."""
    client = _get_gemini_client()
    if not client:
        return ""
    try:
        model = getattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=user_message,
            config={"tools": [{"google_search": {}}]},
        )
        text = (response.text or "").strip()
        if not text:
            return ""
        return f"[Gemini 실시간 검색 결과]\n{text}"
    except Exception as e:
        print(f"[Gemini grounded query failed] {e}")
        return ""


async def _summarize_with_gemini(user_message: str, search_text: str) -> str:
    """기존 검색 결과를 Gemini 기본 모델로 요약합니다. (grounding fallback)"""
    client = _get_gemini_client()
    if not client:
        return ""
    if not search_text or len(search_text) > 12000:
        search_text = search_text[:12000]
    prompt = (
        "다음 웹 검색 결과를 바탕으로 사용자 질문에 답변해줘.\n\n"
        f"[사용자 질문]\n{user_message}\n\n"
        f"[검색 결과]\n{search_text}\n\n"
        "실시간 주식/시장 정보를 표 형태로 정리하고, 출처가 있다면 날짜/시간을 함께 표시해줘."
    )
    try:
        model = getattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=prompt,
        )
        text = (response.text or "").strip()
        if not text:
            return ""
        return f"[Gemini 검색 요약]\n{text}"
    except Exception as e:
        print(f"[Gemini summarize fallback failed] {e}")
        return ""


TELEGRAM_TOKEN = config.TELEGRAM_TOKEN
CHAT_ID = config.TELEGRAM_CHAT_ID
conversation_history = []

# ── 검색/지오코딩 결과 단기 캐시 ─────────────────────────────────────
class _TimedCache:
    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        self._data: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str):
        if key not in self._data:
            return None
        ts, value = self._data[key]
        if time.time() - ts > self._ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any):
        self._data[key] = (time.time(), value)

    def clear(self):
        self._data.clear()


_search_cache = _TimedCache(ttl_seconds=300.0)
_geocode_cache = _TimedCache(ttl_seconds=600.0)

# 외부 HTTP 요청 동시 실행 제한 (Naver rate-limit / 403 방지)
_HTTP_SEMAPHORE = asyncio.Semaphore(3)


def _perf_log(step: str, start: float):
    print(f"[PERF] {step}: {time.perf_counter() - start:.2f}s")


# ── 날씨 조회 ─────────────────────────────────────────────────────

def get_weather(city="Seoul", lat: Optional[float] = None, lon: Optional[float] = None):
    """wttr.in API로 날씨 조회 (API 키 불필요). city 또는 lat,lon 사용."""
    if lat is not None and lon is not None:
        location = f"{lat},{lon}"
    else:
        location = city
    url = f"https://wttr.in/{location}?format=j1&lang=ko"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()

    current = data["current_condition"][0]
    area = data["nearest_area"][0]
    city_name = area["areaName"][0]["value"]
    country = area["country"][0]["value"]

    temp = current["temp_C"]
    feels = current["FeelsLikeC"]
    humidity = current["humidity"]
    desc = current["lang_ko"][0]["value"] if current.get("lang_ko") else current["weatherDesc"][0]["value"]
    wind = current["windspeedKmph"]

    return (
        f"🌤 {city_name}, {country} 날씨\n"
        f"━━━━━━━━━━━━━━\n"
        f"날씨: {desc}\n"
        f"기온: {temp}°C (체감 {feels}°C)\n"
        f"습도: {humidity}%\n"
        f"풍속: {wind}km/h"
    )


# ── 웹 검색 ──────────────────────────────────────────────────────

def search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """DuckDuckGo로 검색 (API 키 불필요, 무료)"""
    try:
        from ddgs import DDGS
        with DDGS(timeout=10) as ddgs:
            results = ddgs.text(query, region="kr-kr", max_results=max_results)
            return [
                {"title": r.get("title", ""), "link": r.get("href", ""), "snippet": r.get("body", "")}
                for r in results
            ]
    except Exception as e:
        print(f"[DuckDuckGo search failed] {e}")
        return []


def _extract_status_from_text(text: str) -> str:
    """텍스트에서 영업/운영/휴무 상태를 우선적으로 추출합니다."""
    if not text:
        return ""
    # 우선순위 높은 상태 키워드
    status_keywords = [
        "24시간 영업", "24시간 운영", "24시간",
        "연중무휴",
        "영업 중", "영업중", "운영 중", "운영중", "영업중임",
        "영업 종료", "영업종료", "운영 종료", "운영종료", "영업마감",
        "영업 준비 중", "영업준비중", "준비 중", "준비중", "오픈준비",
        "오늘 휴무", "오늘휴무", "휴무일", "정기 휴무", "정기휴무",
        "임시 휴무", "임시휴무", "공휴일 휴무",
    ]
    for kw in status_keywords:
        if kw in text:
            return kw
    return ""


def _build_status_summary(results: list, today: str) -> str:
    """장소 검색 결과에서 상태/시간을 간단히 요약합니다."""
    lines = [f"오늘({today}) 기준 상태 요약:"]
    for r in results[:5]:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        snippet = (r.get("snippet") or "").replace("\n", " ")
        hours = (r.get("hours") or "").replace("\n", " ")
        status = _extract_status_from_text(hours) or _extract_status_from_text(snippet)
        # 시간 패턴이 있으면 함께 표시
        time_match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", f"{snippet} {hours}")
        time_str = time_match.group(0) if time_match else ""
        parts = [p for p in [title, status, time_str, hours[:40] if not status else ""] if p]
        if status or time_str:
            lines.append(" - " + " | ".join(dict.fromkeys(parts)))
    return "\n".join(lines)


def _is_meaningful_hours(text: str) -> bool:
    """영업시간/휴무 문자열이 의미 있는 정보인지 판단합니다.
    모든 시간이 00:00인 값은 상품 카운트다운 등으로 보고 무의미로 처리합니다.
    '오늘 휴무'/'정기 휴무'/'영업 중' 등 상태 단독 정보도 의미 있다고 봅니다."""
    if not text:
        return False
    cleaned = re.sub(r"[^\d가-힣a-zA-Z:\-\s]", "", text)
    # 휴무/영업 상태 단독 정보도 의미 있음
    if any(k in cleaned for k in ["오늘휴무", "오늘 휴무", "정기휴무", "정기 휴무", "연중무휴", "24시간", "오픈", "영업중", "영업 중", "영업종료", "영업 종료", "준비중", "준비 중"]):
        return True
    digits_only = re.sub(r"[^0-9]", "", cleaned)
    if not digits_only:
        return any(k in cleaned for k in ["휴무", "영업", "열", "닫"])
    # 모든 숫자가 0이면 의미 없는 시간(상품 카운트다운 등)
    if not re.search(r"[1-9]", digits_only):
        return False
    # 시간 패턴 중 적어도 하나가 의미 있는 시간을 포함해야 함
    for m in re.finditer(r"\d{1,2}:\d{2}", cleaned):
        if re.search(r"[1-9]", m.group(0).replace(":", "")):
            return True
    return any(k in cleaned for k in ["휴무", "영업", "연중무휴", "24시간", "오픈"])


def _is_market_ranking_query(query: str) -> bool:
    """시장/순위/시가총액/거래대금/거래량 질문인지 판단합니다."""
    if not query:
        return False
    q = query.lower()
    market_keywords = [
        "코스피", "코스닥", "코스피200", "시가총액", "거래대금", "거래량",
        "순위", "상위", "top", "지수", "주가지수",
    ]
    return any(k in q for k in market_keywords)


def _is_real_time_query(query: str) -> bool:
    """실시간/현재/오늘/지금 정보를 요구하는 일반 질문인지 판단합니다.

    날씨, 영업시간, 교통, 행사, 운영 상태 등 시간 민감 질문을 브라우저 직행
    대상으로 식별합니다.
    """
    if not query:
        return False
    q = query.lower()
    time_keywords = [
        "오늘", "지금", "현재", "방금", "금일", "이번 주", "이번주",
        "영업", "휴무", "열어", "닫아", "열려", "닫혀", "운영",
        "몇 시", "몇시", "언제", "시간", "오픈", "마감",
        "날씨", "기온", "비 와", "비와", "눈 와", "눈와",
        "교통", "길", "혼잡", "지연", "행사", "축제", "일정",
    ]
    return any(k in q for k in time_keywords)


def _extract_market_ranking_table(text: str, max_rows: int = 5) -> str:
    """검색 결과에서 '순위 종목' 리스트를 추출해 최대 max_rows개의 라인으로 만듭니다.
    특정 검색어나 종목명은 하드코딩하지 않고, 순위/종목 패턴만 인식합니다."""
    if not text:
        return ""

    try:
        soup = BeautifulSoup(text, "html.parser")
        plain = soup.get_text("\n", strip=True)
    except Exception:
        plain = text

    rows = []
    used_names = set()

    # 1) <table> 우선 파싱: 순위/종목/값
    for table in soup.find_all("table"):
        header_cells = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if any(cell.name == "th" for cell in cells):
                header_cells = [c.get_text(" ", strip=True) for c in cells]
                break
        if not header_cells:
            first_row = table.find("tr")
            if first_row:
                header_cells = [c.get_text(" ", strip=True) for c in first_row.find_all(["th", "td"])]
        header_lower = " ".join(header_cells).lower()
        if not (re.search(r"(?:순위|no|rank|#)", header_lower) and
                re.search(r"(?:종목|종목명|기업|이름|name)", header_lower)):
            continue

        # 값 컬럼 인덱스: 시가총액 > 거래대금 > 거래량 > 현재가
        value_idx = None
        for key in ("시가총액", "거래대금", "거래량", "현재가"):
            for i, h in enumerate(header_cells):
                if key in h:
                    value_idx = i
                    break
            if value_idx is not None:
                break
        if value_idx is None:
            value_idx = min(2, len(header_cells) - 1)

        for row in table.find_all("tr")[1:] if header_cells else table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            txts = [c.get_text(" ", strip=True) for c in cells]
            rank_raw = re.sub(r"[^0-9]", "", txts[0])
            if not rank_raw.isdigit():
                continue
            rank = int(rank_raw)
            if rank < 1 or rank > 1000:
                continue
            name = txts[1].strip()
            if not name or name in used_names or len(name) > 40:
                continue
            if re.fullmatch(r"\d{6,}", name):
                continue
            if name.lower() in ("no", "rank", "순위", "종목", "종목명"):
                continue
            value = ""
            if value_idx < len(txts):
                value = re.sub(r"[^\d,.조억만천백원\-+%]", "", txts[value_idx])
            rows.append((rank, name, value))
            used_names.add(name)

    # 2) 텍스트/리스트 패턴 파싱
    # "1위 삼성전자", "2. SK하이닉스", "1) 현대차" 등
    name_token = r"[A-Za-z0-9가-힣]+(?:[\._-]?[A-Za-z0-9가-힣]+)*"
    patterns = [
        re.compile(r"(?:(\d+)\s*위)\s*(" + name_token + r")", re.UNICODE),
        re.compile(r"^[\s\-\*)•]*(?:(\d+)[.)\s]+)(" + name_token + r")", re.MULTILINE | re.UNICODE),
    ]
    for pat in patterns:
        for m in pat.finditer(plain):
            rank = int(m.group(1))
            name = m.group(2).strip()
            if not name or name in used_names or len(name) > 40:
                continue
            if re.fullmatch(r"\d+", name):
                continue
            if name.lower() in ("위", "순위", "종목", "종목명", "기업"):
                continue
            # 주변에 숫자값이 있으면 함께 취함
            line = plain[m.start():m.end()+80]
            value = ""
            for v in re.findall(r"[\d,]+(?:\.[\d]+)?(?:\s*[조억만천백원]|원)?", line):
                if re.search(r"\d", v) and len(re.sub(r"[^\d]", "", v)) > 2:
                    value = re.sub(r"[^\d,.조억만천백원\-+%]", "", v)
                    break
            rows.append((rank, name, value))
            used_names.add(name)

    if not rows:
        return ""

    rows.sort(key=lambda x: x[0])
    final = []
    seen = set()
    for rank, name, value in rows:
        if name in seen:
            continue
        seen.add(name)
        final.append((rank, name, value))

    lines = ["[시장 순위표]"]
    for idx, (_, name, value) in enumerate(final[:max_rows], start=1):
        if value:
            lines.append(f"{idx}위 {name} - {value}")
        else:
            lines.append(f"{idx}위 {name}")
    return "\n".join(lines)

def search_naver_web(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """네이버 검색 API + search.naver.com HTML 요약으로 웹 검색.
    영업시간/휴무 관련 질문일 때, API 스니펫만으로는 부족한 정보를
    네이버 검색 결과 페이지에서 보강 크롤링합니다."""
    client_id = getattr(config, "NAVER_CLIENT_ID", "")
    client_secret = getattr(config, "NAVER_CLIENT_SECRET", "")
    results: List[Dict[str, str]] = []
    # 1. OpenAPI 결과
    if client_id and client_secret:
        try:
            from urllib.parse import quote
            url = f"https://openapi.naver.com/v1/search/webkr.json?query={quote(query)}&display={max_results}"
            headers = {
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
            }
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            results = [
                {"title": re.sub(r"<[^>]+>", "", item.get("title", "")),
                 "link": item.get("link", ""),
                 "snippet": re.sub(r"<[^>]+>", "", item.get("description", "")),
                 "source": "Naver 웹"}
                for item in items
            ]
        except Exception as e:
            print(f"[Naver web API search failed] {e}")

    # 2. 네이버 일반 검색 페이지에서 영업시간/휴무 요약 크롤링 (API 부족 시 보강)
    # 동시 요청이 많으면 403/차단되므로 세마포어 + 실패 캐시 + 1회 retry로 제한합니다.
    html_failure_key = f"naver_html_fail:{query}"
    if _search_cache.get(html_failure_key):
        return results[:max_results]

    snippets: List[str] = []
    try:
        from urllib.parse import quote
        search_url = f"https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query={quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }

        def _fetch_naver_html(timeout: float = 5.0):
            return requests.get(search_url, headers=headers, timeout=timeout, verify=False)

        # 1회 retry: 첫 실패(403/timeout) 시 짧게 대기 후 재시도
        try:
            resp = _fetch_naver_html()
            resp.raise_for_status()
        except Exception:
            import time as _time
            _time.sleep(0.5)
            resp = _fetch_naver_html()
            resp.raise_for_status()

        html = resp.text
        html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

        for pat in [
            # 영업/휴무 상태를 우선적으로 잡음
            r"영업\s*중",
            r"영업\s*종료",
            r"영업\s*준비\s*중",
            r"오늘\s*휴무",
            r"정기\s*휴무",
            r"매주\s*\S요일\s*휴무",
            r"공휴일\s*휴무",
            r"연중무휴",
            r"24\s*시간\s*영업",
            # 시간 + 상태 조합
            r"영업\s*중\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*에\s*영업\s*(?:종료|시작)",
            r"(\d{1,2}:\d{2}(?::\d{2})?)\s*에\s*영업\s*(?:종료|시작)",
            r"영업\s*시간\s*[:\-]?\s*([^\n]{3,80})",
            r"정기\s*휴무\s*[:\-]?\s*([^\n]{3,60})",
            r"(?:휴무일|쉬는\s*날|브레이크\s*타임)\s*[:\-]?\s*([^\n]{3,60})",
        ]:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                snippet = m.group(0).strip()
                if _is_meaningful_hours(snippet) and snippet not in snippets:
                    snippets.append(snippet)
        if re.search(r"연중무휴", text, flags=re.IGNORECASE):
            if "연중무휴" not in snippets:
                snippets.insert(0, "연중무휴")
        if re.search(r"24\s*시간\s*영업", text, flags=re.IGNORECASE):
            if "24시간 영업" not in snippets:
                snippets.insert(0, "24시간 영업")
        # 네이버 AI 브리핑/시총 순위 요약 추출 (주식/시장 질문용)
        briefing_snippets = []
        briefing_patterns = [
            r"코스피\s*시가총액\s*상위\s*\d+개(?:는|는)\s*([^.]{10,200})",
            r"코스피\s*시가총액\s*순위[^.]{0,40}([^.]{10,200})",
            r"AI\s*브리핑[^\n]{0,60}([^.]{10,300})",
            r"시가총액\s*순위[^.]{0,60}([^.]{10,200})",
            r"상위\s*5개\s*기업[^.]{0,40}([^.]{10,200})",
            # 네이버 시가총액 순위 표: "no 종목명 시가총액 (단위: 억원) 1 삼성전자 ..."
            r"no\s+종목명\s+시가총액\s*(?:\([^)]{3,30}\))?\s*\d+\s+\S+\s+[\d,]+\s+\d+\s+\S+",
            r"(코스피|코스닥)\s*시가총액\s*순위[^.]{0,60}\d+\s+(?:삼성|SK|현대|LG|NAVER|카카오|KB|POSCO|셀트리온)[^.]{10,300}",
        ]
        for bp in briefing_patterns:
            for m in re.finditer(bp, text, flags=re.IGNORECASE):
                s = m.group(0).strip()
                if s and s not in briefing_snippets:
                    briefing_snippets.append(s)
        if briefing_snippets:
            # AI 브리핑 결과를 별도 result로 추가
            briefing_summary = " | ".join(briefing_snippets[:2])
            results.insert(0, {
                "title": f"네이버 AI 브리핑: {query}",
                "link": search_url,
                "snippet": briefing_summary,
                "source": "Naver AI 브리핑",
            })
        # 시장/순위 질문일 때는 HTML 텍스트에서 표 형태 데이터를 추가로 추출
        if _is_market_ranking_query(query):
            table_text = _extract_market_ranking_table(text, max_rows=5)
            if not table_text and ("코스피" in query or "코스닥" in query):
                # 자연어 질문으로는 네이버가 표를 노출하지 않을 수 있으므로,
                # '시가총액 순위' 키워드를 붙여 재검색 후 다시 추출
                fallback_q = re.sub(
                    r"\b(지금|현재|오늘|정보|알려줘|줘|몇|개|종목|상위|[\d]+)\b",
                    "",
                    query,
                    flags=re.IGNORECASE,
                ).strip()
                fallback_q = re.sub(r"\s+", " ", fallback_q).strip()
                if fallback_q:
                    fallback_q = fallback_q.rstrip("의은는이가을를") + " 시가총액 순위"
                else:
                    fallback_q = "코스피 시가총액 순위"
                try:
                    fb_url = f"https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query={quote(fallback_q)}"
                    fb_resp = requests.get(fb_url, headers=headers, timeout=5, verify=False)
                    fb_resp.raise_for_status()
                    fb_html = fb_resp.text
                    fb_html = re.sub(r"<script[^>]*>.*?</script>", " ", fb_html, flags=re.DOTALL | re.IGNORECASE)
                    fb_html = re.sub(r"<style[^>]*>.*?</style>", " ", fb_html, flags=re.DOTALL | re.IGNORECASE)
                    fb_text = re.sub(r"<[^>]+>", " ", fb_html)
                    fb_text = re.sub(r"\s+", " ", fb_text).strip()
                    table_text = _extract_market_ranking_table(fb_text, max_rows=5)
                except Exception:
                    table_text = ""
            if table_text:
                results.insert(0, {
                    "title": "시장 순위표 (네이버 검색)",
                    "link": search_url,
                    "snippet": table_text,
                    "source": "Naver 시장 순위표",
                })
        if snippets:
            hours_summary = " | ".join(dict.fromkeys(snippets[:4]))
            if results and results[0].get("source") in ("Naver 웹", "Naver 검색 요약"):
                results[0]["hours"] = hours_summary
                results[0]["snippet"] = (results[0].get("snippet", "") + f" | {hours_summary}").strip(" |")
            else:
                results.append({
                    "title": f"네이버 검색: {query}",
                    "link": search_url,
                    "snippet": hours_summary,
                    "hours": hours_summary,
                    "source": "Naver 검색 요약",
                })
    except Exception as e:
        print(f"[Naver search page hours fallback failed] {e}")
        # 짧은 TTL로 실패 기록 → 동일 query 반복 HTML 호출 방지
        _search_cache.set(html_failure_key, True)

    return results[:max_results]


def _fetch_business_hours_sync(url: str) -> str:
    """임의의 웹 페이지에서 영업시간/휴무 정보를 범용적으로 추출합니다.
    동기 함수이며 asyncio.to_thread()로 감싸서 사용하세요."""
    if not url or not url.startswith(("http://", "https://")):
        return ""

    # SNS/동영상/지도 링크는 정적 HTML에서 영업시간을 얻기 어려우므로 스킵
    skip_hosts = [
        "facebook.com", "instagram.com", "youtube.com", "youtu.be",
        "pf.kakao.com", "map.naver.com", "naver.me",
    ]
    if any(h in url.lower() for h in skip_hosts):
        return ""

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        # SSL 인증서 오류를 무시하고 우선 시도
        resp = requests.get(url, headers=headers, timeout=3, verify=False)
        resp.raise_for_status()
        html = resp.text

        def _clean_text(text: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()

        # 1. JSON-LD openingHoursSpecification / openingHours 추출
        json_ld_hours = ""
        m = re.search(r'"openingHours"\s*:\s*"([^"]+)"', html)
        if m:
            json_ld_hours = m.group(1).strip()
        if not json_ld_hours:
            spec_match = re.search(r'"openingHoursSpecification"\s*:\s*(\[[^\]]+\])', html)
            if spec_match:
                json_ld_hours = spec_match.group(1)[:200]
        # 의미없는 값(모든 시간이 00:00, 공백, 시간 형식 없음)은 버림
        if json_ld_hours and _is_meaningful_hours(json_ld_hours) and json_ld_hours != "00:00:00":
            return f"영업시간: {json_ld_hours[:200]}"

        # 휴무 정보 우선 추출 (일반 정규식 패턴; 특정 업체/URL은 하드코딩하지 않음)
        closed_pats = [
            r"오늘\s*휴무",
            r"정기\s*휴무",
            r"매주\s*\S요일\s*휴무",
            r"(?:휴무일|쉬는\s*날)\s*[:\-]?\s*([^\n]{3,60})",
            r"공휴일\s*휴무",
            r"임시\s*휴무",
            r"(?:매달|매월)\s*\S+\s*휴무",
            r"(?:방문의\s*날|문중)\s*휴무",
            r"\d{1,2}\s*월\s*\d{1,2}\s*일\s*휴무",
            r"연말\s*연휴\s*휴무",
            r"신정\s*휴무",
            r"설\s*연휴\s*휴무",
            r"추석\s*연휴\s*휴무",
        ]
        for p in closed_pats:
            cm = re.search(p, html, flags=re.IGNORECASE)
            if cm:
                return f"휴무: {cm.group(0).strip()[:120]}"

        # 2. <time> 태그나 영업시간 관련 클래스/속성 추출
        time_texts = []
        for pat in [
            r'<time[^>]*>([^<]+)</time>',
            r'<[^>]*class="[^"]*(?:business|open|hour|time|operating)[^"]*"[^>]*>([^<]{5,60})</[^>]+>',
        ]:
            for match in re.findall(pat, html, flags=re.IGNORECASE):
                txt = _clean_text(match)
                if txt and txt not in time_texts and _is_meaningful_hours(txt) and any(k in txt for k in ["시", ":", "월", "화", "수", "목", "금", "토", "일", "휴무"]):
                    time_texts.append(txt)
        if time_texts:
            return "영업시간: " + " | ".join(time_texts[:3])

        # 3. 한국어 영업시간/휴무일 패턴 추출 (HTML 태그 제거 후 텍스트에서)
        text = _clean_text(html)
        found = []
        patterns = [
            # 요일별/평일/주말/매일 시간
            r"((?:월|화|수|목|금|토|일)\s*[-~]?\s*(?:월|화|수|목|금|토|일)?[^\n]{0,60}\d{1,2}\s*[:시]\s*\d{1,2}[^\n]{0,60})",
            r"((?:평일|주말|매일)[^\n]{0,40}\d{1,2}\s*[:시]\s*\d{1,2}[^\n]{0,40})",
            # 영업시간/휴무/브레이크타임/라스트오더 안내
            r"(영업\s*시간[^\n]{0,100})",
            r"(휴무\s*(?:일|안내)[^\n]{0,100})",
            r"(브레이크\s*타임[^\n]{0,60})",
            r"(라스트\s*오더[^\n]{0,60})",
            # 오늘/내일/현재 영업 상태 및 휴무
            r"(오늘\s*(?:휴무|영업|열|닫)[^\n]{0,40})",
            r"(내일\s*(?:휴무|영업|열|닫)[^\n]{0,40})",
            r"(현재\s*영업\s*중[^\n]{0,40})",
            r"(현재\s*휴무[^\n]{0,40})",
            r"(\d{1,2}\s*월\s*\d{1,2}\s*일\s*(?:휴무|영업)[^\n]{0,40})",
        ]
        for pat in patterns:
            for match in re.findall(pat, text, flags=re.IGNORECASE):
                txt = match.strip()
                if txt and txt not in found and len(txt) > 4 and _is_meaningful_hours(txt):
                    found.append(txt)
        if found:
            return " | ".join(found[:4])
        return ""
    except Exception as e:
        print(f"[Business hours fetch failed for {url}] {e}")
        return ""


# requests SSL 경고 억제 (verify=False 사용 시)
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


def _looks_like_news_or_blog(item: dict) -> bool:
    """Naver 웹 결과가 뉴스/블로그/커뮤니티/유튜브 성격인지 제목/링크/요약으로 추정합니다."""
    title = (item.get("title") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    link = (item.get("link") or "").lower()
    indicators = [
        "뉴스", "기사", "인터뷰", "보도", "속보", "리포트",
        "블로그", "카페", "동네생활", "커뮤니티",
        "youtube", "youtu.be", "instagram", "tiktok",
    ]
    if any(i in title or i in snippet or i in link for i in indicators):
        return True
    # 뉴스/블로그 도메인이면 강하게 필터링
    news_or_blog_hosts = [
        "news.", ".news", ".blog.", "blog.naver.com", "cafe.naver.com",
        "tistory.com", "egloos.com", "medium.com",
        "youtube.com", "instagram.com", "tiktok.com",
    ]
    return any(h in link for h in news_or_blog_hosts)


def _refine_for_local_search(query: str, display_name: str, dong: str, gu: str, state: str) -> list:
    """자연어 질문에서 장소/업종 키워드를 추출해 Naver 지역 검색용 쿼리 후보를 생성합니다."""
    # 1. 시간/상태/의문사 제거
    noise = ["오늘", "내일", "이번 주", "지금", "현재", "근처", "주변", "가까운", "이곳", "여기", "있나요", "있어", "없어", "해", "해줘", "할까", "인가", "인지", "문열어", "문닫아", "열려", "닫혀", "언제", "언제까지", "몇 시", "몇시"]
    cleaned = query
    for w in noise:
        cleaned = re.sub(rf"\b{re.escape(w)}\b", "", cleaned)
    cleaned = re.sub(r"[?.,!]", "", cleaned).strip()

    # 2. 업종/장소 키워드 사전
    place_keywords = {
        "대형마트": ["대형마트", "마트", "홈플러스", "이마트", "롯데마트", "트레이더스", "코스트코"],
        "편의점": ["편의점", "CU", "GS25", "세븐일레븐", "미니스톱"],
        "식당": ["식당", "맛집", "음식점", "한식", "중식", "일식", "양식"],
        "카페": ["카페", "커피숍", "스타벅스", "투썸"],
        "병원": ["병원", "의원", "내과", "소아과", "치과"],
        "약국": ["약국", "약", "약방"],
    }

    found_categories = []
    for category, kws in place_keywords.items():
        for kw in kws:
            if kw.lower() in cleaned.lower() or kw.lower() in query.lower():
                if category not in found_categories:
                    found_categories.append(category)
                break

    # 3. 지역 접두사 후보
    location_prefixes = []
    if dong:
        location_prefixes.append(dong)
    if gu and gu not in (dong or ""):
        location_prefixes.append(gu)
    if state and gu and f"{state} {gu}" not in " ".join(location_prefixes):
        location_prefixes.append(f"{state} {gu}")
    if display_name and display_name not in " ".join(location_prefixes):
        location_prefixes.append(display_name)

    # 4. 최종 쿼리 조합
    candidates = []
    if found_categories:
        for prefix in location_prefixes:
            # 첫 번째 발견된 카테고리의 핵심 키워드로 검색
            for kw in place_keywords[found_categories[0]]:
                candidate = f"{prefix} {kw}".strip()
                if candidate not in candidates:
                    candidates.append(candidate)
            # 두 번째 카테고리가 있다면 결합 (예: 편의점 카페)
            if len(found_categories) > 1:
                for kw in place_keywords[found_categories[1]]:
                    candidate = f"{prefix} {kw}".strip()
                    if candidate not in candidates:
                        candidates.append(candidate)
    else:
        # 업종 키워드 못 찾으면 정제된 원문으로 지역 접두사 붙여 검색
        for prefix in location_prefixes:
            candidate = f"{prefix} {cleaned}".strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    return list(dict.fromkeys([c for c in candidates if c]))


def _extract_place_titles(results: list) -> list:
    """검색 결과에서 장소/상호명으로 보이는 제목을 추출합니다."""
    titles = []
    for r in results:
        if not isinstance(r, dict):
            continue
        title = r.get("title", "").strip()
        if not title:
            continue
        # 괄호, 대시, 슬래시 뒤의 보조 설명 제거
        title = re.split(r"[\(\)\-/|]", title)[0].strip()
        if title and title not in titles:
            titles.append(title)
    return titles


def search_naver_local(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """네이버 지역 검색 API로 장소/상호 검색 (주소, 전화, 링크, 영업시간 시도). 캐시 적용."""
    cache_key = f"naver_local:{query}:{max_results}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    client_id = getattr(config, "NAVER_CLIENT_ID", "")
    client_secret = getattr(config, "NAVER_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return []
    try:
        from urllib.parse import quote
        # 네이버는 최대 display=100까지 허용; 더 많은 후보를 받아서 상위 max_results개 반환
        fetch_count = min(max(max_results, 10), 100)
        url = f"https://openapi.naver.com/v1/search/local.json?query={quote(query)}&display={fetch_count}&sort=random"
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        results = []
        for item in items[:max_results]:
            title = re.sub(r"<[^>]+>", "", item.get("title", ""))
            link = item.get("link", "")
            address = item.get("address", "")
            road_address = item.get("roadAddress", "")
            telephone = item.get("telephone", "")
            category = item.get("category", "")
            # search_naver_local은 API 응답만 반환합니다.
            # hours 크롤링은 _answer_with_location의 async 단계에서 제어합니다.
            snippet = f"{category} | {road_address or address}".strip(" |")
            if telephone:
                snippet += f" | 전화: {telephone}"
            results.append({
                "title": title,
                "link": link,
                "snippet": snippet,
                "hours": "",
                "source": "Naver 지역",
            })
        _search_cache.set(cache_key, results)
        return results
    except Exception as e:
        print(f"[Naver local search failed] {e}")
        return []


def aggregate_search_results(query: str, max_results: int = 5, use_ddg: bool = True) -> list:
    """네이버 지역 → 네이버 웹 → DuckDuckGo 순으로 검색해서 결과를 합칩니다. (Google/Brave는 제외).
    시간/영업시간 질문처럼 불안정한 DuckDuckGo가 지연을 유발할 때는 use_ddg=False로 호출하세요. 캐시 적용."""
    cache_key = f"agg:{query}:{max_results}:{int(bool(use_ddg))}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    sources = [
        ("Naver 지역", search_naver_local),
        ("Naver 웹", search_naver_web),
    ]
    if use_ddg:
        sources.append(("DuckDuckGo", search_duckduckgo))
    seen_links = set()
    aggregated = []
    for name, func in sources:
        try:
            # 각 provider는 충분한 후보를 가져오도록 요청
            results = func(query, max_results=max(max_results, 10))
            if not isinstance(results, list):
                print(f"[{name} search error] unexpected return type: {type(results)}")
                continue
            source_count = 0
            for r in results:
                if not isinstance(r, dict):
                    continue
                link = r.get("link", "")
                if link in seen_links:
                    continue
                seen_links.add(link)
                aggregated.append({**r, "source": name})
                source_count += 1
                if source_count >= max_results:
                    break
        except Exception as e:
            print(f"[{name} search error] {e}")
    result = aggregated[:max_results]
    _search_cache.set(cache_key, result)
    return result


def format_search_results(results: list, max_results: int = 5) -> str:
    """aggregate_search_results() 의 list[dict] 결과를 사용자에게 표시할 문자열로 포맷합니다."""
    if not results:
        return "검색 결과를 찾을 수 없습니다."
    lines = []
    for i, r in enumerate(results[:max_results], 1):
        entry = f"[{i}] {r.get('title', '')} ({r.get('source', '')})"
        snippet = r.get('snippet')
        if snippet:
            entry += f"\n{snippet}"
        hours = r.get('hours')
        if hours:
            entry += f"\n🕒 {hours}"
        entry += f"\n{r.get('link', '')}"
        lines.append(entry)
    return "\n\n".join(lines)


# ── 뉴스 조회 ─────────────────────────────────────────────────────

def translate_keyword(keyword):
    """검색어 영문 번역 (API 키 불필요)"""
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source="ko", target="en").translate(keyword)
        print(f"[DEBUG] 번역 결과: {result}")
        return result
    except Exception as e:
        print(f"[DEBUG] 번역 실패: {e}")
        return keyword


def get_news(keyword="경제", period="1d", display=10):
    """네이버 뉴스 검색 API로 뉴스 조회"""
    from urllib.parse import quote
    from datetime import datetime, timedelta

    encoded_keyword = quote(keyword)
    url = f"https://openapi.naver.com/v1/search/news.json?query={encoded_keyword}&display={display}&sort=date"
    headers = {
        "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
    }

    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    data = res.json()
    items = data.get("items", [])

    period_days = {"1d": 1, "7d": 7, "1m": 30, "1y": 365}
    days = period_days.get(period, 1)
    cutoff = datetime.now() - timedelta(days=days)

    news_list = []
    for item in items:
        title = item.get("title", "").replace("<b>", "").replace("</b>", "").replace("&quot;", '"')
        link = item.get("originallink") or item.get("link", "")
        pub_date = item.get("pubDate", "")[:16]

        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(item.get("pubDate", ""))
            if pub_dt.replace(tzinfo=None) < cutoff:
                continue
        except:
            pass

        if title:
            news_list.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
            })

    return news_list


def make_news_file(keyword, period, news_list):
    """뉴스 목록을 HTML 파일로 저장"""
    from datetime import datetime
    period_str = {"1d": "오늘", "7d": "일주일", "1m": "한달", "1y": "1년"}.get(period, "오늘")
    filename = f"/tmp/news_{keyword}_{period}.html"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{keyword} 뉴스 ({period_str})</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 2px solid #007aff; padding-bottom: 10px; }}
        .meta {{ color: #888; font-size: 0.9em; margin-bottom: 20px; }}
        .news-item {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .news-item a {{ color: #007aff; text-decoration: none; font-size: 1.0em; font-weight: bold; line-height: 1.5; }}
        .news-item a:hover {{ text-decoration: underline; }}
        .date {{ color: #999; font-size: 0.85em; margin-top: 6px; }}
        .count {{ color: #007aff; font-weight: bold; }}
    </style>
</head>
<body>
    <h1>📰 '{keyword}' 뉴스</h1>
    <div class="meta">기간: {period_str} | 생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 총 <span class="count">{len(news_list)}건</span></div>
""")
        for i, item in enumerate(news_list, 1):
            f.write(f"""    <div class="news-item">
        <span style="color:#999">[{i}]</span>
        <a href="{item['link']}" target="_blank">{item['title']}</a>
        <div class="date">📅 {item['pub_date']}</div>
    </div>
""")
        f.write("</body>\n</html>")

    return filename


# ── 명령어 핸들러 ─────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 안녕하세요! KIS 자동매매 + AI 어시스턴트 봇입니다.\n\n"
        "📌 사용 가능한 명령어:\n"
        "/status [종목코드] - 현재가 및 보유 현황\n"
        "/buy - 수동 매수\n"
        "/sell - 수동 매도\n"
        "/weather [도시명] - 날씨 조회\n"
        "/news - 최신 경제 뉴스\n"
        "/search [검색어] - 인터넷 검색\n"
        "/clear - 대화 초기화\n\n"
        "💬 자연어 주문: 메시지를 '[주문]'으로 시작해 주세요.\n"
        "예: [주문] 삼성전자 100주 시장가로 사줘\n\n"
        "검색, 날씨, 뉴스, 주식은 실시간 정보를 가져와 답변합니다."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock_code = context.args[0] if context.args else config.STOCK_CODE
        price = kis_api.get_current_price(stock_code)
        try:
            holdings, deposit, _ = kis_api.get_balance()
            qty = holdings.get(stock_code, 0)
            balance_str = f"보유수량: {qty}주\n예수금: {deposit:,}원\n"
        except:
            balance_str = "보유수량: 조회불가\n"

        msg = (
            f"📊 현재 현황\n"
            f"━━━━━━━━━━━━━━\n"
            f"종목코드: {stock_code}\n"
            f"현재가: {price:,}원\n"
            f"{balance_str}"
            f"모드: {'🟡 모의투자' if config.IS_PAPER_TRADING else '🔴 실전투자'}"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ 조회 실패: {e}")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = kis_api.get_current_price(config.STOCK_CODE)
        ok, order_no = kis_api.place_order(config.STOCK_CODE, "BUY", config.ORDER_QUANTITY)
        if ok:
            await update.message.reply_text(
                f"✅ 매수 완료!\n"
                f"종목코드: {config.STOCK_CODE}\n"
                f"가격: {price:,}원\n"
                f"수량: {config.ORDER_QUANTITY}주\n"
                f"주문번호: {order_no}"
            )
        else:
            await update.message.reply_text("❌ 매수 실패")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = kis_api.get_current_price(config.STOCK_CODE)
        holdings, _, _ = kis_api.get_balance()
        qty = holdings.get(config.STOCK_CODE, 0)
        if qty == 0:
            await update.message.reply_text("⚠️ 보유 중인 주식이 없습니다.")
            return
        ok, order_no = kis_api.place_order(config.STOCK_CODE, "SELL", qty)
        if ok:
            await update.message.reply_text(
                f"✅ 매도 완료!\n"
                f"종목코드: {config.STOCK_CODE}\n"
                f"가격: {price:,}원\n"
                f"수량: {qty}주\n"
                f"주문번호: {order_no}"
            )
        else:
            await update.message.reply_text("❌ 매도 실패")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        city = context.args[0] if context.args else "Seoul"
        await update.message.reply_text("⏳ 날씨 조회 중...")
        result = get_weather(city)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ 날씨 조회 실패: {e}")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = " ".join(context.args) if context.args else "경제"
        await update.message.reply_text("⏳ 인터넷 검색 중...")
        result = format_search_results(aggregate_search_results(query, max_results=5))
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ 검색 실패: {e}")


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        keyword = context.args[0] if len(context.args) > 0 else "경제"
        period = context.args[1] if len(context.args) > 1 else "1d"

        if period not in ["1d", "7d", "1m", "1y"]:
            await update.message.reply_text(
                "⚠️ 기간은 아래 중 하나를 입력하세요:\n"
                "1d (오늘) / 7d (일주일) / 1m (한달) / 1y (1년)"
            )
            return

        await update.message.reply_text("⏳ 뉴스 조회 중...")
        news_list = get_news(keyword, period, display=10)
        period_str = {"1d": "오늘", "7d": "일주일", "1m": "한달", "1y": "1년"}.get(period, "오늘")

        if not news_list:
            await update.message.reply_text(f"📰 '{keyword}' 관련 뉴스를 찾을 수 없습니다. ({period_str})")
            return

        if len(news_list) <= 5:
            msg = f"📰 '{keyword}' 뉴스 ({period_str})\n━━━━━━━━━━━━━━\n\n"
            for item in news_list:
                msg += f"• <a href='{item['link']}'>{item['title']}</a>\n  📅 {item['pub_date']}\n\n"
            await update.message.reply_text(msg, parse_mode="HTML")
        else:
            msg = f"📰 '{keyword}' 뉴스 ({period_str}) - 총 {len(news_list)}건\n━━━━━━━━━━━━━━\n\n"
            for item in news_list[:5]:
                msg += f"• <a href='{item['link']}'>{item['title']}</a>\n  📅 {item['pub_date']}\n\n"
            msg += f"\n📎 전체 {len(news_list)}건은 아래 파일을 확인하세요."
            await update.message.reply_text(msg, parse_mode="HTML")

            filename = make_news_file(keyword, period, news_list)
            with open(filename, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"{keyword}_{period}_뉴스.html",
                    caption=f"📰 '{keyword}' 전체 뉴스 ({period_str}) - {len(news_list)}건"
                )

    except Exception as e:
        await update.message.reply_text(f"❌ 뉴스 조회 실패: {e}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global conversation_history
    conversation_history = []
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화되었습니다.")


# ── 일반 메시지 핸들러 ────────────────────────────────────────────

# Ollama Pro Cloud/로컬 모델 설정 (환경변수 OLLAMA_CHAT_MODELS / OLLAMA_NLU_MODELS로 재정의 가능)
# 추천 클라우드 모델: command-a(요약/RAG), kimi-k2.7-code(안정), nemotron-3.5-lightning(빠른 tool), deepseek-v4-flash(장문 추론)
# 402(extra usage only)가 발생하는 모델은 fallback 마지막에 배치
OLLAMA_HOST = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
OLLAMA_CHAT_MODELS = [
    m.strip()
    for m in os.getenv(
        "OLLAMA_CHAT_MODELS",
        "command-a:cloud,kimi-k2.7-code:cloud,nemotron-3.5-lightning:cloud,deepseek-v4-flash:cloud,gpt-oss:120b-cloud,qwen3:4b,kimi-k3:cloud",
    ).split(",")
    if m.strip()
]
OLLAMA_NLU_MODELS = [
    m.strip()
    for m in os.getenv(
        "OLLAMA_NLU_MODELS",
        "command-a:cloud,kimi-k2.7-code:cloud,nemotron-3.5-lightning:cloud,qwen3:4b,kimi-k3:cloud",
    ).split(",")
    if m.strip()
]


async def _ollama_chat_with_fallback(
    user_message: str,
    models: List[str],
    system: str,
    temperature: float = 0.7,
    format: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Ollama 서버를 통해 Cloud/로컬 모델 fallback 실행."""
    # 안전장치: 빈/유효하지 않은 모델 이름 필터링
    valid_models = [m.strip() for m in models if m and m.strip()]
    if not valid_models:
        raise RuntimeError("사용 가능한 Ollama 모델이 없습니다. OLLAMA_CHAT_MODELS/OLLAMA_NLU_MODELS를 확인하세요.")
    print(f"[Ollama fallback] trying models: {valid_models}")
    last_error = None
    for model in valid_models:
        try:
            client = ollama.Client(host=OLLAMA_HOST)
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                ] + conversation_history + [
                    {"role": "user", "content": user_message},
                ],
                "options": {"temperature": temperature},
            }
            if format:
                kwargs["format"] = format
            response = client.chat(**kwargs)
            print(f"[Ollama fallback] success with {model}")
            return response["message"]["content"], f"{model}"
        except Exception as e:
            last_error = e
            print(f"[Ollama fallback] {model}@{OLLAMA_HOST} failed: {e}")
    raise last_error or RuntimeError("Ollama 응답 실패")


def _fallback_classify_intent(user_message: str) -> Dict[str, Any]:
    """키워드 기반 의도 분류 폴백 (LLM 실패/느릴 때 사용)."""
    msg = user_message.lower()
    # 주식 관련 질문만 별도 처리
    stock_keywords = ["주가", "주식", "현재가", "매수", "매도", "종목", "보유", "예수금", "balance", "portfolio", "수익", "손실"]
    stock_names = {
        "삼성전자": "005930", "카카오": "035720", "네이버": "035420",
        "테슬라": "TSLA", "애플": "AAPL", "마이크로소프트": "MSFT",
        "구글": "GOOGL", "아마존": "AMZN",
    }
    # 시장/순위/지수 질문은 market_ranking 도구로 직접 분류
    if _is_market_ranking_query(user_message):
        return {"intent": "market_ranking", "params": {"query": user_message}, "reason": "키워드 기반 시장 순위 분류"}
    is_stock = any(w in msg for w in stock_keywords) or any(name in user_message for name in stock_names)
    if is_stock:
        stock_code = config.STOCK_CODE
        for name, code in stock_names.items():
            if name in user_message:
                stock_code = code
                break
        return {"intent": "stock", "params": {"stock_code": stock_code}, "reason": "키워드 기반 주식 분류"}
    # 그 외 모든 질문은 인터넷 검색으로 처리 (날씨, 뉴스, 영업시간 등 포함)
    return {"intent": "web_search", "params": {"query": user_message}, "reason": "주식 외 질문은 모두 웹검색"}


async def _classify_intent(user_message: str) -> Dict[str, Any]:
    """사용자 메시지의 의도를 분류하고 필요한 파라미터를 추출합니다.

    실시간/현재 시장 데이터 요청(코스피·코스닥 순위, 시가총액, 거래대금 등)은
    Ollama를 거치지 않고 market_ranking으로 직행합니다.
    그 외 질문은 Ollama LLM으로 의도를 분류하며, 실패/느릴 때만 키워드 폴백을 사용합니다.
    """
    # 1. 실시간/현재 데이터 요청은 브라우저 직행
    if _is_market_ranking_query(user_message):
        return {"intent": "market_ranking", "params": {"query": user_message}, "reason": "실시간/현재 시장 데이터 요청은 브라우저 직행"}

    # 1.25 일반 실시간/현재 상태 질문(날씨, 영업시간, 교통 등)도 브라우저 직행
    if _is_real_time_query(user_message):
        return {"intent": "browser_search", "params": {"query": user_message}, "reason": "실시간/현재/오늘/지금 정보 요청은 브라우저 직행"}

    # 2. 그 외 질문은 Ollama로 의도 분류
    system = """당신은 한국어 사용자 메시지의 의도를 분류하는 분류기입니다.
가능한 의도는 'stock'과 'web_search' 두 가지입니다.

- stock: 특정 종목(삼성전자, 현대차 등)의 현재가, 매수, 매도, 보유, 종목코드, 포트폴리오, 수익률 등 특정 종목 거래/투자와 관련된 질문
- web_search: 그 외 모든 질문. 날씨, 뉴스, 시간, 영업시간, 지역 정보, 교통, 맛집, 일반 지식, 코스피/코스닥 지수나 시장 순위 요청 등 모두 포함.

단, '/weather [도시]' 또는 '/news [키워드]' 같은 슬래시 명령은 별도 처리되므로 무시해도 됩니다.

반드시 아래 JSON 형식으로만 답변하세요 (JSON 외 설명 금지):
{"intent": "web_search", "params": {"query": "사용자 질문 원문"}, "reason": "분류 이유"}"""
    prompt = f"""아래 메시지를 분류하세요. 위 system 지시에 따라 JSON만 반환하세요.

메시지: {user_message}"""
    try:
        content, _ = await _ollama_chat_with_fallback(
            prompt,
            models=OLLAMA_NLU_MODELS,
            system=system,
            temperature=0.1,
        )
        content = content.strip()
        # JSON 추출 (코드 블록 내부/외부 모두 지원)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            content = match.group(0)
        parsed = json.loads(content)
        intent = parsed.get("intent", "web_search")
        if intent not in ["stock", "web_search"]:
            raise ValueError(f"Invalid intent: {intent}")
        # query가 비어있으면 원문 사용
        params = parsed.get("params", {})
        if not params.get("query"):
            params["query"] = user_message
        return {"intent": intent, "params": params, "reason": parsed.get("reason", "")}
    except Exception as e:
        print(f"[Intent classification failed] {e}, using keyword fallback")
        return _fallback_classify_intent(user_message)


async def _execute_tool(intent: str, params: Dict[str, Any], user_message: str = "", user_location: Optional[Dict[str, Any]] = None) -> str:
    """의도에 따라 외부 도구/API를 실행하고 결과를 반환합니다."""
    try:
        if intent == "weather":
            city = params.get("city")
            lat = params.get("lat")
            lon = params.get("lon")
            if lat is not None and lon is not None:
                return get_weather(lat=lat, lon=lon)
            return get_weather(city or "Seoul")
        elif intent == "news":
            keyword = params.get("keyword") or "경제"
            period = params.get("period") or "1d"
            news_list = get_news(keyword, period, display=10)
            if not news_list:
                return f"'{keyword}' 관련 뉴스를 찾을 수 없습니다. ({period})"
            result = f"📰 '{keyword}' 뉴스 ({period})\n━━━━━━━━━━━━━━\n\n"
            for item in news_list[:5]:
                result += f"• {item['title']}\n  📅 {item['pub_date']}\n  🔗 {item['link']}\n\n"
            return result
        elif intent == "stock":
            stock_code = params.get("stock_code") or config.STOCK_CODE
            # 종목명일 경우 코드로 변환 시도
            if stock_code and not stock_code.isdigit():
                try:
                    from src import stock_master
                    candidates = stock_master.search(stock_code)
                    if candidates:
                        stock_code = candidates[0]["code"]
                except Exception:
                    pass
            price = kis_api.get_current_price(stock_code)
            return f"📈 종목: {stock_code}\n현재가: {price:,}원"
        elif intent == "market_ranking":
            query = params.get("query") or user_message
            market = "kosdaq" if "코스닥" in query.lower() else "kospi"

            # 시장 순위 데이터는 NAVER AI 브리핑/다음금융 등 JS-rendered 페이지에서
            # 제공되므로, 기존 requests 기반 검색은 정확하지 않습니다.
            # 따라서 시장 순위 질문은 headless browser 병렬 검색을 우선 사용합니다.
            try:
                browser_rows = await _browser_search.search_market_ranking_browser(query, market=market, max_results=5)
                formatted = _browser_search.format_market_ranking(browser_rows)
                if formatted:
                    return formatted
            except Exception as e:
                print(f"[browser search primary] {e}")

            # browser fallback 도 실패하면 기존 검색 결과라도 반환
            search_results = aggregate_search_results(query, max_results=5, use_ddg=False)
            search_text = format_search_results(search_results)
            return search_text or "시장 순위 정보를 가져올 수 없습니다."

        elif intent == "browser_search":
            query = params.get("query") or user_message
            browser_results: List[Dict[str, Any]] = []
            location_prefixes = []
            display_name = ""
            dong = gu = state = city = ""
            if user_location:
                geo = user_location.get("geo", {})
                addr = geo.get("address", {}) if geo else {}
                dong = (
                    addr.get("town")
                    or addr.get("village")
                    or addr.get("neighbourhood")
                    or addr.get("quarter")
                    or ""
                )
                display_name = user_location.get("display_name", "")
                gu_match = re.search(r"([가-힣]+구)\b", display_name)
                gu = gu_match.group(1) if gu_match else ""
                city = user_location.get("city") or addr.get("city") or addr.get("town") or ""
                state = addr.get("state") or ""
                if dong:
                    location_prefixes.append(dong)
                if gu:
                    location_prefixes.append(gu)
                if state and gu:
                    location_prefixes.append(f"{state} {gu}")
                elif city and city != dong and city != gu:
                    location_prefixes.append(city)
            location_prefixes = list(dict.fromkeys([p.strip() for p in location_prefixes if p.strip()]))

            browser_query_candidates = _refine_for_local_search(query, display_name, dong, gu, state)
            browser_query = browser_query_candidates[0] if browser_query_candidates else query
            if any(k in user_message for k in ["영업", "휴무", "개점", "운영", "오픈", "열어", "닫아", "시간", "언제까지"]) \
               and "영업시간" not in browser_query and "휴무" not in browser_query:
                browser_query = f"{browser_query} 영업시간"

            for prefix in location_prefixes:
                try:
                    rows = await _browser_search.search_naver_browser(browser_query, max_results=5, location=prefix)
                    browser_results.extend([r for r in rows if "error" not in r])
                    if len(browser_results) >= 5:
                        break
                except Exception as e:
                    print(f"[Browser search error for {prefix}] {e}")

            if not browser_results:
                try:
                    rows = await _browser_search.search_naver_browser(browser_query, max_results=5)
                    browser_results.extend([r for r in rows if "error" not in r])
                except Exception as e:
                    print(f"[Browser search direct] {e}")

            formatted = _browser_search.format_local_results(browser_results)
            if formatted:
                return formatted
            search_results = aggregate_search_results(query, max_results=5)
            search_text = format_search_results(search_results)
            return search_text or "검색 결과를 가져올 수 없습니다."

        elif intent == "web_search":
            query = params.get("query") or user_message
            # 저장된 위치가 있으면 쿼리에 위치 맥락 추가
            if user_location:
                city = user_location.get("city") or user_location.get("display_name", "")
                if city and city not in query:
                    query = f"{city} {query}"
            # web_search 의도는 Ollama가 1순위로 답변을 생성합니다.
            # 따라서 이 단계에서는 기존 검색 엔진 결과만 수집하고,
            # 답변 정리는 handle_message()의 Ollama 호출에 맡깁니다.
            search_results = aggregate_search_results(query, max_results=5)
            search_text = format_search_results(search_results)
            return search_text
        else:
            return ""
    except Exception as e:
        return f"❌ 도구 실행 실패: {e}"


# ── 위치 기반 질문 처리 유틸리티 ──────────────────────────────────

_LOCATION_KEYWORDS = [
    "근처", "주변", "여기", "이곳", "주변에", "근처에", "가까운",
    "영업", "오픈", "열어", "닫아", "운영", "시간", "언제까지",
    "편의점", "마트", "대형마트", "식당", "맛집", "병원", "약국",
    "카페", "주유소", "세탁소", "은행", "ATM",
]


async def _ask_for_location(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str):
    """위치가 필요한 질문에서 사용자에게 위치 보내기 버튼을 보여줍니다."""
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="📍 위치 보내기", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    context.user_data["pending_location_question"] = question
    await update.message.reply_text(
        "이 질문은 현재 위치가 필요해요.\n"
        "아래 버튼으로 위치를 보내주시면 주변 기준으로 검색해 드릴게요.\n"
        "또는 지역명을 직접 입력해 주세요 (예: 서울 강남구, 부산 해운대구).",
        reply_markup=keyboard,
    )


async def _answer_with_location(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str, user_location: dict):
    """저장된 위치를 바탕으로 사용자 질문에 답변합니다. 동 → 구 → 광역 순으로 검색을 확장하며,
    시간 관련 질문일 경우 장소별 영업시간 크롤링을 추가로 시도합니다.
    검색/크롤링은 병렬로 실행하고 단계별 소요시간을 로깅합니다.
    Naver API rate-limit(429)과 search.naver.com 403을 피하기 위해 호출 수와 동시성을 제한합니다."""
    total_start = time.perf_counter()
    display_name = user_location.get("display_name", "")
    city = user_location.get("city") or display_name or ""
    geo = user_location.get("geo", {})
    addr = geo.get("address", {})
    state = addr.get("state") or addr.get("province") or ""

    # display_name 에서 동/구/광역시도를 추출하는 fallback (Nominatim address 가 비어있을 때 대비)
    _display_dong_match = re.search(r"([가-힣]+동)\b", display_name)
    _display_gu_match = re.search(r"([가-힣]+구)\b", display_name)

    # 구/군 후보 추출
    gu_match = re.search(r"([가-힣]+구)\b", city) or re.search(r"([가-힣]+구)\b", display_name)
    gu = gu_match.group(1) if gu_match else (_display_gu_match.group(1) if _display_gu_match else "")

    # 동/읍/면 후보
    dong = (
        addr.get("town")
        or addr.get("village")
        or addr.get("neighbourhood")
        or addr.get("quarter")
        or (_display_dong_match.group(1) if _display_dong_match else "")
    )
    if not dong and city and not city.endswith("구"):
        dong = city

    # 시간 관련 질문인지 판단
    time_keywords = ["영업", "휴무", "개점", "운영", "오픈", "열어", "닫아", "시간", "언제까지"]
    has_time_keyword = any(k in user_message for k in time_keywords)

    # 기본 확장 검색어 구성
    base_variants = [user_message]
    if has_time_keyword:
        for suffix in ["영업시간", "휴무", "개점"]:
            if suffix not in user_message:
                base_variants.append(f"{user_message} {suffix}")

    queries = []
    for variant in base_variants:
        if dong and dong not in variant:
            queries.append(f"{dong} {variant}")
        if gu and gu not in variant:
            queries.append(f"{gu} {variant}")
        if state and gu and f"{state} {gu}" not in variant:
            queries.append(f"{state} {gu} {variant}")
        if display_name and display_name not in variant:
            queries.append(f"{display_name} {variant}")
        queries.append(variant)
    queries = list(dict.fromkeys([q.strip() for q in queries if q.strip()]))

    progress_msg = await update.message.reply_text("⏳ 주변 정보 검색 중...")
    step_start = time.perf_counter()

    # 1단계: Naver 지역 검색. 병렬 대신 순차+짧은 딜레이로 rate-limit 방지
    local_queries = _refine_for_local_search(user_message, display_name, dong, gu, state)
    local_fetch_count = 5

    async def _fetch_naver_local(q: str):
        try:
            # 네이버 지역 API는 과도한 동시 호출 시 429 → 세마포어 안에서 실행
            async with _HTTP_SEMAPHORE:
                return await asyncio.to_thread(search_naver_local, q, local_fetch_count)
        except Exception as e:
            print(f"[Local search error for {q}] {e}")
            return []

    # 최대 4개 local query + 2개 일반 query만 실행 (이전 10개 → 6개로 축소)
    local_tasks = [_fetch_naver_local(q) for q in local_queries[:4]]
    local_tasks += [_fetch_naver_local(q) for q in queries[:2] if q not in local_queries[:4]]
    local_results_nested = []
    for coro in local_tasks:
        local_results_nested.append(await coro)
        await asyncio.sleep(0.15)
    local_results = [r for sub in local_results_nested for r in sub]

    # 중복 제거 (link 기준)
    seen_local_links = set()
    unique_local_results = []
    for r in local_results:
        link = r.get("link", "")
        if link not in seen_local_links:
            seen_local_links.add(link)
            unique_local_results.append(r)
    _perf_log("step1_naver_local", step_start)

    # 1.25단계: 브라우저 기반 네이버 검색으로 위치 맥락 강화 (동 → 구 → 광역 순)
    # 네이버 검색창은 대화체("오늘 근처 대형마트 문열어?")를 장소 UI가 아닌
    # 질문 해석 페이지로 처리할 수 있으므로, 장소/업종 키워드만 남긴
    # 정제된 쿼리로 브라우저 검색을 수행합니다.
    step_start = time.perf_counter()
    browser_results: List[Dict[str, Any]] = []
    location_prefixes = []
    if dong:
        location_prefixes.append(dong)
    if gu and gu != dong:
        location_prefixes.append(gu)
    if state and gu:
        location_prefixes.append(f"{state} {gu}")
    elif city and city != dong and city != gu:
        location_prefixes.append(city)
    location_prefixes = list(dict.fromkeys([p.strip() for p in location_prefixes if p.strip()]))

    browser_query_candidates = _refine_for_local_search(user_message, display_name, dong, gu, state)
    browser_query = browser_query_candidates[0] if browser_query_candidates else user_message
    # 시간/영업 질문이면 영업시간 키워드를 추가해 상태/시간 정보가 포함되도록 유도
    if has_time_keyword and "영업시간" not in browser_query and "휴무" not in browser_query:
        browser_query = f"{browser_query} 영업시간"

    for prefix in location_prefixes:
        try:
            rows = await _browser_search.search_naver_browser(
                browser_query, max_results=5, location=prefix
            )
            browser_results.extend([r for r in rows if "error" not in r])
            if len(browser_results) >= 5:
                break
        except Exception as e:
            print(f"[Browser search error for {prefix}] {e}")
    _perf_log("step1.25_browser_local", step_start)

    def _format_browser_results(rows: List[Dict[str, Any]]) -> str:
        return _browser_search.format_local_results(rows)

    # 1.5단계: 시간 질문일 경우 상위 3개 장소에 대해 네이버 웹 검색으로 영업시간 보강 (병렬)
    step_start = time.perf_counter()
    if has_time_keyword and unique_local_results:
        location_text = display_name or dong or gu or ""

        async def _enhance_hours(r: dict):
            hours = r.get("hours", "")
            if _is_meaningful_hours(hours):
                return
            title = r.get("title", "")
            if not title:
                return
            try:
                async with _HTTP_SEMAPHORE:
                    web_results = await asyncio.to_thread(
                        search_naver_web, f"{location_text} {title} 영업시간".strip(), 3
                    )
                for wr in web_results:
                    candidate = wr.get("hours", "")
                    if _is_meaningful_hours(candidate):
                        r["hours"] = candidate
                        if "Naver 지역" in r.get("source", ""):
                            r["source"] = "Naver 지역 + 검색"
                        break
            except Exception as e:
                print(f"[Hours fallback search error for {title}] {e}")

        await asyncio.gather(*[_enhance_hours(r) for r in unique_local_results[:3]])
    _perf_log("step1.5_hours_enhance", step_start)

    # 2단계: 웹 검색으로 상호명 후보를 얻어 Naver 지역 검색 보강 (제한적으로)
    step_start = time.perf_counter()

    async def _fetch_web_candidates(q: str):
        try:
            # 시간 질문일 땐 DuckDuckGo 제외로 403/429/지연 방지
            return await asyncio.to_thread(aggregate_search_results, q, 3, use_ddg=not has_time_keyword)
        except Exception as e:
            print(f"[Web candidate search error for {q}] {e}")
            return []

    web_candidates_nested = await asyncio.gather(*[_fetch_web_candidates(q) for q in queries[:2]])
    web_candidates = [r for sub in web_candidates_nested for r in sub]
    candidate_titles = _extract_place_titles(web_candidates)

    # 상위 2개 상호만 추가 지역 검색 (접두사 2개로 축소)
    candidate_tasks = []
    for title in candidate_titles[:2]:
        for prefix in [dong, gu]:
            q = f"{prefix} {title}".strip() if prefix else title
            if q:
                candidate_tasks.append(_fetch_naver_local(q))
    if candidate_tasks:
        candidate_results_nested = []
        for coro in candidate_tasks:
            candidate_results_nested.append(await coro)
            await asyncio.sleep(0.15)
        for sub in candidate_results_nested:
            local_results.extend(sub)

    # 중복 재적용
    seen_local_links = set()
    unique_local_results = []
    for r in local_results:
        link = r.get("link", "")
        if link not in seen_local_links:
            seen_local_links.add(link)
            unique_local_results.append(r)
    _perf_log("step2_web_candidates", step_start)

    # 3단계: 시간 질문일 경우 상위 3개 장소에 대해서만 추가 영업시간 재검색 (병렬, 축소)
    step_start = time.perf_counter()
    if has_time_keyword and unique_local_results:
        place_titles = _extract_place_titles(unique_local_results)
        # 휴무 정보를 먼저 찾고, 없을 때만 영업시간을 크롤링합니다.
        suffixes_priority = ["휴무", "영업시간"]

        async def _fetch_hours_for_title(title: str, suffix: str):
            try:
                # aggregate_search_results 대신 Naver 웹 검색만 사용 (DuckDuckGo/지역은 이미 앞에서 수행)
                async with _HTTP_SEMAPHORE:
                    extra = await asyncio.to_thread(search_naver_web, f"{title} {suffix}", 3)
                found = []
                for r in extra:
                    if not isinstance(r, dict):
                        continue
                    if _looks_like_news_or_blog(r):
                        continue
                    hours = await asyncio.to_thread(_fetch_business_hours_sync, r.get("link", ""))
                    if hours:
                        r["hours"] = hours
                        r["source"] = "영업시간 검색"
                        found.append(r)
                return found
            except Exception as e:
                print(f"[Hours search error for {title} {suffix}] {e}")
                return []

        hours_tasks = [
            _fetch_hours_for_title(title, suffix)
            for title in place_titles[:3]
            for suffix in suffixes_priority
        ]
        hours_results_nested = await asyncio.gather(*hours_tasks)
        for sub in hours_results_nested:
            for r in sub:
                link = r.get("link", "")
                if link not in seen_local_links:
                    seen_local_links.add(link)
                    unique_local_results.append(r)
    _perf_log("step3_hours_crawl", step_start)

    # 4단계: 일반 집계 검색 (Naver 웹/DuckDuckGo) - 병렬, 최소화
    step_start = time.perf_counter()

    async def _fetch_general(q: str):
        try:
            # 시간/영업시간 질문에서는 DuckDuckGo를 건너뛰어 속도/안정성 향상
            results = await asyncio.to_thread(aggregate_search_results, q, 3, use_ddg=not has_time_keyword)
            if has_time_keyword:
                results = [r for r in results if r.get("source") == "Naver 지역" or not _looks_like_news_or_blog(r)]
            formatted = format_search_results(results, 3)
            if formatted and not formatted.startswith("❌") and len(formatted) > 20:
                return f"[검색: {q}]\n{formatted}"
            return ""
        except Exception as e:
            print(f"[Expanded search error for {q}] {e}")
            return ""

    general_results = await asyncio.gather(*[_fetch_general(q) for q in queries[:2]])
    all_results = [r for r in general_results if r]
    if browser_results:
        browser_text = _format_browser_results(browser_results)
        if browser_text:
            all_results.append(f"[브라우저 검색 결과]\n{browser_text}")
    if len("\n\n".join(all_results)) > 12000:
        all_results = all_results[:2]
    _perf_log("step4_general_search", step_start)

    # 5단계: 시간 질문일 때 장소 결과를 최상단에 병합
    from datetime import datetime
    today = datetime.now().strftime("%Y년 %m월 %d일 %A")
    if has_time_keyword and unique_local_results:
        local_formatted = format_search_results(unique_local_results, max_results=3)
        status_summary = _build_status_summary(unique_local_results, today)
        if local_formatted and not local_formatted.startswith("❌"):
            search_result = f"[상태 요약]\n{status_summary}\n\n[주변 장소 검색]\n{local_formatted}\n\n" + "\n\n".join(all_results)
    else:
        search_result = "\n\n".join(all_results) if all_results else format_search_results(
            aggregate_search_results(user_message, max_results=5), max_results=5
        )

    # 브라우저 기반 local 검색은 실시간 영업 상태/시간을 직접 포함하고 있으므로,
    # 시간/영업 질문일 때는 정제된 브라우저 결과를 Ollama 요약 없이 바로 보여줍니다.
    if has_time_keyword and browser_results:
        browser_text = _format_browser_results(browser_results)
        if browser_text:
            header = f"📍 {display_name or '현재 위치'} 근처 검색 결과\n({today})"
            ai_reply = f"{header}\n\n{browser_text}"
            if len(ai_reply) > 4000:
                ai_reply = ai_reply[:4000] + "\n\n...(이하 생략)"
            await progress_msg.edit_text(ai_reply)
            _perf_log("total_answer_with_location", total_start)
            return

    # LLM이 너무 장황해지지 않도록 search_result 길이 제한
    if len(search_result) > 10000:
        search_result = search_result[:10000] + "\n...(이하 생략)"

    system = f"""당신은 한국어로 대화하는 친절한 AI 어시스턴트입니다.
사용자 위치: {display_name or '현재 위치'}
오늘 날짜: {today}
아래 [상태 요약]과 [실시간 정보]를 바탕으로 사용자 질문에 자연스럽고 간결하게 답변하세요.
실시간 정보에 없는 내용은 절대 언급하지 마세요. 답변에 출처 링크는 절대 포함하지 마세요.

답변 규칙 (가장 중요):
- [상태 요약]에 '영업 중', '영업 종료', '오늘 휴무', '정기 휴무', '연중무휴', '24시간 영업', '운영 중', '열림', '닫힘', '준비 중' 등 상태가 명시되어 있으면, 오늘({today}) 기준으로 단정적으로 알려주세요.
- '24시간 영업', '연중무휴'가 표시된 장소는 '오늘도 24시간 영업 중입니다'라고 단정적으로 답변하세요.
- '오늘 휴무', '정기 휴무', '영업 종료', '준비 중'이 표시된 장소는 '오늘 휴무입니다'/'오늘 영업 종료입니다'라고 단정적으로 답변하세요.
- [상태 요약]에 아무 상태도 없을 때만 '방문 전 확인 권장'을 덧붙이세요. 상태가 명확하면 '확인 권장', '확인되지 않음' 등의 문구를 쓰지 마세요.
- 검색 결과에 여러 장소가 있으면, 각 장소의 상태를 먼저 표시하고 상세 시간은 그 다음에 적어주세요.

답변 예시:
질문: "오늘 근처 대형마트 문열어?"
상태 요약: GS더프레시 강서염창점 | 24시간 영업
→ 답변: "오늘 근처 GS더프레시 강서염창점은 24시간 영업 중이에요. 언제든 방문 가능합니다."

질문: "오늘 근처 대형마트 문열어?"
상태 요약: 홈플러스 강서점 | 영업 중 | 10:00
→ 답변: "오늘 홈플러스 강서점은 오전 10:00부터 영업 중입니다."

답변 구조:
1. 사용자 질문에 대한 직접적인 답변을 먼저 하세요. (예: "오늘 홈플러스 강서점은 영업 중입니다.")
2. 주요 장소/지점명과 위치를 간단히 요약하세요.
3. 시간/운영/휴무/상태 정보를 정리하세요.
4. 추가로 언급할 정보가 있으면 자연스럽게 덧붙이세요.

[상태 요약]
{status_summary if 'status_summary' in locals() else ''}

[실시간 정보]
{search_result}"""
    try:
        ai_reply, _ = await _ollama_chat_with_fallback(
            user_message,
            models=OLLAMA_CHAT_MODELS,
            system=system,
        )
    except Exception as e:
        ai_reply = f"📍 주변 검색 결과:\n\n{search_result}\n\n⚠️ AI 요약 실패: {e}"

    if len(ai_reply) > 4000:
        ai_reply = ai_reply[:4000] + "\n\n...(이하 생략)"
    await progress_msg.edit_text(ai_reply)
    _perf_log("total_answer_with_location", total_start)


def reverse_geocode(lat: float, lon: float) -> Dict[str, str]:
    """OpenStreetMap Nominatim으로 위도/경도 → 주소/지역명 변환 (캐시 적용)."""
    cache_key = f"geo:{lat:.5f}:{lon:.5f}"
    cached = _geocode_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}&accept-language=ko"
        headers = {"User-Agent": "HarryTradingBot/1.0"}
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        addr = data.get("address", {})
        result = {
            "display_name": data.get("display_name", "알 수 없는 위치"),
            "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or "",
            "state": addr.get("state") or addr.get("province") or "",
            "country": addr.get("country", ""),
            "address": addr,
        }
        _geocode_cache.set(cache_key, result)
        return result
    except Exception as e:
        print(f"[Reverse geocode failed] {e}")
        return {"display_name": "알 수 없는 위치", "city": "", "state": "", "country": ""}


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사용자가 위치를내면 저장하고, 대기 중인 질문이 있으면 먼저 답변합니다."""
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    print(f"[Location received] lat={lat}, lon={lon}")

    progress_msg = await update.message.reply_text("⏳ 위치 정보 확인 중...")

    try:
        geo = reverse_geocode(lat, lon)
        city = geo.get("city") or geo.get("state") or geo.get("display_name", "현재 위치")
        location_summary = (
            f"📍 {geo.get('display_name', '현재 위치')}\n"
            f"(위도 {lat:.4f}, 경도 {lon:.4f})"
        )

        # 대화 맥락에 위치 저장
        context.user_data["location"] = {"lat": lat, "lon": lon, "city": city, "display_name": geo.get("display_name", ""), "geo": geo}
        conversation_history.append({
            "role": "system",
            "content": f"사용자의 최근 위치: {geo.get('display_name')} (lat={lat}, lon={lon})",
        })

        pending = context.user_data.pop("pending_location_question", None)
        if pending:
            await progress_msg.edit_text(f"{location_summary}\n\n이 위치를 바탕으로 질문에 답변하겠습니다.")
            await _answer_with_location(update, context, pending, context.user_data["location"])
            return

        # 위치만 보낸 경우: 날씨/검색은 하지 않고 위치 확인 + 안내 문구만 전송
        await progress_msg.edit_text(
            f"{location_summary}\n\n"
            f"이 위치를 저장했습니다.\n"
            f"'근처 마트 영업해?', '주변 카페 추천해', '여기 날씨' 등으로 물어보실 수 있습니다."
        )
    except Exception as e:
        await progress_msg.edit_text(f"❌ 위치 정보 처리 실패: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global conversation_history
    user_message = update.message.text or ""

    # 저장된 위치 정보가 있으면 맥락에 추가
    user_location = context.user_data.get("location") if context.user_data else None
    location_context = ""
    if user_location:
        location_context = f"[사용자 최근 위치: {user_location.get('city', '알 수 없음')} (lat={user_location.get('lat')}, lon={user_location.get('lon')})]\n"

    # 위치 기반 질문 처리 (단, 시장/순위/거래소/주가지수 관련 질문은 일반 검색 경로로)
    msg_lower = user_message.lower()
    is_location_query = any(k in msg_lower for k in _LOCATION_KEYWORDS)
    _MARKET_KEYWORDS = ["코스피", "코스닥", "코스피200", "지수", "시가총액", "순위", "top", "거래대금", "거래량", "상승", "하락"]
    # 코스피/순위 등 시장 질문은 위치 기반으로 보지 않음
    if is_location_query and any(k in msg_lower for k in _MARKET_KEYWORDS) and any(k in msg_lower for k in ["코스피", "코스닥"]):
        is_location_query = False

    if is_location_query:
        # 저장된 위치가 있더라도, 이번 메시지에 "여기" 같은 명시적 위치 참조가 없으면
        # 새로운 위치 공유를 요청합니다.
        if user_location and "여기" in msg_lower:
            return await _answer_with_location(update, context, user_message, user_location)
        else:
            return await _ask_for_location(update, context, user_message)

    # 지역명 직접 입력 fallback: "서울 강남구 마트"처럼 특정 지역이 포함된 경우
    region_patterns = re.findall(r"([가-힣]+(?:시|도|구|군|읍|면|동|리)(?:\s+[가-힣]+(?:구|동))?)\s+(.*)", user_message)
    if region_patterns and not user_location:
        region, rest = region_patterns[0]
        search_query = f"{region} {rest}".strip()
        await update.message.reply_text(f"⏳ {region} 기준으로 검색 중...")
        search_result = aggregate_search_results(search_query, max_results=5)
        try:
            system = f"""당신은 한국어로 대화하는 친절한 AI 어시스턴트입니다.
아래 [실시간 정보]를 바탕으로 사용자 질문에 자연스럽고 간결하게 답변하세요.
실시간 정보에 없는 내용은 언급하지 마세요. 답변에 출처 링크는 절대 포함하지 마세요.

[실시간 정보]
{search_result}"""
            ai_reply, _ = await _ollama_chat_with_fallback(
                user_message,
                models=OLLAMA_CHAT_MODELS,
                system=system,
            )
            if len(ai_reply) > 4000:
                ai_reply = ai_reply[:4000] + "\n\n...(이하 생략)"
            await update.message.reply_text(ai_reply)
        except Exception as e:
            await update.message.reply_text(f"📍 검색 결과:\n\n{search_result}\n\n⚠️ AI 요약 실패: {e}")
        return

    progress_msg = await update.message.reply_text("⏳ 답변 생성 중...")
    conversation_history.append({"role": "user", "content": user_message})
    try:
        # 1. 의도 분류
        classification = await _classify_intent(user_message)
        intent = classification.get("intent", "general")
        params = classification.get("params", {})
        reason = classification.get("reason", "")
        print(f"[Intent] {intent}, params={params}, reason={reason}")

        # 브라우저/위치 기반 검색은 저장된 위치가 필요합니다.
        # 위치가 없고 질문에 지역명도 없으면 위치 공유를 요청합니다.
        if intent == "browser_search" and not user_location:
            region_patterns = re.findall(r"([가-힣]+(?:시|도|구|군|읍|면|동|리)(?:\s+[가-힣]+(?:구|동))?)", user_message)
            if not region_patterns:
                await progress_msg.delete()
                return await _ask_for_location(update, context, user_message)

        # 2. 실시간 정보가 필요한 경우 도구 실행
        tool_result = ""
        if intent in ("stock", "web_search", "market_ranking", "browser_search"):
            tool_result = await _execute_tool(intent, params, user_message, user_location)
            if tool_result.startswith("❌"):
                await progress_msg.edit_text(tool_result)
                return

        # browser_search 결과는 정제된 형태로 바로 반환 (Ollama 거치지 않음)
        if intent == "browser_search":
            ai_reply = tool_result if tool_result else "검색 결과를 가져올 수 없습니다."
            conversation_history.append({"role": "assistant", "content": ai_reply})
            if len(ai_reply) > 4000:
                ai_reply = ai_reply[:4000] + "\n\n...(이하 생략)"
            await progress_msg.edit_text(ai_reply)
            return

        # 3. 최종 답변 생성
        if intent == "market_ranking":
            # 실시간/현재 시장 데이터는 브라우저 결과를 정리해서 바로 보여줌 (Ollama 거치지 않음)
            ai_reply = tool_result if tool_result else "시장 순위 정보를 가져올 수 없습니다."
            conversation_history.append({"role": "assistant", "content": ai_reply})
            if len(ai_reply) > 4000:
                ai_reply = ai_reply[:4000] + "\n\n...(이하 생략)"
            await progress_msg.edit_text(ai_reply)
            return

        if intent in ("stock", "web_search"):
            system = f"""당신은 한국어로 대화하는 친절한 AI 어시스턴트입니다.
{location_context}사용자가 '{intent}'에 대해 질문했습니다.
아래 [실시간 정보]를 바탕으로 자연스럽고 간결하게 답변하세요.
실시간 정보에 없는 내용은 절대 언급하지 마세요. 답변에 출처 링크는 절대 포함하지 마세요.

순위/표 형식 데이터 처리 규칙:
- [실시간 정보]에 "1위 삼성전자 - 15,668,027억원" 같은 형식이 있으면 그대로 사용자에게 깔끔하게 나열하세요.
- "no 종목명 시가총액 1 삼성전자 15,668,027"처럼 원본 표 형식이 들어있으면, 반드시 "1위 삼성전자 - 15,668,027억원" 형태로 변환해서 답변하세요.
- 상위 5개까지만 요약하고, 불필요한 설명은 덧붙이지 마세요.

[실시간 정보]
{tool_result}"""
        else:
            system = f"""당신은 한국어로 대화하는 친절한 AI 어시스턴트입니다.
{location_context}주식 자동매매 시스템과 연동되어 있으며 투자 관련 질문에도 답변할 수 있습니다."""

        try:
            ai_reply, _ = await _ollama_chat_with_fallback(
                user_message,
                models=OLLAMA_CHAT_MODELS,
                system=system,
            )
        except Exception as e:
            # Ollama 1순위 실패 시 브라우저/기존 검색 결과를 2순위로 직접 반환
            print(f"[Ollama primary failed] {e}, falling back to raw search result")
            if intent == "web_search" and tool_result:
                ai_reply = f"[Ollama 응답 실패로 검색 결과를 직접 표시합니다]\n\n{tool_result}"
            else:
                ai_reply = f"❌ AI 응답 오류: {e}"

        conversation_history.append({"role": "assistant", "content": ai_reply})
        if len(ai_reply) > 4000:
            ai_reply = ai_reply[:4000] + "\n\n...(이하 생략)"
        await progress_msg.edit_text(ai_reply)
    except Exception as e:
        await progress_msg.edit_text(f"❌ AI 응답 오류: {e}")


# ── 메인 실행 ─────────────────────────────────────────────────────

def _kill_existing_bot_processes():
    """같은 토큰으로 충돌하는 기존 봇 프로세스를 강제 종료합니다."""
    import os
    current_pid = os.getpid()
    try:
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "src/telegram_bot.py"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().splitlines():
            try:
                pid = int(line.strip())
                if pid != current_pid:
                    os.kill(pid, 9)
                    print(f"[run_bot] killed existing bot process pid={pid}")
            except Exception:
                pass
    except Exception as e:
        print(f"[run_bot] process cleanup warning: {e}")


def run_bot():
    _kill_existing_bot_processes()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # 자연어 주문 ConversationHandler (검색/AI 기능과 분리된 모듈)
    app.add_handler(create_nl_conv_handler())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ 텔레그램 봇 시작됨")
    app.run_polling(drop_pending_updates=True, poll_interval=1.0)


if __name__ == "__main__":
    run_bot()
