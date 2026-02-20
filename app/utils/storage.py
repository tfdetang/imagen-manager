"""File storage and URL generation."""
import secrets
import shutil
import time
from pathlib import Path


class ImageStorage:
    """Manages image file storage and cleanup."""

    def __init__(self, storage_dir: Path, base_url: str):
        self.storage_dir = storage_dir
        self.base_url = base_url.rstrip("/")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_image(self, source_path: Path) -> tuple[str, str]:
        """
        Save image and return (url, file_path).

        Args:
            source_path: Path to source image file

        Returns:
            Tuple of (public URL, absolute file path)
        """
        # Generate unique filename: img_<timestamp>_<random>.png
        filename = f"img_{int(time.time())}_{secrets.token_hex(8)}.png"
        dest_path = self.storage_dir / filename

        # Move file to storage
        shutil.move(str(source_path), str(dest_path))

        # Generate URL
        url = f"{self.base_url}/static/generated/{filename}"

        return url, str(dest_path.absolute())

    def cleanup_old_files(self, max_age_hours: int = 24) -> list[str]:
        """
        Remove files older than max_age_hours.

        Args:
            max_age_hours: Maximum file age in hours

        Returns:
            List of deleted filenames
        """
        cutoff = time.time() - (max_age_hours * 3600)
        deleted = []

        for file in self.storage_dir.glob("img_*.png"):
            if file.stat().st_mtime < cutoff:
                deleted.append(file.name)
                file.unlink()
                print(f"Cleaned up old file: {file.name}")

        return deleted
