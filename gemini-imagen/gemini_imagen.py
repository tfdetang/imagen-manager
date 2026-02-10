#!/usr/bin/env python3
"""
Gemini Imagen 3 图片生成工具
使用 Playwright 自动化 Gemini 生成 AI 图片
支持上传参考图片
支持 Cookies 持久化（自动保存/加载）
"""

import asyncio
import argparse
import json
import os
import sys
import time
import base64
from pathlib import Path
from playwright.async_api import async_playwright

# Default cookies store location (next to this script)
SCRIPT_DIR = Path(__file__).parent
DEFAULT_COOKIES_STORE = SCRIPT_DIR / "data" / "cookies.json"


def convert_cookies(raw_cookies):
    """Convert browser export format to Playwright format"""
    playwright_cookies = []
    for c in raw_cookies:
        domain = c.get("domain", "")
        if not any(x in domain for x in ["google.com", "gemini.google"]):
            continue
        
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
            "sameSite": same_site
        }
        
        exp = c.get("expirationDate", 0)
        if exp and exp > 0:
            cookie["expires"] = exp
        
        playwright_cookies.append(cookie)
    return playwright_cookies


def save_cookies_to_store(cookies_file: str, store_path: str):
    """Save cookies to persistent store"""
    store = Path(store_path)
    store.parent.mkdir(parents=True, exist_ok=True)
    
    with open(cookies_file, "r") as f:
        raw_cookies = json.load(f)
    
    store_data = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": os.path.abspath(cookies_file),
        "cookies": raw_cookies
    }
    
    with open(store, "w") as f:
        json.dump(store_data, f, indent=2)
    
    print(f"Cookies saved to store: {store}")
    return raw_cookies


def load_cookies_from_store(store_path: str):
    """Load cookies from persistent store. Returns raw cookies or None."""
    store = Path(store_path)
    if not store.exists():
        return None
    
    with open(store, "r") as f:
        store_data = json.load(f)
    
    saved_at = store_data.get("saved_at", "unknown")
    cookies = store_data.get("cookies", [])
    
    if not cookies:
        return None
    
    # Check for obviously expired cookies (all session cookies or all expired)
    now = time.time()
    has_valid = False
    for c in cookies:
        exp = c.get("expirationDate", 0)
        if exp == 0 or exp > now:
            has_valid = True
            break
    
    if not has_valid:
        print(f"WARNING: All stored cookies appear expired (saved: {saved_at})")
        print("Please provide fresh cookies with --cookies")
        return None
    
    print(f"Loaded cookies from store (saved: {saved_at})")
    return cookies


def resolve_cookies(cookies_file: str, cookies_store: str, save_cookies: bool):
    """Resolve which cookies to use: explicit file > store > error"""
    raw_cookies = None
    
    if cookies_file:
        # Explicit cookies file provided
        print(f"Loading cookies from {cookies_file}...")
        with open(cookies_file, "r") as f:
            raw_cookies = json.load(f)
        
        # Auto-save to store (or if --save-cookies explicitly set)
        if save_cookies or not Path(cookies_store).exists():
            save_cookies_to_store(cookies_file, cookies_store)
        elif save_cookies:
            save_cookies_to_store(cookies_file, cookies_store)
    else:
        # Try loading from store
        print(f"No cookies file specified, checking store: {cookies_store}")
        raw_cookies = load_cookies_from_store(cookies_store)
        
        if raw_cookies is None:
            print("ERROR: No cookies available!")
            print("Please provide cookies with --cookies <file>")
            print("They will be saved automatically for future use.")
            sys.exit(1)
    
    return raw_cookies


async def generate_image(cookies_file: str, prompt: str, output_path: str, 
                         proxy: str = None, timeout: int = 60,
                         reference_image: str = None,
                         cookies_store: str = None, save_cookies: bool = False):
    """Generate image using Gemini Imagen 3"""
    
    store_path = cookies_store or str(DEFAULT_COOKIES_STORE)
    raw_cookies = resolve_cookies(cookies_file, store_path, save_cookies)
    
    cookies = convert_cookies(raw_cookies)
    print(f"Converted {len(cookies)} Google cookies")
    
    async with async_playwright() as p:
        # Browser launch options
        launch_opts = {"headless": True}
        if proxy:
            launch_opts["proxy"] = {"server": proxy}
            print(f"Using proxy: {proxy}")
        
        browser = await p.chromium.launch(**launch_opts)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            accept_downloads=True
        )
        
        await context.add_cookies(cookies)
        page = await context.new_page()
        
        print("Navigating to Gemini...")
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        
        # Check login
        content = await page.content()
        if "Sign in" in content and "PRO" not in content and "1.5" not in content:
            print("ERROR: Not logged in! Cookies may be expired.")
            print("Please provide fresh cookies with --cookies <file>")
            # Mark stored cookies as invalid
            sp = Path(store_path)
            if sp.exists():
                print(f"Removing expired cookies store: {sp}")
                sp.unlink()
            await browser.close()
            return False
        
        print("Logged in!")
        
        # Upload reference image if provided
        if reference_image and os.path.exists(reference_image):
            print(f"Uploading reference image: {reference_image}")
            
            uploaded = False
            
            # Method 1: Look for the upload button
            # The correct button has aria-label="Open upload file menu"
            add_button_selectors = [
                'button[aria-label="Open upload file menu"]',
                'button[aria-label*="upload file menu" i]',
                'button[aria-label*="Upload" i]',
                'button.upload-card-button',
                '.uploader button',
                'uploader button',
            ]
            
            # First, try to find and use a hidden file input directly
            file_inputs = await page.query_selector_all('input[type="file"]')
            for fi in file_inputs:
                try:
                    accept = await fi.get_attribute("accept")
                    if accept and "image" in accept:
                        await fi.set_input_files(reference_image)
                        print("Uploaded via existing file input!")
                        uploaded = True
                        await asyncio.sleep(3)
                        break
                except:
                    continue
            
            if not uploaded:
                # Click the add/plus button to reveal file input
                for sel in add_button_selectors:
                    try:
                        btn = await page.wait_for_selector(sel, timeout=2000)
                        if btn:
                            await btn.click()
                            print(f"Clicked add button: {sel}")
                            await asyncio.sleep(1)
                            
                            # Now look for file input or upload option
                            # After clicking +, there might be a menu or direct file input
                            file_input = await page.query_selector('input[type="file"][accept*="image"]')
                            if not file_input:
                                file_input = await page.query_selector('input[type="file"]')
                            
                            if file_input:
                                await file_input.set_input_files(reference_image)
                                print("Image uploaded via file input!")
                                uploaded = True
                                await asyncio.sleep(3)
                                break
                            
                            # Maybe there's an "Upload image" menu item
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
                                        await menu_item.click()
                                        print(f"Clicked menu item: {menu_sel}")
                                        await asyncio.sleep(1)
                                        
                                        file_input = await page.query_selector('input[type="file"]')
                                        if file_input:
                                            await file_input.set_input_files(reference_image)
                                            print("Image uploaded!")
                                            uploaded = True
                                            await asyncio.sleep(3)
                                            break
                                except:
                                    continue
                            
                            if uploaded:
                                break
                    except:
                        continue
            
            if not uploaded:
                # Method 2: Use keyboard shortcut or look for any file input
                print("Trying to find any file input...")
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
                        await file_input.set_input_files(reference_image)
                        print("Image uploaded via revealed file input!")
                        uploaded = True
                        await asyncio.sleep(3)
                    except Exception as e:
                        print(f"Failed to upload: {e}")
            
            if not uploaded:
                print("WARNING: Could not upload reference image, continuing without it...")
        
        # Enter prompt
        print("Entering prompt...")
        if reference_image and os.path.exists(reference_image):
            full_prompt = f"使用 Imagen 3 基于这张图片生成新图片：{prompt}"
        else:
            full_prompt = f"使用 Imagen 3 生成一张图片：{prompt}"
        
        # Find and fill input
        for sel in ['div[contenteditable="true"]', 'textarea', 'rich-textarea']:
            try:
                elem = await page.wait_for_selector(sel, timeout=5000)
                if elem:
                    await elem.click()
                    await page.keyboard.type(full_prompt, delay=30)
                    print("Prompt entered")
                    break
            except:
                continue
        
        await page.screenshot(path=output_path.replace('.png', '_before_submit.png'))
        
        # Submit - try to find and click send button first, then fallback to Enter
        print("Submitting prompt...")
        send_clicked = False
        send_button_selectors = [
            'button[aria-label*="send" i]',
            'button[aria-label*="Send" i]',
            'button[aria-label*="提交" i]',
            'button[aria-label*="生成" i]',
            'button[aria-label*="Generate" i]',
            'button[data-testid="send-button"]',
            'button[type="submit"]',
        ]
        
        for sel in send_button_selectors:
            try:
                btn = await page.wait_for_selector(sel, timeout=1000)
                if btn:
                    await btn.click()
                    print(f"Clicked send button: {sel}")
                    send_clicked = True
                    await asyncio.sleep(1)
                    break
            except:
                continue
        
        if not send_clicked:
            print("Send button not found, using Enter key...")
            await page.keyboard.press("Enter")
            await asyncio.sleep(1)
        
        print(f"Waiting up to {timeout}s for image generation...")
        await asyncio.sleep(timeout)
        
        # Save a debug screenshot
        await page.screenshot(path=output_path.replace('.png', '_debug.png'), full_page=True)
        
        downloaded = False
        
        # Strategy 1: Find the generated image container and its download button
        print("Looking for generated image...")
        
        try:
            await asyncio.sleep(3)
            
            all_imgs = await page.query_selector_all('img')
            generated_img = None
            
            for img in all_imgs:
                try:
                    box = await img.bounding_box()
                    if box and box['width'] > 200 and box['height'] > 200:
                        src = await img.get_attribute("src")
                        if src and "googleusercontent" in src and "/a/" not in src and "/a-/" not in src:
                            generated_img = img
                            print(f"Found large image: {box['width']}x{box['height']}")
                            break
                except:
                    continue
            
            if generated_img:
                download_btn = await page.query_selector('button[aria-label*="download" i]:not([aria-label*="App"]):not([aria-label*="app"])')
                if not download_btn:
                    download_btn = await page.query_selector('button[aria-label*="Download" i]:not([aria-label*="App"]):not([aria-label*="app"])')
                if not download_btn:
                    download_btn = await page.query_selector('button[aria-label*="下载" i]')
                
                if download_btn:
                    try:
                        async with page.expect_download(timeout=30000) as download_info:
                            await download_btn.click()
                            print("Clicked download button")
                        download = await download_info.value
                        await download.save_as(output_path)
                        print(f"Downloaded to: {output_path}")
                        downloaded = True
                    except Exception as e:
                        print(f"Download button click failed: {e}")
        except Exception as e:
            print(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Direct image save from large googleusercontent images
        if not downloaded:
            print("Trying to save image directly...")
            try:
                all_imgs = await page.query_selector_all('img[src*="googleusercontent"]')
                for img in all_imgs:
                    try:
                        src = await img.get_attribute("src")
                        if not src or "/a/" in src or "/a-/" in src:
                            continue
                        
                        box = await img.bounding_box()
                        if not box or box['width'] < 200 or box['height'] < 200:
                            continue
                        
                        print(f"Saving image from: {src[:80]}...")
                        
                        response = await page.evaluate('''async (url) => {
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
                        }''', src)
                        
                        if response and response.startswith("data:image"):
                            data = response.split(",")[1]
                            with open(output_path, "wb") as f:
                                f.write(base64.b64decode(data))
                            print(f"Saved image to: {output_path}")
                            downloaded = True
                            break
                    except Exception as e:
                        print(f"Failed to process image: {e}")
                        continue
            except Exception as e:
                print(f"Strategy 2 failed: {e}")
        
        await browser.close()
        
        if downloaded and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            if file_size < 10000:
                print(f"WARNING: Downloaded file is very small ({file_size} bytes), might be wrong image")
            print("SUCCESS!")
            return True
        else:
            print("FAILED: Could not download image")
            return False


def main():
    parser = argparse.ArgumentParser(description="Generate images using Gemini Imagen 3")
    parser.add_argument("--cookies", "-c", default=None, help="Path to cookies JSON file (auto-saved for reuse)")
    parser.add_argument("--prompt", "-p", required=True, help="Image generation prompt")
    parser.add_argument("--output", "-o", default="./gemini_image.png", help="Output file path")
    parser.add_argument("--proxy", default="http://127.0.0.1:7897", help="Proxy server")
    parser.add_argument("--no-proxy", action="store_true", help="Don't use proxy")
    parser.add_argument("--timeout", "-t", type=int, default=60, help="Generation timeout in seconds")
    parser.add_argument("--image", "-i", help="Reference image to upload")
    parser.add_argument("--save-cookies", action="store_true", 
                        help="Force save cookies to store (default: auto-save on first use)")
    parser.add_argument("--cookies-store", default=None,
                        help=f"Custom cookies store path (default: {DEFAULT_COOKIES_STORE})")
    
    args = parser.parse_args()
    
    proxy = None if args.no_proxy else args.proxy
    
    success = asyncio.run(generate_image(
        cookies_file=args.cookies,
        prompt=args.prompt,
        output_path=args.output,
        proxy=proxy,
        timeout=args.timeout,
        reference_image=args.image,
        cookies_store=args.cookies_store,
        save_cookies=args.save_cookies
    ))
    
    exit(0 if success else 1)


if __name__ == "__main__":
    main()
