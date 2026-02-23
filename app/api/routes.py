"""API route definitions."""
import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import List
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException

from app.auth import verify_api_key
from app.models import (
    CleanupResponse,
    CookiesUploadResponse,
    GenerateImageRequest,
    GenerateVideoTaskRequest,
    GenerateVideoRequest,
    HealthResponse,
    ImageData,
    ImageResponse,
    VideoData,
    VideoResponse,
    VideoTaskAssetsResponse,
    VideoTaskResponse,
)
from app.core.browser import CookieManager
from app.core.account_pool import AccountPool
from app.core.semaphore import ConcurrencyManager
from app.core.video_generator import JimengVideoGenerator, VideoGenerationResult, VideoSubmitOptions
from app.core.video_tasks import VideoTaskManager, VideoTaskProcessResult
from app.utils.storage import ImageStorage
from app.config import settings

logger = logging.getLogger(__name__)


def _discover_account_sources() -> list[tuple[str, Path]]:
    """Discover account cookie files from accounts directory, fallback to default."""
    account_sources: list[tuple[str, Path]] = []
    accounts_dir = settings.accounts_dir

    if accounts_dir.exists() and accounts_dir.is_dir():
        for entry in sorted(accounts_dir.iterdir()):
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                txt_path = entry / "cookies.txt"
                json_path = entry / "cookies.json"
                if txt_path.exists():
                    account_sources.append((entry.name, txt_path))
                elif json_path.exists():
                    account_sources.append((entry.name, json_path))
                continue

            if entry.suffix.lower() in {".json", ".txt"}:
                account_sources.append((entry.stem, entry))

    if not account_sources:
        account_sources.append(("default", settings.cookies_path))

    return account_sources


def _is_cookies_expired_error(exc: HTTPException) -> bool:
    """Check whether HTTPException indicates cookie expiration."""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error", {}) if isinstance(detail, dict) else {}
    return error.get("code") == "cookies_expired"


def _normalize_account_id(raw_value: str | None) -> str:
    """Normalize and validate account id."""
    value = (raw_value or "default").strip().lower()
    if not value:
        return "default"

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Invalid account_id. Use 1-64 chars: a-z, 0-9, -, _",
                    "type": "invalid_request_error",
                    "code": "invalid_account_id",
                }
            },
        )

    return value


def _guess_temp_suffix(remote_url: str, default_suffix: str) -> str:
    parsed = urlparse(remote_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix and 1 < len(suffix) <= 8:
        return suffix
    return default_suffix


async def _download_remote_to_temp(remote_url: str, default_suffix: str) -> Path:
    """Download remote file to local temp path for browser upload."""

    def _download() -> Path:
        suffix = _guess_temp_suffix(remote_url, default_suffix)
        fd, temp_path = tempfile.mkstemp(prefix="jimeng_ref_", suffix=suffix)
        os.close(fd)
        path = Path(temp_path)
        req = Request(remote_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=120) as resp:
            data = resp.read()
        path.write_bytes(data)
        return path

    return await asyncio.to_thread(_download)


async def _prepare_video_reference_files(
    image_urls: list[str] | None,
    video_urls: list[str] | None,
    first_frame_url: str | None,
    last_frame_url: str | None,
) -> tuple[list[Path], list[Path], Path | None, Path | None]:
    """Resolve remote reference URLs to local temp files."""
    image_urls = [item for item in (image_urls or []) if item]
    video_urls = [item for item in (video_urls or []) if item]

    image_paths: list[Path] = []
    video_paths: list[Path] = []
    first_frame_path: Path | None = None
    last_frame_path: Path | None = None

    for image_url in image_urls:
        image_paths.append(await _download_remote_to_temp(image_url, ".jpg"))

    for video_url in video_urls:
        video_paths.append(await _download_remote_to_temp(video_url, ".mp4"))

    if first_frame_url:
        first_frame_path = await _download_remote_to_temp(first_frame_url, ".jpg")
    if last_frame_url:
        last_frame_path = await _download_remote_to_temp(last_frame_url, ".jpg")

    return image_paths, video_paths, first_frame_path, last_frame_path


def _cleanup_temp_paths(*paths: Path | None):
    """Best-effort cleanup for temporary local files."""
    for path in paths:
        if not path:
            continue
        try:
            if path.exists():
                path.unlink()
        except Exception:
            continue


async def _generate_with_account_pool(
    prompt: str,
    timeout: int,
    reference_images: list[Path] | None = None,
) -> Path:
    """Generate image with account failover on cookie-expired errors."""
    # Check if any account is available before starting
    stats = account_pool.stats()
    if stats["accounts_available"] == 0:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "No available cookie account. All accounts are either busy or in cooldown.",
                    "type": "service_error",
                    "code": "accounts_unavailable",
                }
            },
        )

    # Try all available accounts
    max_attempts = stats["accounts_total"]
    last_error: HTTPException | None = None
    tried_accounts: set[str] = set()

    for attempt in range(1, max_attempts + 1):
        try:
            lease = await account_pool.acquire()
        except HTTPException:
            # No available accounts
            break

        # Skip if we already tried this account
        if lease.account_id in tried_accounts:
            account_pool.release(lease)
            break

        tried_accounts.add(lease.account_id)
        logger.info(f"Assigned account '{lease.account_id}' for generation (attempt {attempt}/{max_attempts})")

        try:
            image_path = await lease.generator.generate(
                prompt=prompt,
                timeout=timeout,
                reference_images=reference_images,
            )
            return image_path
        except HTTPException as exc:
            if _is_cookies_expired_error(exc):
                account_pool.mark_cooldown(
                    lease.account_id,
                    settings.account_cooldown_seconds,
                    reason="cookies_expired",
                )
                logger.warning(
                    f"Account '{lease.account_id}' marked cooldown for {settings.account_cooldown_seconds}s (cookies expired)"
                )
                last_error = exc
                # Continue to try next account
                continue
            raise
        finally:
            account_pool.release(lease)

    if last_error:
        raise last_error

    raise HTTPException(
        status_code=503,
        detail={
            "error": {
                "message": "No available cookie account",
                "type": "service_error",
                "code": "accounts_unavailable",
            }
        },
    )


async def _generate_video_with_account_pool(
    prompt: str,
    timeout: int,
    on_binding=None,
    submit_options: VideoSubmitOptions | None = None,
    reference_images: list[Path] | None = None,
    reference_videos: list[Path] | None = None,
    first_frame_image: Path | None = None,
    last_frame_image: Path | None = None,
) -> VideoGenerationResult:
    """Generate Jimeng video with account failover on cookie-expired errors."""
    stats = account_pool.stats()
    if stats["accounts_available"] == 0:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "No available cookie account. All accounts are either busy or in cooldown.",
                    "type": "service_error",
                    "code": "accounts_unavailable",
                }
            },
        )

    max_attempts = stats["accounts_total"]
    last_error: HTTPException | None = None
    tried_accounts: set[str] = set()

    for attempt in range(1, max_attempts + 1):
        try:
            lease = await account_pool.acquire()
        except HTTPException:
            break

        if lease.account_id in tried_accounts:
            account_pool.release(lease)
            break

        tried_accounts.add(lease.account_id)
        logger.info(f"Assigned account '{lease.account_id}' for video generation (attempt {attempt}/{max_attempts})")

        try:
            cookie_manager_for_account = account_pool.get_cookie_manager(lease.account_id)
            if not cookie_manager_for_account:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": {
                            "message": f"Cookie manager not found for account: {lease.account_id}",
                            "type": "service_error",
                            "code": "account_unavailable",
                        }
                    },
                )

            video_generator = JimengVideoGenerator(
                cookie_manager_for_account,
                proxy=settings.effective_proxy,
            )
            generation_result = await video_generator.generate(
                prompt=prompt,
                timeout=timeout,
                on_binding=on_binding,
                submit_options=submit_options,
                reference_images=reference_images,
                reference_videos=reference_videos,
                first_frame_image=first_frame_image,
                last_frame_image=last_frame_image,
            )
            return generation_result
        except HTTPException as exc:
            if _is_cookies_expired_error(exc):
                account_pool.mark_cooldown(
                    lease.account_id,
                    settings.account_cooldown_seconds,
                    reason="cookies_expired",
                )
                logger.warning(
                    f"Account '{lease.account_id}' marked cooldown for {settings.account_cooldown_seconds}s (cookies expired)"
                )
                last_error = exc
                continue
            raise
        finally:
            account_pool.release(lease)

    if last_error:
        raise last_error

    raise HTTPException(
        status_code=503,
        detail={
            "error": {
                "message": "No available cookie account",
                "type": "service_error",
                "code": "accounts_unavailable",
            }
        },
    )


async def _process_video_task_request(
    request: GenerateVideoTaskRequest,
    on_binding,
) -> VideoTaskProcessResult:
    """Background processor for async video generation task."""
    await concurrency_manager.acquire()
    image_paths: list[Path] = []
    video_paths: list[Path] = []
    first_frame_path: Path | None = None
    last_frame_path: Path | None = None
    try:
        image_paths, video_paths, first_frame_path, last_frame_path = await _prepare_video_reference_files(
            image_urls=request.images,
            video_urls=request.reference_videos,
            first_frame_url=request.first_frame_image,
            last_frame_url=request.last_frame_image,
        )

        generation_result = await _generate_video_with_account_pool(
            prompt=request.prompt,
            timeout=max(settings.default_timeout, settings.video_timeout),
            on_binding=on_binding,
            submit_options=VideoSubmitOptions(
                model=request.model or "seedance-2.0",
                reference_mode=request.reference_mode or "omni",
                ratio=request.ratio or "16:9",
                duration=request.duration or 5,
            ),
            reference_images=image_paths,
            reference_videos=video_paths,
            first_frame_image=first_frame_path,
            last_frame_image=last_frame_path,
        )
        url, _ = storage.save_file(generation_result.media_path, prefix="vid")
        return VideoTaskProcessResult(
            url=url,
            provider_task_id=generation_result.provider_task_id,
            provider_item_ids=generation_result.provider_item_ids,
            provider_generate_id=generation_result.provider_generate_id,
        )
    finally:
        for temp_path in image_paths:
            _cleanup_temp_paths(temp_path)
        for temp_path in video_paths:
            _cleanup_temp_paths(temp_path)
        _cleanup_temp_paths(first_frame_path, last_frame_path)
        concurrency_manager.release()


# Initialize singletons
cookie_manager = CookieManager(settings.cookies_path)
concurrency_manager = ConcurrencyManager(settings.max_concurrent_tasks)
storage = ImageStorage(settings.storage_dir, settings.base_url)
account_pool = AccountPool(
    _discover_account_sources(),
    proxy=settings.effective_proxy,
    per_account_concurrent=settings.per_account_concurrent_tasks,
)
video_task_manager = VideoTaskManager(
    _process_video_task_request,
    storage_path=settings.video_tasks_path,
)

router = APIRouter()


@router.post("/v1/images/generations", response_model=ImageResponse)
async def generate_image(
    request: GenerateImageRequest,
    _: str = Depends(verify_api_key),
):
    """
    Generate image from text prompt (OpenAI compatible).

    Compatible with OpenAI's images.generate() API.
    """
    # Validate n parameter
    if request.n != 1:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Only n=1 is supported",
                    "type": "invalid_request_error",
                    "code": "invalid_n",
                }
            },
        )

    # Acquire semaphore
    await concurrency_manager.acquire()

    try:
        # Generate image
        temp_image = await _generate_with_account_pool(
            prompt=request.prompt,
            timeout=settings.default_timeout,
        )

        # Save and get URL
        url, _ = storage.save_image(temp_image)

        return ImageResponse(
            created=int(time.time()),
            data=[ImageData(url=url)],
        )

    finally:
        concurrency_manager.release()


@router.post("/v1/videos/generations", response_model=VideoResponse)
async def generate_video(
    request: GenerateVideoRequest,
    _: str = Depends(verify_api_key),
):
    """
    Generate video from text prompt using Jimeng.
    """
    if request.n != 1:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Only n=1 is supported",
                    "type": "invalid_request_error",
                    "code": "invalid_n",
                }
            },
        )

    await concurrency_manager.acquire()
    image_paths: list[Path] = []
    video_paths: list[Path] = []
    first_frame_path: Path | None = None
    last_frame_path: Path | None = None

    try:
        image_paths, video_paths, first_frame_path, last_frame_path = await _prepare_video_reference_files(
            image_urls=request.images,
            video_urls=request.reference_videos,
            first_frame_url=request.first_frame_image,
            last_frame_url=request.last_frame_image,
        )

        generation_result = await _generate_video_with_account_pool(
            prompt=request.prompt,
            timeout=max(settings.default_timeout, settings.video_timeout),
            submit_options=VideoSubmitOptions(
                model=request.model or "seedance-2.0",
                reference_mode=request.reference_mode or "omni",
                ratio=request.ratio or "16:9",
                duration=request.duration or 5,
            ),
            reference_images=image_paths,
            reference_videos=video_paths,
            first_frame_image=first_frame_path,
            last_frame_image=last_frame_path,
        )

        url, _ = storage.save_file(generation_result.media_path, prefix="vid")

        return VideoResponse(
            created=int(time.time()),
            data=[VideoData(url=url)],
        )
    finally:
        for temp_path in image_paths:
            _cleanup_temp_paths(temp_path)
        for temp_path in video_paths:
            _cleanup_temp_paths(temp_path)
        _cleanup_temp_paths(first_frame_path, last_frame_path)
        concurrency_manager.release()


@router.post("/v2/videos/generations", response_model=VideoTaskResponse)
async def create_video_task(
    request: GenerateVideoTaskRequest,
    _: str = Depends(verify_api_key),
):
    """
    Create async video generation task.
    """
    return await video_task_manager.create_task(request)


@router.get("/v2/videos/generations/{task_id}", response_model=VideoTaskResponse)
async def get_video_task(
    task_id: str,
    _: str = Depends(verify_api_key),
):
    """
    Get async video task status/result.
    """
    return await video_task_manager.get_task(task_id)


@router.get("/v2/videos/generations/{task_id}/assets", response_model=VideoTaskAssetsResponse)
async def get_video_task_assets(
    task_id: str,
    _: str = Depends(verify_api_key),
):
    """
    Fetch matched asset URLs for one async video task by provider task id.
    """
    task = await video_task_manager.get_task(task_id)
    if not task.provider_task_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "message": "Task is not bound to provider_task_id yet",
                    "type": "invalid_request_error",
                    "code": "task_not_bound",
                }
            },
        )

    matched_assets: list[str] = []
    account_ids = [item["account_id"] for item in account_pool.stats().get("accounts", [])]

    for account_id in account_ids:
        cm = account_pool.get_cookie_manager(account_id)
        if not cm:
            continue
        fetcher = JimengVideoGenerator(cm, proxy=settings.effective_proxy)
        urls = await fetcher.fetch_asset_urls_by_submit_id(
            task.provider_task_id,
            provider_item_ids=task.provider_item_ids,
        )
        if urls:
            matched_assets = urls
            break

    local_assets: list[str] = []
    for remote_url in matched_assets:
        try:
            local_url, _ = await asyncio.to_thread(storage.save_remote_file, remote_url, "vid")
            local_assets.append(local_url)
        except Exception:
            # Keep remote URL as fallback if caching fails.
            local_assets.append(remote_url)

    return VideoTaskAssetsResponse(
        id=task.id,
        status=task.status,
        provider_task_id=task.provider_task_id,
        assets=local_assets,
    )


@router.post("/v1/images/edits", response_model=ImageResponse)
async def edit_image(
    image: List[UploadFile] = File(..., description="Images to edit (supports multiple)"),
    prompt: str = Form(..., description="Edit instructions"),
    n: int = Form(1, description="Number of images (only 1 supported)"),
    size: str | None = Form(None, description="Size (ignored)"),
    _: str = Depends(verify_api_key),
):
    """
    Edit image based on prompt (OpenAI compatible).

    Supports multiple reference images - send multiple 'image' fields in multipart/form-data.

    Compatible with OpenAI's images.edit() API.
    """
    # Validate n parameter
    if n != 1:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Only n=1 is supported",
                    "type": "invalid_request_error",
                    "code": "invalid_n",
                }
            },
        )

    # image is now a List[UploadFile]
    upload_files = image

    if not upload_files:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "At least one image is required",
                    "type": "invalid_request_error",
                    "code": "missing_image",
                }
            },
        )

    logger.info(f"Received {len(upload_files)} image(s) for upload")

    temp_uploads = []

    try:
        # Save all uploaded images
        for idx, uploaded_file in enumerate(upload_files):
            logger.info(f"Processing image {idx+1}/{len(upload_files)}: filename={uploaded_file.filename}, content_type={uploaded_file.content_type}")

            # Read uploaded file content
            content = await uploaded_file.read()
            file_size = len(content)
            logger.info(f"Read {file_size} bytes from uploaded file")

            # Validate file content
            if file_size == 0:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "message": f"Uploaded file {idx+1} is empty",
                            "type": "invalid_request_error",
                            "code": "empty_file",
                        }
                    },
                )

            if file_size > 10 * 1024 * 1024:  # 10MB limit
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "message": f"File {idx+1} too large ({file_size} bytes). Maximum size is 10MB",
                            "type": "invalid_request_error",
                            "code": "file_too_large",
                        }
                    },
                )

            # Save uploaded image temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                temp_path = Path(tmp.name)
                temp_uploads.append(temp_path)
                logger.info(f"Saved image {idx+1} to: {temp_path}")

            # Verify file was saved
            if not temp_path.exists():
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {
                            "message": f"Failed to save uploaded file {idx+1}",
                            "type": "server_error",
                            "code": "file_save_failed",
                        }
                    },
                )

        # Acquire semaphore
        await concurrency_manager.acquire()

        try:
            # Generate image with reference images
            logger.info(f"Generating image with {len(temp_uploads)} reference image(s)")
            temp_image = await _generate_with_account_pool(
                prompt=prompt,
                timeout=settings.default_timeout,
                reference_images=temp_uploads,  # Pass list of images
            )

            # Save and get URL
            url, _ = storage.save_image(temp_image)

            return ImageResponse(
                created=int(time.time()),
                data=[ImageData(url=url)],
            )

        finally:
            # Release semaphore
            concurrency_manager.release()

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise

    except Exception as e:
        logger.exception(f"Error processing image edit: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": f"Failed to process image edit: {str(e)}",
                    "type": "server_error",
                    "code": "processing_failed",
                }
            },
        )

    finally:
        # Cleanup all temporary files
        for temp_upload in temp_uploads:
            if temp_upload.exists():
                try:
                    temp_upload.unlink()
                    logger.info(f"Cleaned up temporary file: {temp_upload}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup temporary file {temp_upload}: {e}")


@router.get("/v1/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint (no authentication required)."""
    account_stats = account_pool.stats()
    return HealthResponse(
        status="ok",
        concurrent_tasks=concurrency_manager.active_tasks,
        max_concurrent=concurrency_manager.max_concurrent,
        accounts_total=account_stats["accounts_total"],
        accounts_available=account_stats["accounts_available"],
        accounts=account_stats["accounts"],
    )


@router.post("/v1/cleanup", response_model=CleanupResponse)
async def cleanup_old_images(
    max_age_hours: int | None = None,
    _: str = Depends(verify_api_key),
):
    """
    Manually trigger cleanup of old generated images.

    Args:
        max_age_hours: Override default cleanup age (uses CLEANUP_HOURS from config if not specified)
    """
    hours = max_age_hours if max_age_hours is not None else settings.cleanup_hours
    deleted_files = storage.cleanup_old_files(hours)

    return CleanupResponse(
        deleted_count=len(deleted_files),
        deleted_files=deleted_files,
    )


@router.post("/v1/cookies", response_model=CookiesUploadResponse)
async def upload_cookies(
    file: UploadFile = File(..., description="Cookies JSON file exported from browser"),
    account_id: str | None = Form(None, description="Account id to update (default: default)"),
    _: str = Depends(verify_api_key),
):
    """
    Upload and update cookies file for Gemini authentication.

    Accepts JSON format exported from browser extensions like "EditThisCookie" or "Cookie-Editor".
    """
    import json

    try:
        content = await file.read()
        cookies_data = json.loads(content.decode("utf-8"))

        if not isinstance(cookies_data, list):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": "Invalid cookies format. Expected a JSON array.",
                        "type": "invalid_request_error",
                        "code": "invalid_format",
                    }
                },
            )

        target_account = _normalize_account_id(account_id)
        target_manager = account_pool.get_cookie_manager(target_account)
        if not target_manager:
            if target_account == "default":
                target_manager = cookie_manager
            else:
                account_dir = settings.accounts_dir / target_account
                account_dir.mkdir(parents=True, exist_ok=True)
                cookies_file = account_dir / "cookies.json"
                target_manager = account_pool.add_or_update_account(target_account, cookies_file)

        saved_path = target_manager.save_cookies(cookies_data)
        account_pool.clear_cooldown(target_account)
        logger.info(f"Cookies saved to {saved_path}, account={target_account}, {len(cookies_data)} cookies")

        return CookiesUploadResponse(
            success=True,
            message=f"Cookies saved successfully to {saved_path.name}",
            cookie_count=len(cookies_data),
            account_id=target_account,
        )

    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": f"Invalid JSON: {str(e)}",
                    "type": "invalid_request_error",
                    "code": "invalid_json",
                }
            },
        )
