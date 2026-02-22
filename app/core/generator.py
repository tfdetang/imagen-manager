"""Core image generation logic using Playwright."""
import asyncio
import base64
import logging
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
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                accept_downloads=True,
            )

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
        launch_opts = {"headless": True}
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

        while elapsed < timeout:
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
            full_prompt = f"基于上传的参考图片，使用 Imagen 3 生成新图片：{prompt}"
        else:
            full_prompt = f"使用 Imagen 3 生成一张图片：{prompt}"

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
