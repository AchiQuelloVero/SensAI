import re
import time
from typing import Literal

import anyio
import trafilatura
from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

MAX_RESULTS = 10
MAX_SNIPPET_CHARS = 500
MAX_PAGE_CHARS = 4000
CACHE_SECONDS = 600
RETRY_DELAY = 2

WORD = re.compile(r"\w{3,}")
GENERIC = {"latest", "news", "new", "recent", "today", "this", "week", "month", "year", "update", "updates", "the", "and", "for", "what", "about"}

mcp = FastMCP("web")

cache: dict[tuple, tuple[float, list[dict]]] = {}


def run_search(query: str, max_results: int, timelimit: str | None, news: bool) -> list[dict]:
    if news:
        return [
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": result.get("body", "")[:MAX_SNIPPET_CHARS],
                "date": result.get("date", ""),
                "source": result.get("source", ""),
            }
            for result in DDGS().news(query, max_results=max_results, timelimit=timelimit)
        ]
    return [
        {
            "title": result.get("title", ""),
            "url": result.get("href", ""),
            "snippet": result.get("body", "")[:MAX_SNIPPET_CHARS],
        }
        for result in DDGS().text(query, max_results=max_results, timelimit=timelimit)
    ]


def relevant(results: list[dict], query: str) -> list[dict]:
    words = {word for word in WORD.findall(query.lower()) if word not in GENERIC and not word.isdigit()}
    if not words:
        return results
    return [
        result for result in results
        if words & set(WORD.findall(f"{result['title']} {result['snippet']}".lower()))
    ]


@mcp.tool
async def search(
    query: str,
    max_results: int = 6,
    timelimit: Literal["d", "w", "m", "y"] | None = None,
    news: bool = False,
) -> list[dict]:
    """Search the web for current information. Returns numbered results with title, url and snippet.
    Set timelimit to 'd', 'w', 'm' or 'y' (past day/week/month/year) to only get recent pages.
    Set news=True only for news and events, not for facts like versions or prices."""
    max_results = max(1, min(max_results, MAX_RESULTS))
    key = (query.strip().lower(), max_results, timelimit, news)
    if key in cache and time.monotonic() - cache[key][0] < CACHE_SECONDS:
        return cache[key][1]

    for attempt in range(2):
        try:
            results = await anyio.to_thread.run_sync(
                lambda: run_search(query, max_results, timelimit, news)
            )
            break
        except RatelimitException:
            if attempt == 1:
                raise ToolError("Search is rate-limited right now. Answer from what you know or try again later.")
            await anyio.sleep(RETRY_DELAY)
        except DDGSException as exc:
            if "No results" in str(exc):
                raise ToolError("No results. Try a shorter or differently worded query.") from exc
            raise ToolError(f"Search failed: {exc}") from exc

    results = [{"id": index, **result} for index, result in enumerate(relevant(results, query), 1)]
    if not results:
        raise ToolError("No results. Try a shorter or differently worded query.")
    cache[key] = (time.monotonic(), results)
    return results


def main_text(url: str) -> str:
    html = DDGS().extract(url, fmt="text")["content"]
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
    if text:
        return text
    return str(DDGS().extract(url, fmt="text_markdown")["content"])


def best_parts(text: str, query: str, limit: int) -> str:
    paragraphs = [part.strip() for part in text.split("\n") if part.strip()]
    words = set(WORD.findall(query.lower()))

    def score(index: int) -> int:
        return len(words & set(WORD.findall(paragraphs[index].lower())))

    # first paragraph always kept
    ranked = [0] + sorted(range(1, len(paragraphs)), key=score, reverse=True)

    picked, used = set(), 0
    for index in ranked:
        if index > 0 and score(index) == 0:
            break
        if used + len(paragraphs[index]) > limit:
            continue
        picked.add(index)
        used += len(paragraphs[index]) + 1

    parts, last = [], -1
    for index in sorted(picked):
        if last != -1 and index != last + 1:
            parts.append("[...]")
        parts.append(paragraphs[index])
        last = index
    return "\n".join(parts)


@mcp.tool
async def fetch(url: str, query: str = "") -> str:
    """Read the main text of a web page. Use it on a url from search when the snippet is not enough.
    Pass `query` (what you are looking for) to get the most relevant parts of long pages."""
    try:
        text = await anyio.to_thread.run_sync(lambda: main_text(url))
    except DDGSException as exc:
        raise ToolError(f"Could not read {url}: {exc}") from exc

    if len(text) <= MAX_PAGE_CHARS:
        return text
    if query:
        return best_parts(text, query, MAX_PAGE_CHARS)
    return text[:MAX_PAGE_CHARS] + "\n[...page truncated, call fetch again with a query to get other parts]"


if __name__ == "__main__":
    mcp.run()
