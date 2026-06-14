"""
Design Convert - License API
Servidor central de validación de licencias
"""
import os, hashlib, sqlite3, secrets, string, random
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Configuración ─────────────────────────────────────────────────────────────
SECRET = os.environ.get("LICENSE_SECRET", "JEANDESIGN2024XK")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "cambia-esta-clave-admin")
DB_PATH = os.environ.get("DB_PATH", "licenses.db")

app = FastAPI(title="Design Convert License API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Base de datos ─────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            code TEXT PRIMARY KEY,
            hardware_id TEXT,
            email TEXT,
            name TEXT,
            created_at TEXT,
            activated_at TEXT,
            status TEXT DEFAULT 'unused'
        )
    """)
    return conn


# ── Utilidades de licencia ──────────────────────────────────────────────────────
def generate_license_hash(prefix: str) -> str:
    return hashlib.sha256(f"{prefix}{SECRET}".encode()).hexdigest()[:4].upper()

def validate_format(code: str) -> bool:
    parts = code.upper().strip().split('-')
    if len(parts) != 4 or not all(len(p) == 4 for p in parts):
        return False
    prefix = ''.join(parts[:3])
    expected = generate_license_hash(prefix)[:4]
    return parts[3] == expected

def generate_code() -> str:
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace('0','').replace('O','').replace('I','').replace('1','')
    parts = [''.join(random.choices(chars, k=4)) for _ in range(3)]
    prefix = ''.join(parts)
    checksum = generate_license_hash(prefix)
    parts.append(checksum)
    return '-'.join(parts)


# ── Modelos ──────────────────────────────────────────────────────────────────
class ActivateRequest(BaseModel):
    code: str
    hardware_id: str
    email: str = ""
    name: str = ""

class GenerateRequest(BaseModel):
    quantity: int = 1
    note: str = ""


# ── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "online", "service": "Design Convert License API"}


@app.post("/activate")
def activate(req: ActivateRequest):
    code = req.code.upper().strip()

    if not validate_format(code):
        raise HTTPException(400, "Formato de licencia inválido")

    db = get_db()
    cur = db.execute("SELECT * FROM licenses WHERE code = ?", (code,))
    row = cur.fetchone()

    if row is None:
        db.close()
        raise HTTPException(404, "Licencia no encontrada. Contacta al vendedor.")

    columns = [d[0] for d in cur.description]
    data = dict(zip(columns, row))

    if data['status'] == 'used' and data['hardware_id'] != req.hardware_id:
        db.close()
        raise HTTPException(403, "Esta licencia ya está activada en otro equipo")

    # Activar o re-confirmar
    db.execute(
        "UPDATE licenses SET hardware_id=?, email=?, name=?, activated_at=?, status='used' WHERE code=?",
        (req.hardware_id, req.email, req.name, datetime.now().isoformat(), code)
    )
    db.commit()
    db.close()

    return {"success": True, "message": "Licencia activada correctamente"}


@app.post("/verify")
def verify(req: ActivateRequest):
    """Verifica que una licencia activada sigue siendo válida para este hardware."""
    code = req.code.upper().strip()
    db = get_db()
    cur = db.execute("SELECT hardware_id, status FROM licenses WHERE code = ?", (code,))
    row = cur.fetchone()
    db.close()

    if row is None:
        raise HTTPException(404, "Licencia no encontrada")

    hwid, status = row
    if status != 'used':
        raise HTTPException(403, "Licencia no activada")
    if hwid != req.hardware_id:
        raise HTTPException(403, "Licencia activada en otro equipo")

    return {"valid": True}


# ── Endpoints de administración (requieren ADMIN_KEY) ────────────────────────
def check_admin(x_admin_key: str):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "No autorizado")


@app.post("/admin/generate")
def admin_generate(req: GenerateRequest, x_admin_key: str = Header(...)):
    check_admin(x_admin_key)

    db = get_db()
    codes = []
    for _ in range(req.quantity):
        code = generate_code()
        # Asegurar que no exista ya
        while db.execute("SELECT 1 FROM licenses WHERE code=?", (code,)).fetchone():
            code = generate_code()
        db.execute(
            "INSERT INTO licenses (code, created_at, status) VALUES (?, ?, 'unused')",
            (code, datetime.now().isoformat())
        )
        codes.append(code)
    db.commit()
    db.close()

    return {"codes": codes, "quantity": len(codes)}


@app.get("/admin/list")
def admin_list(x_admin_key: str = Header(...)):
    check_admin(x_admin_key)
    db = get_db()
    cur = db.execute("SELECT code, status, hardware_id, email, name, created_at, activated_at FROM licenses ORDER BY created_at DESC")
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    db.close()
    return {"licenses": rows, "total": len(rows)}


@app.post("/admin/reset/{code}")
def admin_reset(code: str, x_admin_key: str = Header(...)):
    """Resetea una licencia para que pueda usarse en otro PC (ej: cliente cambió de equipo)."""
    check_admin(x_admin_key)
    code = code.upper().strip()
    db = get_db()
    cur = db.execute("UPDATE licenses SET hardware_id=NULL, status='unused', activated_at=NULL WHERE code=?", (code,))
    db.commit()
    affected = cur.rowcount
    db.close()
    if affected == 0:
        raise HTTPException(404, "Licencia no encontrada")
    return {"success": True, "message": f"Licencia {code} reseteada"}


@app.delete("/admin/delete/{code}")
def admin_delete(code: str, x_admin_key: str = Header(...)):
    check_admin(x_admin_key)
    code = code.upper().strip()
    db = get_db()
    cur = db.execute("DELETE FROM licenses WHERE code=?", (code,))
    db.commit()
    affected = cur.rowcount
    db.close()
    if affected == 0:
        raise HTTPException(404, "Licencia no encontrada")
    return {"success": True, "message": f"Licencia {code} eliminada"}
