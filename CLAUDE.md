# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenAI-compatible REST API for Google Gemini Imagen image generation, built with FastAPI and dual engines (HTTP + Playwright). The project has two components:

1. **`gemini-imagen/`** - Original CLI script for direct automation
2. **`app/`** - Production FastAPI service with OpenAI-compatible endpoints

## Common Commands

```bash
# Install dependencies
uv sync
uv run playwright install chromium

# Run CLI script
uv run gemini-imagen/gemini_imagen.py --prompt "a cat" --output cat.png

# Development server (hot reload)
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# Run tests
uv run pytest
uv run pytest --cov=app
```

## Architecture

### Project Structure

```
app/
├── main.py              # FastAPI app with CORS middleware
├── config.py            # Pydantic settings from .env
├── auth.py              # Bearer token authentication
├── models.py            # OpenAI-compatible request/response models
├── api/
│   └── routes.py        # /v1/* (legacy) + /http/v1/* + /playwright/v1/*
├── core/
│   ├── browser.py       # CookieManager for cookie persistence
│   ├── generator.py     # ImageGenerator - Playwright automation
│   ├── http_generator.py# HttpImageGenerator - Gemini HTTP API
│   ├── account_pool.py  # AccountPool + engine-specific account pools
│   └── semaphore.py     # ConcurrencyManager - rate limiting
└── utils/
    └── storage.py       # ImageStorage + auto-cleanup

gemini-imagen/
└── gemini_imagen.py     # Original CLI script (reference implementation)
```

### Request Flow

```
Client Request
    ↓
API Key Auth (auth.py)
    ↓
Concurrency Check (semaphore.py)
    ↓
Parse Request (routes.py)
    ↓
Route Prefix Selects Engine
    ├→ /http/v1/...       -> HttpImageGenerator.generate()
    ├→ /playwright/v1/... -> ImageGenerator.generate()
    └→ /v1/...            -> default engine (IMAGE_ENGINE)
    ↓
Engine Execution
    ├→ HTTP: init tokens -> rotate cookies -> upload refs -> StreamGenerate -> parse image URL -> download
    └→ Playwright: launch browser -> upload refs -> submit -> wait -> download
    ↓
Save to Storage (storage.py)
    ↓
Return OpenAI-compatible Response
```

### Key Architectural Decisions

**CORS Configuration:** The API includes `CORSMiddleware` with `allow_origins=["*"]` for external tool access. For production, modify `app/main.py:31-37` to specify allowed origins.

**Engine Routing by URL Prefix:** Image routes are engine-specific by path prefix:
- `/http/v1/images/generations`
- `/playwright/v1/images/generations`
- `/http/v1/images/edits`
- `/playwright/v1/images/edits`

Legacy routes `/v1/images/generations` and `/v1/images/edits` remain for compatibility and use `IMAGE_ENGINE` as default.

**Multi-File Upload:** The `/v1/images/edits` endpoint uses `List[UploadFile]` to support multiple reference images. FastAPI automatically collects all fields with the same name into a list. Single `UploadFile` would only capture the last file.

**Concurrency Limit:** Uses an in-process semaphore (`ConcurrencyManager`). **Critical:** This means `--workers 1` is required in production. Multiple workers would each have independent semaphores, bypassing the limit. For multi-worker setups, implement a distributed lock (Redis).

**Cookie Management:** Cookies are loaded from account files and used by both engine pools (`http` and `playwright`). Uploading cookies via `/v1/cookies` updates and clears cooldown for both pools.

**Playwright Download Fallback:** Two strategies for retrieving generated images:
1. Click download button and use Playwright's download handler
2. Parse page for `googleusercontent` image URLs and fetch via JavaScript

**HTTP Download Flow:** Generated `lh3.googleusercontent.com` URLs are fetched through Google's text redirect chain with cookies.

**Proxy Support:** Configurable via `PROXY` and `USE_PROXY` env vars. Default proxy is `http://127.0.0.1:7897`. Use `USE_PROXY=false` to disable.

## Important Implementation Details

### Image Upload to Gemini (Playwright Engine)

Gemini's UI changes frequently, so `ImageGenerator._upload_image()` uses multiple strategies:
1. Direct `input[type="file"]` with `accept*="image"`
2. Click upload button → look for file input
3. Click upload button → find/click menu items (handles Chinese/English)
4. JavaScript to reveal hidden file inputs (`style.display = 'block'`)

Debug screenshots are saved to `/tmp/debug_*.png` for troubleshooting upload failures.

### Prompt Handling

In Playwright engine, when reference images are uploaded, the prompt is prefixed with "基于上传的参考图片，使用 Imagen 3 生成新图片：" to reinforce image-to-image intent.

In HTTP engine, prompt text is sent as-is in StreamGenerate payload.

### HTTP Engine Notes

`HttpImageGenerator` implements a minimal Gemini web protocol:
1. Initialize session (`curl_cffi`, Chrome impersonation, HTTP/2)
2. Fetch `/app` and extract tokens (`SNlM0e`/`cfb2h`/`FdrFJe`)
3. Refresh `__Secure-1PSIDTS` via `RotateCookies`
4. Upload reference files via resumable upload (`push.clients6.google.com`)
5. Call `StreamGenerate` with Imagen model header
6. Parse generated image URLs from candidate payload (`candidate_data[12][7][0]`)
7. Download image to temp file and return local path

### Error Responses

All errors follow OpenAI's format:
```python
{
  "error": {
    "message": "...",
    "type": "...",  # invalid_request_error, authentication_error, server_error
    "code": "..."   # invalid_api_key, rate_limit_exceeded, etc.
  }
}
```

### Static File Serving

Generated images are served at `/static/generated/`. Cleanup is triggered manually via `POST /v1/cleanup` (not automatic at startup).

## Configuration

Required env vars (create `.env` from `.env.example`):
- `API_KEY` - Bearer token for authentication

Optional env vars:
- `MAX_CONCURRENT_TASKS=5` - Concurrent request limit
- `DEFAULT_TIMEOUT=80` - Generation timeout in seconds
- `PROXY=http://127.0.0.1:7897` - Proxy URL
- `USE_PROXY=true` - Enable/disable proxy
- `IMAGE_ENGINE=http` - Default engine for legacy `/v1/images/*` routes only
- `CLEANUP_HOURS=24` - Auto-delete images older than X hours
- `COOKIES_PATH=./data/cookies.json` - Path to Google cookies

## Known Issue: Playwright Chromium 被 Gemini 检测 (2026-02-25)

### 现象

Gemini 在收到 prompt 后 3 秒内开始处理（URL 跳转到会话页，显示"正在加载 Nano Banana Pro..."），但 6 秒后主动取消生成，将 prompt 放回输入框，页面回到 landing 状态。

### 已排除的因素

- headless vs headed 模式（headed 同样被取消）
- 反自动化检测脚本（有无均被取消）
- "Create image" pill 点击（有无均被取消）
- `/app` vs `/app/new` URL
- `launch_persistent_context` 持久化上下文
- Cookies 有效性（已确认登录成功，生成启动后才被取消）

### 根本原因

Playwright Chromium 被 Gemini 服务端识别为自动化浏览器。同一账号、同一网络环境下，真实 Chrome（有插件、有 Google 账号登录）可以正常生成图片。Playwright Chromium 与真实 Chrome 的差异可能包括：

- CDP (Chrome DevTools Protocol) 连接特征
- 缺少 Chrome 浏览器级别的 Google 账号登录（不仅是 cookies）
- 缺少浏览器扩展/插件
- 浏览器指纹差异（尽管已做大量伪装）

### 已测试的所有方案（均失败）

| 方案 | 结果 |
|------|------|
| Playwright Chromium (headed) | 生成 6s 后被取消，prompt 放回输入框 |
| Playwright + `channel="chrome"` | 同样被取消，CDP 控制特征无法避免 |
| Playwright + `connect_over_cdp` | 更糟，Google 检测到 remote-debugging-port 直接使 session 失效（cookies 被登出） |
| Patchright (`launch_persistent_context`) | 仍显示"Chrome is being controlled by automated test software"横幅，账号被登出 |
| nodriver (undetected-chromedriver 继任者) | 进入 Gemini 页面即被登出，cookies 失效 |

### 结论

**所有基于 CDP (Chrome DevTools Protocol) 的浏览器自动化方案都被 Google/Gemini 检测到。** Google 的检测不仅限于 `navigator.webdriver` 或 `Runtime.enable`，而是多层次的：CDP 连接特征、浏览器配置文件差异、缺少扩展/登录状态等。每次自动化访问还会加速 cookies 失效。

### 可行的替代方向

1. **Gemini API 直接调用（推荐）** — 使用 `google-genai` SDK 调用 `imagen-3.0-generate-002` 或 `gemini-2.0-flash-exp`（支持图文混合生成），完全绕过浏览器。需要 API key（Google AI Studio 免费获取）。
2. **Camoufox + PyAutoGUI** — Firefox 变体 + OS 级别鼠标键盘模拟，无 CDP 痕迹。方案复杂、依赖屏幕坐标、维护成本高。
3. **Chrome 扩展注入** — 通过 Chrome 扩展而非 CDP 来控制页面行为，但开发和调试成本高。

### 已完成的代码修复（generator.py）

这些修复解决了流程中的其他 bug，待浏览器检测问题解决后应能正常工作：

- headed 模式 + off-screen window（避免 headless 检测）
- Pro mode → Image tool 顺序调换（image tool 会禁用 model selector）
- 跳过 disabled 的 model selector（避免 30s 超时）
- `a[href*="ServiceLogin"]` 登录检测（语言无关）
- `_dismiss_overlays()` 关闭 pill 点击后的弹窗
- `_submit_prompt` JS focus fallback（overlay 阻挡时）
- pill 点击后等待页面过渡完成
- `_keep_alive_wait` 模拟用户活动
- `_is_page_idle` 增加 Loading 文本检测
- 重试时重新选择 image tool

### Cookies 自动失效

多次自动化测试后 cookies 会失效（sign-in link 出现），需要重新从浏览器导出。这与生成被取消是两个独立问题。
