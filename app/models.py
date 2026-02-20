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


class ErrorDetail(BaseModel):
    """Error detail (OpenAI compatible)."""

    message: str
    type: str
    code: str


class ErrorResponse(BaseModel):
    """Error response (OpenAI compatible)."""

    error: ErrorDetail


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    concurrent_tasks: int
    max_concurrent: int


class CleanupResponse(BaseModel):
    """Cleanup operation response."""

    deleted_count: int = Field(..., description="Number of files deleted")
    deleted_files: list[str] = Field(..., description="List of deleted filenames")


class CookiesUploadResponse(BaseModel):
    """Cookies upload response."""

    success: bool = Field(..., description="Whether upload was successful")
    message: str = Field(..., description="Status message")
    cookie_count: int = Field(..., description="Number of cookies saved")
