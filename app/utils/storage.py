"""File storage and URL generation."""
import hashlib
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


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
        return self.save_file(source_path, prefix="img")

    def save_file(self, source_path: Path, prefix: str = "img") -> tuple[str, str]:
        """
        Save media file and return (url, file_path).

        Args:
            source_path: Path to source file
            prefix: Filename prefix (e.g. img, vid)

        Returns:
            Tuple of (public URL, absolute file path)
        """
        suffix = source_path.suffix.lower() or ".bin"
        filename = f"{prefix}_{int(time.time())}_{secrets.token_hex(8)}{suffix}"
        dest_path = self.storage_dir / filename

        shutil.move(str(source_path), str(dest_path))
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

        for pattern in ("img_*.*", "vid_*.*"):
            for file in self.storage_dir.glob(pattern):
                if file.stat().st_mtime < cutoff:
                    deleted.append(file.name)
                    file.unlink()
                    print(f"Cleaned up old file: {file.name}")

        return deleted

    def save_remote_file(self, remote_url: str, prefix: str = "vid") -> tuple[str, str]:
        """
        Download remote media and cache into static storage.

        If already downloaded before, return cached path directly.
        """
        digest = hashlib.sha256(remote_url.encode("utf-8")).hexdigest()[:16]
        parsed = urlparse(remote_url)
        suffix = Path(parsed.path).suffix.lower()
        if not suffix or len(suffix) > 8:
            suffix = ".mp4"

        filename = f"{prefix}_remote_{digest}{suffix}"
        dest_path = self.storage_dir / filename
        if dest_path.exists():
            return f"{self.base_url}/static/generated/{filename}", str(dest_path.absolute())

        req = Request(remote_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=120) as resp:
            data = resp.read()

        temp_path = self.storage_dir / f".{filename}.tmp"
        temp_path.write_bytes(data)
        temp_path.replace(dest_path)
        return f"{self.base_url}/static/generated/{filename}", str(dest_path.absolute())
