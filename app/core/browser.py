"""Browser and cookie management."""
import json
import time
from pathlib import Path
from typing import Any


class CookieManager:
    """Manages Google cookies for Gemini authentication."""

    def __init__(self, cookies_path: Path):
        self.cookies_path = cookies_path
        self._cookies_cache: list[dict[str, Any]] | None = None
        self._last_load_time: float | None = None

    def load_cookies(self) -> list[dict[str, Any]]:
        """Load and convert cookies with 5-minute caching."""
        # Check cache first
        if self._cookies_cache and self._last_load_time:
            if time.time() - self._last_load_time < 300:  # 5 minutes
                return self._cookies_cache

        # Auto-detect cookies file (cookies.txt or cookies.json)
        cookies_file = self._find_cookies_file()
        if not cookies_file:
            raise FileNotFoundError(
                f"Cookies file not found. Please provide either:\n"
                f"  - {self.cookies_path.parent}/cookies.txt\n"
                f"  - {self.cookies_path}"
            )

        # Load from detected file
        with open(cookies_file) as f:
            raw_cookies = json.load(f)

        # Convert and cache
        self._cookies_cache = self._convert_cookies(raw_cookies)
        self._last_load_time = time.time()

        return self._cookies_cache

    def _find_cookies_file(self) -> Path | None:
        """Auto-detect cookies file (cookies.txt or cookies.json)."""
        # Priority 1: cookies.txt (browser export format)
        txt_path = self.cookies_path.parent / "cookies.txt"
        if txt_path.exists():
            return txt_path

        # Priority 2: configured cookies_path (usually cookies.json)
        if self.cookies_path.exists():
            return self.cookies_path

        return None

    def _convert_cookies(self, raw_cookies: list[dict]) -> list[dict[str, Any]]:
        """Convert browser export format to Playwright format."""
        playwright_cookies = []

        for c in raw_cookies:
            domain = c.get("domain", "")
            # Only keep Google/Gemini cookies
            if not any(x in domain for x in ["google.com", "gemini.google"]):
                continue

            # Normalize sameSite
            same_site = c.get("sameSite", "Lax")
            if same_site == "no_restriction":
                same_site = "None"
            elif same_site in ["unspecified", "lax"]:
                same_site = "Lax"
            elif same_site == "strict":
                same_site = "Strict"
            if same_site not in ["Strict", "Lax", "None"]:
                same_site = "Lax"

            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": domain,
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": same_site,
            }

            # Add expiration if present
            exp = c.get("expirationDate", 0)
            if exp and exp > 0:
                cookie["expires"] = exp

            playwright_cookies.append(cookie)

        return playwright_cookies
