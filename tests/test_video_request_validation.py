"""Validation tests for video generation requests."""
import pytest
from pydantic import ValidationError

from app.models import GenerateVideoRequest, GenerateVideoTaskRequest


def test_v1_first_last_frame_requires_pair_or_two_images():
    with pytest.raises(ValidationError):
        GenerateVideoRequest.model_validate(
            {
                "prompt": "test",
                "reference_mode": "first_last_frame",
                "first_frame_image": "https://example.com/first.jpg",
            }
        )


def test_v2_first_last_frame_accepts_two_images():
    req = GenerateVideoTaskRequest.model_validate(
        {
            "prompt": "test",
            "reference_mode": "first_last_frame",
            "images": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
        }
    )
    assert req.reference_mode == "first_last_frame"


def test_reference_assets_limit_rejects_more_than_five():
    with pytest.raises(ValidationError):
        GenerateVideoRequest.model_validate(
            {
                "prompt": "test",
                "images": [
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                    "https://example.com/4.jpg",
                    "https://example.com/5.jpg",
                ],
                "reference_videos": ["https://example.com/1.mp4"],
            }
        )
