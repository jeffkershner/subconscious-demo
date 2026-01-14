# Subconscious Demo Prototype

A demo application showcasing improved UX for AI inference job submission with progressive status updates and streaming results.

## Quick Start

```bash
docker-compose up --build
```

Then open http://localhost:5173 in your browser.

## Architecture

Four Docker services working together:

| Service | Tech | Port | Purpose |
|---------|------|------|---------|
| frontend | React + Vite + TypeScript | 5173 | Dashboard UI |
| api | FastAPI + Python | 8000 | REST + SSE endpoints |
| worker | Python script | - | Simulates inference, publishes progress |
| redis | Redis 7 | 6379 | Job queue + pub/sub |

## Communication Flow

1. User submits prompt → Frontend POSTs to API
2. API creates job, pushes to Redis queue, returns job_id immediately
3. Frontend opens SSE connection to API for that job_id
4. Worker pulls job from queue, simulates warm-up and inference
5. Worker publishes status updates and tree nodes to Redis pub/sub
6. API subscribes to pub/sub, forwards events over SSE to frontend
7. Frontend renders status and tree nodes as they arrive

## Key Features

- **Never blocks the user** - Job submission returns immediately
- **Transparent status** - Always shows what's happening (Queued → Warming → Running → Complete)
- **Progress bar** - Shows estimated time remaining during warm-up
- **Progressive rendering** - Tree nodes appear one by one with animation
- **Streaming updates** - Real-time updates via Server-Sent Events

## Development

### API Service (Port 8000)

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --reload
```

### Worker Service

```bash
cd worker
pip install -r requirements.txt
python main.py
```

### Frontend (Port 5173)

```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

- `POST /jobs` - Create a new job
- `GET /jobs/{job_id}` - Get job status
- `GET /jobs/{job_id}/stream` - SSE stream for real-time updates
- `GET /health` - Health check

## Environment Variables

### Frontend
- `VITE_API_URL` - API base URL (default: http://localhost:8000)

### API / Worker
- `REDIS_URL` - Redis connection URL (default: redis://localhost:6379)
