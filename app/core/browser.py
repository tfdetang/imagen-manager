"""Browser and cookie management."""
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


class CookieManager:
    """Manages Google cookies for Gemini authentication."""

    def __init__(self, cookies_path: Path, prefer_configured_path: bool = False):
        self.cookies_path = cookies_path
        self.prefer_configured_path = prefer_configured_path
        self._cookies_cache: list[dict[str, Any]] | None = None
        self._last_load_time: float | None = None
        self._account_email_cache: str | None = None
        self._account_email_last_load_time: float | None = None
        self._identity_cache: dict[str, str] | None = None
        self._identity_last_load_time: float | None = None

    def clear_cache(self):
        """Clear the cookies cache to force reload on next access."""
        self._cookies_cache = None
        self._last_load_time = None
        self._account_email_cache = None
        self._account_email_last_load_time = None
        self._identity_cache = None
        self._identity_last_load_time = None

    def get_account_email(self) -> str | None:
        """Best-effort extract account email from raw cookies (cached for 5 minutes)."""
        if self._account_email_last_load_time:
            if time.time() - self._account_email_last_load_time < 300:
                return self._account_email_cache

        email = self._extract_email_from_raw_cookies()
        self._account_email_cache = email
        self._account_email_last_load_time = time.time()
        return email

    def get_account_identity(self) -> dict[str, str]:
        """Return best-effort identity for account display and management."""
        if self._identity_last_load_time:
            if time.time() - self._identity_last_load_time < 300 and self._identity_cache:
                return self._identity_cache

        identity = self._extract_account_identity()
        self._identity_cache = identity
        self._identity_last_load_time = time.time()
        return identity

    def save_cookies(self, cookies_data: list[dict]) -> Path:
        """
        Save cookies to cookies.txt file.

        Args:
            cookies_data: List of cookie dictionaries

        Returns:
            Path to saved file
        """
        # Ensure parent directory exists
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)

        # Save strategy:
        # - Multi-account mode (prefer_configured_path): save to configured path
        # - Legacy mode: save to cookies.txt for compatibility
        save_path = self.cookies_path if self.prefer_configured_path else (self.cookies_path.parent / "cookies.txt")
        with open(save_path, "w") as f:
            json.dump(cookies_data, f, indent=2)

        # Clear cache so new cookies are loaded
        self.clear_cache()

        return save_path

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

    def load_cookies_for_domains(self, domain_keywords: list[str]) -> list[dict[str, Any]]:
        """Load and convert cookies filtered by provided domain keywords."""
        raw_cookies = self._load_raw_cookies()
        if raw_cookies is None:
            raise FileNotFoundError(
                f"Cookies file not found. Please provide either:\n"
                f"  - {self.cookies_path.parent}/cookies.txt\n"
                f"  - {self.cookies_path}"
            )

        return self._convert_cookies(raw_cookies, domain_keywords=domain_keywords)

    def _find_cookies_file(self) -> Path | None:
        """Auto-detect cookies file (cookies.txt or cookies.json)."""
        # Prefer configured path when using per-account cookie files.
        if self.prefer_configured_path and self.cookies_path.exists():
            return self.cookies_path

        # Priority 1: cookies.txt (browser export format)
        txt_path = self.cookies_path.parent / "cookies.txt"
        if txt_path.exists():
            return txt_path

        # Priority 2: configured cookies_path (usually cookies.json)
        if self.cookies_path.exists():
            return self.cookies_path

        return None

    def _convert_cookies(
        self,
        raw_cookies: list[dict],
        domain_keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Convert browser export format to Playwright format."""
        playwright_cookies = []
        allowed_keywords = [item.lower() for item in domain_keywords] if domain_keywords else ["google.com", "gemini.google"]

        for c in raw_cookies:
            domain = str(c.get("domain", ""))
            # Keep cookies that match requested domain keywords
            normalized_domain = domain.lower()
            if not any(keyword in normalized_domain for keyword in allowed_keywords):
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

    def _extract_email_from_raw_cookies(self) -> str | None:
        """Try extracting Google account email by scanning cookie payload fields."""
        raw_cookies = self._load_raw_cookies()
        if raw_cookies is None:
            return None

        email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

        for item in raw_cookies:
            if not isinstance(item, dict):
                continue

            domain = str(item.get("domain", ""))
            if domain and "google" not in domain and "gmail" not in domain:
                continue

            for value in item.values():
                if not isinstance(value, str):
                    continue
                match = email_pattern.search(value)
                if match:
                    return match.group(0).lower()

        return None

    def _extract_account_identity(self) -> dict[str, str]:
        """Extract an identity label with fallback strategy from cookies content."""
        raw_cookies = self._load_raw_cookies()
        if raw_cookies is None:
            return {
                "label": "unknown",
                "kind": "unknown",
                "email": "",
            }

        email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        name_patterns = [
            re.compile(r'"name"\s*:\s*"([^"@]{2,64})"', re.IGNORECASE),
            re.compile(r"(?:display_name|displayName|fullname|full_name|profile_name)=([A-Za-z0-9_\-\s]{2,64})", re.IGNORECASE),
        ]
        chooser_patterns = [
            re.compile(r"(?:gaia|account|obfuscated|id)=([A-Za-z0-9._\-]{6,64})", re.IGNORECASE),
            re.compile(r"\b([0-9]{12,})\b"),
        ]

        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", ""))
            if domain and "google" not in domain and "gmail" not in domain:
                continue
            for value in item.values():
                if not isinstance(value, str):
                    continue
                match = email_pattern.search(value)
                if match:
                    email = match.group(0).lower()
                    return {
                        "label": email,
                        "kind": "email",
                        "email": email,
                    }

        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", ""))
            if domain and "google" not in domain and "gmail" not in domain:
                continue
            for value in item.values():
                if not isinstance(value, str):
                    continue
                for pattern in name_patterns:
                    match = pattern.search(value)
                    if match:
                        name_value = match.group(1).strip()
                        if name_value:
                            return {
                                "label": name_value,
                                "kind": "name",
                                "email": "",
                            }

        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            cookie_name = str(item.get("name", "")).upper()
            if cookie_name not in {"ACCOUNT_CHOOSER", "LSID", "__SECURE-1PSIDTS"}:
                continue
            value = str(item.get("value", ""))
            for pattern in chooser_patterns:
                match = pattern.search(value)
                if match:
                    suffix = match.group(1)
                    if suffix:
                        clipped = suffix[-8:] if len(suffix) > 8 else suffix
                        return {
                            "label": f"acct-{clipped}",
                            "kind": "account_hint",
                            "email": "",
                        }

        return {
            "label": self._build_cookie_fingerprint(raw_cookies),
            "kind": "fingerprint",
            "email": "",
        }

    def _build_cookie_fingerprint(self, raw_cookies: list[dict]) -> str:
        """Build deterministic non-reversible short fingerprint from stable cookie fields."""
        chunks = []
        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", ""))
            if domain and "google" not in domain and "gmail" not in domain:
                continue
            name = str(item.get("name", ""))
            value = str(item.get("value", ""))
            if not name or not value:
                continue
            digest = hashlib.sha256(f"{name}={value}".encode("utf-8")).hexdigest()[:8]
            chunks.append(f"{name}:{digest}")

        if not chunks:
            base = self.cookies_path.name
            digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
            return f"fp-{digest}"

        chunks.sort()
        merged = "|".join(chunks)
        digest = hashlib.sha256(merged.encode("utf-8")).hexdigest()[:10]
        return f"fp-{digest}"

    def _load_raw_cookies(self) -> list[dict] | None:
        """Load raw cookies JSON list from detected cookies file."""
        cookies_file = self._find_cookies_file()
        if not cookies_file:
            return None

        with open(cookies_file) as f:
            raw_cookies = json.load(f)

        if not isinstance(raw_cookies, list):
            return None

        return raw_cookies
