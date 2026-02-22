"""API request and response models (OpenAI compatible)."""
from pydantic import BaseModel, Field
from typing import Literal


# Request Models
class GenerateImageRequest(BaseModel):
    """Request for /v1/images/generations endpoint."""

    prompt: str = Field(..., description="Text description of the desired image")
    n: int = Field(1, description="Number of images to generate (only 1 supported)")
    size: str | None = Field(
        None, description="Image size (ignored, Gemini decides)"
    )
    response_format: Literal["url"] = Field(
        "url", description="Format of response (only 'url' supported)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "A cute orange cat",
                "n": 1,
                "size": "1024x1024",
                "response_format": "url",
            }
        }


class GenerateVideoRequest(BaseModel):
    """Request for /v1/videos/generations endpoint."""

    prompt: str = Field(..., description="Text description of the desired video")
    n: int = Field(1, description="Number of videos to generate (only 1 supported)")
    size: str | None = Field(
        None, description="Video size (ignored, Jimeng decides)"
    )
    response_format: Literal["url"] = Field(
        "url", description="Format of response (only 'url' supported)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "A panda dancing in the snow, cinematic lighting",
                "n": 1,
                "size": "1080p",
                "response_format": "url",
            }
        }


class GenerateVideoTaskRequest(BaseModel):
    """Request for async /v2/videos/generations endpoint."""

    prompt: str = Field(
        ...,
        description="Text prompt. Max 800 chars.",
        max_length=800,
    )
    model: Literal[
        "doubao-seedance-1-0-lite-i2v-250428",
        "doubao-seedance-1-0-lite-i2v-first-last-frame-250428",
        "doubao-seedance-1-0-lite-i2v-reference-250428",
    ] = Field(
        ...,
        description="Model name",
    )
    images: list[str] = Field(
        ...,
        description="Reference image URLs",
        min_length=1,
    )
    duration: Literal[5, 10] | None = Field(
        5,
        description="Video duration in seconds",
    )
    resolution: Literal["480p", "720p", "1080p"] | None = Field(
        "720p",
        description="Video resolution",
    )
    ratio: Literal["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "9:21", "keep_ratio", "adaptive"] | None = Field(
        None,
        description="Video aspect ratio",
    )
    watermark: bool | None = Field(None, description="Add watermark")
    seed: int | None = Field(
        None,
        ge=0,
        le=2147483647,
        description="Random seed",
    )
    camerafixed: bool | None = Field(None, description="Fix camera")
    return_last_frame: bool | None = Field(None, description="Return last frame")
    generate_audio: bool | None = Field(None, description="Generate audio")


class EditImageRequest(BaseModel):
    """Request for /v1/images/edits endpoint (form data)."""

    prompt: str = Field(..., description="Edit instructions")
    n: int = Field(1, description="Number of images to generate (only 1 supported)")
    size: str | None = Field(None, description="Image size (ignored)")


# Response Models
class ImageData(BaseModel):
    """Single image data in response."""

    url: str = Field(..., description="URL of the generated image")


class ImageResponse(BaseModel):
    """Response for image generation endpoints (OpenAI compatible)."""

    created: int = Field(..., description="Unix timestamp of creation")
    data: list[ImageData] = Field(..., description="List of generated images")

    class Config:
        json_schema_extra = {
            "example": {
                "created": 1707566096,
                "data": [
                    {"url": "http://localhost:8000/static/generated/img_abc123.png"}
                ],
            }
        }


class VideoData(BaseModel):
    """Single video data in response."""

    url: str = Field(..., description="URL of the generated video")


class VideoResponse(BaseModel):
    """Response for video generation endpoints."""

    created: int = Field(..., description="Unix timestamp of creation")
    data: list[VideoData] = Field(..., description="List of generated videos")

    class Config:
        json_schema_extra = {
            "example": {
                "created": 1707566096,
                "data": [
                    {"url": "http://localhost:8000/static/generated/vid_abc123.mp4"}
                ],
            }
        }


class VideoTaskResult(BaseModel):
    """Async video task result."""

    url: str = Field(..., description="Generated video URL")
    provider_task_id: str | None = Field(None, description="Provider-side submit/task id")
    provider_item_ids: list[str] | None = Field(None, description="Provider-side generated item ids")
    provider_generate_id: str | None = Field(None, description="Provider-side generate id")
    last_frame_url: str | None = Field(None, description="Optional generated last frame URL")


class VideoTaskResponse(BaseModel):
    """Async video task response."""

    id: str = Field(..., description="Task id")
    created: int = Field(..., description="Unix timestamp")
    status: Literal["queued", "processing", "succeeded", "failed"] = Field(..., description="Task status")
    model: str = Field(..., description="Requested model")
    provider_task_id: str | None = Field(None, description="Provider-side submit/task id")
    provider_item_ids: list[str] | None = Field(None, description="Provider-side generated item ids")
    provider_generate_id: str | None = Field(None, description="Provider-side generate id")
    result: VideoTaskResult | None = None
    error: "ErrorDetail | None" = None


class VideoTaskAssetsResponse(BaseModel):
    """Assets lookup response for one async video task."""

    id: str = Field(..., description="Task id")
    status: Literal["queued", "processing", "succeeded", "failed"] = Field(..., description="Task status")
    provider_task_id: str = Field(..., description="Provider-side submit/task id")
    assets: list[str] = Field(..., description="Matched video asset urls")


class ErrorDetail(BaseModel):
    """Error detail (OpenAI compatible)."""

    message: str
    type: str
    code: str


class ErrorResponse(BaseModel):
    """Error response (OpenAI compatible)."""

    error: ErrorDetail


class AccountHealth(BaseModel):
    """Per-account runtime status."""

    account_id: str
    email: str | None = None
    identity_label: str | None = None
    identity_kind: str | None = None
    enabled: bool
    active_tasks: int
    in_cooldown: bool
    cooldown_remaining: int
    last_error: str | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    concurrent_tasks: int
    max_concurrent: int
    accounts_total: int | None = None
    accounts_available: int | None = None
    accounts: list[AccountHealth] | None = None


class CleanupResponse(BaseModel):
    """Cleanup operation response."""

    deleted_count: int = Field(..., description="Number of files deleted")
    deleted_files: list[str] = Field(..., description="List of deleted filenames")


class CookiesUploadResponse(BaseModel):
    """Cookies upload response."""

    success: bool = Field(..., description="Whether upload was successful")
    message: str = Field(..., description="Status message")
    cookie_count: int = Field(..., description="Number of cookies saved")
    account_id: str = Field(..., description="Account id that was updated")
