# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenAI-compatible REST API for Google Gemini Imagen 3 image generation, built with FastAPI and Playwright. The project has two components:

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
│   └── routes.py        # /v1/images/generations, /v1/images/edits, /v1/health
├── core/
│   ├── browser.py       # CookieManager for cookie persistence
│   ├── generator.py     # ImageGenerator - Playwright automation
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
ImageGenerator.generate() (generator.py)
    ├→ Load Cookies (CookieManager)
    ├→ Launch Browser (Playwright)
    ├→ Navigate to Gemini
    ├→ Upload Reference Images (multi-strategy)
    ├→ Submit Prompt
    ├→ Wait for Generation
    └→ Download Image (dual fallback)
    ↓
Save to Storage (storage.py)
    ↓
Return OpenAI-compatible Response
```

### Key Architectural Decisions

**CORS Configuration:** The API includes `CORSMiddleware` with `allow_origins=["*"]` for external tool access. For production, modify `app/main.py:31-37` to specify allowed origins.

**Multi-File Upload:** The `/v1/images/edits` endpoint uses `List[UploadFile]` to support multiple reference images. FastAPI automatically collects all fields with the same name into a list. Single `UploadFile` would only capture the last file.

**Concurrency Limit:** Uses an in-process semaphore (`ConcurrencyManager`). **Critical:** This means `--workers 1` is required in production. Multiple workers would each have independent semaphores, bypassing the limit. For multi-worker setups, implement a distributed lock (Redis).

**Cookie Management:** Three-tier system - explicit cookies → persistent store (`data/cookies.json`) → error recovery. The `CookieManager` auto-converts browser-exported cookies to Playwright format and validates login status on each run.

**Download Fallback:** Two strategies for retrieving generated images:
1. Click download button and use Playwright's download handler
2. Parse page for `googleusercontent` image URLs and fetch via JavaScript

**Proxy Support:** Configurable via `PROXY` and `USE_PROXY` env vars. Default proxy is `http://127.0.0.1:7897`. Use `USE_PROXY=false` to disable.

## Important Implementation Details

### Image Upload to Gemini

Gemini's UI changes frequently, so `ImageGenerator._upload_image()` uses multiple strategies:
1. Direct `input[type="file"]` with `accept*="image"`
2. Click upload button → look for file input
3. Click upload button → find/click menu items (handles Chinese/English)
4. JavaScript to reveal hidden file inputs (`style.display = 'block'`)

Debug screenshots are saved to `/tmp/debug_*.png` for troubleshooting upload failures.

### Prompt Handling

When reference images are uploaded, the prompt is prefixed with "基于上传的参考图片，使用 Imagen 3 生成新图片：" (Based on the uploaded reference image, generate a new image using Imagen 3:). This ensures Gemini understands it's an image-to-image generation task.

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

Generated images are served at `/static/generated/` and automatically cleaned up based on `CLEANUP_HOURS` (default: 24h). The cleanup runs at startup.

## Configuration

Required env vars (create `.env` from `.env.example`):
- `API_KEY` - Bearer token for authentication

Optional env vars:
- `MAX_CONCURRENT_TASKS=5` - Concurrent browser limit
- `DEFAULT_TIMEOUT=80` - Generation timeout in seconds
- `PROXY=http://127.0.0.1:7897` - Proxy URL
- `USE_PROXY=true` - Enable/disable proxy
- `CLEANUP_HOURS=24` - Auto-delete images older than X hours
- `COOKIES_PATH=./data/cookies.json` - Path to Google cookies
