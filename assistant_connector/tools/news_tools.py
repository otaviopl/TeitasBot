from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from urllib.request import urlopen
import xml.etree.ElementTree as ET


DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_NEWS_QUERY = "tecnologia"
DEFAULT_MAX_AGE_HOURS = 72
DEFAULT_LANGUAGE = "pt-BR"
DEFAULT_COUNTRY = "BR"
GOOGLE_NEWS_SEARCH_RSS_URL = (
    "https://news.google.com/rss/search?q={query}&hl={language}&gl={country}&ceid={country}:{language}"
)
HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL_TEMPLATE = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"


def list_tech_news(arguments, _context):
    try:
        limit = int(arguments.get("limit", 8))
    except (ValueError, TypeError):
        raise ValueError("limit must be a valid integer")
    limit = min(max(limit, 1), 20)

    query = _normalize_query(arguments)
    cutoff_utc = _build_requested_cutoff(arguments)
    include_hacker_news = bool(arguments.get("include_hacker_news", False))

    all_items = []
    errors = []

    try:
        all_items.extend(_fetch_google_news_items(query=query, cutoff_utc=cutoff_utc))
    except (OSError, ValueError, ET.ParseError) as exc:
        errors.append(f"Google News: {exc}")

    if include_hacker_news:
        try:
            all_items.extend(_fetch_hacker_news_items(query=query, cutoff_utc=cutoff_utc))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Hacker News: {exc}")

    all_items.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    selected = all_items[:limit]

    return {
        "topic": query,
        "query": query,
        "total_collected": len(all_items),
        "returned": len(selected),
        "news": selected,
        "sources": sorted({item["source"] for item in all_items}),
        "errors": errors,
    }


def list_news(arguments, context):
    return list_tech_news(arguments, context)


def _normalize_query(arguments: dict[str, object]) -> str:
    raw_query = str(arguments.get("query") or arguments.get("topic") or "").strip()
    return raw_query or DEFAULT_NEWS_QUERY


def _build_requested_cutoff(arguments: dict[str, object]) -> datetime:
    max_age_hours = arguments.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)
    try:
        max_age_hours = int(max_age_hours)
    except (ValueError, TypeError):
        raise ValueError("max_age_hours must be a valid integer")
    max_age_hours = min(max(max_age_hours, 6), 168)
    return datetime.now(timezone.utc) - timedelta(hours=max_age_hours)


def _build_google_news_search_url(query: str) -> str:
    encoded_query = quote_plus(query.strip())
    return GOOGLE_NEWS_SEARCH_RSS_URL.format(
        query=encoded_query,
        language=DEFAULT_LANGUAGE,
        country=DEFAULT_COUNTRY,
    )


def _fetch_google_news_items(*, query: str, cutoff_utc: datetime) -> list[dict[str, str]]:
    rss_url = _build_google_news_search_url(query)
    with urlopen(rss_url, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        payload = response.read()
    root = ET.fromstring(payload)

    items = []
    for item in root.findall("./channel/item"):
        parsed_item = _parse_rss_item(item, source_name="Google News")
        if not parsed_item:
            continue
        if _is_recent_enough(parsed_item.get("published_at", ""), cutoff_utc) and _matches_query(parsed_item, query):
            items.append(parsed_item)
    return items


def _fetch_hacker_news_items(*, query: str, cutoff_utc: datetime) -> list[dict[str, str]]:
    with urlopen(HN_TOP_STORIES_URL, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        top_ids = json.loads(response.read().decode("utf-8"))

    items = []
    for item_id in top_ids[:30]:
        with urlopen(HN_ITEM_URL_TEMPLATE.format(item_id=item_id), timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))

        title = str(payload.get("title", "")).strip()
        url = str(payload.get("url", "")).strip()
        timestamp = payload.get("time")
        if not title or not url or not isinstance(timestamp, int):
            continue

        published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        candidate = {
            "title": title,
            "url": url,
            "source": "Hacker News",
            "published_at": published_at,
            "summary": "",
        }
        if _is_recent_enough(published_at, cutoff_utc) and _matches_query(candidate, query):
            items.append(candidate)
    return items


def _parse_rss_item(item: ET.Element, *, source_name: str) -> dict[str, str] | None:
    title = (item.findtext("title") or "").strip()
    url = (item.findtext("link") or item.findtext("guid") or "").strip()
    published = (item.findtext("pubDate") or item.findtext("published") or "").strip()
    summary = (item.findtext("description") or "").strip()

    source_element = item.find("source")
    source = source_name
    if source_element is not None:
        raw_source = str(source_element.text or "").strip()
        if raw_source:
            source = raw_source

    if not title or not url:
        return None

    return {
        "title": title,
        "url": url,
        "source": source,
        "published_at": _normalize_datetime(published),
        "summary": summary,
    }


def _matches_query(item: dict[str, str], query: str) -> bool:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return True

    searchable_text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    query_tokens = [token for token in re.findall(r"[a-z0-9à-ÿ]{3,}", normalized_query)]
    if not query_tokens:
        return True
    return any(token in searchable_text for token in query_tokens)


def _normalize_datetime(raw_value: str) -> str:
    if not raw_value:
        return ""
    try:
        parsed = parsedate_to_datetime(raw_value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        parsed_iso = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        if parsed_iso.tzinfo is None:
            parsed_iso = parsed_iso.replace(tzinfo=timezone.utc)
        return parsed_iso.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def _is_recent_enough(published_at: str, cutoff_utc: datetime) -> bool:
    if not published_at:
        return True
    try:
        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if published_dt.tzinfo is None:
        published_dt = published_dt.replace(tzinfo=timezone.utc)
    return published_dt >= cutoff_utc
