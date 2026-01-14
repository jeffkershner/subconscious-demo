import os
import json
import asyncio
from uuid import uuid4
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

redis_pool: Optional[redis.Redis] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_pool
    redis_pool = redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await redis_pool.close()


app = FastAPI(title="Subconscious Demo API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobRequest(BaseModel):
    prompt: str


class JobResponse(BaseModel):
    job_id: str


class JobState(BaseModel):
    id: str
    prompt: str
    status: str
    estimated_wait_seconds: Optional[int] = None
    created_at: Optional[str] = None
    error: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: List[JobState]


@app.post("/jobs", response_model=JobResponse)
async def create_job(request: JobRequest):
    """Create a new inference job and queue it for processing."""
    job_id = str(uuid4())
    created_at = datetime.utcnow().isoformat() + "Z"

    # Store initial job state
    job_data = {
        "id": job_id,
        "prompt": request.prompt,
        "status": "queued",
        "estimated_wait_seconds": "30",
        "created_at": created_at,
    }
    await redis_pool.hset(f"jobs:{job_id}", mapping=job_data)

    # Add to jobs index (sorted set with timestamp as score for ordering)
    await redis_pool.zadd("jobs:index", {job_id: datetime.utcnow().timestamp()})

    # Push job to queue for worker to pick up
    queue_data = json.dumps({"job_id": job_id, "prompt": request.prompt})
    await redis_pool.lpush("jobs:queue", queue_data)

    return JobResponse(job_id=job_id)


@app.get("/jobs", response_model=JobListResponse)
async def list_jobs(limit: int = 50):
    # Get job IDs from sorted set (newest first)
    job_ids = await redis_pool.zrevrange("jobs:index", 0, limit - 1)

    jobs = []
    for job_id in job_ids:
        job_data = await redis_pool.hgetall(f"jobs:{job_id}")
        if job_data:
            jobs.append(JobState(
                id=job_data.get("id", job_id),
                prompt=job_data.get("prompt", ""),
                status=job_data.get("status", "unknown"),
                estimated_wait_seconds=int(job_data["estimated_wait_seconds"]) if job_data.get("estimated_wait_seconds") else None,
                created_at=job_data.get("created_at"),
                error=job_data.get("error"),
            ))

    return JobListResponse(jobs=jobs)


@app.get("/jobs/{job_id}", response_model=JobState)
async def get_job(job_id: str):
    """Get current state of a job."""
    job_data = await redis_pool.hgetall(f"jobs:{job_id}")

    if not job_data:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobState(
        id=job_data.get("id", job_id),
        prompt=job_data.get("prompt", ""),
        status=job_data.get("status", "unknown"),
        estimated_wait_seconds=int(job_data["estimated_wait_seconds"]) if job_data.get("estimated_wait_seconds") else None,
        created_at=job_data.get("created_at"),
        error=job_data.get("error"),
    )


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    """SSE endpoint for streaming job updates."""

    # Check job exists
    exists = await redis_pool.exists(f"jobs:{job_id}")
    if not exists:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        pubsub = redis_pool.pubsub()
        channel = f"jobs:{job_id}:events"
        await pubsub.subscribe(channel)

        try:
            # Send initial state
            job_data = await redis_pool.hgetall(f"jobs:{job_id}")
            initial_event = {
                "type": "status",
                "payload": {
                    "status": job_data.get("status", "queued"),
                    "estimatedWaitSeconds": int(job_data["estimated_wait_seconds"]) if job_data.get("estimated_wait_seconds") else None,
                }
            }
            yield f"data: {json.dumps(initial_event)}\n\n"

            # Listen for updates
            heartbeat_interval = 15  # seconds
            last_heartbeat = asyncio.get_event_loop().time()

            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0
                    )

                    if message and message["type"] == "message":
                        data = message["data"]
                        yield f"data: {data}\n\n"

                        # Check if job is complete
                        try:
                            event = json.loads(data)
                            if event.get("type") == "status" and event.get("payload", {}).get("status") in ["complete", "error"]:
                                break
                        except json.JSONDecodeError:
                            pass

                    # Send heartbeat
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_heartbeat >= heartbeat_interval:
                        yield f"data: {json.dumps({'type': 'heartbeat', 'payload': {}})}\n\n"
                        last_heartbeat = current_time

                except asyncio.TimeoutError:
                    # Check if job completed while we were waiting
                    job_data = await redis_pool.hgetall(f"jobs:{job_id}")
                    if job_data.get("status") in ["complete", "error"]:
                        break

                    # Send heartbeat if needed
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_heartbeat >= heartbeat_interval:
                        yield f"data: {json.dumps({'type': 'heartbeat', 'payload': {}})}\n\n"
                        last_heartbeat = current_time

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
