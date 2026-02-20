"""API route definitions."""
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException

from app.auth import verify_api_key
from app.models import GenerateImageRequest, ImageResponse, ImageData, HealthResponse, CleanupResponse, CookiesUploadResponse
from app.core.generator import ImageGenerator
from app.core.browser import CookieManager
from app.core.semaphore import ConcurrencyManager
from app.utils.storage import ImageStorage
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize singletons
cookie_manager = CookieManager(settings.cookies_path)
concurrency_manager = ConcurrencyManager(settings.max_concurrent_tasks)
storage = ImageStorage(settings.storage_dir, settings.base_url)
generator = ImageGenerator(cookie_manager, settings.effective_proxy)

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
        temp_image = await generator.generate(
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
            temp_image = await generator.generate(
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
    return HealthResponse(
        status="ok",
        concurrent_tasks=concurrency_manager.active_tasks,
        max_concurrent=concurrency_manager.max_concurrent,
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

        saved_path = cookie_manager.save_cookies(cookies_data)
        logger.info(f"Cookies saved to {saved_path}, {len(cookies_data)} cookies")

        return CookiesUploadResponse(
            success=True,
            message=f"Cookies saved successfully to {saved_path.name}",
            cookie_count=len(cookies_data),
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
