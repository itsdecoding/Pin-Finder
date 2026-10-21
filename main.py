import asyncio
import io
import csv
import logging
import os
import httpx
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from database import (
    init_db, create_job, update_job_progress, get_job, list_jobs,
    insert_leads, get_lead, update_lead_enrichment, list_leads, get_connection,
    create_api_key, list_api_keys, revoke_api_key, validate_api_key,
    get_webhook_config, save_webhook_config
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

class ApiKeyCreateRequest(BaseModel):
    name: str = Field("CRM Integration Key", example="GoHighLevel Key")

class WebhookConfigRequest(BaseModel):
    webhook_url: str = Field(..., example="https://services.leadconnectorhq.com/hooks/...")
    auto_push: bool = Field(True, example=True)

class ExternalJobRequest(BaseModel):
    niche: str = Field(..., example="Dentists")
    location: str = Field(..., example="Miami, FL")
    target_count: int = Field(10, ge=1, le=500, example=10)
    webhook_url: Optional[str] = Field(None, example="https://webhook.site/...")

# --- AUTH DEPENDENCIES ---
def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token is invalid or expired")
    return payload

def get_api_key_auth(x_api_key: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    key_info = validate_api_key(x_api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid or deactivated API Key")
    return key_info

# --- CRM WEBHOOK DISPATCHER ---
async def dispatch_webhook(event: str, data: Dict[str, Any], custom_url: Optional[str] = None):
    target_url = custom_url
    if not target_url:
        cfg = get_webhook_config()
        if not cfg["auto_push"] or not cfg["webhook_url"]:
            return
        target_url = cfg["webhook_url"]

    if not target_url:
        return

    payload = {
        "event": event,
        "timestamp": datetime.utcnow().isoformat(),
        "source": "Pin Finder",
        "data": data
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(target_url, json=payload)
            logger.info(f"Webhook dispatched to {target_url} - Status: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Failed to dispatch webhook to {target_url}: {e}")

# --- BACKGROUND SCRAPING WORKER ---
async def run_scraper_task(job_id: int, niche: str, location: str, target_count: int, custom_webhook: Optional[str] = None):
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

        # Dispatches leads automatically to CRM Webhook
        await dispatch_webhook(
            event="leads.scraped",
            data={
                "job_id": job_id,
                "niche": niche,
                "location": location,
                "total_scraped": len(leads),
                "leads": list_leads(job_id=job_id)
            },
            custom_url=custom_webhook
        )

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

# --- API KEY MANAGEMENT ENDPOINTS ---
@app.post("/api/v1/integrations/api-keys", tags=["Integrations"])
def generate_key(req: ApiKeyCreateRequest, user: Dict[str, Any] = Depends(get_current_user)):
    user_id = user.get("user_id") or 1
    new_key = create_api_key(user_id=user_id, name=req.name)
    return {"message": "API key generated successfully", "api_key": new_key}

@app.get("/api/v1/integrations/api-keys", tags=["Integrations"])
def get_user_keys(user: Dict[str, Any] = Depends(get_current_user)):
    user_id = user.get("user_id") or 1
    keys = list_api_keys(user_id=user_id)
    return {"keys": keys}

@app.delete("/api/v1/integrations/api-keys/{key_id}", tags=["Integrations"])
def delete_key(key_id: int, user: Dict[str, Any] = Depends(get_current_user)):
    user_id = user.get("user_id") or 1
    revoked = revoke_api_key(key_id=key_id, user_id=user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Key not found or already inactive")
    return {"message": "API key revoked successfully"}

# --- CRM WEBHOOK ENDPOINTS ---
@app.get("/api/v1/integrations/webhook", tags=["Integrations"])
def get_webhook():
    return get_webhook_config()

@app.post("/api/v1/integrations/webhook", tags=["Integrations"])
def update_webhook(req: WebhookConfigRequest):
    save_webhook_config(req.webhook_url, req.auto_push)
    return {"message": "Webhook settings saved successfully", "config": req}

@app.post("/api/v1/integrations/webhook/test", tags=["Integrations"])
async def test_webhook_dispatch():
    cfg = get_webhook_config()
    if not cfg["webhook_url"]:
        raise HTTPException(status_code=400, detail="No CRM Webhook URL configured. Please enter a URL first.")

    sample_lead = {
        "title": "Sample Premier Dental Clinic",
        "category": "Dentist",
        "phone": "+1 305-555-0199",
        "website": "https://sampledentalclinic.com",
        "has_website": True,
        "emails": ["contact@sampledentalclinic.com"],
        "socials": {"instagram": "https://instagram.com/sampledental"},
        "rating": 4.9,
        "reviews_count": 210,
        "address": "777 Brickell Ave, Miami, FL 33131",
        "enrichment_status": "enriched"
    }

    test_payload = {
        "event": "test.ping",
        "timestamp": datetime.utcnow().isoformat(),
        "source": "Pin Finder Test Dispatcher",
        "data": {
            "message": "Webhook verified successfully from Pin Finder!",
            "sample_lead": sample_lead
        }
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(cfg["webhook_url"], json=test_payload)
            return {
                "message": "Test webhook dispatched successfully!",
                "status_code": resp.status_code,
                "response_body": resp.text[:200]
            }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to webhook URL: {str(e)}")

# --- INBOUND EXTERNAL API (REMOTE CONTROL FROM CRM / ZAPIER) ---
@app.post("/api/v1/external/jobs", tags=["External Inbound API"])
def trigger_external_job(req: ExternalJobRequest, bg: BackgroundTasks, key_info: Dict[str, Any] = Depends(get_api_key_auth)):
    """
    Allow external CRMs, Zapier, Make, or internal scripts to remotely launch a scrape.
    Protected by header: X-API-Key: pf_live_...
    """
    job_id = create_job(req.niche, req.location, req.target_count)
    bg.add_task(run_scraper_task, job_id, req.niche, req.location, req.target_count, req.webhook_url)
    return {
        "success": True,
        "message": "Scrape task triggered remotely via API",
        "job_id": job_id,
        "authorized_by": key_info["username"],
        "status": "pending",
        "niche": req.niche,
        "location": req.location,
        "target_count": req.target_count,
        "status_check_url": f"/api/v1/external/jobs/{job_id}"
    }

@app.get("/api/v1/external/jobs/{job_id}", tags=["External Inbound API"])
def get_external_job_status(job_id: int, key_info: Dict[str, Any] = Depends(get_api_key_auth)):
    """Fetch status and leads of a job using an API Key."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    leads = list_leads(job_id=job_id)
    return {
        "job": job,
        "total_leads": len(leads),
        "leads": leads
    }

# --- MOUNT FRONTEND WEB APPLICATION ---
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
