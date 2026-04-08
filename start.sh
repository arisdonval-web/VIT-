#!/bin/bash
# Start backend on port 8000 in background
export DATABASE_URL="sqlite+aiosqlite:///vit.db"
export AUTH_ENABLED="false"
export LOG_LEVEL="INFO"

echo "Starting FastAPI backend on port 8000..."
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "Starting Vite frontend on port 5000..."
cd frontend && npm run dev
