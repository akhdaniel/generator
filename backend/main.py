import os
import json
import logging
import secrets
import sqlite3
import hashlib
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=logging.INFO)
DB_PATH = os.getenv("DIAGRAM_DB_PATH", os.path.join(os.path.dirname(__file__), "diagrams.db"))
SYSTEM_PROMPT = """
You are a UML Class Diagram Generator.
YOUR TASK: Convert the user's description into a raw JSON structure representing a UML class diagram for the frontend renderer.
OUTPUT FORMAT: Provide ONLY the JSON object. Do not wrap it in markdown code blocks like ```json. Do not add any explanation.

The JSON structure must strictly follow this data shape:

interface DiagramData {
  nodes: UMLNode[];
  edges: UMLEdge[];
}

interface UMLNode {
  id: string; // Unique string ID
  type: "uml";
  position: { x: number; y: number };
  data: {
    name: string; // Class name
    attributes: string[]; // e.g. ["status: string", "createdAt: Date"]
    methods: string[]; // e.g. ["calculateTotal(): number"]
  };
}

interface UMLEdge {
  id: string; // Unique string ID
  source: string; // id of the source node
  target: string; // id of the target node
  type: "floating";
  data: {
    label?: string; // Relation name or verb
    relationType: "association" | "inheritance" | "composition" | "aggregation" | "dependency";
    sourceMultiplicity?: "one2many" | "many2one" | "many2many";
    targetMultiplicity?: "one2many" | "many2one" | "many2many";
    sourceRole?: string;
    targetRole?: string;
  };
}

Notes:
- Attributes and methods are plain strings, not structured objects.
- Use reasonable positions (x,y) for new nodes; avoid overlap by spacing them out in a grid.
- If multiplicities are not specified, default sourceMultiplicity to "many2one" and targetMultiplicity to "one2many".
- If label or roles are unknown, omit them or use empty strings.

Example Output:
{
  "nodes": [
    {
      "id": "cls-1",
      "type": "uml",
      "position": { "x": 120, "y": 140 },
      "data": { "name": "User", "attributes": [], "methods": [] }
    }
  ],
  "edges": []
}

IMPORTANT:
If the user provides an EXISTING DIAGRAM JSON, you must modify/extend it based on the new prompt.
- KEEP existing nodes/edges unless explicitly asked to remove them.
- ADD new nodes/edges as requested.
- MODIFY existing attributes/methods or relations if requested.
- ENSURE all IDs remain unique.
- Preserve existing node positions unless explicitly asked to relayout.
"""


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: Optional[str] = None


app = FastAPI(title="UML Diagram Chat API", version="0.1.0")

# Allow local dev origins by default; adjust in prod
origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiagramPayload(BaseModel):
    nodes: list
    edges: list
    chatMessages: Optional[list] = None


class AuthRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    email: str


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagrams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id),
                UNIQUE (owner_id, name)
            )
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(diagrams)").fetchall()
        }
        if "id" not in columns:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagrams_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER,
                    name TEXT NOT NULL,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (owner_id) REFERENCES users(id),
                    UNIQUE (owner_id, name)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO diagrams_new (owner_id, name, data, updated_at)
                SELECT owner_id, name, data, updated_at FROM diagrams
                """
            )
            conn.execute("DROP TABLE diagrams")
            conn.execute("ALTER TABLE diagrams_new RENAME TO diagrams")
        elif "owner_id" not in columns:
            conn.execute("ALTER TABLE diagrams ADD COLUMN owner_id INTEGER")
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/healthz")
async def healthcheck():
    return {"status": "ok"}


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user_id, datetime.utcnow().isoformat() + "Z"),
    )
    return token


async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    token = authorization.split(" ", 1)[1].strip()
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT users.id, users.email
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"id": row["id"], "email": row["email"]}
    finally:
        conn.close()


@app.post("/api/auth/signup", response_model=AuthResponse)
async def signup(req: AuthRequest):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    salt = secrets.token_hex(16)
    pwd_hash = hash_password(req.password, salt)
    conn = get_db_connection()
    try:
        try:
            conn.execute(
                "INSERT INTO users (email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?)",
                (email, pwd_hash, salt, datetime.utcnow().isoformat() + "Z"),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Email already exists")
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        token = create_session(conn, user_id)
        conn.commit()
        return AuthResponse(token=token, email=email)
    finally:
        conn.close()


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(req: AuthRequest):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, password_hash, password_salt FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        expected = row["password_hash"]
        actual = hash_password(req.password, row["password_salt"])
        if actual != expected:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_session(conn, row["id"])
        conn.commit()
        return AuthResponse(token=token, email=email)
    finally:
        conn.close()


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    prompt = req.message.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Message is empty")

    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    model = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
    api_endpoint = f"{base_url}/v1/chat/completions"

    if not api_key or not base_url:
        # Safe fallback when not configured
        reply = "LLM not configured. Please set LLM_API_KEY and LLM_BASE_URL in .env."
        return ChatResponse(reply=reply, conversation_id=req.conversation_id)

    try:
        timeout = httpx.Timeout(90.0, connect=10.0, read=60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
          # Simple OpenAI-compatible chat/completions request
            llm_res = await client.post(
                api_endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                },
            )
            llm_res.raise_for_status()
            payload = llm_res.json()
            reply_text = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not reply_text:
                reply_text = "No response from LLM."
            return ChatResponse(reply=reply_text, conversation_id=req.conversation_id)
    except httpx.RequestError as req_err:
        logging.exception("LLM request error")
        raise HTTPException(
            status_code=502,
            detail=f"LLM request failed: {req_err.__class__.__name__}: {req_err}",
        )
    except httpx.HTTPStatusError as http_err:
        logging.exception("LLM HTTP status error")
        raise HTTPException(
            status_code=502,
            detail=f"LLM service error: {http_err.response.status_code} {http_err.response.text}",
        )
    except Exception as err:
        logging.exception("LLM unexpected error")
        raise HTTPException(
            status_code=500,
            detail=f"LLM request failed: {err.__class__.__name__}: {err}",
        )


@app.get("/api/diagrams")
async def list_diagrams(user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT name, data FROM diagrams WHERE owner_id = ? ORDER BY updated_at DESC",
            (user["id"],),
        ).fetchall()
        diagrams = {}
        for row in rows:
            try:
                diagrams[row["name"]] = json.loads(row["data"])
            except json.JSONDecodeError:
                diagrams[row["name"]] = {}
        return {"diagrams": diagrams}
    finally:
        conn.close()


@app.get("/api/diagrams/{name}")
async def get_diagram(name: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT name, data, updated_at FROM diagrams WHERE name = ? AND owner_id = ?",
            (name, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Diagram not found")
        try:
            data = json.loads(row["data"])
        except json.JSONDecodeError:
            data = {}
        return {"name": row["name"], "data": data, "updated_at": row["updated_at"]}
    finally:
        conn.close()


@app.post("/api/diagrams/{name}")
async def save_diagram(name: str, payload: DiagramPayload, user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        data_json = json.dumps(payload.dict())
        updated_at = datetime.utcnow().isoformat() + "Z"
        conn.execute(
            """
            INSERT INTO diagrams (owner_id, name, data, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, name)
            DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (user["id"], name, data_json, updated_at),
        )
        conn.commit()
        return {"status": "ok", "name": name, "updated_at": updated_at}
    finally:
        conn.close()


@app.delete("/api/diagrams/{name}")
async def delete_diagram(name: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "DELETE FROM diagrams WHERE name = ? AND owner_id = ?",
            (name, user["id"]),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Diagram not found")
        return {"status": "ok"}
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
