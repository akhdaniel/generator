import os
import json
import logging
import secrets
import sqlite3
import hashlib
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=logging.INFO)
DB_PATH = os.getenv("DIAGRAM_DB_PATH", os.path.join(os.path.dirname(__file__), "diagrams.db"))
ODOO_VERSION = os.getenv("ODOO_VERSION", "17")
SQL_DIALECT = os.getenv("SQL_DIALECT", "postgresql")
TEMPLATE_ROOT = Path(os.path.dirname(__file__)) / "templates"
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
    name: Optional[str] = None


class AuthResponse(BaseModel):
    token: str
    email: str
    name: Optional[str] = None
    credits: int = 0


class UserProfileResponse(BaseModel):
    email: str
    name: Optional[str] = None
    credits: int = 0


class GenerateRequest(BaseModel):
    type: str
    diagram: DiagramPayload
    template: Optional[str] = None


class GenerateResponse(BaseModel):
    result: str
    files: Optional[list] = None
    download_url: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TopUpRequest(BaseModel):
    amount: int


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
                created_at TEXT NOT NULL,
                name TEXT,
                credits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        user_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "name" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN name TEXT")
        if "credits" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 0")
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


GENERATED_ZIPS: Dict[str, dict] = {}


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
            SELECT users.id, users.email, users.name, users.credits
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
            "credits": row["credits"] or 0
        }
    finally:
        conn.close()


async def request_llm(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    model = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
    api_endpoint = f"{base_url}/v1/chat/completions"

    if not api_key or not base_url:
        raise HTTPException(
            status_code=500,
            detail="LLM not configured. Please set LLM_API_KEY and LLM_BASE_URL in .env.",
        )

    timeout = httpx.Timeout(90.0, connect=10.0, read=60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        llm_res = await client.post(
            api_endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
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
        return reply_text or "No response from LLM."


@app.post("/api/auth/signup", response_model=AuthResponse)
async def signup(req: AuthRequest):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    name = (req.name or "").strip() or email.split("@", 1)[0]
    salt = secrets.token_hex(16)
    pwd_hash = hash_password(req.password, salt)
    conn = get_db_connection()
    try:
        try:
            conn.execute(
                """
                INSERT INTO users (email, password_hash, password_salt, created_at, name, credits)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (email, pwd_hash, salt, datetime.utcnow().isoformat() + "Z", name, 0),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Email already exists")
        user_row = conn.execute(
            "SELECT id, name, credits FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        user_id = user_row["id"]
        token = create_session(conn, user_id)
        conn.commit()
        return AuthResponse(token=token, email=email, name=user_row["name"], credits=user_row["credits"] or 0)
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
            "SELECT id, password_hash, password_salt, name, credits FROM users WHERE email = ?",
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
        return AuthResponse(token=token, email=email, name=row["name"], credits=row["credits"] or 0)
    finally:
        conn.close()


@app.get("/api/user/me", response_model=UserProfileResponse)
async def user_profile(user=Depends(get_current_user)):
    return UserProfileResponse(
        email=user["email"],
        name=user.get("name"),
        credits=user.get("credits", 0),
    )


@app.post("/api/user/change-password")
async def change_password(req: ChangePasswordRequest, user=Depends(get_current_user)):
    if not req.current_password or not req.new_password:
        raise HTTPException(status_code=400, detail="Current and new password required")
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT password_hash, password_salt FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        expected = row["password_hash"]
        actual = hash_password(req.current_password, row["password_salt"])
        if actual != expected:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        salt = secrets.token_hex(16)
        pwd_hash = hash_password(req.new_password, salt)
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (pwd_hash, salt, user["id"]),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.post("/api/user/topup", response_model=UserProfileResponse)
async def top_up(req: TopUpRequest, user=Depends(get_current_user)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE users SET credits = COALESCE(credits, 0) + ? WHERE id = ?",
            (req.amount, user["id"]),
        )
        conn.commit()
        row = conn.execute(
            "SELECT email, name, credits FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
        return UserProfileResponse(
            email=row["email"],
            name=row["name"],
            credits=row["credits"] or 0,
        )
    finally:
        conn.close()


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    prompt = req.message.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Message is empty")

    try:
        reply_text = await request_llm(SYSTEM_PROMPT, prompt)
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


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, user=Depends(get_current_user)):
    mode = req.type.strip().lower()
    if mode not in {"db", "odoo"}:
        raise HTTPException(status_code=400, detail="type must be 'db' or 'odoo'")
    diagram = req.diagram.dict()
    if mode == "db":
        dialect = (req.template or SQL_DIALECT).strip().lower()
        result = diagram_to_sql(diagram, dialect)
        return GenerateResponse(result=result)
    version = (req.template or ODOO_VERSION).strip()
    temp_dir = Path(tempfile.mkdtemp(prefix="odoo_addon_"))
    files_written = write_odoo_addon(diagram, temp_dir, version)
    files_sorted = sorted(files_written, key=lambda p: (p.split("/", 1)[0], p))
    zip_id = secrets.token_urlsafe(16)
    zip_path = temp_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel_path in files_sorted:
            archive.write(temp_dir / rel_path, rel_path)
    GENERATED_ZIPS[zip_id] = {"user_id": user["id"], "path": str(zip_path)}
    return GenerateResponse(
        result="\n".join(files_sorted),
        files=files_sorted,
        download_url=f"/api/generate/download/{zip_id}",
    )


def normalize_identifier(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name.strip())
    return cleaned or "field"


def camel_to_snake(name: str) -> str:
    out = []
    for ch in name.strip():
        if ch.isupper() and out:
            out.append("_")
        out.append(ch.lower())
    return normalize_identifier("".join(out))


def parse_attribute(attr: str) -> tuple[str, str]:
    if ":" in attr:
        left, right = attr.split(":", 1)
        return normalize_identifier(left.strip()), right.strip().lower()
    parts = attr.split()
    if len(parts) >= 2:
        return normalize_identifier(parts[0]), " ".join(parts[1:]).strip().lower()
    return normalize_identifier(attr.strip()), "string"


def sql_type_for(typ: str) -> str:
    if typ in {"int", "integer", "number"}:
        return "INTEGER"
    if typ in {"float", "double", "decimal"}:
        return "REAL"
    if typ in {"bool", "boolean"}:
        return "BOOLEAN"
    if typ in {"date", "datetime", "timestamp"}:
        return "TEXT"
    return "TEXT"


def odoo_field_for(typ: str) -> str:
    if typ in {"int", "integer", "number"}:
        return "fields.Integer()"
    if typ in {"float", "double", "decimal"}:
        return "fields.Float()"
    if typ in {"bool", "boolean"}:
        return "fields.Boolean()"
    if typ in {"date"}:
        return "fields.Date()"
    if typ in {"datetime", "timestamp"}:
        return "fields.Datetime()"
    return "fields.Char()"


def read_template(folder: str, name: str) -> str:
    path = TEMPLATE_ROOT / folder / name
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Missing Odoo template: {path}")
    return path.read_text(encoding="utf-8")


def read_sql_template(dialect: str, name: str) -> str:
    path = TEMPLATE_ROOT / f"sql-{dialect}" / name
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Missing SQL template: {path}")
    return path.read_text(encoding="utf-8")


def get_odoo_templates(version: str) -> dict:
    return {
        "manifest": read_template(f"odoo-{version}", "manifest.tmpl"),
        "model": read_template(f"odoo-{version}", "model.py.tmpl"),
        "view": read_template(f"odoo-{version}", "view.xml.tmpl"),
        "access": read_template(f"odoo-{version}", "access.csv.tmpl"),
        "init": "from . import models\n",
        "models_init": "from . import {model}\n",
        "access_row": "access_{model},{model},model_{model},,1,1,1,1",
    }


def diagram_to_sql(diagram: dict, dialect: str) -> str:
    nodes = diagram.get("nodes", [])
    edges = diagram.get("edges", [])
    table_map = {n["id"]: camel_to_snake(n.get("data", {}).get("name", "class")) for n in nodes}
    table_defs = {}
    foreign_keys = {table: [] for table in table_map.values()}
    template = read_sql_template(dialect, "create_table.tmpl")

    for node in nodes:
        table = table_map[node["id"]]
        attrs = node.get("data", {}).get("attributes", [])
        cols = ["id INTEGER PRIMARY KEY"]
        for attr in attrs:
            name, typ = parse_attribute(attr)
            cols.append(f"{name} {sql_type_for(typ)}")
        table_defs[table] = cols

    for edge in edges:
        data = edge.get("data", {})
        rel = data.get("relationType", "association")
        source_id = edge.get("source")
        target_id = edge.get("target")
        if source_id not in table_map or target_id not in table_map:
            continue
        source_table = table_map[source_id]
        target_table = table_map[target_id]
        source_mult = data.get("sourceMultiplicity")
        target_mult = data.get("targetMultiplicity")
        source_role = normalize_identifier((data.get("sourceRole") or f"{target_table}_id"))
        target_role = normalize_identifier((data.get("targetRole") or f"{source_table}_id"))

        if rel == "inheritance":
            continue

        if source_mult == "many2many" or target_mult == "many2many":
            join_table = f"{source_table}_{target_table}_rel"
            table_defs.setdefault(join_table, ["id INTEGER PRIMARY KEY"])
            table_defs[join_table].append(f"{source_table}_id INTEGER")
            table_defs[join_table].append(f"{target_table}_id INTEGER")
            foreign_keys[join_table] = [
                f"FOREIGN KEY ({source_table}_id) REFERENCES {source_table}(id)",
                f"FOREIGN KEY ({target_table}_id) REFERENCES {target_table}(id)",
            ]
            continue

        if source_mult == "many2one" or target_mult == "one2many":
            table_defs[source_table].append(f"{source_role} INTEGER")
            foreign_keys[source_table].append(
                f"FOREIGN KEY ({source_role}) REFERENCES {target_table}(id)"
            )
        elif target_mult == "many2one" or source_mult == "one2many":
            table_defs[target_table].append(f"{target_role} INTEGER")
            foreign_keys[target_table].append(
                f"FOREIGN KEY ({target_role}) REFERENCES {source_table}(id)"
            )

    statements = []
    for table, cols in table_defs.items():
        fk = foreign_keys.get(table) or []
        body = cols + fk
        statements.append(
            template.format(table_name=table, columns=",\n  ".join(body))
        )
    return "\n\n".join(statements)


def diagram_to_odoo(diagram: dict) -> str:
    nodes = diagram.get("nodes", [])
    edges = diagram.get("edges", [])
    model_map = {n["id"]: camel_to_snake(n.get("data", {}).get("name", "class")) for n in nodes}
    class_map = {n["id"]: n.get("data", {}).get("name", "Class") for n in nodes}
    fields_map = {model: [] for model in model_map.values()}

    for node in nodes:
        model = model_map[node["id"]]
        attrs = node.get("data", {}).get("attributes", [])
        for attr in attrs:
            name, typ = parse_attribute(attr)
            fields_map[model].append(f"    {name} = {odoo_field_for(typ)}")

    for edge in edges:
        data = edge.get("data", {})
        source_id = edge.get("source")
        target_id = edge.get("target")
        if source_id not in model_map or target_id not in model_map:
            continue
        source_model = model_map[source_id]
        target_model = model_map[target_id]
        source_mult = data.get("sourceMultiplicity")
        target_mult = data.get("targetMultiplicity")
        source_role = normalize_identifier((data.get("sourceRole") or f"{target_model}_id"))
        target_role = normalize_identifier((data.get("targetRole") or f"{source_model}_ids"))

        if source_mult == "many2many" or target_mult == "many2many":
            rel_table = f"{source_model}_{target_model}_rel"
            fields_map[source_model].append(
                f"    {target_role} = fields.Many2many('{target_model}', relation='{rel_table}')"
            )
            fields_map[target_model].append(
                f"    {source_model}_ids = fields.Many2many('{source_model}', relation='{rel_table}')"
            )
        elif source_mult == "many2one" or target_mult == "one2many":
            fields_map[source_model].append(
                f"    {source_role} = fields.Many2one('{target_model}')"
            )
            fields_map[target_model].append(
                f"    {target_role} = fields.One2many('{source_model}', '{source_role}')"
            )
        elif target_mult == "many2one" or source_mult == "one2many":
            fields_map[target_model].append(
                f"    {source_role} = fields.Many2one('{source_model}')"
            )
            fields_map[source_model].append(
                f"    {target_role} = fields.One2many('{target_model}', '{source_role}')"
            )

    files = []
    files.append(
        ("__manifest__.py", "{\n    'name': 'Generated UML Addon',\n    'version': '1.0.0',\n    'depends': ['base'],\n    'data': [\n        'security/ir.model.access.csv',\n" +
         "\n".join([f"        'views/{model}_views.xml'," for model in model_map.values()]) +
         "\n    ],\n}")
    )
    files.append(("__init__.py", "from . import models"))
    files.append(("models/__init__.py", "\n".join([f"from . import {model}" for model in model_map.values()])))

    for node_id, model in model_map.items():
        class_name = class_map[node_id]
        model_name = model
        lines = [
            "from odoo import models, fields",
            "",
            f"class {class_name}(models.Model):",
            f"    _name = '{model_name}'",
            f"    _description = '{class_name}'",
            "",
        ]
        lines.extend(fields_map[model] or ["    name = fields.Char()"])
        files.append((f"models/{model}.py", "\n".join(lines)))

        view_xml = f"""<odoo>
  <record id="{model}_view_tree" model="ir.ui.view">
    <field name="name">{model}.tree</field>
    <field name="model">{model}</field>
    <field name="arch" type="xml">
      <tree>
        <field name="id"/>
      </tree>
    </field>
  </record>
  <record id="{model}_view_form" model="ir.ui.view">
    <field name="name">{model}.form</field>
    <field name="model">{model}</field>
    <field name="arch" type="xml">
      <form>
        <sheet>
          <group>
            <field name="id"/>
          </group>
        </sheet>
      </form>
    </field>
  </record>
</odoo>"""
        files.append((f"views/{model}_views.xml", view_xml))

    access_rows = ["id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink"]
    for model in model_map.values():
        access_rows.append(f"access_{model},{model},model_{model},,1,1,1,1")
    files.append(("security/ir.model.access.csv", "\n".join(access_rows)))

    rendered = []
    for path, content in files:
        rendered.append(f"FILE: {path}\n{content}\n")
    return "\n".join(rendered)


def write_odoo_addon(diagram: dict, root: Path, version: str) -> list:
    templates = get_odoo_templates(version)
    nodes = diagram.get("nodes", [])
    edges = diagram.get("edges", [])
    model_map = {n["id"]: camel_to_snake(n.get("data", {}).get("name", "class")) for n in nodes}
    class_map = {n["id"]: n.get("data", {}).get("name", "Class") for n in nodes}
    fields_map = {model: [] for model in model_map.values()}

    for node in nodes:
        model = model_map[node["id"]]
        attrs = node.get("data", {}).get("attributes", [])
        for attr in attrs:
            name, typ = parse_attribute(attr)
            fields_map[model].append(f"    {name} = {odoo_field_for(typ)}")

    for edge in edges:
        data = edge.get("data", {})
        source_id = edge.get("source")
        target_id = edge.get("target")
        if source_id not in model_map or target_id not in model_map:
            continue
        source_model = model_map[source_id]
        target_model = model_map[target_id]
        source_mult = data.get("sourceMultiplicity")
        target_mult = data.get("targetMultiplicity")
        source_role = normalize_identifier((data.get("sourceRole") or f"{target_model}_id"))
        target_role = normalize_identifier((data.get("targetRole") or f"{source_model}_ids"))

        if source_mult == "many2many" or target_mult == "many2many":
            rel_table = f"{source_model}_{target_model}_rel"
            fields_map[source_model].append(
                f"    {target_role} = fields.Many2many('{target_model}', relation='{rel_table}')"
            )
            fields_map[target_model].append(
                f"    {source_model}_ids = fields.Many2many('{source_model}', relation='{rel_table}')"
            )
        elif source_mult == "many2one" or target_mult == "one2many":
            fields_map[source_model].append(
                f"    {source_role} = fields.Many2one('{target_model}')"
            )
            fields_map[target_model].append(
                f"    {target_role} = fields.One2many('{source_model}', '{source_role}')"
            )
        elif target_mult == "many2one" or source_mult == "one2many":
            fields_map[target_model].append(
                f"    {source_role} = fields.Many2one('{source_model}')"
            )
            fields_map[source_model].append(
                f"    {target_role} = fields.One2many('{target_model}', '{source_role}')"
            )

    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "views").mkdir(parents=True, exist_ok=True)
    (root / "security").mkdir(parents=True, exist_ok=True)

    view_entries = "\n".join([f"        'views/{model}_views.xml'," for model in model_map.values()])
    manifest = templates["manifest"].format(
        addon_name="Generated UML Addon",
        version="1.0.0",
        view_entries=view_entries,
    )

    files_written = []
    (root / "__manifest__.py").write_text(manifest, encoding="utf-8")
    files_written.append("__manifest__.py")
    (root / "__init__.py").write_text(templates["init"], encoding="utf-8")
    files_written.append("__init__.py")
    (root / "models" / "__init__.py").write_text(
        "".join([templates["models_init"].format(model=model) for model in model_map.values()]),
        encoding="utf-8",
    )
    files_written.append("models/__init__.py")

    for node_id, model in model_map.items():
        class_name = class_map[node_id]
        field_lines = fields_map[model] or ["    name = fields.Char()"]
        model_content = templates["model"].format(
            class_name=class_name,
            model_name=model,
            fields="\n".join(field_lines),
        )
        (root / "models" / f"{model}.py").write_text(model_content, encoding="utf-8")
        files_written.append(f"models/{model}.py")

        field_tags = fields_map[model] or ["    name = fields.Char()"]
        tree_fields = "\n".join([f"        <field name=\"{line.split('=')[0].strip()}\"/>" for line in field_tags])
        form_fields = "\n".join([f"            <field name=\"{line.split('=')[0].strip()}\"/>" for line in field_tags])
        view_xml = templates["view"].format(
            model=model,
            tree_fields=tree_fields,
            form_fields=form_fields,
        )
        (root / "views" / f"{model}_views.xml").write_text(view_xml, encoding="utf-8")
        files_written.append(f"views/{model}_views.xml")

    access_rows = [templates["access_row"].format(model=model) for model in model_map.values()]
    access_content = templates["access"].format(rows="\n".join(access_rows))
    (root / "security" / "ir.model.access.csv").write_text(access_content, encoding="utf-8")
    files_written.append("security/ir.model.access.csv")
    return files_written


@app.get("/api/templates")
async def list_templates(user=Depends(get_current_user)):
    templates = {"odoo": [], "sql": []}
    if TEMPLATE_ROOT.exists():
        for path in TEMPLATE_ROOT.iterdir():
            if path.is_dir() and path.name.startswith("odoo-"):
                templates["odoo"].append(path.name.replace("odoo-", "", 1))
            if path.is_dir() and path.name.startswith("sql-"):
                templates["sql"].append(path.name.replace("sql-", "", 1))
    templates["odoo"].sort()
    templates["sql"].sort()
    return templates


@app.get("/api/generate/download/{zip_id}")
async def download_generated(zip_id: str, user=Depends(get_current_user)):
    entry = GENERATED_ZIPS.get(zip_id)
    if not entry or entry["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(entry["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="application/zip", filename="odoo_addon.zip")


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
