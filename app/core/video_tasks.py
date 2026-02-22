"""In-memory async video task manager."""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import HTTPException

from app.models import ErrorDetail, GenerateVideoTaskRequest, VideoTaskResponse, VideoTaskResult


@dataclass
class VideoTaskState:
    """Internal task state."""

    id: str
    created: int
    status: str
    model: str
    request: GenerateVideoTaskRequest
    provider_task_id: str | None = None
    provider_item_ids: list[str] | None = None
    provider_generate_id: str | None = None
    result: VideoTaskResult | None = None
    error: ErrorDetail | None = None


@dataclass
class VideoTaskProcessResult:
    """Processor output for one async video task."""

    url: str
    provider_task_id: str | None = None
    provider_item_ids: list[str] | None = None
    provider_generate_id: str | None = None


class VideoTaskManager:
    """Manages lifecycle for async video generation tasks."""

    def __init__(
        self,
        processor: Callable[
            [GenerateVideoTaskRequest, Callable[[str, list[str] | None, str | None], Awaitable[None]]],
            Awaitable[VideoTaskProcessResult],
        ],
        storage_path: Path,
    ):
        self._processor = processor
        self._storage_path = storage_path
        self._tasks: dict[str, VideoTaskState] = {}
        self._lock = asyncio.Lock()
        self._load_from_disk()

    async def create_task(self, request: GenerateVideoTaskRequest) -> VideoTaskResponse:
        """Create and enqueue one task."""
        task_id = f"vtask_{uuid.uuid4().hex}"
        state = VideoTaskState(
            id=task_id,
            created=int(time.time()),
            status="queued",
            model=request.model,
            request=request,
        )

        async with self._lock:
            self._tasks[task_id] = state
            self._persist_locked()

        asyncio.create_task(self._run_task(task_id))
        return self._to_response(state)

    async def get_task(self, task_id: str) -> VideoTaskResponse:
        """Get task by id."""
        async with self._lock:
            state = self._tasks.get(task_id)

        if not state:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "message": f"Task not found: {task_id}",
                        "type": "invalid_request_error",
                        "code": "task_not_found",
                    }
                },
            )

        return self._to_response(state)

    async def _run_task(self, task_id: str):
        await self._update_status(task_id, "processing")
        try:
            async with self._lock:
                state = self._tasks.get(task_id)
            if not state:
                return

            async def on_binding(
                provider_task_id: str,
                provider_item_ids: list[str] | None,
                provider_generate_id: str | None,
            ):
                await self._set_provider_binding(task_id, provider_task_id, provider_item_ids, provider_generate_id)

            processed = await self._processor(state.request, on_binding)
            await self._set_success(
                task_id,
                processed.url,
                provider_task_id=processed.provider_task_id,
                provider_item_ids=processed.provider_item_ids,
                provider_generate_id=processed.provider_generate_id,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            error = detail.get("error", {}) if isinstance(detail, dict) else {}
            provider_task_id = detail.get("provider_task_id") if isinstance(detail, dict) else None
            provider_item_ids = detail.get("provider_item_ids") if isinstance(detail, dict) else None
            provider_generate_id = detail.get("provider_generate_id") if isinstance(detail, dict) else None
            await self._set_failure(
                task_id,
                ErrorDetail(
                    message=str(error.get("message", "Video generation failed")),
                    type=str(error.get("type", "generation_error")),
                    code=str(error.get("code", "generation_failed")),
                ),
                provider_task_id=provider_task_id if isinstance(provider_task_id, str) else None,
                provider_item_ids=provider_item_ids if isinstance(provider_item_ids, list) else None,
                provider_generate_id=provider_generate_id if isinstance(provider_generate_id, str) else None,
            )
        except Exception:
            await self._set_failure(
                task_id,
                ErrorDetail(
                    message="Video generation failed",
                    type="server_error",
                    code="generation_failed",
                ),
            )

    async def _update_status(self, task_id: str, status: str):
        async with self._lock:
            state = self._tasks.get(task_id)
            if state:
                state.status = status
                self._persist_locked()

    async def _set_success(
        self,
        task_id: str,
        url: str,
        provider_task_id: str | None = None,
        provider_item_ids: list[str] | None = None,
        provider_generate_id: str | None = None,
    ):
        async with self._lock:
            state = self._tasks.get(task_id)
            if state:
                state.status = "succeeded"
                state.provider_task_id = provider_task_id
                state.provider_item_ids = provider_item_ids
                state.provider_generate_id = provider_generate_id
                state.result = VideoTaskResult(
                    url=url,
                    provider_task_id=provider_task_id,
                    provider_item_ids=provider_item_ids,
                    provider_generate_id=provider_generate_id,
                )
                state.error = None
                self._persist_locked()

    async def _set_provider_binding(
        self,
        task_id: str,
        provider_task_id: str,
        provider_item_ids: list[str] | None = None,
        provider_generate_id: str | None = None,
    ):
        async with self._lock:
            state = self._tasks.get(task_id)
            if not state:
                return
            state.provider_task_id = provider_task_id
            if provider_item_ids:
                state.provider_item_ids = provider_item_ids
            if provider_generate_id:
                state.provider_generate_id = provider_generate_id
            self._persist_locked()

    async def _set_failure(
        self,
        task_id: str,
        error: ErrorDetail,
        provider_task_id: str | None = None,
        provider_item_ids: list[str] | None = None,
        provider_generate_id: str | None = None,
    ):
        async with self._lock:
            state = self._tasks.get(task_id)
            if state:
                state.status = "failed"
                if provider_task_id:
                    state.provider_task_id = provider_task_id
                if provider_item_ids:
                    state.provider_item_ids = provider_item_ids
                if provider_generate_id:
                    state.provider_generate_id = provider_generate_id
                state.error = error
                self._persist_locked()

    def _to_response(self, state: VideoTaskState) -> VideoTaskResponse:
        return VideoTaskResponse(
            id=state.id,
            created=state.created,
            status=state.status,  # type: ignore[arg-type]
            model=state.model,
            provider_task_id=state.provider_task_id,
            provider_item_ids=state.provider_item_ids,
            provider_generate_id=state.provider_generate_id,
            result=state.result,
            error=state.error,
        )

    def _load_from_disk(self):
        """Load persisted task states at startup."""
        if not self._storage_path.exists():
            return

        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(raw, dict):
            return

        tasks_raw = raw.get("tasks")
        if not isinstance(tasks_raw, list):
            return

        for item in tasks_raw:
            if not isinstance(item, dict):
                continue
            try:
                request_payload = item.get("request", {})
                request = GenerateVideoTaskRequest.model_validate(request_payload)
                result_payload = item.get("result")
                error_payload = item.get("error")
                state = VideoTaskState(
                    id=str(item["id"]),
                    created=int(item["created"]),
                    status=str(item["status"]),
                    model=str(item["model"]),
                    request=request,
                    provider_task_id=item.get("provider_task_id"),
                    provider_item_ids=item.get("provider_item_ids"),
                    provider_generate_id=item.get("provider_generate_id"),
                    result=VideoTaskResult.model_validate(result_payload) if isinstance(result_payload, dict) else None,
                    error=ErrorDetail.model_validate(error_payload) if isinstance(error_payload, dict) else None,
                )
                self._tasks[state.id] = state
            except Exception:
                continue

    def _persist_locked(self):
        """Persist current task states to disk (must be called under lock)."""
        payload = {
            "updated_at": int(time.time()),
            "tasks": [self._serialize_state(task) for task in self._tasks.values()],
        }

        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temp_path.replace(self._storage_path)

    def _serialize_state(self, state: VideoTaskState) -> dict:
        return {
            "id": state.id,
            "created": state.created,
            "status": state.status,
            "model": state.model,
            "request": state.request.model_dump(),
            "provider_task_id": state.provider_task_id,
            "provider_item_ids": state.provider_item_ids,
            "provider_generate_id": state.provider_generate_id,
            "result": state.result.model_dump() if state.result else None,
            "error": state.error.model_dump() if state.error else None,
        }
