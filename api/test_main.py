import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from fakeredis import aioredis as fakeredis

import main


@pytest.fixture
async def fake_redis():
    """Create a fake Redis instance for testing."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


@pytest.fixture
async def client(fake_redis):
    """Create a test client with mocked Redis."""
    main.redis_pool = fake_redis
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_healthy(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestCreateJob:
    @pytest.mark.asyncio
    async def test_create_job_success(self, client, fake_redis):
        response = await client.post(
            "/jobs",
            json={"prompt": "Test prompt"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data

        # Verify job was stored in Redis
        job_data = await fake_redis.hgetall(f"jobs:{data['job_id']}")
        assert job_data["prompt"] == "Test prompt"
        assert job_data["status"] == "queued"
        assert "created_at" in job_data

    @pytest.mark.asyncio
    async def test_create_job_adds_to_queue(self, client, fake_redis):
        response = await client.post(
            "/jobs",
            json={"prompt": "Test prompt"}
        )
        assert response.status_code == 200

        # Verify job was added to queue
        queue_item = await fake_redis.rpop("jobs:queue")
        assert queue_item is not None
        queue_data = json.loads(queue_item)
        assert queue_data["prompt"] == "Test prompt"

    @pytest.mark.asyncio
    async def test_create_job_adds_to_index(self, client, fake_redis):
        response = await client.post(
            "/jobs",
            json={"prompt": "Test prompt"}
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Verify job was added to sorted set index
        job_ids = await fake_redis.zrange("jobs:index", 0, -1)
        assert job_id in job_ids

    @pytest.mark.asyncio
    async def test_create_job_missing_prompt(self, client):
        response = await client.post("/jobs", json={})
        assert response.status_code == 422  # Validation error


class TestGetJob:
    @pytest.mark.asyncio
    async def test_get_job_success(self, client, fake_redis):
        # Create a job first
        job_id = "test-job-123"
        await fake_redis.hset(f"jobs:{job_id}", mapping={
            "id": job_id,
            "prompt": "Test prompt",
            "status": "running",
            "estimated_wait_seconds": "30",
            "created_at": "2024-01-01T00:00:00Z"
        })

        response = await client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["prompt"] == "Test prompt"
        assert data["status"] == "running"
        assert data["estimated_wait_seconds"] == 30

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client):
        response = await client.get("/jobs/nonexistent-job")
        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    @pytest.mark.asyncio
    async def test_get_job_with_error(self, client, fake_redis):
        job_id = "error-job"
        await fake_redis.hset(f"jobs:{job_id}", mapping={
            "id": job_id,
            "prompt": "Test",
            "status": "error",
            "error": "Something went wrong"
        })

        response = await client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"] == "Something went wrong"


class TestListJobs:
    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, client):
        response = await client.get("/jobs")
        assert response.status_code == 200
        assert response.json() == {"jobs": []}

    @pytest.mark.asyncio
    async def test_list_jobs_returns_jobs(self, client, fake_redis):
        # Create multiple jobs
        for i in range(3):
            job_id = f"job-{i}"
            await fake_redis.hset(f"jobs:{job_id}", mapping={
                "id": job_id,
                "prompt": f"Prompt {i}",
                "status": "complete",
                "created_at": f"2024-01-0{i+1}T00:00:00Z"
            })
            await fake_redis.zadd("jobs:index", {job_id: float(i)})

        response = await client.get("/jobs")
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 3

    @pytest.mark.asyncio
    async def test_list_jobs_ordered_by_newest(self, client, fake_redis):
        # Create jobs with different timestamps
        await fake_redis.hset("jobs:old", mapping={
            "id": "old",
            "prompt": "Old job",
            "status": "complete"
        })
        await fake_redis.zadd("jobs:index", {"old": 1.0})

        await fake_redis.hset("jobs:new", mapping={
            "id": "new",
            "prompt": "New job",
            "status": "complete"
        })
        await fake_redis.zadd("jobs:index", {"new": 2.0})

        response = await client.get("/jobs")
        assert response.status_code == 200
        jobs = response.json()["jobs"]
        assert jobs[0]["id"] == "new"  # Newest first
        assert jobs[1]["id"] == "old"

    @pytest.mark.asyncio
    async def test_list_jobs_respects_limit(self, client, fake_redis):
        # Create 10 jobs
        for i in range(10):
            job_id = f"job-{i}"
            await fake_redis.hset(f"jobs:{job_id}", mapping={
                "id": job_id,
                "prompt": f"Prompt {i}",
                "status": "complete"
            })
            await fake_redis.zadd("jobs:index", {job_id: float(i)})

        response = await client.get("/jobs?limit=5")
        assert response.status_code == 200
        assert len(response.json()["jobs"]) == 5


class TestStreamJob:
    @pytest.mark.asyncio
    async def test_stream_job_not_found(self, client):
        response = await client.get("/jobs/nonexistent/stream")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="SSE streaming requires real Redis for pubsub - use integration tests")
    async def test_stream_job_sends_initial_state(self, client, fake_redis):
        """Test that stream endpoint sends initial state.

        Note: This test is skipped because fakeredis doesn't properly
        simulate pubsub timeouts. For full SSE testing, use integration
        tests with a real Redis instance.
        """
        job_id = "stream-job"
        await fake_redis.hset(f"jobs:{job_id}", mapping={
            "id": job_id,
            "prompt": "Test",
            "status": "complete",
            "estimated_wait_seconds": "30"
        })

        async with client.stream("GET", f"/jobs/{job_id}/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
