# Imagen API

OpenAI-compatible REST API for Google Gemini Imagen 3 image generation and editing.

## Features

✅ **OpenAI Compatible** - Drop-in replacement for OpenAI Images API
✅ **Text-to-Image** - Generate images from text prompts
✅ **Image-to-Image** - Edit existing images with prompts
✅ **Text-to-Video (Jimeng)** - Generate videos from text prompts via 即梦
✅ **Concurrent Control** - Configurable concurrent request limits
✅ **Multi-Account Pool** - Multiple cookies accounts with automatic scheduling/failover
✅ **Auto Cleanup** - Automatic cleanup of old generated images
✅ **Cookie Persistence** - Smart cookie caching and management

## Quick Start

### 1. Installation

```bash
# Install dependencies
uv sync
uv run playwright install chromium
```

### 2. Configuration

Create `.env` file:

```bash
cp .env.example .env
```

Edit `.env` and set your API key:

```bash
API_KEY=sk-your-secret-key-here
```

### 3. Add Google Cookies

Export your Google cookies from browser and save to `data/cookies.json`:

- Use browser extension like "EditThisCookie" or "Cookie-Editor"
- Visit https://gemini.google.com and login
- Export cookies as JSON
- Save to `data/cookies.json`

#### Optional: Multi-account cookies pool

You can place multiple accounts under `data/accounts/`:

```text
data/accounts/
├── account-a/
│   └── cookies.json
└── account-b/
  └── cookies.txt
```

The service auto-discovers all accounts and schedules requests across them.

### 4. Run Server

```bash
# Development mode (with hot reload)
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production mode
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Usage

### Using Python (OpenAI SDK)

```python
from openai import OpenAI

# Point to your local API
client = OpenAI(
    api_key="sk-your-secret-key-here",
    base_url="http://localhost:8000/v1"
)

# Generate image
response = client.images.generate(
    prompt="A cute orange cat",
    n=1,
)
print(response.data[0].url)

# Edit image
with open("cat.png", "rb") as image_file:
    response = client.images.edit(
        image=image_file,
        prompt="Make the cat black",
        n=1,
    )
print(response.data[0].url)
```

### Using cURL

```bash
# Generate image
curl -X POST http://localhost:8000/v1/images/generations \
  -H "Authorization: Bearer sk-your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A cute orange cat",
    "n": 1
  }'

# Edit image
curl -X POST http://localhost:8000/v1/images/edits \
  -H "Authorization: Bearer sk-your-secret-key-here" \
  -F "image=@cat.png" \
  -F "prompt=Make the cat black" \
  -F "n=1"

# Generate video (Jimeng)
curl -X POST http://localhost:8000/v1/videos/generations \
  -H "Authorization: Bearer sk-your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A panda dancing in the snow, cinematic lighting",
    "n": 1
  }'

# Async video task create (recommended)
curl -X POST http://localhost:8000/v2/videos/generations \
  -H "Authorization: Bearer sk-your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "dance",
    "model": "doubao-seedance-1-0-lite-i2v-250428",
    "images": [
      "https://webstatic.aiproxy.vip/dist/demo.jpg"
    ]
  }'

# Async video task query
curl -X GET http://localhost:8000/v2/videos/generations/<task_id> \
  -H "Authorization: Bearer sk-your-secret-key-here"
```

## API Endpoints

### POST /v1/images/generations

Generate image from text prompt.

**Request:**
```json
{
  "prompt": "A cute orange cat",
  "n": 1,
  "size": "1024x1024",
  "response_format": "url"
}
```

**Response:**
```json
{
  "created": 1707566096,
  "data": [
    {
      "url": "http://localhost:8000/static/generated/img_abc123.png"
    }
  ]
}
```

### POST /v1/images/edits

Edit image based on prompt.

**Request:** (multipart/form-data)
- `image`: PNG file
- `prompt`: Edit instructions
- `n`: Number of images (only 1 supported)

**Response:** Same as generations

### POST /v1/videos/generations

Generate video from text prompt using Jimeng.

**Request:**
```json
{
  "prompt": "A panda dancing in the snow, cinematic lighting",
  "n": 1,
  "response_format": "url"
}
```

### POST /v2/videos/generations

Create async video generation task.

**Request:**
```json
{
  "prompt": "dance",
  "model": "doubao-seedance-1-0-lite-i2v-250428",
  "images": [
    "https://webstatic.aiproxy.vip/dist/demo.jpg"
  ],
  "duration": 5,
  "resolution": "720p",
  "ratio": "16:9"
}
```

**Response:**
```json
{
  "id": "vtask_xxx",
  "created": 1707566096,
  "status": "queued",
  "model": "doubao-seedance-1-0-lite-i2v-250428",
  "result": null,
  "error": null
}
```

### GET /v2/videos/generations/{task_id}

Query async video task status/result.

**Succeeded example:**
```json
{
  "id": "vtask_xxx",
  "created": 1707566096,
  "status": "succeeded",
  "model": "doubao-seedance-1-0-lite-i2v-250428",
  "result": {
    "url": "http://localhost:8000/static/generated/vid_abc123.mp4",
    "last_frame_url": null
  },
  "error": null
}
```

**Response:**
```json
{
  "created": 1707566096,
  "data": [
    {
      "url": "http://localhost:8000/static/generated/vid_abc123.mp4"
    }
  ]
}
```

### GET /v1/health

Health check (no authentication required).

**Response:**
```json
{
  "status": "ok",
  "concurrent_tasks": 2,
  "max_concurrent": 5,
  "accounts_total": 2,
  "accounts_available": 1,
  "accounts": [
    {
      "account_id": "account-a",
      "enabled": true,
      "active_tasks": 1,
      "in_cooldown": false,
      "cooldown_remaining": 0,
      "last_error": null
    }
  ]
}
```

## Configuration

Environment variables in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | - | **Required.** API key for authentication |
| `MAX_CONCURRENT_TASKS` | 5 | Max concurrent browser instances |
| `DEFAULT_TIMEOUT` | 60 | Generation timeout in seconds |
| `VIDEO_TIMEOUT` | 1800 | Video generation timeout in seconds |
| `PROXY` | http://127.0.0.1:7897 | Proxy server URL |
| `USE_PROXY` | true | Enable/disable proxy |
| `CLEANUP_HOURS` | 24 | Auto-delete images older than X hours |
| `VIDEO_TASKS_PATH` | ./data/video_tasks.json | Persistent JSON file for async video task states |
| `COOKIES_PATH` | ./data/cookies.json | Path to Google cookies file |
| `ACCOUNTS_DIR` | ./data/accounts | Directory for multi-account cookies |
| `PER_ACCOUNT_CONCURRENT_TASKS` | 1 | Max concurrent tasks per cookies account |
| `ACCOUNT_COOLDOWN_SECONDS` | 600 | Cooldown when an account cookies expires |

## Testing

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=app
```

## Architecture

```
app/
├── main.py              # FastAPI application entry
├── config.py            # Configuration management
├── auth.py              # API Key authentication
├── models.py            # Request/Response models
├── api/
│   └── routes.py        # API endpoints
├── core/
│   ├── browser.py       # Cookie management
│   ├── generator.py     # Image generation logic
│   └── semaphore.py     # Concurrency control
└── utils/
    └── storage.py       # File storage & cleanup
```

## Limitations

⚠️ **OpenAI Compatibility Notes:**
- `n` parameter: Only `n=1` is supported (Gemini generates one image at a time)
- `size` parameter: Ignored (Gemini automatically determines size)
- `response_format`: Only `url` is supported (no `b64_json`)

⚠️ **Production Deployment:**
- Use `--workers 1` with uvicorn (semaphore is process-local)
- For multi-worker setup, use Redis-based distributed locks

## Troubleshooting

### Cookies Expired

**Error:** `503 Service Unavailable: Google cookies expired`

**Solution:**
1. Re-login to Gemini in your browser
2. Export fresh cookies
3. Update `data/cookies.json`
4. Restart the server

For Jimeng video generation, ensure the same cookies file also contains login cookies for `jimeng.jianying.com` / `jianying.com`.

### Too Many Requests

**Error:** `429 Too Many Concurrent Requests`

**Solution:** Increase `MAX_CONCURRENT_TASKS` in `.env` or wait for current requests to complete

### Image Generation Failed

**Error:** `500 Failed to download generated image`

**Solution:**
1. Check if Gemini is accessible (test in browser)
2. Increase `DEFAULT_TIMEOUT`
3. Check proxy settings
4. Review server logs for details

## License

MIT

## Credits

Based on the original Gemini Imagen automation script, refactored into a production-ready API service.
