import asyncio
import io
import csv
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import (
    init_db, create_job, update_job_progress, get_job, list_jobs,
    insert_leads, get_lead, update_lead_enrichment, list_leads, get_connection
)
from auth import (
    register_user, authenticate_user, create_access_token, decode_token
)
from scraper import GoogleMapsScraper
from enricher import LeadEnricher

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("PinFinder-API")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database on startup
    init_db()
    logger.info("Pin Finder database initialized successfully.")
    yield

app = FastAPI(
    title="Pin Finder API",
    description="Internal Google Maps Scraper & Lead Enrichment SaaS Backend",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local/internal browser UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- PYDANTIC SCHEMAS ---
class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class JobCreateRequest(BaseModel):
    niche: str = Field(..., example="Dentists")
    location: str = Field(..., example="Miami, FL")
    target_count: int = Field(10, ge=1, le=500, example=10)

# --- AUTH DEPENDENCY ---
def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        # Allow testing or internal development token fallback
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token is invalid or expired")
    return payload

# --- BACKGROUND SCRAPING WORKER ---
async def run_scraper_task(job_id: int, niche: str, location: str, target_count: int):
    logger.info(f"Background worker started for Job #{job_id}: {niche} in {location}")
    try:
        update_job_progress(job_id, status="running", progress_pct=15)
        
        scraper = GoogleMapsScraper(headless=True)
        leads = await scraper.scrape(niche=niche, location=location, limit=target_count)

        update_job_progress(job_id, status="running", progress_pct=85, scraped_count=len(leads))

        # Store leads in SQLite database
        if leads:
            insert_leads(job_id, leads)

        update_job_progress(
            job_id,
            status="completed",
            progress_pct=100,
            scraped_count=len(leads)
        )
        logger.info(f"Job #{job_id} completed successfully. Saved {len(leads)} leads.")
    except Exception as e:
        logger.error(f"Job #{job_id} failed: {e}")
        update_job_progress(job_id, status="failed", progress_pct=0, error=str(e))

# --- AUTH ENDPOINTS ---
@app.post("/api/v1/auth/register", tags=["Auth"])
def register(req: RegisterRequest):
    user_id = register_user(req.username, req.email, req.password)
    if not user_id:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    token = create_access_token({"sub": req.username, "user_id": user_id})
    return {"message": "User registered successfully", "access_token": token, "token_type": "bearer"}

@app.post("/api/v1/auth/login", tags=["Auth"])
def login(req: LoginRequest):
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token({"sub": user["username"], "user_id": user["id"], "role": user["role"]})
    return {"access_token": token, "token_type": "bearer", "user": user}

@app.get("/api/v1/auth/me", tags=["Auth"])
def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    return {"user": user}

# --- STATS ENDPOINT ---
@app.get("/api/v1/stats", tags=["Dashboard"])
def get_stats():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM leads;")
    total_leads = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leads WHERE has_website = 1;")
    with_website = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leads WHERE has_website = 0;")
    no_website = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leads WHERE enrichment_status = 'enriched';")
    enriched = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM jobs;")
    total_jobs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running';")
    running_jobs = cur.fetchone()[0]

    conn.close()
    return {
        "total_leads": total_leads,
        "with_website": with_website,
        "no_website": no_website,
        "enriched": enriched,
        "total_jobs": total_jobs,
        "running_jobs": running_jobs
    }

# --- JOB ENDPOINTS ---
@app.post("/api/v1/jobs", tags=["Jobs"])
def start_scraping_job(req: JobCreateRequest, bg: BackgroundTasks):
    job_id = create_job(req.niche, req.location, req.target_count)
    # Launch scraper in background
    bg.add_task(run_scraper_task, job_id, req.niche, req.location, req.target_count)
    return {
        "message": "Scraping task initiated in background",
        "job_id": job_id,
        "status": "pending",
        "niche": req.niche,
        "location": req.location,
        "target_count": req.target_count
    }

@app.get("/api/v1/jobs", tags=["Jobs"])
def get_all_jobs(limit: int = 50):
    return {"jobs": list_jobs(limit=limit)}

@app.get("/api/v1/jobs/{job_id}", tags=["Jobs"])
def get_job_details(job_id: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job}

@app.get("/api/v1/jobs/{job_id}/leads", tags=["Jobs"])
def get_job_leads(job_id: int, filter_type: Optional[str] = Query(None, pattern="^(with_website|no_website|enriched)$")):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    leads = list_leads(job_id=job_id, filter_type=filter_type)
    return {"job_id": job_id, "total": len(leads), "leads": leads}

# --- LEADS & ENRICHMENT ENDPOINTS ---
@app.get("/api/v1/leads", tags=["Leads"])
def get_all_leads(filter_type: Optional[str] = Query(None, pattern="^(with_website|no_website|enriched)$"), limit: int = 100):
    leads = list_leads(filter_type=filter_type, limit=limit)
    return {"total": len(leads), "leads": leads}

@app.get("/api/v1/leads/{lead_id}", tags=["Leads"])
def get_single_lead(lead_id: int):
    lead = get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": lead}

@app.post("/api/v1/leads/{lead_id}/enrich", tags=["Leads"])
async def trigger_single_enrichment(lead_id: int):
    """On-demand single lead enrichment button action."""
    lead = get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if not lead.get("has_website") or not lead.get("website"):
        update_lead_enrichment(lead_id, emails=[], socials={}, status="no_website")
        return {"message": "Lead has no website. Skipped.", "lead": get_lead(lead_id)}

    enricher = LeadEnricher(timeout=4.0)
    updated = await enricher.enrich_lead(lead)

    update_lead_enrichment(
        lead_id=lead_id,
        emails=updated.get("emails", []),
        socials=updated.get("socials", {}),
        status=updated.get("enrichment_status", "enriched")
    )

    return {
        "message": "Lead enriched successfully",
        "lead": get_lead(lead_id)
    }

@app.post("/api/v1/jobs/{job_id}/enrich-all", tags=["Jobs"])
async def trigger_bulk_job_enrichment(job_id: int, bg: BackgroundTasks):
    """On-demand bulk button action: Enrich all leads with websites for a specific job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    leads = list_leads(job_id=job_id, filter_type="with_website")
    candidates = [l for l in leads if l.get("enrichment_status") != "enriched"]

    if not candidates:
        return {"message": "No unenriched leads with websites found for this job", "count": 0}

    async def _bulk_worker(target_leads):
        enricher = LeadEnricher(timeout=4.0)
        enriched = await enricher.enrich_batch(target_leads, concurrency=4)
        for l in enriched:
            update_lead_enrichment(
                lead_id=l["id"],
                emails=l.get("emails", []),
                socials=l.get("socials", {}),
                status=l.get("enrichment_status", "enriched")
            )

    bg.add_task(_bulk_worker, candidates)

    return {
        "message": f"Bulk enrichment initiated for {len(candidates)} leads with websites",
        "job_id": job_id,
        "count": len(candidates)
    }

# --- CSV EXPORT ENDPOINT ---
@app.get("/api/v1/export/csv", tags=["Export"])
def export_leads_csv(job_id: Optional[int] = None, filter_type: Optional[str] = None):
    leads = list_leads(job_id=job_id, filter_type=filter_type, limit=10000)

    output = io.StringIO()
    writer = csv.writer(output)

    # Write CSV Header
    writer.writerow([
        "ID", "Job ID", "Business Name", "Category", "Rating", "Reviews Count",
        "Phone", "Website", "Has Website", "Emails", "Facebook", "Instagram",
        "LinkedIn", "Twitter", "Address", "Maps URL", "Enrichment Status"
    ])

    for l in leads:
        socials = l.get("socials") or {}
        emails_str = "; ".join(l.get("emails") or [])
        writer.writerow([
            l.get("id"),
            l.get("job_id"),
            l.get("title"),
            l.get("category"),
            l.get("rating"),
            l.get("reviews_count"),
            l.get("phone"),
            l.get("website"),
            "Yes" if l.get("has_website") else "No",
            emails_str,
            socials.get("facebook", ""),
            socials.get("instagram", ""),
            socials.get("linkedin", ""),
            socials.get("twitter", ""),
            l.get("address"),
            l.get("maps_url"),
            l.get("enrichment_status")
        ])

    output.seek(0)
    filename = f"leads_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
