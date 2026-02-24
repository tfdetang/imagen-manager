"""Jimeng video generation logic using Playwright."""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from playwright.async_api import Browser, Page, async_playwright

from app.core.browser import CookieManager

logger = logging.getLogger(__name__)


@dataclass
class VideoGenerationResult:
    """Generation output with provider binding."""

    media_path: Path
    provider_task_id: str | None = None
    provider_item_ids: list[str] | None = None
    provider_generate_id: str | None = None


@dataclass(frozen=True)
class VideoSubmitOptions:
    """UI-selectable options for Jimeng video generation."""

    model: str = "seedance-2.0"
    reference_mode: str = "omni"
    ratio: str = "16:9"
    duration: int = 5


class JimengVideoGenerator:
    """Handles Jimeng video generation via browser automation."""

    DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    COOKIE_DOMAINS = [
        "jimeng.jianying.com",
        "jianying.com",
        "douyin.com",
        "bytedance.com",
    ]
    ASSET_LIST_ENDPOINT = (
        "https://jimeng.jianying.com/mweb/v1/get_asset_list"
        "?aid=513695&web_version=7.5.0&da_version=3.3.9&aigc_features=app_lip_sync"
    )
    MODEL_LABELS = {
        "seedance-2.0-fast": "Seedance 2.0 Fast",
        "seedance-2.0": "Seedance 2.0",
        "video-3.5-pro": "视频 3.5 Pro",
        "video-3.0-pro": "视频 3.0 Pro",
        "video-3.0-fast": "视频 3.0 Fast",
        "video-3.0": "视频 3.0",
        # Backward-compat aliases from previous API shape.
        "doubao-seedance-1-0-lite-i2v-250428": "Seedance 2.0",
        "doubao-seedance-1-0-lite-i2v-first-last-frame-250428": "Seedance 2.0",
        "doubao-seedance-1-0-lite-i2v-reference-250428": "Seedance 2.0",
    }
    REFERENCE_MODE_LABELS = {
        "omni": "全能参考",
        "first_last_frame": "首尾帧",
        "multi_frame": "智能多帧",
        "subject": "主体参考",
    }
    RATIO_LABELS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
    DURATION_LABELS = {4, 5, 6, 7, 8, 9, 10}

    def __init__(self, cookie_manager: CookieManager, proxy: str | None = None):
        self.cookie_manager = cookie_manager
        self.proxy = proxy

    async def generate(
        self,
        prompt: str,
        timeout: int = 180,
        on_binding: Callable[[str, list[str] | None, str | None], Awaitable[None] | None] | None = None,
        submit_options: VideoSubmitOptions | None = None,
        reference_images: list[Path] | None = None,
        reference_videos: list[Path] | None = None,
        first_frame_image: Path | None = None,
        last_frame_image: Path | None = None,
    ) -> VideoGenerationResult:
        """Generate one video in Jimeng and return local temp file path + provider ids."""
        try:
            cookies = self.cookie_manager.load_cookies_for_domains(self.COOKIE_DOMAINS)
        except FileNotFoundError:
            cookies = []
        if not cookies:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "message": "Service temporarily unavailable: missing Jimeng cookies",
                        "type": "service_error",
                        "code": "cookies_expired",
                    }
                },
            )

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                viewport=self.DEFAULT_VIEWPORT,
                user_agent=self.DEFAULT_USER_AGENT,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                accept_downloads=True,
            )
            # 注入反自动化检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
                window.chrome = {runtime: {}};
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery(parameters);
            """);
            await context.add_cookies(cookies)
            page = await context.new_page()
            binding: dict[str, object] = {
                "submit_id": None,
                "pre_gen_item_ids": None,
                "conversation_id": None,
                "generate_id": None,
            }
            binding_event = asyncio.Event()

            try:
                page.on(
                    "response",
                    lambda response: asyncio.create_task(
                        self._capture_binding_from_response(response, binding, binding_event)
                    ),
                )
                await page.goto(
                    "https://jimeng.jianying.com/ai-tool/generate",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await asyncio.sleep(4)

                await self._verify_login(page)
                baseline_candidates = set(await self._extract_video_candidates(page))
                await self._submit_prompt(
                    page,
                    prompt,
                    submit_options=submit_options or VideoSubmitOptions(),
                    reference_images=reference_images or [],
                    reference_videos=reference_videos or [],
                    first_frame_image=first_frame_image,
                    last_frame_image=last_frame_image,
                )
                await self._wait_for_binding(binding_event, timeout=90)
                submit_id = self._as_optional_str(binding.get("submit_id"))
                item_ids = self._as_optional_str_list(binding.get("pre_gen_item_ids"))
                generate_id = self._as_optional_str(binding.get("generate_id"))
                if submit_id and on_binding:
                    maybe_awaitable = on_binding(submit_id, item_ids, generate_id)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
                try:
                    new_candidates = await self._wait_for_generation(
                        page,
                        timeout,
                        baseline_candidates,
                        submit_id=submit_id,
                    )
                except HTTPException as exc:
                    if submit_id and isinstance(exc.detail, dict):
                        exc.detail["provider_task_id"] = submit_id
                    if item_ids and isinstance(exc.detail, dict):
                        exc.detail["provider_item_ids"] = item_ids
                    if generate_id and isinstance(exc.detail, dict):
                        exc.detail["provider_generate_id"] = generate_id
                    raise
                output_path = await self._download_video(page, preferred_urls=new_candidates)
                return VideoGenerationResult(
                    media_path=output_path,
                    provider_task_id=self._as_optional_str(binding.get("submit_id")),
                    provider_item_ids=self._as_optional_str_list(binding.get("pre_gen_item_ids")),
                    provider_generate_id=self._as_optional_str(binding.get("generate_id")),
                )
            finally:
                await browser.close()

    async def _launch_browser(self, playwright) -> Browser:
        """Launch browser with optional proxy."""
        launch_opts = {
            "headless": True,
            "args": [
                "--lang=zh-CN",
                "--window-size=1920,1080",
                # 反自动化检测
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-features=IsolateOrigins,site-per-process",
                "--ignore-certificate-errors",
                "--allow-running-insecure-content",
            ],
        }
        if self.proxy:
            launch_opts["proxy"] = {"server": self.proxy}
        return await playwright.chromium.launch(**launch_opts)

    async def _verify_login(self, page: Page):
        """Verify user is logged in to Jimeng."""
        current_url = page.url.lower()
        if any(token in current_url for token in ["passport", "login", "signin"]):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "message": "Service temporarily unavailable: Jimeng cookies expired",
                        "type": "service_error",
                        "code": "cookies_expired",
                    }
                },
            )

        login_selectors = [
            'button:has-text("登录")',
            'a:has-text("登录")',
            'button:has-text("Sign in")',
            'a:has-text("Sign in")',
        ]
        for selector in login_selectors:
            try:
                node = await page.query_selector(selector)
                if node and await node.is_visible():
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": {
                                "message": "Service temporarily unavailable: Jimeng cookies expired",
                                "type": "service_error",
                                "code": "cookies_expired",
                            }
                        },
                    )
            except HTTPException:
                raise
            except Exception:
                continue

    async def _submit_prompt(
        self,
        page: Page,
        prompt: str,
        submit_options: VideoSubmitOptions,
        reference_images: list[Path],
        reference_videos: list[Path],
        first_frame_image: Path | None,
        last_frame_image: Path | None,
    ):
        """Fill prompt and click generate."""
        await self._dismiss_blocking_modal(page)
        await self._apply_submit_options(page, submit_options)
        uploaded_image_count = await self._upload_reference_assets(
            page=page,
            submit_options=submit_options,
            reference_images=reference_images,
            reference_videos=reference_videos,
            first_frame_image=first_frame_image,
            last_frame_image=last_frame_image,
        )

        final_prompt = self._compose_prompt(
            prompt=prompt,
            reference_mode=submit_options.reference_mode,
            uploaded_image_count=uploaded_image_count,
        )

        input_selectors = [
            "textarea",
            'div[contenteditable="true"]',
            '[contenteditable="plaintext-only"]',
        ]

        input_node = None
        for selector in input_selectors:
            try:
                input_node = await page.wait_for_selector(selector, timeout=5000)
                if input_node:
                    break
            except Exception:
                continue

        if not input_node:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "message": "Failed to find Jimeng prompt input",
                        "type": "generation_error",
                        "code": "input_not_found",
                    }
                },
            )

        try:
            await input_node.click()
        except Exception:
            await input_node.click(force=True)
        tag_name = (await input_node.evaluate("node => node.tagName")).lower()
        if tag_name == "textarea":
            await input_node.fill(final_prompt)
        else:
            await page.keyboard.type(final_prompt, delay=30)

        generate_selectors = [
            'button:has-text("生成视频")',
            'button:has-text("立即生成")',
            'button:has-text("开始生成")',
            'button:has-text("生成")',
            'button:has-text("Generate")',
        ]

        for selector in generate_selectors:
            try:
                btn = await page.wait_for_selector(selector, timeout=4000)
                if not btn:
                    continue
                if await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    return
            except Exception:
                continue

        # Some Jimeng variants submit by Enter and render icon-only send controls.
        await input_node.press("Enter")

    def _compose_prompt(self, prompt: str, reference_mode: str | None, uploaded_image_count: int) -> str:
        """Compose final prompt with optional first/last frame guidance."""
        if reference_mode == "first_last_frame" and uploaded_image_count >= 2:
            return f"请将@图片1作为首帧，@图片2作为尾帧。{prompt}"
        return prompt

    async def _upload_reference_assets(
        self,
        page: Page,
        submit_options: VideoSubmitOptions,
        reference_images: list[Path],
        reference_videos: list[Path],
        first_frame_image: Path | None,
        last_frame_image: Path | None,
    ) -> int:
        """Upload reference files and return uploaded image count."""
        uploaded_image_count = 0

        # In first/last mode, try slot-specific upload first.
        if submit_options.reference_mode == "first_last_frame":
            if first_frame_image and await self._upload_frame_slot(page, "首帧", first_frame_image):
                if self._is_image_path(first_frame_image):
                    uploaded_image_count += 1
                first_frame_image = None

            if last_frame_image and await self._upload_frame_slot(page, "尾帧", last_frame_image):
                if self._is_image_path(last_frame_image):
                    uploaded_image_count += 1
                last_frame_image = None

        upload_queue: list[Path] = []
        if first_frame_image:
            upload_queue.append(first_frame_image)
        if last_frame_image:
            upload_queue.append(last_frame_image)
        upload_queue.extend(reference_images)
        upload_queue.extend(reference_videos)

        if upload_queue and await self._upload_files(page, upload_queue):
            for path in upload_queue:
                if self._is_image_path(path):
                    uploaded_image_count += 1

        return uploaded_image_count

    def _is_image_path(self, path: Path) -> bool:
        return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    async def _upload_files(self, page: Page, files: list[Path]) -> bool:
        """Upload one batch of files through available file-input controls."""
        existing = [path for path in files if path and path.exists()]
        if not existing:
            return False

        try:
            file_inputs = await page.query_selector_all('input[type="file"]')
        except Exception:
            file_inputs = []

        if file_inputs:
            for file_input in file_inputs:
                try:
                    await file_input.set_input_files([str(path) for path in existing])
                    await asyncio.sleep(2)
                    return True
                except Exception:
                    continue

        trigger_selectors = [
            'button:has-text("参考内容")',
            'div:has-text("参考内容")',
            'button:has-text("上传")',
            'button:has-text("Upload")',
            'button[aria-label*="upload" i]',
            'button[class*="upload" i]',
        ]

        for selector in trigger_selectors:
            try:
                trigger = await page.query_selector(selector)
                if not trigger or not await trigger.is_visible():
                    continue
                async with page.expect_file_chooser(timeout=8000) as chooser_info:
                    await trigger.click(force=True)
                chooser = await chooser_info.value
                await chooser.set_files([str(path) for path in existing])
                await asyncio.sleep(2)
                return True
            except Exception:
                continue

        logger.warning("Failed to upload reference files: %s", [str(path) for path in existing])
        return False

    async def _upload_frame_slot(self, page: Page, slot_label: str, file_path: Path) -> bool:
        """Upload one file by clicking specific frame slot controls."""
        if not file_path.exists():
            return False

        label_selectors = [
            f'button:has-text("{slot_label}")',
            f'div:has-text("{slot_label}")',
            f'span:has-text("{slot_label}")',
            f'text="{slot_label}"',
        ]

        for selector in label_selectors:
            try:
                nodes = await page.query_selector_all(selector)
            except Exception:
                continue
            if not nodes:
                continue

            for node in reversed(nodes):
                try:
                    if not await node.is_visible():
                        continue
                    async with page.expect_file_chooser(timeout=4000) as chooser_info:
                        await node.click(force=True)
                    chooser = await chooser_info.value
                    await chooser.set_files(str(file_path))
                    await asyncio.sleep(1.5)
                    return True
                except Exception:
                    continue

        return False

    async def _apply_submit_options(self, page: Page, submit_options: VideoSubmitOptions):
        """Apply configurable UI options before submitting prompt."""
        # Keep the first creation-type option fixed to video generation.
        await self._open_and_select(
            page,
            trigger_texts=["Agent 模式", "图片生成", "视频生成", "数字人", "动作模仿"],
            option_text="视频生成",
        )

        await self._open_and_select(
            page,
            trigger_texts=[
                "Seedance 2.0 Fast",
                "Seedance 2.0",
                "Seedance",
                "2.0",
                "视频 3.5 Pro",
                "视频 3.0 Pro",
                "视频 3.0 Fast",
                "视频 3.0",
            ],
            option_text=self._model_label(submit_options.model),
        )

        await self._open_and_select(
            page,
            trigger_texts=["全能参考", "首尾帧", "智能多帧", "主体参考"],
            option_text=self._reference_mode_label(submit_options.reference_mode),
        )

        await self._select_ratio(page, self._ratio_label(submit_options.ratio))
        await self._select_duration(page, f"{self._duration_value(submit_options.duration)}s")

    async def _select_ratio(self, page: Page, ratio: str):
        await self._open_and_select(
            page,
            trigger_texts=["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
            option_text=ratio,
        )
        if await self._option_selected(page, ratio):
            return

        try:
            clicked = await page.evaluate(
                """([target]) => {
                    const radios = Array.from(document.querySelectorAll('[role="radio"]'));
                    for (const radio of radios) {
                        const text = (radio.getAttribute('aria-label') || radio.innerText || '').trim();
                        if (text === target) {
                            const clickable = radio.closest('[role="radio"]') || radio.parentElement;
                            if (clickable) { clickable.click(); return true; }
                        }
                    }
                    const labels = Array.from(document.querySelectorAll('*'))
                      .filter(n => (n.textContent || '').trim() === target);
                    for (const node of labels) {
                        if (node instanceof HTMLElement) {
                            node.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                [ratio],
            )
            if clicked:
                await asyncio.sleep(0.25)
        except Exception:
            return

    async def _select_duration(self, page: Page, duration_text: str):
        await self._open_and_select(
            page,
            trigger_texts=["4s", "5s", "6s", "7s", "8s", "9s", "10s"],
            option_text=duration_text,
        )
        if await self._option_selected(page, duration_text):
            return

        try:
            clicked = await page.evaluate(
                """([target]) => {
                    const options = Array.from(document.querySelectorAll('[role="option"]'));
                    for (const option of options) {
                        const text = (option.innerText || option.textContent || '').trim();
                        if (text === target) {
                            option.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                [duration_text],
            )
            if clicked:
                await asyncio.sleep(0.25)
        except Exception:
            return

    async def _open_and_select(self, page: Page, trigger_texts: list[str], option_text: str):
        """Open one dropdown-like control and select target option text."""
        if await self._option_selected(page, option_text):
            return

        opened = False
        for trigger in trigger_texts:
            if await self._click_text(page, trigger, prefer_last=True):
                opened = True
                await asyncio.sleep(0.25)
                break

        if not opened:
            # Fallback: click the last visible combobox to force one selector open.
            try:
                comboboxes = await page.query_selector_all('[role="combobox"]')
            except Exception:
                comboboxes = []
            for box in reversed(comboboxes):
                try:
                    if not await box.is_visible():
                        continue
                    await box.click(timeout=1500)
                    opened = True
                    await asyncio.sleep(0.25)
                    break
                except Exception:
                    continue
            if not opened:
                if await self._option_selected(page, option_text):
                    return
                logger.warning("Failed to open selector for option=%s", option_text)
                return

        if await self._click_text(page, option_text, prefer_last=True):
            await asyncio.sleep(0.25)
            if await self._option_selected(page, option_text):
                return

        if await self._option_selected(page, option_text):
            return

        if await self._text_exists(page, option_text):
            return

        logger.warning("Failed to select option text=%s", option_text)

    async def _option_selected(self, page: Page, option_text: str) -> bool:
        """Check whether target option already appears in current controls."""
        try:
            return await page.evaluate(
                """([text]) => {
                    const nodes = Array.from(
                      document.querySelectorAll('[role="combobox"], button, [class*="select"], [class*="dropdown"]')
                    );
                    return nodes.some(node => (node.innerText || '').includes(text));
                }""",
                [option_text],
            )
        except Exception:
            return False

    async def _text_exists(self, page: Page, text: str) -> bool:
        try:
            return await page.evaluate(
                """([target]) => (document.body.innerText || '').includes(target)""",
                [text],
            )
        except Exception:
            return False

    async def _click_text(self, page: Page, text: str, prefer_last: bool) -> bool:
        """Best-effort click any visible clickable element containing text."""
        selectors = [
            f'[role="option"]:has-text("{text}")',
            f'[role="combobox"]:has-text("{text}")',
            f'button:has-text("{text}")',
            f'div[role="button"]:has-text("{text}")',
            f'[class*="select"]:has-text("{text}")',
            f'[class*="dropdown"]:has-text("{text}")',
            f'[class*="option"]:has-text("{text}")',
            f'text="{text}"',
        ]

        for selector in selectors:
            try:
                nodes = await page.query_selector_all(selector)
            except Exception:
                continue
            if not nodes:
                continue

            iterable = range(len(nodes) - 1, -1, -1) if prefer_last else range(len(nodes))
            for idx in iterable:
                node = nodes[idx]
                try:
                    if not await node.is_visible():
                        continue
                    await node.click(timeout=1500)
                    return True
                except Exception:
                    continue

        return False

    def _model_label(self, value: str | None) -> str:
        if not value:
            return "Seedance 2.0"
        return self.MODEL_LABELS.get(value, "Seedance 2.0")

    def _reference_mode_label(self, value: str | None) -> str:
        if not value:
            return "全能参考"
        return self.REFERENCE_MODE_LABELS.get(value, "全能参考")

    def _ratio_label(self, value: str | None) -> str:
        if value in self.RATIO_LABELS:
            return value
        return "16:9"

    def _duration_value(self, value: int | None) -> int:
        if isinstance(value, int) and value in self.DURATION_LABELS:
            return value
        return 5

    async def _dismiss_blocking_modal(self, page: Page):
        """Best-effort close onboarding/login popups that block interactions."""
        close_selectors = [
            'button:has-text("知道了")',
            'button:has-text("我知道了")',
            'button:has-text("稍后")',
            'button:has-text("跳过")',
            'button:has-text("关闭")',
            'button[aria-label*="close" i]',
            'button[class*="close" i]',
            ".lv-modal-close-icon",
            'div[class*="icon-close" i]',
            '[class*="modal"] button:has-text("x")',
        ]

        for _ in range(2):
            for selector in close_selectors:
                try:
                    node = await page.query_selector(selector)
                    if node and await node.is_visible():
                        await node.click(timeout=2000, force=True)
                        await asyncio.sleep(0.5)
                except Exception:
                    continue

            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _wait_for_generation(
        self,
        page: Page,
        timeout: int,
        baseline_candidates: set[str],
        submit_id: str | None = None,
    ) -> list[str]:
        """
        Poll until this submission completes and new downloadable videos appear.

        Returns list of new candidate URLs for current submission.
        """
        elapsed = 0
        interval = 5
        initial_wait = 8
        progress_pattern = re.compile(r"\b([1-9]\d?|100)\s*%")
        observed_progress = False
        observed_new_candidate = False
        await asyncio.sleep(initial_wait)
        elapsed += initial_wait

        while elapsed <= timeout:
            text = await page.evaluate("document.body.innerText || ''")
            dreaming = "正在造梦中" in text
            has_percent = bool(progress_pattern.search(text))
            if dreaming or has_percent:
                observed_progress = True

            candidates = await self._extract_video_candidates(page)
            new_candidates = [url for url in candidates if url not in baseline_candidates]
            if new_candidates:
                observed_new_candidate = True

            # Strict path: if we saw generation progress, wait until progress disappeared
            # and new candidates are available.
            if observed_progress and observed_new_candidate and not dreaming:
                return new_candidates

            # Fallback path: some variants may not expose progress text in DOM.
            if not observed_progress and observed_new_candidate and elapsed >= 20:
                return new_candidates

            await asyncio.sleep(interval)
            elapsed += interval

        if submit_id:
            asset_urls = await self._fetch_asset_urls_by_submit_id(page, submit_id)
            if asset_urls:
                return asset_urls

        raise HTTPException(
            status_code=504,
            detail={
                "error": {
                    "message": f"Jimeng video generation timeout ({timeout}s)",
                    "type": "generation_error",
                    "code": "generation_timeout",
                }
            },
        )

    async def _fetch_asset_urls_by_submit_id(
        self,
        page: Page,
        submit_id: str,
        provider_item_ids: list[str] | None = None,
    ) -> list[str]:
        """Fetch paged asset list and return video urls bound to submit_id/item ids."""
        matched_urls: list[str] = []
        seen_urls: set[str] = set()

        for payload_template in self._asset_list_payload_templates():
            offset: int | None = None
            max_pages = 20

            for _ in range(max_pages):
                payload = self._build_asset_list_payload(payload_template, offset=offset)
                try:
                    resp = await page.context.request.post(
                        self.ASSET_LIST_ENDPOINT,
                        data=payload,
                        timeout=45000,
                    )
                    if not resp.ok:
                        break
                    data = await resp.json()
                except Exception:
                    break

                response_data = data.get("data") if isinstance(data, dict) else None
                if not isinstance(response_data, dict):
                    break

                assets = response_data.get("asset_list")
                if not isinstance(assets, list):
                    break

                for asset in assets:
                    if not self._asset_matches_binding(asset, submit_id, provider_item_ids):
                        continue
                    urls = self._extract_urls_from_object(asset)
                    selected = self._select_primary_video_url(urls)
                    if not selected:
                        continue
                    if selected in seen_urls:
                        continue
                    seen_urls.add(selected)
                    matched_urls.append(selected)

                has_more = bool(response_data.get("has_more"))
                next_offset = response_data.get("next_offset")
                if not has_more:
                    break
                if not isinstance(next_offset, int):
                    break
                if offset == next_offset:
                    break
                offset = next_offset

            if matched_urls:
                break

        return matched_urls

    async def fetch_asset_urls_by_submit_id(
        self,
        submit_id: str,
        provider_item_ids: list[str] | None = None,
        timeout: int = 60,
    ) -> list[str]:
        """Open Jimeng context and fetch asset urls that match submit_id."""
        if not submit_id:
            return []

        try:
            cookies = self.cookie_manager.load_cookies_for_domains(self.COOKIE_DOMAINS)
        except FileNotFoundError:
            cookies = []
        if not cookies:
            return []

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                viewport=self.DEFAULT_VIEWPORT,
                user_agent=self.DEFAULT_USER_AGENT,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                accept_downloads=False,
            )
            # 注入反自动化检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
                window.chrome = {runtime: {}};
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery(parameters);
            """);
            await context.add_cookies(cookies)
            page = await context.new_page()
            try:
                await page.goto(
                    "https://jimeng.jianying.com/ai-tool/asset",
                    wait_until="domcontentloaded",
                    timeout=timeout * 1000,
                )
                await asyncio.sleep(3)
                return await self._fetch_asset_urls_by_submit_id(
                    page,
                    submit_id,
                    provider_item_ids=provider_item_ids,
                )
            finally:
                await browser.close()

    def _extract_urls_from_object(self, obj: object) -> list[str]:
        """Recursively extract likely video URLs from nested object."""
        found: list[str] = []

        def walk(node: object):
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, str):
                if node.startswith("http") and (".mp4" in node or "video" in node):
                    found.append(node)

        walk(obj)
        return found

    def _select_primary_video_url(self, urls: list[str]) -> str | None:
        """
        Select one canonical video URL from multiple variants for one asset.

        Jimeng often returns the same clip in multiple bitrates/resolutions.
        Prefer the highest quality variant by query hints (br/bt/ds).
        """
        if not urls:
            return None

        candidates = [url for url in urls if url.startswith("http")]
        if not candidates:
            return None

        def metric(url: str) -> tuple[int, int, int, int]:
            try:
                parsed = urlparse(url)
                query = parse_qs(parsed.query)
            except Exception:
                return (0, 0, 0, 0)

            def _as_int(name: str) -> int:
                values = query.get(name)
                if not values:
                    return 0
                try:
                    return int(values[0])
                except Exception:
                    return 0

            # Prefer higher bitrate first, then transfer bitrate, then stream idx.
            br = _as_int("br")
            bt = _as_int("bt")
            ds = _as_int("ds")
            has_mp4 = 1 if ".mp4" in url else 0
            return (br, bt, ds, has_mp4)

        return max(candidates, key=metric)

    def _asset_list_payload_templates(self) -> list[dict]:
        """Known-good payload templates observed from Jimeng asset page."""
        return [
            {
                "count": 20,
                "direction": 1,
                "mode": "workbench",
                "asset_type_list": [1, 2, 5, 6, 7, 8, 9, 10],
                "option": {
                    "video_info": {
                        "video_scene_list": [{"scene": "normal"}],
                    },
                    "hide_story_agent_result": True,
                },
            },
            {
                "count": 30,
                "direction": 1,
                "mode": "workbench",
                "asset_type_list": [2, 5, 7, 8, 9, 10],
                "option": {
                    "origin_image_info": {"width": 96},
                    "only_favorited": False,
                    "with_task_status": [50, 45],
                    "end_time_stamp": 0,
                },
            },
            {
                "count": 30,
                "direction": 1,
                "mode": "workbench",
                "asset_type_list": [6],
                "option": {
                    "origin_image_info": {"width": 96},
                    "only_favorited": False,
                    "with_task_status": [50, 45],
                    "end_time_stamp": 0,
                    "aigc_generate_type_filters": [],
                },
            },
            {
                "count": 48,
                "direction": 1,
                "mode": "workbench",
                "asset_type_list": [1, 10],
                "option": {
                    "origin_image_info": {"width": 96},
                    "only_favorited": False,
                    "with_task_status": [50, 45],
                    "end_time_stamp": 0,
                },
            },
        ]

    def _build_asset_list_payload(self, template: dict, offset: int | None = None) -> dict:
        """
        Build the validated payload shape for Jimeng asset list.

        This mirrors the browser request structure observed on the asset page.
        """
        payload = json.loads(json.dumps(template, ensure_ascii=False))
        if isinstance(offset, int):
            payload["offset"] = offset
        return payload

    def _asset_matches_binding(
        self,
        asset: object,
        submit_id: str,
        provider_item_ids: list[str] | None = None,
    ) -> bool:
        """Return True when one asset contains exact submit_id/item-id binding."""
        if not submit_id or not isinstance(asset, dict):
            return False

        # Preferred path: exact-key match in nested structures.
        if self._contains_submit_id_value(asset, submit_id):
            return True

        normalized_item_ids = [item for item in (provider_item_ids or []) if item]
        if normalized_item_ids and self._contains_any_item_id_value(asset, normalized_item_ids):
            return True

        # Fallback: some fields are JSON strings; parse then retry.
        for key in ("agent_conversation_session", "conversation_ext", "extra"):
            raw = asset.get(key)
            if not isinstance(raw, str):
                continue
            if submit_id not in raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if self._contains_submit_id_value(parsed, submit_id):
                return True
            if normalized_item_ids and self._contains_any_item_id_value(parsed, normalized_item_ids):
                return True

        # Last fallback keeps compatibility with historical responses where
        # submit_id only appeared in stringified blobs.
        try:
            blob = json.dumps(asset, ensure_ascii=False)
            if submit_id in blob:
                return True
            return any(item_id in blob for item_id in normalized_item_ids)
        except Exception:
            return False

    def _contains_submit_id_value(self, node: object, submit_id: str) -> bool:
        """Recursively check exact `submit_id` field equality."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() == "submit_id" and isinstance(value, str) and value == submit_id:
                    return True
                if self._contains_submit_id_value(value, submit_id):
                    return True
            return False
        if isinstance(node, list):
            for item in node:
                if self._contains_submit_id_value(item, submit_id):
                    return True
            return False
        if isinstance(node, str):
            if submit_id not in node or not node.strip().startswith(("{", "[")):
                return False
            try:
                parsed = json.loads(node)
            except Exception:
                return False
            return self._contains_submit_id_value(parsed, submit_id)
        return False

    def _contains_any_item_id_value(self, node: object, item_ids: list[str]) -> bool:
        """Recursively check exact item-id field equality."""
        if not item_ids:
            return False

        if isinstance(node, dict):
            for key, value in node.items():
                lower_key = key.lower()
                key_hint = (
                    lower_key == "item_id"
                    or lower_key == "item_ids"
                    or lower_key.endswith("_item_id")
                    or lower_key.endswith("_item_ids")
                    or "item_id" in lower_key
                )
                if key_hint:
                    if isinstance(value, str) and value in item_ids:
                        return True
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, str) and item in item_ids:
                                return True
                if self._contains_any_item_id_value(value, item_ids):
                    return True
            return False
        if isinstance(node, list):
            for item in node:
                if self._contains_any_item_id_value(item, item_ids):
                    return True
            return False
        if isinstance(node, str):
            if not any(item_id in node for item_id in item_ids):
                return False
            if not node.strip().startswith(("{", "[")):
                return False
            try:
                parsed = json.loads(node)
            except Exception:
                return False
            return self._contains_any_item_id_value(parsed, item_ids)
        return False

    async def _wait_for_binding(self, event: asyncio.Event, timeout: int = 90):
        """Wait briefly for provider binding data from SSE."""
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return

    async def _capture_binding_from_response(
        self,
        response,
        binding: dict[str, object],
        event: asyncio.Event,
    ):
        """Extract submit_id and related ids from conversation SSE responses."""
        if "creation_agent/v2/conversation" not in response.url:
            return

        content_type = (response.headers.get("content-type") or "").lower()
        if "event-stream" not in content_type and "json" not in content_type:
            return

        try:
            body_text = await response.text()
        except Exception:
            return

        for line in body_text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue

            self._merge_binding(binding, self._extract_binding_info(obj))

            value = obj.get("value") if isinstance(obj, dict) else None
            if isinstance(value, str) and value.startswith("{") and "submit_id" in value:
                try:
                    nested = json.loads(value)
                    self._merge_binding(binding, self._extract_binding_info(nested))
                except Exception:
                    pass

            if binding.get("submit_id"):
                event.set()

    def _extract_binding_info(self, obj: object) -> dict[str, object]:
        """Recursively search for binding fields."""
        result: dict[str, object] = {}

        def walk(node: object):
            if isinstance(node, dict):
                for key, value in node.items():
                    lk = key.lower()
                    if lk == "submit_id" and isinstance(value, str) and value:
                        result["submit_id"] = value
                    elif lk == "pre_gen_item_ids" and isinstance(value, list):
                        result["pre_gen_item_ids"] = [str(item) for item in value if isinstance(item, (str, int))]
                    elif lk == "conversation_id" and isinstance(value, str) and value:
                        result["conversation_id"] = value
                    elif lk == "generate_id" and isinstance(value, str) and value:
                        result["generate_id"] = value
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(obj)
        return result

    def _merge_binding(self, binding: dict[str, object], incoming: dict[str, object]):
        for key, value in incoming.items():
            if not binding.get(key):
                binding[key] = value

    def _as_optional_str(self, value: object) -> str | None:
        if isinstance(value, str) and value:
            return value
        return None

    def _as_optional_str_list(self, value: object) -> list[str] | None:
        if isinstance(value, list):
            items = [str(item) for item in value if str(item)]
            return items or None
        return None

    async def _extract_video_candidates(self, page: Page) -> list[str]:
        """Collect probable generated video URLs from page."""
        urls: list[str] = []

        try:
            videos = await page.query_selector_all("video")
            for video in videos:
                src = await video.get_attribute("src")
                if src and src.startswith("http"):
                    urls.append(src)
        except Exception:
            pass

        try:
            links = await page.query_selector_all('a[href*=".mp4"], a[href*=".webm"], a[download]')
            for link in links:
                href = await link.get_attribute("href")
                if href and href.startswith("http"):
                    urls.append(href)
        except Exception:
            pass

        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        return deduped

    async def _download_video(self, page: Page, preferred_urls: list[str] | None = None) -> Path:
        """Download generated video for current submission."""
        if preferred_urls:
            for url in preferred_urls:
                output_path = await self._download_video_from_url(page, url)
                if output_path:
                    return output_path

        # Fallback to page scan if preferred URLs are not directly downloadable.
        candidates = await self._extract_video_candidates(page)
        for url in candidates:
            output_path = await self._download_video_from_url(page, url)
            if output_path:
                return output_path

        # Last fallback: button-triggered download.
        download_selectors = [
            'button:has-text("下载")',
            'a:has-text("下载")',
            'button:has-text("Download")',
            'a:has-text("Download")',
        ]

        for selector in download_selectors:
            try:
                btn = await page.query_selector(selector)
                if not btn or not await btn.is_visible():
                    continue
                async with page.expect_download(timeout=45000) as download_info:
                    await btn.click()
                download = await download_info.value
                filename = download.suggested_filename or f"jimeng_{int(time.time())}.mp4"
                suffix = Path(filename).suffix or ".mp4"
                output_path = Path(f"/tmp/jimeng_{int(time.time())}{suffix}")
                await download.save_as(str(output_path))
                return output_path
            except Exception:
                continue

        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Failed to download generated Jimeng video",
                    "type": "generation_error",
                    "code": "download_failed",
                }
            },
        )

    async def _download_video_from_url(self, page: Page, url: str) -> Path | None:
        """Download video from direct URL using browser context request client."""
        try:
            response = await page.context.request.get(url, timeout=60000)
            if not response.ok:
                return None

            body = await response.body()
            if not body:
                return None

            suffix = self._guess_suffix(url, response.headers.get("content-type", ""))
            output_path = Path(f"/tmp/jimeng_{int(time.time() * 1000)}{suffix}")
            output_path.write_bytes(body)
            return output_path
        except Exception as err:
            logger.warning(f"Failed to download video from url={url}: {err}")
            return None

    def _guess_suffix(self, url: str, content_type: str) -> str:
        """Infer file suffix from URL or content type."""
        path = urlparse(url).path.lower()
        for suffix in [".mp4", ".webm", ".mov", ".mkv"]:
            if path.endswith(suffix):
                return suffix

        normalized = content_type.lower()
        if "webm" in normalized:
            return ".webm"
        if "quicktime" in normalized:
            return ".mov"
        return ".mp4"
