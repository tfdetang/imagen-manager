"""HTTP-based Gemini image generator."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import random
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import orjson
from curl_cffi import CurlHttpVersion
from curl_cffi.requests import AsyncSession, Cookies, Response
from curl_cffi.requests.errors import RequestsError
from fastapi import HTTPException

from app.core.browser import CookieManager

logger = logging.getLogger(__name__)


class HttpImageGenerator:
    """Generate images using Gemini HTTP endpoints."""

    INIT_URL = "https://gemini.google.com/app"
    STREAM_GENERATE_URL = (
        "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
    )
    ROTATE_COOKIES_URL = "https://accounts.google.com/RotateCookies"
    UPLOAD_URL_TEMPLATE = "https://push.clients6.google.com/upload/?authuser=0"

    # Imagen 3 model header (same request header style used by Gemini web client).
    IMAGEN_MODEL_HEADER = '[1,null,null,null,"e6fa609c3fa255c0",null,null,null,[4]]'

    BASE_HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/144.0.0.0 Safari/537.36"
        ),
        "X-Same-Domain": "1",
    }
    ROTATE_HEADERS = {
        "Content-Type": "application/json",
    }
    UPLOAD_START_HEADERS = {
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Push-ID": "feeds/mcudyrk2a4khkz",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Protocol": "resumable",
        "X-Tenant-ID": "bard-storage",
    }
    UPLOAD_FINALIZE_HEADERS = {
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Push-ID": "feeds/mcudyrk2a4khkz",
        "X-Goog-Upload-Command": "upload, finalize",
        "X-Goog-Upload-Offset": "0",
        "X-Tenant-ID": "bard-storage",
    }
    IMAGE_DOWNLOAD_HEADERS = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }

    RATE_LIMIT_PATTERNS = [
        r"I couldn't do that because I'm getting a lot of requests right now",
        r"I'm getting a lot of requests right now",
        r"Please try again later",
    ]
    IMAGE_GEN_BLOCKED_PATTERNS = [
        r"can't seem to create any.*for you right now",
        r"image creation isn't available in your location",
        r"I can search for images, but can't.*create",
    ]

    TOKEN_PATTERN = {
        "snlm0e": re.compile(r'"SNlM0e":\s*"(.*?)"'),
        "cfb2h": re.compile(r'"cfb2h":\s*"(.*?)"'),
        "fdrfje": re.compile(r'"FdrFJe":\s*"(.*?)"'),
    }
    FRAME_LENGTH_PATTERN = re.compile(r"(\d+)\n")

    def __init__(
        self,
        cookie_manager: CookieManager,
        proxy: str | None = None,
        account_id: str | None = None,
    ):
        self.cookie_manager = cookie_manager
        self.proxy = proxy
        self.account_id = account_id or "unknown"

        self._session: AsyncSession | None = None
        self._cookies = Cookies()
        self._reqid = random.randint(10000, 99999)
        self._at_token = ""
        self._build_label: str | None = None
        self._session_id: str | None = None

        self._init_lock = asyncio.Lock()
        self._rotate_lock = asyncio.Lock()
        self._last_rotate_time = 0.0
        self._rotate_task: asyncio.Task | None = None

    async def generate(
        self,
        prompt: str,
        timeout: int = 60,
        reference_image: Path | None = None,
        reference_images: list[Path] | None = None,
    ) -> Path:
        """Generate one image and return downloaded local file path."""
        if not prompt.strip():
            raise self._build_http_exception(
                status_code=400,
                code="invalid_request_error",
                message="Prompt cannot be empty",
                error_type="invalid_request_error",
            )

        try:
            if reference_image and not reference_images:
                reference_images = [reference_image]
            elif not reference_images:
                reference_images = []

            await self._ensure_session()
            await self._rotate_cookies()

            uploaded_refs = []
            for path in reference_images:
                uploaded_refs.append(await self._upload_file(path))

            payload = self._build_generate_payload(prompt, uploaded_refs or None)
            response_text = await self._send_generate_request(payload, timeout=timeout)
            image_urls = self._parse_image_urls(response_text)
            if not image_urls:
                self._raise_for_generation_text(response_text)
                raise self._build_http_exception(
                    status_code=500,
                    code="generation_failed",
                    message="No generated image URL found in response",
                )

            return await self._download_image(image_urls[0])
        except HTTPException:
            raise
        except RequestsError as exc:
            if "timeout" in str(exc).lower():
                raise self._build_http_exception(
                    status_code=504,
                    code="request_timeout",
                    message="Gemini HTTP request timed out",
                    error_type="server_error",
                ) from exc
            raise self._build_http_exception(
                status_code=503,
                code="service_unavailable",
                message=f"Gemini HTTP request failed: {exc}",
                error_type="service_error",
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error during HTTP image generation")
            raise self._build_http_exception(
                status_code=500,
                code="generation_failed",
                message=f"Unexpected generation error: {exc}",
            ) from exc

    async def _ensure_session(self):
        if self._session is not None:
            return

        async with self._init_lock:
            if self._session is not None:
                return

            self._session = AsyncSession(
                proxy=self.proxy,
                allow_redirects=True,
                impersonate="chrome",
                http_version=CurlHttpVersion.V2_0,
                timeout=120,
            )
            self._session.headers.update(self.BASE_HEADERS)
            self._session.cookies = self._extract_google_cookies()

            if not self._session.cookies.get("__Secure-1PSID", domain=".google.com"):
                raise self._cookies_expired("Missing __Secure-1PSID cookie")

            await self._init_tokens()
            self._start_rotate_task_if_needed()

    async def _init_tokens(self):
        session = self._require_session()
        response = await session.get(self.INIT_URL)
        self._assert_auth_ok(response)
        response.raise_for_status()

        snlm0e = self._extract_token(response.text, "snlm0e")
        cfb2h = self._extract_token(response.text, "cfb2h")
        fdrfje = self._extract_token(response.text, "fdrfje")
        if not any([snlm0e is not None, cfb2h, fdrfje]):
            raise self._build_http_exception(
                status_code=503,
                code="cookies_expired",
                message="Failed to initialize Gemini tokens from /app",
                error_type="service_error",
            )

        self._at_token = snlm0e or cfb2h or ""
        self._build_label = cfb2h
        self._session_id = fdrfje
        self._cookies.update(response.cookies)

    async def _rotate_cookies(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_rotate_time <= 60:
            return

        async with self._rotate_lock:
            now = time.time()
            if not force and now - self._last_rotate_time <= 60:
                return

            session = self._require_session()
            response = await session.post(
                self.ROTATE_COOKIES_URL,
                headers=self.ROTATE_HEADERS,
                data='[000,"-0000000000000000000"]',
            )
            if response.status_code == 401:
                raise self._cookies_expired("Cookie rotation failed with 401")
            if response.status_code == 429:
                raise self._rate_limited("Cookie rotation is rate limited")
            response.raise_for_status()

            new_1psidts = response.cookies.get("__Secure-1PSIDTS")
            if new_1psidts:
                session.cookies.set("__Secure-1PSIDTS", new_1psidts, domain=".google.com")
                self._cookies.set("__Secure-1PSIDTS", new_1psidts, domain=".google.com")
            self._last_rotate_time = time.time()

    async def _upload_file(self, file_path: Path) -> str:
        if not file_path.exists() or not file_path.is_file():
            raise self._build_http_exception(
                status_code=400,
                code="invalid_reference_image",
                message=f"Reference image not found: {file_path}",
                error_type="invalid_request_error",
            )

        session = self._require_session()
        file_content = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

        start_headers = {
            **self.UPLOAD_START_HEADERS,
            "X-Goog-Upload-Header-Content-Length": str(len(file_content)),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }
        init_response = await session.post(
            self.UPLOAD_URL_TEMPLATE,
            headers=start_headers,
            data=f"File name: {file_path.name}",
        )
        self._raise_for_status(init_response, stage="upload_init")

        resumable_url = init_response.headers.get("x-goog-upload-url")
        if not resumable_url:
            raise self._build_http_exception(
                status_code=500,
                code="upload_failed",
                message="Upload init response does not contain x-goog-upload-url",
            )

        finalize_response = await session.post(
            resumable_url,
            headers=self.UPLOAD_FINALIZE_HEADERS,
            data=file_content,
        )
        self._raise_for_status(finalize_response, stage="upload_finalize")

        upload_ref = finalize_response.text.strip().strip('"')
        if not upload_ref:
            raise self._build_http_exception(
                status_code=500,
                code="upload_failed",
                message="Upload finalize response is empty",
            )
        return upload_ref

    def _build_generate_payload(self, prompt: str, file_refs: list[str] | None) -> dict[str, str]:
        req_file_data = None
        if file_refs:
            req_file_data = [[[url], f"input_{idx + 1}.png"] for idx, url in enumerate(file_refs)]

        message_content: list[Any] = [
            prompt,
            0,
            None,
            req_file_data,
            None,
            None,
            0,
        ]

        inner_req_list: list[Any] = [None] * 73
        inner_req_list[0] = message_content
        inner_req_list[2] = ["", "", "", None, None, None, None, None, None, ""]
        inner_req_list[7] = 1

        return {
            "at": self._at_token,
            "f.req": orjson.dumps([None, orjson.dumps(inner_req_list).decode("utf-8")]).decode("utf-8"),
        }

    async def _send_generate_request(self, payload: dict[str, str], timeout: int) -> str:
        session = self._require_session()
        params: dict[str, Any] = {
            "_reqid": self._reqid,
            "rt": "c",
        }
        self._reqid += 100000
        if self._build_label:
            params["bl"] = self._build_label
        if self._session_id:
            params["f.sid"] = self._session_id

        headers = {
            "x-goog-ext-525001261-jspb": self.IMAGEN_MODEL_HEADER,
        }

        response = await session.post(
            self.STREAM_GENERATE_URL,
            params=params,
            headers=headers,
            data=payload,
            timeout=timeout,
        )
        self._raise_for_status(response, stage="stream_generate")
        self._cookies.update(response.cookies)

        response_text = response.text or ""
        if not response_text:
            raise self._build_http_exception(
                status_code=500,
                code="generation_failed",
                message="Gemini StreamGenerate returned empty body",
            )
        return response_text

    def _parse_image_urls(self, response_text: str) -> list[str]:
        image_urls: list[str] = []
        for part in self._parse_stream_parts(response_text):
            inner_json_str = self._get_nested_value(part, [2])
            if not isinstance(inner_json_str, str) or not inner_json_str:
                continue

            try:
                part_json = orjson.loads(inner_json_str)
            except orjson.JSONDecodeError:
                continue

            candidate_containers: list[Any] = []
            direct_candidate = self._get_nested_value(part_json, [4, 0])
            if isinstance(direct_candidate, list):
                candidate_containers.append(direct_candidate)

            candidates_list = self._get_nested_value(part_json, [4, 0, 1], [])
            if isinstance(candidates_list, list):
                candidate_containers.extend(item for item in candidates_list if isinstance(item, list))

            for candidate_data in candidate_containers:
                self._append_candidate_image_urls(candidate_data, image_urls)

        return image_urls

    def _append_candidate_image_urls(self, candidate_data: list[Any], image_urls: list[str]):
        generated_nodes = self._get_nested_value(candidate_data, [12, 7, 0], [])
        if not isinstance(generated_nodes, list):
            return
        for node in generated_nodes:
            url = self._get_nested_value(node, [0, 3, 3])
            if isinstance(url, str) and url.startswith("http") and url not in image_urls:
                image_urls.append(url)

    async def _download_image(self, url: str) -> Path:
        session = self._require_session()

        download_url = url if re.search(r"=s\d+$", url) else f"{url}=s2048"
        current_url = download_url
        response = None

        for _ in range(5):
            response = await session.get(current_url, headers=self.IMAGE_DOWNLOAD_HEADERS)
            if response.status_code in {401, 403}:
                raise self._cookies_expired("Image download got unauthorized response")
            if response.status_code != 200:
                break

            content_type = str(response.headers.get("content-type", "")).lower()
            if "image" in content_type:
                suffix = self._guess_image_suffix(content_type)
                with tempfile.NamedTemporaryFile(prefix="gemini_http_", suffix=suffix, delete=False) as tmp:
                    tmp.write(response.content)
                    return Path(tmp.name)

            if content_type.startswith("text/plain"):
                next_url = response.text.strip()
                if next_url.startswith("http"):
                    current_url = next_url
                    continue
            break

        status = response.status_code if response else "unknown"
        raise self._build_http_exception(
            status_code=500,
            code="download_failed",
            message=f"Failed to download generated image, status={status}",
        )

    def _parse_stream_parts(self, response_text: str) -> list[Any]:
        content = response_text
        if content.startswith(")]}'"):
            content = content[4:]
        content = content.lstrip()

        parsed, _ = self._parse_length_prefixed_frames(content)
        if parsed:
            return parsed

        fallback_parts: list[Any] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.isdigit():
                continue
            try:
                parsed_line = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if isinstance(parsed_line, list):
                fallback_parts.extend(parsed_line)
            else:
                fallback_parts.append(parsed_line)
        return fallback_parts

    def _parse_length_prefixed_frames(self, content: str) -> tuple[list[Any], str]:
        consumed_pos = 0
        total_len = len(content)
        parsed_frames: list[Any] = []

        while consumed_pos < total_len:
            while consumed_pos < total_len and content[consumed_pos].isspace():
                consumed_pos += 1
            if consumed_pos >= total_len:
                break

            match = self.FRAME_LENGTH_PATTERN.match(content, pos=consumed_pos)
            if not match:
                break

            length_val = match.group(1)
            utf16_units = int(length_val)
            start_content = match.start() + len(length_val)
            char_count, units_found = self._get_char_count_for_utf16_units(content, start_content, utf16_units)
            if units_found < utf16_units:
                break

            end_pos = start_content + char_count
            chunk = content[start_content:end_pos].strip()
            consumed_pos = end_pos
            if not chunk:
                continue

            try:
                parsed = orjson.loads(chunk)
            except orjson.JSONDecodeError:
                continue

            if isinstance(parsed, list):
                parsed_frames.extend(parsed)
            else:
                parsed_frames.append(parsed)

        return parsed_frames, content[consumed_pos:]

    @staticmethod
    def _get_char_count_for_utf16_units(s: str, start_idx: int, utf16_units: int) -> tuple[int, int]:
        count = 0
        units = 0
        limit = len(s)

        while units < utf16_units and (start_idx + count) < limit:
            char = s[start_idx + count]
            unit_count = 2 if ord(char) > 0xFFFF else 1
            if units + unit_count > utf16_units:
                break
            units += unit_count
            count += 1

        return count, units

    def _extract_google_cookies(self) -> Cookies:
        raw_loader = getattr(self.cookie_manager, "_load_raw_cookies", None)
        if not callable(raw_loader):
            raise self._cookies_expired("Cookie loader unavailable")

        raw_cookies = raw_loader()
        if not isinstance(raw_cookies, list) or not raw_cookies:
            raise self._cookies_expired("Cookies file is empty")

        jar = Cookies()
        auth_candidates: dict[str, list[dict[str, Any]]] = {
            "__Secure-1PSID": [],
            "__Secure-1PSIDTS": [],
        }
        now = time.time()

        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            value = str(item.get("value", ""))
            domain = str(item.get("domain", ""))
            if not name or not value:
                continue
            if "google" not in domain:
                continue
            jar.set(name, value, domain=domain or ".google.com", path=item.get("path", "/"))

            if name not in auth_candidates:
                continue
            normalized_domain = self._normalize_cookie_domain(domain)
            if not self._is_auth_google_domain(normalized_domain):
                continue

            expiration = item.get("expirationDate", 0)
            try:
                exp_ts = float(expiration) if expiration else 0.0
            except (TypeError, ValueError):
                exp_ts = 0.0
            # Skip obviously expired persistent cookies.
            if exp_ts and exp_ts < now:
                continue

            auth_candidates[name].append(
                {
                    "value": value,
                    "domain": normalized_domain,
                    "group": self._auth_domain_group(normalized_domain),
                    "rank": self._auth_domain_rank(normalized_domain),
                    "expires": exp_ts,
                }
            )

        selected_psid, selected_psidts = self._select_auth_cookie_pair(auth_candidates)
        if selected_psid:
            jar.set("__Secure-1PSID", selected_psid["value"], domain=".google.com", path="/")
        if selected_psidts:
            jar.set("__Secure-1PSIDTS", selected_psidts["value"], domain=".google.com", path="/")

        if selected_psid:
            logger.debug(
                "Selected auth cookies domains: __Secure-1PSID=%s, __Secure-1PSIDTS=%s",
                selected_psid["domain"],
                selected_psidts["domain"] if selected_psidts else "missing",
            )

        return jar

    @staticmethod
    def _normalize_cookie_domain(domain: str) -> str:
        return domain.strip().lower().lstrip(".")

    @staticmethod
    def _is_auth_google_domain(normalized_domain: str) -> bool:
        return (
            normalized_domain == "google.com"
            or normalized_domain.endswith(".google.com")
            or normalized_domain.startswith("google.")
            or ".google." in normalized_domain
        )

    @staticmethod
    def _auth_domain_group(normalized_domain: str) -> int:
        # Highest priority: google.com + *.google.com
        if normalized_domain == "google.com" or normalized_domain.endswith(".google.com"):
            return 0
        # Fallback: regional Google domains like google.com.hk / *.google.com.hk
        if normalized_domain.startswith("google.") or ".google." in normalized_domain:
            return 1
        return 2

    @staticmethod
    def _auth_domain_rank(normalized_domain: str) -> int:
        # Prefer root google.com over subdomains in primary group.
        if normalized_domain == "google.com":
            return 0
        if normalized_domain.endswith(".google.com"):
            return 1
        if normalized_domain.startswith("google."):
            return 2
        return 3

    def _pick_best_auth_candidate(self, candidates: list[dict[str, Any]], group: int | None = None) -> dict[str, Any] | None:
        pool = [c for c in candidates if group is None or c["group"] == group]
        if not pool:
            return None
        # Prefer lower group/rank, then later expiration.
        return sorted(pool, key=lambda c: (c["group"], c["rank"], -c["expires"]))[0]

    def _select_auth_cookie_pair(
        self,
        auth_candidates: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        psid_list = auth_candidates.get("__Secure-1PSID", [])
        psidts_list = auth_candidates.get("__Secure-1PSIDTS", [])
        if not psid_list:
            return None, None

        psid_groups = {item["group"] for item in psid_list}
        psidts_groups = {item["group"] for item in psidts_list}

        selected_group: int | None = None
        for group in (0, 1, 2):
            if group in psid_groups and group in psidts_groups:
                selected_group = group
                break

        if selected_group is not None:
            return (
                self._pick_best_auth_candidate(psid_list, selected_group),
                self._pick_best_auth_candidate(psidts_list, selected_group),
            )

        return (
            self._pick_best_auth_candidate(psid_list),
            self._pick_best_auth_candidate(psidts_list),
        )

    def _start_rotate_task_if_needed(self):
        if self._rotate_task and not self._rotate_task.done():
            return
        self._rotate_task = asyncio.create_task(self._rotate_loop())

    async def _rotate_loop(self):
        while True:
            await asyncio.sleep(540)
            try:
                await self._rotate_cookies(force=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Cookie rotation background task failed for account '%s': %s",
                    self.account_id,
                    exc,
                )

    def _assert_auth_ok(self, response: Response):
        response_url = str(response.url).lower()
        if (
            "accounts.google.com" in response_url
            or "consent.google.com" in response_url
            or "servicelogin" in response_url
        ):
            raise self._cookies_expired("Redirected to Google sign-in page")

    def _raise_for_status(self, response: Response, stage: str):
        self._assert_auth_ok(response)
        if response.status_code in {401, 403}:
            raise self._cookies_expired(f"{stage} unauthorized ({response.status_code})")
        if response.status_code == 429:
            raise self._rate_limited(f"{stage} rate limited")
        if response.status_code >= 500:
            raise self._build_http_exception(
                status_code=503,
                code="service_unavailable",
                message=f"Gemini upstream temporary error during {stage} ({response.status_code})",
                error_type="service_error",
            )
        if response.status_code >= 400:
            raise self._build_http_exception(
                status_code=500,
                code="generation_failed",
                message=f"Gemini request failed during {stage} ({response.status_code})",
                error_type="generation_error",
            )

    def _raise_for_generation_text(self, text: str):
        lowered = text.lower()
        for pattern in self.RATE_LIMIT_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                raise self._rate_limited("Gemini is rate limiting image generation requests")
        for pattern in self.IMAGE_GEN_BLOCKED_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                raise self._build_http_exception(
                    status_code=503,
                    code="generation_blocked",
                    message="Image generation is blocked for this account/region",
                    error_type="service_error",
                )
        if "sign in" in lowered or "servicelogin" in lowered:
            raise self._cookies_expired("Gemini response indicates sign-in is required")

    def _extract_token(self, text: str, key: str) -> str | None:
        pattern = self.TOKEN_PATTERN[key]
        match = pattern.search(text)
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _get_nested_value(data: Any, path: list[int | str], default: Any = None) -> Any:
        current = data
        for key in path:
            if isinstance(key, int) and isinstance(current, list) and -len(current) <= key < len(current):
                current = current[key]
                continue
            if isinstance(key, str) and isinstance(current, dict) and key in current:
                current = current[key]
                continue
            return default
        return current if current is not None else default

    def _require_session(self) -> AsyncSession:
        if not self._session:
            raise self._build_http_exception(
                status_code=503,
                code="service_unavailable",
                message="HTTP generator session is not initialized",
                error_type="service_error",
            )
        return self._session

    @staticmethod
    def _guess_image_suffix(content_type: str) -> str:
        if "image/jpeg" in content_type:
            return ".jpg"
        if "image/webp" in content_type:
            return ".webp"
        return ".png"

    @staticmethod
    def _build_http_exception(
        status_code: int,
        code: str,
        message: str,
        error_type: str = "generation_error",
    ) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "message": message,
                    "type": error_type,
                    "code": code,
                }
            },
        )

    def _cookies_expired(self, message: str) -> HTTPException:
        return self._build_http_exception(
            status_code=503,
            code="cookies_expired",
            message=message,
            error_type="service_error",
        )

    def _rate_limited(self, message: str) -> HTTPException:
        return self._build_http_exception(
            status_code=429,
            code="rate_limit_exceeded",
            message=message,
            error_type="server_error",
        )
