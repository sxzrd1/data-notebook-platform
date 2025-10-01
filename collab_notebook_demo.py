# backend/app/main.py
import json
import re
import sqlite3
import time
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body
from pydantic import BaseModel
import socketio
from jose import jwt
from passlib.context import CryptContext
import os

# Simple settings (for demo only)
JWT_SECRET = "change_me_to_a_random_secret"
JWT_ALG = "HS256"

# --- Database (SQLite simple) ---
DB_PATH = os.environ.get("DEMO_DB", "demo.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notebooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        content TEXT,
        owner INTEGER,
        created_at REAL,
        updated_at REAL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        notebook_id INTEGER,
        action TEXT,
        who TEXT,
        ts REAL,
        details TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Simple user utils (demo) ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_user(username: str, password: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    ph = pwd_context.hash(password)
    try:
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, ph))
        conn.commit()
    except Exception as e:
        conn.close()
        raise
    conn.close()

def authenticate_user(username: str, password: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return pwd_context.verify(password, row[0])

def build_token(username: str):
    payload = {"sub": username, "iat": int(time.time())}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


# --- FastAPI + Socket.IO setup ---
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for demo; restrict in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
asgi_app = socketio.ASGIApp(sio, other_asgi_app=app)

# Simple in-memory mapping of rooms -> set of connected sids (for presence)
ROOMS = {}

# --- Models ---
class Credentials(BaseModel):
    username: str
    password: str

class NotebookSave(BaseModel):
    id: int | None = None
    title: str
    content: Dict[str, Any]

# --- Demo: create an admin user if not exists ---
try:
    create_user("demo", "demo")
except Exception:
    pass

# --- REST endpoints ---
@app.post("/auth/login")
async def login(creds: Credentials):
    ok = authenticate_user(creds.username, creds.password)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = build_token(creds.username)
    return {"access_token": token, "token_type": "bearer"}

@app.get("/notebooks")
async def list_notebooks():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, owner, created_at, updated_at FROM notebooks ORDER BY updated_at DESC")
    rows = cur.fetchall()
    result = [{"id": r[0], "title": r[1], "owner": r[2], "created_at": r[3], "updated_at": r[4]} for r in rows]
    conn.close()
    return result

@app.get("/notebooks/{nid}")
async def get_notebook(nid: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, content, owner, created_at, updated_at FROM notebooks WHERE id = ?", (nid,))
    r = cur.fetchone()
    conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": r[0], "title": r[1], "content": json.loads(r[2]), "owner": r[3], "created_at": r[4], "updated_at": r[5]}

@app.post("/notebooks")
async def save_notebook(payload: NotebookSave, token: str = Body(...)):
    # For demo, token is required but we won't fully validate claims
    username = "unknown"
    try:
        username = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])["sub"]
    except Exception:
        pass
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    ts = time.time()
    if payload.id:
        cur.execute("UPDATE notebooks SET title=?, content=?, updated_at=? WHERE id=?", (payload.title, json.dumps(payload.content), ts, payload.id))
        nid = payload.id
        action = "update"
    else:
        cur.execute("INSERT INTO notebooks (title, content, owner, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (payload.title, json.dumps(payload.content), username, ts, ts))
        nid = cur.lastrowid
        action = "create"
    cur.execute("INSERT INTO audit (notebook_id, action, who, ts, details) VALUES (?, ?, ?, ?, ?)",
                (nid, action, username, ts, json.dumps({"title": payload.title})))
    conn.commit()
    conn.close()
    return {"id": nid, "status": "saved"}

# --- Query runner (very restricted: only allows SELECT) ---
SELECT_ONLY = re.compile(r"^\s*SELECT\s", re.IGNORECASE | re.DOTALL)

class QueryRequest(BaseModel):
    query: str

@app.post("/query")
async def run_query(q: QueryRequest):
    sql = q.query.strip()
    if not SELECT_ONLY.match(sql):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed in demo.")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        # Convert to simple list of dicts
        results = [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Query error: {str(e)}")
    conn.close()
    return {"columns": cols, "rows": results}

# --- Socket.IO realtime handlers ---
@sio.event
async def connect(sid, environ):
    print("Client connected:", sid)

@sio.event
async def disconnect(sid):
    print("Disconnected:", sid)
    # remove sid from ROOMs
    for room, sids in list(ROOMS.items()):
        if sid in sids:
            sids.remove(sid)
            await sio.emit("presence", {"room": room, "count": len(sids)}, room=room)

@sio.event
async def join_room(sid, data):
    # data: {"room": "<notebook_id>", "username": "bob"}
    room = data.get("room")
    username = data.get("username", "anon")
    if room is None:
        return
    sio.enter_room(sid, room)
    ROOMS.setdefault(room, set()).add(sid)
    await sio.emit("presence", {"room": room, "count": len(ROOMS[room])}, room=room)
    await sio.emit("user_joined", {"username": username}, room=room)

@sio.event
async def leave_room(sid, data):
    room = data.get("room")
    if room is None:
        return
    sio.leave_room(sid, room)
    if room in ROOMS and sid in ROOMS[room]:
        ROOMS[room].remove(sid)
    await sio.emit("presence", {"room": room, "count": len(ROOMS.get(room, []))}, room=room)

@sio.event
async def notebook_edit(sid, data):
    """
    broadcast notebook edits to all clients in the room.
    data := {"room": "<id>", "patch": {"cursor":..., "content": ...}, "username": "bob"}
    """
    room = data.get("room")
    if room:
        # broadcast to room
        await sio.emit("notebook_patch", data, room=room)

# --- Provide a simple endpoint that seeds demo tables to query ---
@app.post("/seed-demo-data")
async def seed_demo():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # create a sample sales table
    cur.execute("CREATE TABLE IF NOT EXISTS sales (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, country TEXT, product TEXT, amount REAL)")
    cur.execute("DELETE FROM sales")  # reset for demo
    demo_rows = [
        ("2025-01-01","US","A", 120.50),
        ("2025-01-02","US","B", 80.00),
        ("2025-01-03","FR","A", 75.00),
        ("2025-02-01","US","A", 200.00),
        ("2025-02-05","FR","B", 150.00),
        ("2025-03-01","MA","A", 50.00),
    ]
    cur.executemany("INSERT INTO sales (date, country, product, amount) VALUES (?,?,?,?)", demo_rows)
    conn.commit()
    conn.close()
    return {"status": "seeded"}
