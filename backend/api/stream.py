"""The pipeline, narrated over server-sent events while it runs.

`POST /api/hey-kivi/query` and `POST /api/transcripts` answer in one shot: you
send a question and some time later a result appears. That is the right shape
for a client, but it makes the interesting part invisible - a question spends
most of its wait inside one stage, and which stage that is happens to be the
whole story of how the system works.

These two endpoints run exactly the same code and report each stage the moment
it finishes. Nothing here re-implements the pipeline or estimates anything: the
events are emitted by `retrieve`, `ask` and `process_transcript` themselves,
carrying the values they actually computed.

Why a thread and a queue: the pipeline is ordinary synchronous code, and
rewriting it as a coroutine to suit a transport would be the tail wagging the
dog. So it runs on a worker thread, its tracer drops events into a queue, and
the response generator drains that queue and forwards them. The pipeline does
not know it is being watched.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable, Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.config import get_settings
from backend.api.transcripts import get_transcript as transcript_detail
from backend.memory import extractor, store
from backend.memory.heykivi import ask
from backend.memory.trace import Tracer
from backend.models.schemas import QueryRequest, TranscriptIn

router = APIRouter(prefix="/api/stream", tags=["stream"])

_DONE = object()
_KEEPALIVE_SECONDS = 15
_COMMENT = ": still working\n\n"


def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _run(work: Callable[[Tracer], dict[str, Any]]) -> Iterator[str]:
    """Run `work` on a thread, forwarding its stage events as they happen.

    The queue is unbounded and the worker never blocks on it, so a client that
    disconnects mid-stream cannot wedge the pipeline: the work finishes, the
    generator is closed, and the events go nowhere.
    """
    events: queue.Queue = queue.Queue()
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["result"] = work(Tracer(emit=events.put))
        except Exception as error:  # surfaced to the client as an error event
            box["error"] = f"{type(error).__name__}: {error}"
        finally:
            events.put(_DONE)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        try:
            item = events.get(timeout=_KEEPALIVE_SECONDS)
        except queue.Empty:
            # A stage that calls a hosted model can sit for tens of seconds. An
            # SSE comment keeps the connection - and any proxy idle timer along
            # it - from deciding the response has died.
            yield _COMMENT
            continue
        if item is _DONE:
            break
        yield _sse("stage", item)

    thread.join(timeout=5)
    if "error" in box:
        yield _sse("error", {"detail": box["error"]})
    else:
        yield _sse("done", box.get("result"))


def _stream(work: Callable[[Tracer], dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(
        _run(work),
        media_type="text/event-stream",
        # Without this a reverse proxy will happily buffer the whole stream and
        # deliver it as one lump at the end, which defeats the point.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask")
def ask_streaming(payload: QueryRequest) -> StreamingResponse:
    """Answer a question, reporting each stage of the pipeline as it completes."""
    settings = get_settings()

    def work(tracer: Tracer) -> dict[str, Any]:
        answer = ask(
            payload.question,
            user_id=settings.default_user_id,
            top_k=payload.top_k,
            tracer=tracer,
        )
        return answer.as_dict()

    return _stream(work)


@router.post("/dictate")
def dictate_streaming(payload: TranscriptIn) -> StreamingResponse:
    """Ingest one dictation, reporting what is learned from it as it is learned."""
    settings = get_settings()
    text = payload.text()

    def work(tracer: Tracer) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("A transcript needs some text.")

        transcript_id = store.insert_transcript(
            user_id=settings.default_user_id,
            raw_asr=payload.asr(),
            formatted_text=text,
            timestamp=payload.timestamp,
            application=payload.application,
            metadata=payload.metadata,
            external_id=payload.id,
        )
        transcript = store.get_transcript(transcript_id)
        result = extractor.process_transcript(
            transcript, user_id=settings.default_user_id, tracer=tracer
        )

        # The same shape the plain POST returns, so the client renders a
        # streamed dictation and a restored one through one code path.
        return {
            "transcript": transcript_detail(transcript_id).model_dump(),
            "decision": result.decision,
            "rationale": result.rationale,
            "created": result.created,
            "rejected": result.rejected,
            "superseded": result.superseded,
            "duplicates": result.duplicates,
            "latency_ms": round(result.latency_ms, 2),
            "provider": result.provider,
            "model": result.model,
            "outcomes": [o.as_dict() for o in result.outcomes],
        }

    return _stream(work)
