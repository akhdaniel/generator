import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diagrams (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/healthz")
async def healthcheck():
    return {"status": "ok"}


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
async def list_diagrams():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT name, data FROM diagrams ORDER BY updated_at DESC"
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
async def get_diagram(name: str):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT name, data, updated_at FROM diagrams WHERE name = ?",
            (name,),
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
async def save_diagram(name: str, payload: DiagramPayload):
    conn = get_db_connection()
    try:
        data_json = json.dumps(payload.dict())
        updated_at = datetime.utcnow().isoformat() + "Z"
        conn.execute(
            "INSERT OR REPLACE INTO diagrams (name, data, updated_at) VALUES (?, ?, ?)",
            (name, data_json, updated_at),
        )
        conn.commit()
        return {"status": "ok", "name": name, "updated_at": updated_at}
    finally:
        conn.close()


@app.delete("/api/diagrams/{name}")
async def delete_diagram(name: str):
    conn = get_db_connection()
    try:
        cur = conn.execute("DELETE FROM diagrams WHERE name = ?", (name,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Diagram not found")
        return {"status": "ok"}
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
