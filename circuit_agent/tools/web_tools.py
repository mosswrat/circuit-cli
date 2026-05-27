"""
Web tools for Circuit Agent - fetch documentation and search the web.
"""

import hashlib
import json
import re
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from html2text import HTML2Text

    HAS_HTML2TEXT = True
except ImportError:
    HAS_HTML2TEXT = False


# Web tool definitions in OpenAI function calling format
WEB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch content from a URL (documentation, APIs, etc.). Returns the page content as markdown. Use this to look up documentation, read API references, or fetch any web content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"},
                    "selector": {
                        "type": "string",
                        "description": "Optional: CSS selector to extract specific content (e.g., 'article', 'main', '.content')",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Returns a list of relevant results with titles, URLs, and snippets. Use this to find documentation, solutions to errors, or research topics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class WebCache:
    """Simple in-memory cache for web requests."""

    def __init__(self, max_age: int = 300):  # 5 minute default TTL
        self.cache: Dict[str, Tuple[str, float]] = {}
        self.max_age = max_age

    def _hash_key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def get(self, url: str) -> Optional[str]:
        key = self._hash_key(url)
        if key in self.cache:
            content, timestamp = self.cache[key]
            if time.time() - timestamp < self.max_age:
                return content
            del self.cache[key]
        return None

    def set(self, url: str, content: str):
        key = self._hash_key(url)
        self.cache[key] = (content, time.time())

        # Cleanup old entries if cache gets too large
        if len(self.cache) > 100:
            now = time.time()
            self.cache = {k: v for k, v in self.cache.items() if now - v[1] < self.max_age}


class WebTools:
    """Web tool implementations for fetching and searching."""

    def __init__(self):
        self.cache = WebCache()
        self._setup_html_converter()

    def _setup_html_converter(self):
        """Setup HTML to markdown converter."""
        if HAS_HTML2TEXT:
            self.h2t = HTML2Text()
            self.h2t.ignore_links = False
            self.h2t.ignore_images = True
            self.h2t.ignore_emphasis = False
            self.h2t.body_width = 0  # Don't wrap lines
            self.h2t.skip_internal_links = True
        else:
            self.h2t = None

    def _html_to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        if self.h2t:
            return self.h2t.handle(html)
        else:
            # Basic fallback: strip tags
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return text.strip()

    def _extract_with_selector(self, html: str, selector: str) -> str:
        """Extract content matching a CSS selector (basic implementation)."""
        # Basic selector support: tag, .class, #id
        if selector.startswith("."):
            # Class selector
            class_name = selector[1:]
            pattern = (
                rf'<[^>]+class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>(.*?)</\w+>'
            )
        elif selector.startswith("#"):
            # ID selector
            id_name = selector[1:]
            pattern = rf'<[^>]+id=["\']?{re.escape(id_name)}["\']?[^>]*>(.*?)</\w+>'
        else:
            # Tag selector
            pattern = rf"<{re.escape(selector)}[^>]*>(.*?)</{re.escape(selector)}>"

        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        if matches:
            return "\n".join(matches)
        return html

    def _truncate_content(self, content: str, max_chars: int = 15000) -> str:
        """Truncate content to reasonable length."""
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "\n\n... (content truncated)"

    def web_fetch(self, args: dict, confirmed: bool = False) -> str:
        """Fetch content from a URL."""
        if not HAS_HTTPX:
            return "Error: httpx library not installed. Run: pip install httpx"

        url = args.get("url", "")
        selector = args.get("selector")

        if not url:
            return "Error: URL is required"

        # Validate URL
        try:
            parsed = urlparse(url)
            if not parsed.scheme:
                url = "https://" + url
                parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return f"Error: Invalid URL scheme: {parsed.scheme}"
            if not parsed.netloc:
                return "Error: Invalid URL - no domain specified"
        except Exception as e:
            return f"Error: Invalid URL - {e}"

        # Check cache
        cached = self.cache.get(url)
        if cached:
            content = cached
            if selector:
                content = self._extract_with_selector(content, selector)
            markdown = self._html_to_markdown(content)
            return f"[Cached] Fetched: {url}\n\n{self._truncate_content(markdown)}"

        # Fetch URL
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; CircuitAgent/3.0; +https://cisco.com)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }

            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")

                if "application/json" in content_type:
                    # JSON response
                    try:
                        data = response.json()
                        content = json.dumps(data, indent=2)
                        return f"Fetched JSON from: {url}\n\n```json\n{self._truncate_content(content)}\n```"
                    except Exception:
                        content = response.text

                elif "text/plain" in content_type:
                    # Plain text
                    content = response.text
                    return f"Fetched: {url}\n\n{self._truncate_content(content)}"

                else:
                    # HTML or other
                    content = response.text

            # Cache the raw HTML
            self.cache.set(url, content)

            # Apply selector if specified
            if selector:
                content = self._extract_with_selector(content, selector)

            # Convert to markdown
            markdown = self._html_to_markdown(content)
            markdown = self._truncate_content(markdown)

            return f"Fetched: {url}\n\n{markdown}"

        except httpx.TimeoutException:
            return f"Error: Request timed out for {url}"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} for {url}"
        except httpx.RequestError as e:
            return f"Error: Failed to fetch {url} - {e}"
        except Exception as e:
            return f"Error: Unexpected error fetching {url} - {e}"

    def web_search(self, args: dict, confirmed: bool = False) -> str:
        """Search the web using DuckDuckGo."""
        if not HAS_HTTPX:
            return "Error: httpx library not installed. Run: pip install httpx"

        query = args.get("query", "")
        num_results = args.get("num_results", 5)

        if not query:
            return "Error: Search query is required"

        num_results = min(max(num_results, 1), 10)

        try:
            # Use DuckDuckGo HTML search (no API key required)
            search_url = "https://html.duckduckgo.com/html/"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }

            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                response = client.post(search_url, data={"q": query, "b": ""}, headers=headers)
                response.raise_for_status()
                html = response.text

            # Parse results from DuckDuckGo HTML
            results = []

            # Find result blocks
            result_pattern = r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
            snippet_pattern = r'<a class="result__snippet"[^>]*>([^<]+(?:<[^>]+>[^<]*)*)</a>'

            links = re.findall(result_pattern, html)
            snippets = re.findall(snippet_pattern, html)

            for i, (url, title) in enumerate(links[:num_results]):
                # Clean up URL (DuckDuckGo uses redirect URLs)
                if "uddg=" in url:
                    try:
                        from urllib.parse import parse_qs, unquote

                        params = parse_qs(urlparse(url).query)
                        if "uddg" in params:
                            url = unquote(params["uddg"][0])
                    except Exception:
                        pass

                # Clean title and snippet
                title = re.sub(r"<[^>]+>", "", title).strip()
                snippet = ""
                if i < len(snippets):
                    snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
                    snippet = re.sub(r"\s+", " ", snippet)[:200]

                results.append({"title": title, "url": url, "snippet": snippet})

            if not results:
                return f"No results found for: {query}\nTip: Try different keywords or a more general search."

            # Format output
            output = f"Search results for: {query}\n\n"
            for i, r in enumerate(results, 1):
                output += f"{i}. **{r['title']}**\n"
                output += f"   {r['url']}\n"
                if r["snippet"]:
                    output += f"   {r['snippet']}\n"
                output += "\n"

            output += "Tip: Use web_fetch to read the full content of any result."
            return output

        except httpx.TimeoutException:
            return "Error: Search request timed out. Try again."
        except httpx.RequestError as e:
            return f"Error: Search failed - {e}"
        except Exception as e:
            return f"Error: Unexpected error during search - {e}"
