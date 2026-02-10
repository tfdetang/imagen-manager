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

                # Wait for generation
                logger.info(f"⏳ Waiting {timeout} seconds for image generation...")
                await asyncio.sleep(timeout)

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
        content = await page.content()

        # Check for login indicators
        has_signin = "Sign in" in content
        has_pro = "PRO" in content
        has_version = "1.5" in content or "2.0" in content

        logger.info(f"  🔍 Login check - Sign in: {has_signin}, PRO: {has_pro}, Version: {has_version}")

        if has_signin and not has_pro and not has_version:
            logger.error("❌ Not logged in! Cookies may be expired")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "message": "Service temporarily unavailable: Google cookies expired",
                        "type": "service_error",
                        "code": "cookies_expired",
                    }
                },
            )

        logger.info("  ✅ User is logged in")

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

        # Try to find existing file input
        logger.info("  🔍 Looking for file input elements...")
        file_inputs = await page.query_selector_all('input[type="file"]')
        logger.info(f"  ℹ️  Found {len(file_inputs)} file input(s)")

        for i, fi in enumerate(file_inputs):
            try:
                accept = await fi.get_attribute("accept")
                logger.info(f"  📋 Input {i}: accept={accept}")
                if accept and "image" in accept:
                    logger.info(f"  ✅ Using file input {i} to upload image")
                    await fi.set_input_files(str(image_path))
                    uploaded = True
                    logger.info("  ⏳ Waiting 3 seconds after upload...")
                    await asyncio.sleep(3)
                    break
            except Exception as e:
                logger.warning(f"  ⚠️  Failed to use input {i}: {e}")
                continue

        # Try clicking upload button with FileChooser
        if not uploaded:
            logger.info("  🔍 Trying to find upload button...")

            # Strategy 1: Use page.set_input_files with file chooser
            selectors = [
                'button[aria-label="Open upload file menu"]',
                'button[aria-label*="upload file menu" i]',
                'button[aria-label*="Upload" i]',
                'button[aria-label*="attach" i]',
                'button[aria-label*="Attach" i]',
            ]

            for sel in selectors:
                try:
                    logger.info(f"  🔍 Trying selector: {sel}")
                    btn = await page.wait_for_selector(sel, timeout=3000)
                    if btn:
                        logger.info(f"  ✅ Found upload button, clicking...")

                        await btn.click()
                        logger.info("  ⏳ Waiting 1 second after click...")
                        await asyncio.sleep(1)

                        # Try to find file input after clicking
                        file_input = await page.query_selector('input[type="file"][accept*="image"]')
                        if not file_input:
                            file_input = await page.query_selector('input[type="file"]')

                        if file_input:
                            logger.info("  ✅ Found file input after clicking button")
                            await file_input.set_input_files(str(image_path))
                            uploaded = True
                            await asyncio.sleep(3)
                            break
                        else:
                            # Look for menu items
                            logger.info("  🔍 Looking for upload menu items...")
                            upload_menu_items = [
                                'button:has-text("Upload")',
                                'button:has-text("上传")',
                                'div[role="menuitem"]:has-text("image")',
                                'div[role="menuitem"]:has-text("图片")',
                                '[data-value*="image"]',
                            ]

                            for menu_sel in upload_menu_items:
                                try:
                                    menu_item = await page.wait_for_selector(menu_sel, timeout=1000)
                                    if menu_item:
                                        logger.info(f"  ✅ Found menu item: {menu_sel}")
                                        await menu_item.click()
                                        await asyncio.sleep(1)

                                        file_input = await page.query_selector('input[type="file"]')
                                        if file_input:
                                            logger.info("  ✅ Found file input after clicking menu item")
                                            await file_input.set_input_files(str(image_path))
                                            uploaded = True
                                            await asyncio.sleep(3)
                                            break
                                except:
                                    continue

                                if uploaded:
                                    break

                except Exception as e:
                    logger.warning(f"  ⚠️  Selector {sel} failed: {e}")
                    continue

                if uploaded:
                    break

        # Strategy 2: Click button and look for any file input
        if not uploaded:
            logger.info("  🔍 Trying alternative approach: click button then find input...")
            try:
                # Screenshot before clicking
                screenshot_before = f"/tmp/debug_before_upload_click_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=screenshot_before, full_page=True)
                logger.info(f"  📸 Screenshot saved: {screenshot_before}")

                btn = await page.wait_for_selector('button[aria-label*="upload" i]', timeout=3000)
                if btn:
                    await btn.click()
                    logger.info("  ✅ Clicked upload button, waiting 3 seconds for input...")
                    await asyncio.sleep(3)

                    # Screenshot after clicking
                    screenshot_after = f"/tmp/debug_after_upload_click_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=screenshot_after, full_page=True)
                    logger.info(f"  📸 Screenshot saved: {screenshot_after}")

                    # Look for ANY input element (not just type="file")
                    logger.info("  🔍 Looking for ANY input elements on page...")
                    all_inputs = await page.query_selector_all('input')
                    logger.info(f"  ℹ️  Found {len(all_inputs)} total input elements")

                    for i, inp in enumerate(all_inputs):
                        try:
                            inp_type = await inp.get_attribute('type') or 'text'
                            inp_id = await inp.get_attribute('id') or ''
                            inp_name = await inp.get_attribute('name') or ''
                            logger.info(f"  📋 Input {i}: type={inp_type}, id={inp_id}, name={inp_name}")

                            # Try to set file on any input
                            if inp_type in ['file', '']:
                                logger.info(f"  ✅ Trying to set file on input {i}")
                                await inp.set_input_files(str(image_path))
                                uploaded = True
                                await asyncio.sleep(3)
                                break
                        except Exception as e:
                            logger.warning(f"  ⚠️  Input {i} failed: {e}")

                    if not uploaded:
                        # Try to find drop zone or other upload mechanisms
                        logger.info("  🔍 Looking for drop zones or other upload areas...")
                        drop_zones = await page.query_selector_all('[role="presentation"], [class*="drop"], [class*="upload"]')
                        logger.info(f"  ℹ️  Found {len(drop_zones)} potential drop zones")

            except Exception as e:
                logger.warning(f"  ⚠️  Alternative approach failed: {e}")

        # Strategy 3: Last resort - try ALL file inputs on page
        if not uploaded:
            logger.info("  🔍 Last resort: trying all file inputs on page...")
            try:
                file_inputs = await page.query_selector_all('input[type="file"]')
                logger.info(f"  ℹ️  Found {len(file_inputs)} file input(s)")
                for i, fi in enumerate(file_inputs):
                    try:
                        logger.info(f"  📋 Attempting to use input {i}")
                        await fi.set_input_files(str(image_path))
                        uploaded = True
                        logger.info(f"  ✅ Successfully uploaded using input {i}")
                        await asyncio.sleep(2)
                        break
                    except Exception as e:
                        logger.warning(f"  ⚠️  Input {i} failed: {e}")
                        continue
            except Exception as e:
                logger.warning(f"  ⚠️  Last resort failed: {e}")

        # Strategy 4: Try drag and drop approach
        if not uploaded:
            logger.info("  🔍 Trying drag and drop approach...")
            try:
                # Read file as base64 for data transfer
                with open(image_path, "rb") as f:
                    file_data = base64.b64encode(f.read()).decode()

                # Try to find contenteditable area and inject image
                content_editables = await page.query_selector_all('[contenteditable="true"]')
                logger.info(f"  ℹ️  Found {len(content_editables)} contenteditable elements")

                for i, elem in enumerate(content_editables):
                    try:
                        logger.info(f"  📋 Trying contenteditable {i}")
                        await elem.click()

                        # Try to paste image
                        await page.evaluate("""
                            async (data) => {
                                // Create blob from base64
                                const byteCharacters = atob(data);
                                const byteNumbers = new Array(byteCharacters.length);
                                for (let i = 0; i < byteCharacters.length; i++) {
                                    byteNumbers[i] = byteCharacters.charCodeAt(i);
                                }
                                const byteArray = new Uint8Array(byteNumbers);
                                const blob = new Blob([byteArray], {type: 'image/png'});

                                // Create clipboard item
                                const item = new ClipboardItem({'image/png': blob});
                                await navigator.clipboard.write([item]);
                            }
                        """, file_data)

                        logger.info("  ✅ Image data copied to clipboard")
                        await asyncio.sleep(1)

                        # Paste using keyboard
                        await page.keyboard.press('Meta+v')  # Cmd+V on Mac
                        logger.info("  ✅ Pressed Cmd+V to paste")
                        await asyncio.sleep(2)

                        # Verify upload
                        uploaded_img = await page.query_selector('img[src*="blob"], img[src*="googleusercontent"]')
                        if uploaded_img:
                            logger.info("  ✅ Image appears to be uploaded via paste!")
                            uploaded = True
                            break
                    except Exception as e:
                        logger.warning(f"  ⚠️  Contenteditable {i} failed: {e}")
                        continue

            except Exception as e:
                logger.warning(f"  ⚠️  Drag and drop approach failed: {e}")

        # Strategy 5: Use JavaScript to reveal hidden file inputs (from original script)
        if not uploaded:
            logger.info("  🔍 Using JavaScript to reveal hidden file inputs...")
            try:
                await page.evaluate('''() => {
                    const inputs = document.querySelectorAll('input[type="file"]');
                    inputs.forEach(input => {
                        input.style.display = 'block';
                        input.style.visibility = 'visible';
                        input.style.opacity = '1';
                    });
                }''')
                await asyncio.sleep(0.5)

                file_input = await page.query_selector('input[type="file"]')
                if file_input:
                    try:
                        await file_input.set_input_files(str(image_path))
                        logger.info("  ✅ Image uploaded via revealed file input!")
                        uploaded = True
                        await asyncio.sleep(3)
                    except Exception as e:
                        logger.warning(f"  ⚠️  Upload via revealed input failed: {e}")
                else:
                    logger.warning("  ⚠️  No file input found even after revealing")
            except Exception as e:
                logger.warning(f"  ⚠️  Reveal strategy failed: {e}")

        if uploaded:
            logger.info("  ✅ Image uploaded successfully")
        else:
            logger.warning(f"  ⚠️  WARNING: Could not upload reference image: {image_path}")
            logger.warning("  ⚠️  Continuing without reference image...")

        return uploaded

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
                btn = await page.wait_for_selector(sel, timeout=1000)
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
