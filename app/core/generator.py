"""Core image generation logic using Playwright."""
import asyncio
import base64
import logging
import re
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser
from fastapi import HTTPException

from app.core.browser import CookieManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ImageGenerator:
    """Handles Gemini Imagen image generation via browser automation."""

    DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    def __init__(self, cookie_manager: CookieManager, proxy: str | None = None):
        self.cookie_manager = cookie_manager
        self.proxy = proxy

    async def generate(
        self,
        prompt: str,
        timeout: int = 60,
        reference_image: Path | None = None,
        reference_images: list[Path] | None = None,
    ) -> Path:
        """
        Generate image using Gemini Imagen.

        Args:
            prompt: Text prompt for generation
            timeout: Generation timeout in seconds
            reference_image: Optional single reference image for editing (deprecated, use reference_images)
            reference_images: Optional list of reference images for editing

        Returns:
            Path to generated image file

        Raises:
            HTTPException: On authentication, timeout, or generation errors
        """
        # Normalize to list for backward compatibility
        if reference_image and not reference_images:
            reference_images = [reference_image]
        elif not reference_images:
            reference_images = []

        logger.info("=" * 80)
        logger.info(f"🚀 Starting image generation")
        logger.info(f"📝 Prompt: {prompt}")
        logger.info(f"⏱️  Timeout: {timeout}s")
        logger.info(f"🖼️  Reference images: {len(reference_images)} image(s)")
        for idx, ref_img in enumerate(reference_images):
            logger.info(f"   - Image {idx+1}: {ref_img}")
        logger.info("=" * 80)

        logger.info("🔑 Loading cookies...")
        cookies = self.cookie_manager.load_cookies()
        logger.info(f"✅ Loaded {len(cookies)} cookies")

        async with async_playwright() as p:
            logger.info("🌐 Launching browser...")
            browser = await self._launch_browser(p)
            logger.info("✅ Browser launched successfully")

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
            # 注入全面反自动化检测脚本
            await context.add_init_script("""
            (() => {
                // ── 1. navigator.webdriver ──────────────────────────────────
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});

                // ── 2. navigator.plugins / mimeTypes ────────────────────────
                // 伪造接近真实 Chrome 的 PluginArray
                const makePlugin = (name, filename, description, mimeTypes) => {
                    const plugin = Object.create(Plugin.prototype);
                    Object.defineProperty(plugin, 'name',        {get: () => name});
                    Object.defineProperty(plugin, 'filename',    {get: () => filename});
                    Object.defineProperty(plugin, 'description', {get: () => description});
                    Object.defineProperty(plugin, 'length',      {get: () => mimeTypes.length});
                    mimeTypes.forEach((mt, i) => { plugin[i] = mt; });
                    return plugin;
                };
                const makeMime = (type, suffixes, description) => {
                    const mt = Object.create(MimeType.prototype);
                    Object.defineProperty(mt, 'type',        {get: () => type});
                    Object.defineProperty(mt, 'suffixes',    {get: () => suffixes});
                    Object.defineProperty(mt, 'description', {get: () => description});
                    return mt;
                };
                const pdfMime   = makeMime('application/pdf', 'pdf', 'Portable Document Format');
                const pdfMime2  = makeMime('text/pdf', 'pdf', 'Portable Document Format');
                const nacl1     = makeMime('application/x-nacl', '', 'Native Client Executable');
                const nacl2     = makeMime('application/x-pnacl', '', 'Portable Native Client Executable');
                const plugins   = [
                    makePlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime, pdfMime2]),
                    makePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', '', [pdfMime]),
                    makePlugin('Native Client', 'internal-nacl-plugin', '', [nacl1, nacl2]),
                ];
                const pluginArr = Object.create(PluginArray.prototype);
                Object.defineProperty(pluginArr, 'length', {get: () => plugins.length});
                plugins.forEach((p, i) => { pluginArr[i] = p; });
                pluginArr.item    = (i) => pluginArr[i];
                pluginArr.namedItem = (n) => plugins.find(p => p.name === n) || null;
                pluginArr.refresh = () => {};
                Object.defineProperty(navigator, 'plugins',   {get: () => pluginArr,    configurable: true});
                Object.defineProperty(navigator, 'mimeTypes', {get: () => {              // MimeTypeArray
                    const arr = Object.create(MimeTypeArray.prototype);
                    const mts = [pdfMime, pdfMime2, nacl1, nacl2];
                    Object.defineProperty(arr, 'length', {get: () => mts.length});
                    mts.forEach((m, i) => { arr[i] = m; });
                    arr.item      = (i) => mts[i];
                    arr.namedItem = (n) => mts.find(m => m.type === n) || null;
                    return arr;
                }, configurable: true});

                // ── 3. navigator.languages ──────────────────────────────────
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: true});

                // ── 4. navigator.platform ───────────────────────────────────
                // 与 UA 中 "Macintosh" 保持一致
                Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel', configurable: true});

                // ── 5. navigator.hardwareConcurrency / deviceMemory ─────────
                Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8,    configurable: true});
                Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8,    configurable: true});

                // ── 6. window.chrome ────────────────────────────────────────
                window.chrome = {
                    runtime: {
                        connect:          () => {},
                        sendMessage:      () => {},
                        onMessage:        {addListener: () => {}, removeListener: () => {}},
                        onConnect:        {addListener: () => {}, removeListener: () => {}},
                        id:               undefined,
                        getManifest:      () => ({}),
                    },
                    loadTimes: () => ({
                        commitLoadTime:     performance.timeOrigin / 1000,
                        connectionInfo:     'http/1.1',
                        finishDocumentLoadTime: (performance.timeOrigin + performance.now()) / 1000,
                        finishLoadTime:     (performance.timeOrigin + performance.now()) / 1000,
                        firstPaintAfterLoadTime: 0,
                        firstPaintTime:     (performance.timeOrigin + performance.now()) / 1000,
                        navigationType:     'Other',
                        npnNegotiatedProtocol: 'h2',
                        requestTime:        performance.timeOrigin / 1000,
                        startLoadTime:      performance.timeOrigin / 1000,
                        wasAlternateProtocolAvailable: false,
                        wasFetchedViaSpdy:  true,
                        wasNpnNegotiated:   true,
                    }),
                    csi: () => ({
                        onloadT: performance.timeOrigin,
                        pageT:   performance.now(),
                        startE:  performance.timeOrigin,
                        tran:    15,
                    }),
                    app: {
                        isInstalled: false,
                        InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
                        RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'},
                        getDetails:   () => null,
                        getIsInstalled: () => false,
                        installState: () => 'not_installed',
                        runningState: () => 'cannot_run',
                    },
                };

                // ── 7. outerWidth / outerHeight ─────────────────────────────
                // headless 下默认为 0，伪造为视口大小
                if (window.outerWidth === 0) {
                    Object.defineProperty(window, 'outerWidth',  {get: () => window.innerWidth,  configurable: true});
                    Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight + 88, configurable: true});
                }

                // ── 8. screen ───────────────────────────────────────────────
                Object.defineProperty(screen, 'availWidth',  {get: () => 1920, configurable: true});
                Object.defineProperty(screen, 'availHeight', {get: () => 1080, configurable: true});
                Object.defineProperty(screen, 'width',       {get: () => 1920, configurable: true});
                Object.defineProperty(screen, 'height',      {get: () => 1080, configurable: true});
                Object.defineProperty(screen, 'colorDepth',  {get: () => 24,   configurable: true});
                Object.defineProperty(screen, 'pixelDepth',  {get: () => 24,   configurable: true});

                // ── 9. document.hasFocus / visibilityState ──────────────────
                document.hasFocus        = () => true;
                Object.defineProperty(document, 'hidden',          {get: () => false,    configurable: true});
                Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});

                // ── 10. Permissions API ─────────────────────────────────────
                const _origPermQuery = navigator.permissions.query.bind(navigator.permissions);
                navigator.permissions.query = (parameters) => {
                    const alwaysGranted = ['notifications', 'clipboard-read', 'clipboard-write'];
                    if (alwaysGranted.includes(parameters.name)) {
                        return Promise.resolve(Object.assign(Object.create(PermissionStatus.prototype), {
                            state: 'granted', onchange: null
                        }));
                    }
                    return _origPermQuery(parameters);
                };

                // ── 11. 覆盖 toString 防止函数特征检测 ─────────────────────
                const nativeToString = Function.prototype.toString;
                const patchedFns = new WeakSet();
                const markNative = (fn) => { patchedFns.add(fn); return fn; };
                Function.prototype.toString = function() {
                    if (patchedFns.has(this)) return `function ${this.name || ''}() { [native code] }`;
                    return nativeToString.call(this);
                };
                markNative(navigator.permissions.query);
                markNative(Function.prototype.toString);
            })();
            """);

            logger.info("🍪 Adding cookies to browser context...")
            await context.add_cookies(cookies)
            page = await context.new_page()
            logger.info("✅ Browser page created")

            try:
                # Navigate to Gemini
                logger.info("🔗 Navigating to https://gemini.google.com/app...")
                await page.goto(
                    "https://gemini.google.com/app",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                logger.info("✅ Page loaded, waiting 5 seconds...")
                await asyncio.sleep(5)

                # Save screenshot after navigation
                screenshot_path = f"/tmp/debug_navigation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"📸 Screenshot saved: {screenshot_path}")

                # Verify login
                logger.info("🔐 Verifying login status...")
                await self._verify_login(page)
                logger.info("✅ Login verified successfully")

                # Ensure image generation tool is selected
                logger.info("🧰 Ensuring image generation tool is selected...")
                tool_selected = await self._ensure_image_tool(page)
                if tool_selected:
                    logger.info("✅ Image generation tool selected")
                else:
                    logger.warning("⚠️  Could not confirm image generation tool selection")

                # Switch to Pro mode for best image quality
                logger.info("⭐ Switching to Pro mode...")
                pro_selected = await self._ensure_pro_mode(page)
                if pro_selected:
                    logger.info("✅ Pro mode activated")
                else:
                    logger.warning("⚠️  Could not confirm Pro mode selection, continuing anyway")

                # Upload reference images if provided
                uploaded_count = 0
                if reference_images:
                    logger.info(f"📤 Uploading {len(reference_images)} reference image(s)...")
                    for idx, ref_img in enumerate(reference_images):
                        logger.info(f"📤 Uploading reference image {idx+1}/{len(reference_images)}: {ref_img}")
                        upload_success = await self._upload_image(page, ref_img)
                        if upload_success:
                            uploaded_count += 1
                        else:
                            logger.warning(f"⚠️  Reference image {idx+1} upload failed")
                    logger.info(f"✅ Successfully uploaded {uploaded_count}/{len(reference_images)} image(s)")

                # Enter and submit prompt
                logger.info("✍️  Submitting prompt...")
                await self._submit_prompt(page, prompt, uploaded_count > 0)
                logger.info("✅ Prompt submitted")

                # Save screenshot after submission
                screenshot_path = f"/tmp/debug_after_submit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"📸 Screenshot saved: {screenshot_path}")

                # Wait for generation with polling
                logger.info(f"⏳ Waiting for image generation (max {timeout}s)...")
                generation_ready = await self._wait_for_generation(page, timeout)

                if not generation_ready:
                    logger.warning("⚠️  Generation may not be complete, attempting download anyway...")

                # Save screenshot before download attempt
                screenshot_path = f"/tmp/debug_before_download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"📸 Screenshot saved: {screenshot_path}")

                # Download image
                logger.info("⬇️  Attempting to download image...")
                output_path = await self._download_image(page)
                logger.info(f"✅ Image downloaded successfully: {output_path}")

                return output_path

            except Exception as e:
                logger.error(f"❌ Error during generation: {e}")
                # Save error screenshot
                try:
                    screenshot_path = f"/tmp/debug_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    logger.error(f"📸 Error screenshot saved: {screenshot_path}")
                except:
                    pass
                raise
            finally:
                logger.info("🔚 Closing browser...")
                await browser.close()
                logger.info("✅ Browser closed")

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
                # 修复 headless 特有的空 outerWidth/outerHeight
                "--start-maximized",
                # 禁止暴露自动化标志
                "--exclude-switches=enable-automation",
                "--disable-extensions",
                # 避免 WebGL 暴露 SwiftShader 渲染器
                "--use-gl=angle",
                "--use-angle=swiftshader-webgl",
                # 减少熵值泄漏
                "--disable-client-side-phishing-detection",
                "--disable-default-apps",
                "--disable-hang-monitor",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
                "--safebrowsing-disable-auto-update",
                "--password-store=basic",
                "--use-mock-keychain",
            ],
        }
        if self.proxy:
            launch_opts["proxy"] = {"server": self.proxy}
            logger.info(f"🔀 Using proxy: {self.proxy}")
        else:
            logger.info("🌍 No proxy configured")

        return await playwright.chromium.launch(**launch_opts)

    async def _verify_login(self, page: Page):
        """Verify user is logged in to Gemini."""
        logger.info("  🔍 Checking login status...")

        # Check 1: URL should not be redirected to accounts.google.com
        current_url = page.url
        logger.info(f"  🔗 Current URL: {current_url}")

        if "accounts.google.com" in current_url:
            logger.error("❌ Redirected to login page! Cookies may be expired")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "message": "Service temporarily unavailable: Google cookies expired (redirected to login)",
                        "type": "service_error",
                        "code": "cookies_expired",
                    }
                },
            )

        # Check 2: Look for "Sign in" button in the header area (indicates not logged in)
        # Gemini can be accessed without login, but we need login for full functionality
        try:
            signin_button = await page.wait_for_selector(
                'a[href*="signin"], button:has-text("Sign in"), a:has-text("Sign in")',
                timeout=3000
            )
            if signin_button:
                # Verify it's visible in the header area (not just any "sign in" text)
                is_visible = await signin_button.is_visible()
                if is_visible:
                    logger.error("❌ Found 'Sign in' button - not logged in! Cookies may be expired")
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": {
                                "message": "Service temporarily unavailable: Google cookies expired (Sign in button visible)",
                                "type": "service_error",
                                "code": "cookies_expired",
                            }
                        },
                    )
        except HTTPException:
            # Re-raise HTTPException to trigger account switch
            raise
        except Exception as e:
            # No "Sign in" button found (timeout) - this is good, means user is logged in
            if "Timeout" in str(e):
                logger.info("  ✅ No 'Sign in' button found - user appears to be logged in")
            else:
                logger.warning(f"  ⚠️  Error checking sign in button: {e}")

        # Check 3: Verify we have the input area (basic functionality check)
        try:
            input_area = await page.wait_for_selector(
                'div[contenteditable="true"], textarea',
                timeout=5000
            )
            if input_area:
                logger.info("  ✅ Found input area - page loaded correctly")
        except:
            logger.warning("  ⚠️  Could not find input area")

        logger.info("  ✅ Login verification passed")

    async def _upload_image(self, page: Page, image_path: Path):
        """Upload reference image to Gemini."""
        uploaded = False
        logger.info(f"  📤 Attempting to upload image: {image_path}")

        # Verify file exists before trying to upload
        if not image_path.exists():
            logger.error(f"  ❌ Reference image file does not exist: {image_path}")
            return False

        file_size = image_path.stat().st_size
        logger.info(f"  📏 Reference image size: {file_size} bytes")

        # Primary strategy: Click upload button then "Upload files" menu item
        upload_button_selectors = [
            'button[aria-label="Open upload file menu"]',
            'button[aria-label*="upload" i]',
        ]

        for sel in upload_button_selectors:
            try:
                logger.info(f"  🔍 Trying selector: {sel}")
                btn = await page.wait_for_selector(sel, timeout=3000)
                if btn:
                    logger.info(f"  ✅ Found upload button, clicking...")
                    await btn.click()
                    await asyncio.sleep(1)

                    # Look for "Upload files" menu item
                    logger.info("  🔍 Looking for 'Upload files' menu item...")
                    menu_item = await page.wait_for_selector(
                        'button:has-text("Upload files"), button:has-text("Upload"), button:has-text("上传")',
                        timeout=3000
                    )
                    if menu_item:
                        logger.info("  ✅ Found menu item, clicking with file chooser...")
                        try:
                            async with page.expect_file_chooser(timeout=10000) as fc_info:
                                await menu_item.click()
                            file_chooser = await fc_info.value
                            await file_chooser.set_files(str(image_path))
                            logger.info("  ✅ File set via file chooser")
                            uploaded = True
                            await asyncio.sleep(3)
                            break
                        except Exception as fc_err:
                            logger.warning(f"  ⚠️  File chooser failed: {fc_err}")
            except Exception as e:
                logger.warning(f"  ⚠️  Selector {sel} failed: {e}")
                continue

            if uploaded:
                break

        # Fallback: Try to find existing file input
        if not uploaded:
            logger.info("  🔍 Looking for existing file input elements...")
            file_inputs = await page.query_selector_all('input[type="file"]')
            for fi in file_inputs:
                try:
                    accept = await fi.get_attribute("accept")
                    if accept and "image" in accept:
                        await fi.set_input_files(str(image_path))
                        uploaded = True
                        await asyncio.sleep(2)
                        break
                except:
                    continue

        if uploaded:
            logger.info("  ✅ Image uploaded successfully")
        else:
            logger.warning(f"  ⚠️  WARNING: Could not upload reference image: {image_path}")

        return uploaded

    async def _ensure_pro_mode(self, page: Page) -> bool:
        """Switch the Gemini model to Pro mode.

        The model selector appears as a button labeled with the current model
        (e.g. "Pro", "Fast", "Thinking", "1.5 Flash", "2.0 Flash").  Clicking
        it opens a dropdown with at least: Fast / Thinking / Pro.
        If Pro is already active the method returns True immediately.
        """
        pro_regex = re.compile(r"\bPro\b", re.I)

        # -----------------------------------------------------------------------
        # Step 1: Check whether Pro is already the active model.
        # The active state is commonly shown as:
        #   - A button whose text IS "Pro" (with optional chevron)
        #   - An aria-label containing "Pro" + selected / checked indicator
        # -----------------------------------------------------------------------
        already_pro_locators = [
            # English: button text exactly "Pro" (already selected, no need to open menu)
            page.locator('button[aria-pressed="true"]:has-text("Pro")'),
            page.locator('button[aria-selected="true"]:has-text("Pro")'),
            page.locator('[aria-checked="true"]:has-text("Pro")'),
            # The model pill/label at the bottom right often just reads "Pro"
            # If it IS the selector button and reads "Pro", we're already in Pro mode.
        ]
        for loc in already_pro_locators:
            try:
                if await loc.first.is_visible():
                    logger.info("  ✅ Already in Pro mode")
                    return True
            except Exception:
                continue

        # -----------------------------------------------------------------------
        # Step 2: Open the model selector dropdown.
        # The selector button typically shows the current model name.
        # Known labels: "Pro", "Fast", "Thinking", "1.5 Flash", "2.0 Flash",
        #               "Nano Banana Pro", etc.
        # -----------------------------------------------------------------------
        model_selector_locators = [
            # The visible "Pro ▾" or "Fast ▾" button at the bottom-right of the input area
            page.locator('button:has-text("Pro")').filter(has=page.locator('[class*="chevron"], [class*="arrow"], svg')),
            page.locator('button:has-text("Fast")'),
            page.locator('button:has-text("Thinking")'),
            # Aria label variants
            page.locator('button[aria-label*="model" i]'),
            page.locator('button[aria-label*="Model" i]'),
            page.locator('button[aria-haspopup="listbox"]'),
            page.locator('button[aria-haspopup="menu"]').filter(has_text=re.compile(r"(Pro|Fast|Thinking|Flash)", re.I)),
            # Generic: any button that contains one of the known model names
            page.locator('button').filter(has_text=re.compile(r"^(Pro|Fast|Thinking|1\.5 Flash|2\.0 Flash)$", re.I)),
        ]

        opened = False
        for loc in model_selector_locators:
            try:
                if await loc.first.is_visible():
                    logger.info("  🔍 Clicking model selector to open dropdown...")
                    await loc.first.click()
                    await asyncio.sleep(1)
                    opened = True
                    break
            except Exception as e:
                logger.warning(f"  ⚠️  Model selector click failed: {e}")
                continue

        if not opened:
            logger.warning("  ⚠️  Could not open model selector")
            return False

        # Save screenshot for debugging
        try:
            screenshot_path = f"/tmp/debug_model_menu_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"  📸 Model menu screenshot: {screenshot_path}")
        except Exception:
            pass

        # -----------------------------------------------------------------------
        # Step 3: Select "Pro" from the open dropdown.
        # -----------------------------------------------------------------------
        pro_item_locators = [
            page.get_by_role("option",           name=pro_regex),
            page.get_by_role("menuitem",         name=pro_regex),
            page.get_by_role("menuitemradio",    name=pro_regex),
            page.get_by_role("menuitemcheckbox", name=pro_regex),
            page.locator('li[role="option"]:has-text("Pro")'),
            page.locator('li[role="menuitem"]:has-text("Pro")'),
            page.locator('div[role="option"]:has-text("Pro")'),
            page.locator('div[role="menuitem"]:has-text("Pro")'),
            # Plain text match (last resort)
            page.locator(':has-text("Pro"):not(:has(*:has-text("Pro")))'),  # leaf node
        ]

        for loc in pro_item_locators:
            try:
                if await loc.first.is_visible():
                    logger.info("  ⭐ Clicking 'Pro' menu item...")
                    await loc.first.click()
                    await asyncio.sleep(1)
                    logger.info("  ✅ Pro mode selected")
                    return True
            except Exception as e:
                logger.warning(f"  ⚠️  Pro item selector failed: {e}")
                continue

        logger.warning("  ⚠️  Pro menu item not found in dropdown")
        return False

    async def _ensure_image_tool(self, page: Page) -> bool:
        """Ensure the image generation tool is selected in Gemini UI.

        Supports both English and Chinese Gemini interfaces, and both the new UI
        (landing-page shortcut pills + "+" menu button) and old UI ("Tools" button).
        """
        # Regex covering all known English / Chinese labels for the image tool
        image_tool_regex = re.compile(
            r"(Create image|Make image|制作图片|创建图片|Create images|Make images)", re.I
        )

        # -----------------------------------------------------------------------
        # Strategy 1: Click the "Create image" shortcut pill on the landing page.
        # The landing page shows suggestion pills before the user has typed anything.
        # English UI: "Create image"    Chinese UI: "制作图片" / "创建图片"
        # The pills may be <button>, <div>, <a>, or custom elements.
        # -----------------------------------------------------------------------
        create_image_pill_locators = [
            # English – role-based (most reliable)
            page.get_by_role("button", name=re.compile(r"^Create image$", re.I)),
            page.get_by_role("link",   name=re.compile(r"^Create image$", re.I)),
            # English – text-based fallbacks
            page.locator('button:has-text("Create image")'),
            page.locator('a:has-text("Create image")'),
            page.locator('[role="option"]:has-text("Create image")'),
            page.locator('[class*="suggestion"]:has-text("Create image")'),
            page.locator('[class*="chip"]:has-text("Create image")'),
            page.locator('[class*="pill"]:has-text("Create image")'),
            page.locator('*:has-text("Create image"):not(:has(*:has-text("Create image")))'),  # leaf node
            # Chinese – role-based
            page.get_by_role("button", name=re.compile(r"^(制作图片|创建图片)$")),
            # Chinese – text-based fallbacks
            page.locator('button:has-text("制作图片")'),
            page.locator('button:has-text("创建图片")'),
            page.locator('[class*="suggestion"]:has-text("制作图片")'),
            page.locator('[class*="chip"]:has-text("制作图片")'),
        ]
        for loc in create_image_pill_locators:
            try:
                if await loc.first.is_visible():
                    logger.info("  🖼️  Clicking 'Create image' shortcut pill on landing page...")
                    await loc.first.click()
                    await asyncio.sleep(2)
                    logger.info("  ✅ 'Create image' shortcut pill clicked")
                    return True
            except Exception:
                continue

        # -----------------------------------------------------------------------
        # Strategy 2: Menu is already open — select the image item directly.
        # -----------------------------------------------------------------------
        async def _menu_item_visible() -> bool:
            try:
                loc = page.get_by_role("menuitemcheckbox", name=image_tool_regex)
                return await loc.first.is_visible()
            except Exception:
                return False

        if await _menu_item_visible():
            try:
                await page.get_by_role("menuitemcheckbox", name=image_tool_regex).first.click()
                await asyncio.sleep(1)
                logger.info("  ✅ Image tool menu item clicked (menu already open)")
                return True
            except Exception:
                pass

        # -----------------------------------------------------------------------
        # Strategy 3: Open the tools / attachment menu, then select "Create image".
        # New English UI : "+" button at the bottom of the input area.
        # New Chinese UI : "添加" / "+" button.
        # Old UI (both)  : "Tools" / "工具" button.
        # -----------------------------------------------------------------------
        tool_button_locators = [
            # --- English new UI ---
            page.get_by_role("button", name=re.compile(r"^\+$")),
            page.get_by_role("button", name=re.compile(r"^Add$", re.I)),
            page.locator('button[aria-label="+"]'),
            page.locator('button[aria-label="Add"]'),
            page.locator('button[data-test-id="attachment-button"]'),
            # --- Chinese new UI ---
            page.get_by_role("button", name=re.compile(r"^添加$")),
            page.locator('button[aria-label="添加"]'),
            # --- English old UI ---
            page.get_by_role("button", name=re.compile(r"^Tools$", re.I)),
            page.locator('button[aria-label="Tools"]'),
            page.locator('button[aria-label*="Tools"]'),
            page.locator('button:has-text("Tools")'),
            # --- Chinese old UI ---
            page.get_by_role("button", name=re.compile(r"^工具$")),
            page.locator('button[aria-label="工具"]'),
            page.locator('button[aria-label*="工具"]'),
            page.locator('button:has-text("工具")'),
        ]

        opened_menu = False
        for loc in tool_button_locators:
            try:
                if await loc.first.is_visible():
                    logger.info("  🔍 Clicking tool/add button to open menu...")
                    await loc.first.click()
                    await asyncio.sleep(1)
                    opened_menu = True
                    break
            except Exception as e:
                logger.warning(f"  ⚠️  Tool button click failed: {e}")
                continue

        if not opened_menu:
            logger.warning("  ⚠️  Could not open tool menu")
            return False

        # Save screenshot after opening menu for debugging
        try:
            screenshot_path = f"/tmp/debug_tool_menu_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"  📸 Tool menu screenshot: {screenshot_path}")
        except Exception:
            pass

        # Select the image generation item from the now-open menu.
        # English UI: "Create image" / "Make images"
        # Chinese UI: "制作图片" / "创建图片"
        menu_item_locators = [
            # Role-based (most robust, language-agnostic via regex)
            page.get_by_role("menuitemcheckbox", name=image_tool_regex),
            page.get_by_role("menuitem",         name=image_tool_regex),
            page.get_by_role("option",           name=image_tool_regex),
            # English – explicit text selectors
            page.locator('div[role="menuitemcheckbox"]:has-text("Create image")'),
            page.locator('div[role="menuitem"]:has-text("Create image")'),
            page.locator('li[role="menuitem"]:has-text("Create image")'),
            page.locator('button:has-text("Create image")'),
            page.locator('div[role="menuitem"]:has-text("Make images")'),
            page.locator('button:has-text("Make images")'),
            page.locator('div[role="menuitem"]:has-text("Create images")'),
            page.locator('button:has-text("Create images")'),
            # Chinese – explicit text selectors
            page.locator('div[role="menuitemcheckbox"]:has-text("制作图片")'),
            page.locator('div[role="menuitem"]:has-text("制作图片")'),
            page.locator('li[role="menuitem"]:has-text("制作图片")'),
            page.locator('button:has-text("制作图片")'),
            page.locator('div[role="menuitem"]:has-text("创建图片")'),
            page.locator('button:has-text("创建图片")'),
        ]

        for loc in menu_item_locators:
            try:
                if await loc.first.is_visible():
                    await loc.first.click()
                    await asyncio.sleep(2)
                    logger.info("  ✅ Image tool menu item clicked")
                    return True
            except Exception as e:
                logger.warning(f"  ⚠️  Image tool selector failed: {e}")
                continue

        logger.warning("  ⚠️  Image tool menu item not found")
        return False

    async def _wait_for_generation(self, page: Page, timeout: int) -> bool:
        """
        Poll the page to detect when image generation is complete.

        Returns True if generation appears complete, False if timeout reached.
        """
        poll_interval = 5  # Check every 5 seconds
        min_wait = 30  # Minimum wait before first check (generation takes time)
        elapsed = 0

        # Wait minimum time before starting to poll
        logger.info(f"  ⏳ Initial wait of {min_wait}s before polling...")
        await asyncio.sleep(min_wait)
        elapsed = min_wait

        poll_count = 0
        while elapsed < timeout:
            # Take a screenshot on every poll for debugging
            poll_count += 1
            try:
                screenshot_path = f"/tmp/debug_poll_{poll_count:03d}_{elapsed}s_{datetime.now().strftime('%H%M%S')}.png"
                await page.screenshot(path=screenshot_path)
                logger.info(f"  📸 Poll screenshot [{poll_count}]: {screenshot_path}")
            except Exception:
                pass

            # Check for generation complete indicators
            is_ready, reason = await self._check_generation_status(page)

            if is_ready:
                # Double check after a short delay to avoid false positives
                await asyncio.sleep(2)
                is_still_ready, _ = await self._check_generation_status(page)
                if is_still_ready:
                    logger.info(f"  ✅ Generation complete detected after {elapsed}s: {reason}")
                    return True

            # Check for error indicators
            has_error, error_msg = await self._check_generation_error(page)
            if has_error:
                logger.warning(f"  ⚠️  Generation error detected: {error_msg}")
                # Still return True to attempt download (might have partial result)
                return True

            # Wait before next poll
            remaining = timeout - elapsed
            wait_time = min(poll_interval, remaining)
            if wait_time > 0:
                logger.info(f"  ⏳ Polling... ({elapsed}s/{timeout}s elapsed)")
                await asyncio.sleep(wait_time)
                elapsed += wait_time

        logger.warning(f"  ⚠️  Timeout reached ({timeout}s) without detecting completion")
        return False

    async def _check_generation_status(self, page: Page) -> tuple[bool, str]:
        """
        Check if image generation appears to be complete.

        Returns (is_ready, reason) tuple.
        """
        # Check 1: Download button appeared (most reliable indicator)
        try:
            download_btn = await page.query_selector(
                'button[aria-label*="download" i]:not([aria-label*="App"]):not([aria-label*="app"])'
            )
            if download_btn:
                is_visible = await download_btn.is_visible()
                if is_visible:
                    return True, "Download button visible"
        except:
            pass

        # Check 2: Large generated image appeared (must be new, not uploaded reference)
        try:
            images = await page.query_selector_all('img[src*="googleusercontent"]')
            large_image_count = 0
            for img in images:
                src = await img.get_attribute("src") or ""
                # Skip profile pictures and small images
                if "/a/" in src or "/a-/" in src:
                    continue

                box = await img.bounding_box()
                if box and box["width"] >= 256 and box["height"] >= 256:
                    large_image_count += 1

            # Only consider ready if we have at least one large image
            # and it's likely a generated image (not just uploaded reference)
            if large_image_count >= 1:
                return True, f"Large image detected (count: {large_image_count})"
        except:
            pass

        return False, ""

    async def _check_generation_error(self, page: Page) -> tuple[bool, str]:
        """
        Check if there's an error message on the page.

        Returns (has_error, error_message) tuple.
        """
        error_selectors = [
            '[class*="error"]',
            '[class*="warning"]',
            ':has-text("unable to generate")',
            ':has-text("无法生成")',
            ':has-text("try again")',
            ':has-text("重试")',
        ]

        for selector in error_selectors:
            try:
                elem = await page.query_selector(selector)
                if elem:
                    is_visible = await elem.is_visible()
                    if is_visible:
                        text = await elem.text_content()
                        if text and len(text) < 200:  # Avoid capturing large blocks
                            return True, text.strip()[:100]
            except:
                continue

        return False, ""

    async def _submit_prompt(self, page: Page, prompt: str, has_image: bool):
        """Enter and submit prompt to Gemini."""
        # Build full prompt
        if has_image:
            full_prompt = f"基于上传的参考图片：{prompt}"
        else:
            full_prompt = f"{prompt}"

        logger.info(f"  ✍️  Full prompt: {full_prompt}")

        # Find and fill input
        logger.info("  🔍 Looking for input element...")
        input_found = False
        for sel in ['div[contenteditable="true"]', "textarea", "rich-textarea"]:
            try:
                logger.info(f"  🔍 Trying selector: {sel}")
                elem = await page.wait_for_selector(sel, timeout=5000)
                if elem:
                    logger.info(f"  ✅ Found input element: {sel}")
                    await elem.click()
                    logger.info("  ⌨️  Typing prompt...")
                    await page.keyboard.type(full_prompt, delay=30)
                    logger.info("  ✅ Prompt typed successfully")
                    input_found = True
                    break
            except Exception as e:
                logger.warning(f"  ⚠️  Selector {sel} failed: {e}")
                continue

        if not input_found:
            logger.error("  ❌ Could not find input element!")

        # Submit
        logger.info("  🔍 Looking for send button...")
        send_clicked = False
        send_selectors = [
            'button[aria-label*="send" i]',
            'button[aria-label*="Send" i]',
            'button[type="submit"]',
        ]
        for sel in send_selectors:
            try:
                logger.info(f"  🔍 Trying selector: {sel}")
                btn = await page.wait_for_selector(sel, timeout=5000)
                if btn:
                    logger.info(f"  ✅ Found send button: {sel}")
                    await btn.click()
                    send_clicked = True
                    logger.info("  ✅ Send button clicked")
                    await asyncio.sleep(1)
                    break
            except Exception as e:
                logger.warning(f"  ⚠️  Selector {sel} failed: {e}")
                continue

        if not send_clicked:
            logger.info("  ⚠️  No send button found, using Enter key")
            await page.keyboard.press("Enter")
            await asyncio.sleep(1)
            logger.info("  ✅ Enter key pressed")

    async def _download_image(self, page: Page) -> Path:
        """Download generated image from Gemini."""
        logger.info("  ⏳ Waiting 3 seconds before download attempt...")
        await asyncio.sleep(3)

        # Strategy 1: Click download button
        logger.info("  📥 Strategy 1: Looking for download button...")
        try:
            download_btn = await page.query_selector(
                'button[aria-label*="download" i]:not([aria-label*="App"]):not([aria-label*="app"])'
            )
            if download_btn:
                logger.info("  ✅ Found download button, clicking...")
                async with page.expect_download(timeout=100000) as download_info:
                    await download_btn.click()
                download = await download_info.value
                temp_path = Path(f"/tmp/gemini_{asyncio.get_event_loop().time()}.png")
                await download.save_as(str(temp_path))
                logger.info(f"  ✅ Downloaded via button: {temp_path}")
                return temp_path
            else:
                logger.info("  ℹ️  No download button found")
        except Exception as e:
            logger.warning(f"  ⚠️  Download button strategy failed: {e}")

        # Strategy 2: Direct image fetch
        logger.info("  🖼️  Strategy 2: Looking for generated images...")
        try:
            all_imgs = await page.query_selector_all('img[src*="googleusercontent"]')
            logger.info(f"  ℹ️  Found {len(all_imgs)} googleusercontent images")

            for i, img in enumerate(all_imgs):
                src = await img.get_attribute("src")
                logger.info(f"  🔍 Image {i}: src={src[:80]}...")

                if not src or "/a/" in src or "/a-/" in src:
                    logger.info(f"  ⏭️  Skipping image {i} (profile picture)")
                    continue

                box = await img.bounding_box()
                if box:
                    logger.info(f"  📏 Image {i} size: {box['width']}x{box['height']}")
                else:
                    logger.info(f"  ⚠️  Image {i} has no bounding box")

                if not box or box["width"] < 200 or box["height"] < 200:
                    logger.info(f"  ⏭️  Skipping image {i} (too small)")
                    continue

                logger.info(f"  ✅ Image {i} looks good, fetching...")
                # Fetch image data
                response = await page.evaluate(
                    """async (url) => {
                    try {
                        const res = await fetch(url);
                        const blob = await res.blob();
                        return new Promise((resolve) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result);
                            reader.readAsDataURL(blob);
                        });
                    } catch(e) {
                        return null;
                    }
                }""",
                    src,
                )

                if response and response.startswith("data:image"):
                    data = response.split(",")[1]
                    temp_path = Path(f"/tmp/gemini_{asyncio.get_event_loop().time()}.png")
                    with open(temp_path, "wb") as f:
                        f.write(base64.b64decode(data))
                    logger.info(f"  ✅ Downloaded via direct fetch: {temp_path}")
                    return temp_path
                else:
                    logger.warning(f"  ⚠️  Image {i} fetch failed or not image data")
        except Exception as e:
            logger.error(f"  ❌ Direct fetch strategy failed: {e}")

        logger.error("  ❌ All download strategies failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Failed to download generated image",
                    "type": "generation_error",
                    "code": "download_failed",
                }
            },
        )
