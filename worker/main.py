import os
import sys
import json
import time
import random
from uuid import uuid4

import redis


# Force unbuffered output for Docker logs
sys.stdout.reconfigure(line_buffering=True)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


def create_redis_client():
    return redis.from_url(REDIS_URL, decode_responses=True)


def publish_event(r, job_id: str, event_type: str, payload: dict):
    """Publish an event to the job's pub/sub channel."""
    event = {"type": event_type, "payload": payload}
    r.publish(f"jobs:{job_id}:events", json.dumps(event))


def update_job_status(r, job_id: str, status: str, estimated_wait: int = None):
    """Update job status in Redis hash and publish event."""
    updates = {"status": status}
    if estimated_wait is not None:
        updates["estimated_wait_seconds"] = str(estimated_wait)
    r.hset(f"jobs:{job_id}", mapping=updates)

    payload = {"status": status}
    if estimated_wait is not None:
        payload["estimatedWaitSeconds"] = estimated_wait
    publish_event(r, job_id, "status", payload)


def generate_reasoning_tree(prompt: str) -> list:
    """Generate a fake reasoning tree based on the prompt."""
    root_id = str(uuid4())

    # Create root node
    nodes = []

    # Root node
    nodes.append({
        "id": root_id,
        "parentId": None,
        "title": "Analyzing request",
        "content": f"Processing the task: {prompt[:100]}{'...' if len(prompt) > 100 else ''}",
        "status": "complete",
        "toolUsed": None,
        "depth": 0,
    })

    # Subtask 1: Define approach
    subtask1_id = str(uuid4())
    nodes.append({
        "id": subtask1_id,
        "parentId": root_id,
        "title": "Define approach",
        "content": "Breaking down the problem into manageable components and identifying the best strategy.",
        "status": "complete",
        "toolUsed": None,
        "depth": 1,
    })

    # Sub-subtasks for subtask 1
    nodes.append({
        "id": str(uuid4()),
        "parentId": subtask1_id,
        "title": "Analyze requirements",
        "content": "Identified key requirements: accuracy, relevance, and comprehensive coverage of the topic.",
        "status": "complete",
        "toolUsed": None,
        "depth": 2,
    })

    nodes.append({
        "id": str(uuid4()),
        "parentId": subtask1_id,
        "title": "Plan execution",
        "content": "Will proceed with parallel search followed by synthesis and summarization.",
        "status": "complete",
        "toolUsed": None,
        "depth": 2,
    })

    # Subtask 2: Execute search (with tool)
    subtask2_id = str(uuid4())
    nodes.append({
        "id": subtask2_id,
        "parentId": root_id,
        "title": "Execute search",
        "content": "Launching parallel search across multiple knowledge sources.",
        "status": "complete",
        "toolUsed": "ParallelSearch",
        "depth": 1,
    })

    # Sub-subtasks for subtask 2
    nodes.append({
        "id": str(uuid4()),
        "parentId": subtask2_id,
        "title": "Query primary sources",
        "content": "Retrieved relevant information from primary knowledge base with high confidence scores.",
        "status": "complete",
        "toolUsed": None,
        "depth": 2,
    })

    nodes.append({
        "id": str(uuid4()),
        "parentId": subtask2_id,
        "title": "Query secondary sources",
        "content": "Found supplementary information to provide additional context and verification.",
        "status": "complete",
        "toolUsed": None,
        "depth": 2,
    })

    # Subtask 3: Synthesize results
    subtask3_id = str(uuid4())
    nodes.append({
        "id": subtask3_id,
        "parentId": root_id,
        "title": "Synthesize results",
        "content": "Combining and cross-referencing information from all sources.",
        "status": "complete",
        "toolUsed": None,
        "depth": 1,
    })

    nodes.append({
        "id": str(uuid4()),
        "parentId": subtask3_id,
        "title": "Cross-reference findings",
        "content": "Validated consistency across sources. No conflicting information detected.",
        "status": "complete",
        "toolUsed": None,
        "depth": 2,
    })

    # Subtask 4: Final summary
    nodes.append({
        "id": str(uuid4()),
        "parentId": root_id,
        "title": "Generate response",
        "content": "Compiled final response with synthesized information and supporting details.",
        "status": "complete",
        "toolUsed": None,
        "depth": 1,
    })

    return nodes


def process_job(r, job_id: str, prompt: str):
    """Process a single job, simulating the inference pipeline."""
    print(f"[Worker] Processing job {job_id}")

    # Phase 1: Queued (brief)
    update_job_status(r, job_id, "queued", estimated_wait=30)
    time.sleep(2)

    # Phase 2: Warming up
    update_job_status(r, job_id, "warming", estimated_wait=25)
    warmup_time = random.uniform(3, 5)
    time.sleep(warmup_time)

    # Phase 3: Running - stream reasoning tree
    update_job_status(r, job_id, "running")

    # Generate and stream the reasoning tree
    nodes = generate_reasoning_tree(prompt)

    for node in nodes:
        publish_event(r, job_id, "node", {"node": node})
        # Random delay between nodes to simulate streaming
        time.sleep(random.uniform(0.3, 0.8))

    # Phase 4: Complete
    update_job_status(r, job_id, "complete")
    print(f"[Worker] Completed job {job_id}")


def main():
    print("[Worker] Starting worker...")
    r = create_redis_client()

    # Test connection
    try:
        r.ping()
        print("[Worker] Connected to Redis")
    except redis.ConnectionError as e:
        print(f"[Worker] Failed to connect to Redis: {e}")
        return

    print("[Worker] Waiting for jobs...")

    while True:
        try:
            # Block waiting for a job
            result = r.brpop("jobs:queue", timeout=0)

            if result:
                _, job_data = result
                job = json.loads(job_data)
                job_id = job["job_id"]
                prompt = job["prompt"]

                process_job(r, job_id, prompt)

        except redis.ConnectionError as e:
            print(f"[Worker] Redis connection error: {e}")
            time.sleep(5)
            r = create_redis_client()

        except json.JSONDecodeError as e:
            print(f"[Worker] Invalid job data: {e}")

        except KeyboardInterrupt:
            print("[Worker] Shutting down...")
            break

        except Exception as e:
            print(f"[Worker] Error processing job: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
