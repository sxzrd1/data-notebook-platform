# data-notebook-platform
A web-based collaborative analytics notebook where multiple users can write and run data queries/scripts, build visualizations, comment in-line, and publish interactive insight reports.
# Collaborative Data Notebook - Demo

This repo contains a minimal demo of a Collaborative Data Notebook:
- Backend: FastAPI + python-socketio, SQLite for persistence.
- Frontend: React + Vite, Monaco editor, Plotly, and socket.io-client.

## Quickstart (local)

### Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:asgi_app --reload --port 8000
# in a separate terminal:
curl -X POST http://localhost:8000/seed-demo-data
```

### Frontend
```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

Notes:
- This is a demo starter. The query runner allows only read-only SELECT.
- Token handling in the frontend/backend is simplified for demo purposes.
- To extend: replace SQLite with Postgres, add CRDT via Yjs, sandboxed execution, and AI insights.
