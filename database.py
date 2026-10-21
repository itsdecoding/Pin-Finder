import sqlite3
import json
import os
import secrets
from typing import Dict, List, Any, Optional
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "pin_finder.db")

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db(db_path: str = DB_PATH):
    """Initialize SQLite database with proper relational tables."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 1. Users Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        hashed_password TEXT NOT NULL,
        role TEXT DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Jobs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        niche TEXT NOT NULL,
        location TEXT NOT NULL,
        target_count INTEGER NOT NULL,
        scraped_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending', -- pending, running, completed, failed
        progress_pct INTEGER DEFAULT 0,
        error_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Leads Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT,
        rating REAL,
        reviews_count INTEGER,
        address TEXT,
        phone TEXT,
        website TEXT,
        maps_url TEXT,
        has_website BOOLEAN DEFAULT 0,
        emails TEXT DEFAULT '[]', -- JSON string list
        socials TEXT DEFAULT '{}', -- JSON string dict
        enrichment_status TEXT DEFAULT 'not_started', -- not_started, enriched, no_website, failed, no_contact_found
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
    );
    """)

    # 4. API Keys Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        key_value TEXT UNIQUE NOT NULL,
        name TEXT DEFAULT 'Default Key',
        is_active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # 5. Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    conn.commit()
    conn.close()

# --- JOB HELPERS ---
def create_job(niche: str, location: str, target_count: int, db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO jobs (niche, location, target_count, status, progress_pct) VALUES (?, ?, ?, 'pending', 0);",
        (niche, location, target_count)
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return job_id

def update_job_progress(job_id: int, status: str, progress_pct: int, scraped_count: int = 0, error: str = None, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE jobs 
        SET status = ?, progress_pct = ?, scraped_count = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP 
        WHERE id = ?;
        """,
        (status, progress_pct, scraped_count, error, job_id)
    )
    conn.commit()
    conn.close()

def get_job(job_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?;", (job_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def list_jobs(limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?;", (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- LEAD HELPERS ---
def insert_leads(job_id: int, leads: List[Dict[str, Any]], db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    cur = conn.cursor()
    inserted = 0
    for l in leads:
        website = (l.get("website") or "").strip()
        has_website = bool(website)
        emails_json = json.dumps(l.get("emails") or [])
        socials_json = json.dumps(l.get("socials") or {})
        enrichment_status = l.get("enrichment_status") or ("not_started" if has_website else "no_website")

        cur.execute(
            """
            INSERT INTO leads (
                job_id, title, category, rating, reviews_count, address, 
                phone, website, maps_url, has_website, emails, socials, enrichment_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                job_id,
                l.get("title", ""),
                l.get("category", ""),
                l.get("rating"),
                l.get("reviews_count"),
                l.get("address", ""),
                l.get("phone", ""),
                website,
                l.get("maps_url", ""),
                1 if has_website else 0,
                emails_json,
                socials_json,
                enrichment_status
            )
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted

def get_lead(lead_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM leads WHERE id = ?;", (lead_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["has_website"] = bool(d["has_website"])
        d["emails"] = json.loads(d["emails"]) if d["emails"] else []
        d["socials"] = json.loads(d["socials"]) if d["socials"] else {}
        return d
    return None

def update_lead_enrichment(lead_id: int, emails: List[str], socials: Dict[str, str], status: str, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE leads 
        SET emails = ?, socials = ?, enrichment_status = ? 
        WHERE id = ?;
        """,
        (json.dumps(emails), json.dumps(socials), status, lead_id)
    )
    conn.commit()
    conn.close()

def list_leads(job_id: Optional[int] = None, filter_type: Optional[str] = None, limit: int = 100, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()

    query = "SELECT * FROM leads WHERE 1=1"
    params = []

    if job_id:
        query += " AND job_id = ?"
        params.append(job_id)

    if filter_type == "with_website":
        query += " AND has_website = 1"
    elif filter_type == "no_website":
        query += " AND has_website = 0"
    elif filter_type == "enriched":
        query += " AND enrichment_status = 'enriched'"

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        d["has_website"] = bool(d["has_website"])
        d["emails"] = json.loads(d["emails"]) if d["emails"] else []
        d["socials"] = json.loads(d["socials"]) if d["socials"] else {}
        results.append(d)
    return results

# --- API KEY HELPERS ---
def create_api_key(user_id: int, name: str = "Default Key", db_path: str = DB_PATH) -> Dict[str, Any]:
    """Generate a new secure API key with prefix pf_live_."""
    raw_key = f"pf_live_{secrets.token_hex(20)}"
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO api_keys (user_id, key_value, name, is_active) VALUES (?, ?, ?, 1);",
        (user_id, raw_key, name)
    )
    key_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "id": key_id,
        "name": name,
        "key": raw_key,
        "masked": f"pf_live_...{raw_key[-6:]}",
        "is_active": True
    }

def list_api_keys(user_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """List API keys with masked values for security."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, name, key_value, is_active, created_at FROM api_keys WHERE user_id = ? ORDER BY id DESC;", (user_id,))
    rows = cur.fetchall()
    conn.close()
    results = []
    for r in rows:
        raw = r["key_value"]
        results.append({
            "id": r["id"],
            "name": r["name"],
            "masked": f"pf_live_...{raw[-6:]}",
            "is_active": bool(r["is_active"]),
            "created_at": r["created_at"]
        })
    return results

def revoke_api_key(key_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    """Revoke (deactivate) an API key."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?;", (key_id, user_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def validate_api_key(key_value: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Validate an incoming API key against active keys in database."""
    if not key_value or not key_value.startswith("pf_live_"):
        return None
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT k.id, k.user_id, k.name, u.username, u.email, u.role 
        FROM api_keys k 
        JOIN users u ON k.user_id = u.id 
        WHERE k.key_value = ? AND k.is_active = 1;
        """,
        (key_value.strip(),)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

# --- SETTINGS & WEBHOOK HELPERS ---
def get_setting(key: str, default: str = "", db_path: str = DB_PATH) -> str:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?;", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
        (key, value)
    )
    conn.commit()
    conn.close()

def get_webhook_config(db_path: str = DB_PATH) -> Dict[str, Any]:
    url = get_setting("crm_webhook_url", "", db_path)
    auto_push = get_setting("crm_auto_push", "false", db_path) == "true"
    return {"webhook_url": url, "auto_push": auto_push}

def save_webhook_config(url: str, auto_push: bool, db_path: str = DB_PATH):
    set_setting("crm_webhook_url", url.strip(), db_path)
    set_setting("crm_auto_push", "true" if auto_push else "false", db_path)

