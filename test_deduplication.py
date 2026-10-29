import asyncio
import httpx
from scraper import GoogleMapsScraper
from database import init_db, get_job, insert_leads, get_existing_lead_identifiers, list_leads, get_connection
from main import app

def safe_print(*args):
    text = " ".join(str(a) for a in args)
    print(text.encode("ascii", errors="replace").decode("ascii"))

async def test_deduplication():
    safe_print("=" * 65)
    safe_print("TESTING INCREMENTAL DEDUPLICATION & SKIP PREVIOUSLY SCRAPED LEADS")
    safe_print("=" * 65)

    init_db()

    # -------------------------------------------------------------
    # TEST 1: Database Identifier Extraction & Insert Protection
    # -------------------------------------------------------------
    safe_print("\n[TEST 1] Testing Database Pre-loading of Existing Identifiers...")
    
    # Insert known seed lead into database under a dummy job
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO jobs (niche, location, target_count, status) VALUES ('Test', 'Test City', 5, 'completed');")
    test_job_id = cur.lastrowid
    conn.commit()
    conn.close()

    import time
    ts = int(time.time())

    seed_leads = [
        {
            "title": f"Seed Dental Miami Alpha {ts}",
            "category": "Dentist",
            "rating": 5.0,
            "reviews_count": 100,
            "address": "100 Biscayne Blvd, Miami, FL",
            "phone": f"305-555-{ts % 10000:04d}",
            "website": "https://seedalpha.com",
            "maps_url": f"https://www.google.com/maps/place/Seed+Dental+Miami+Alpha+{ts}/data=!4m7!3m6"
        },
        {
            "title": f"Seed Dental Miami Beta {ts}",
            "category": "Dentist",
            "rating": 4.8,
            "reviews_count": 50,
            "address": "200 Biscayne Blvd, Miami, FL",
            "phone": f"305-555-{(ts+1) % 10000:04d}",
            "website": "https://seedbeta.com",
            "maps_url": f"https://www.google.com/maps/place/Seed+Dental+Miami+Beta+{ts}/data=!4m7!3m6"
        }
    ]

    inserted = insert_leads(test_job_id, seed_leads, check_duplicates=True)
    assert inserted == 2, f"Expected 2 inserted, got {inserted}"
    safe_print(f"  [OK] Inserted {inserted} seed leads.")

    # Try inserting duplicate seeds again
    dup_inserted = insert_leads(test_job_id, seed_leads, check_duplicates=True)
    assert dup_inserted == 0, f"Expected 0 duplicate leads inserted, got {dup_inserted}"
    safe_print("  [OK] Database insert protection cleanly blocked duplicate records!")

    # Verify get_existing_lead_identifiers loads them
    identifiers = get_existing_lead_identifiers()
    assert seed_leads[0]["maps_url"].lower() in identifiers["urls"]
    assert seed_leads[0]["title"].lower() in identifiers["titles"]
    safe_print("  [OK] Existing identifiers accurately pre-loaded into memory sets.")

    # -------------------------------------------------------------
    # TEST 2: Live Scraper Sequential Runs (Day 1 vs Day 2 Simulation)
    # -------------------------------------------------------------
    safe_print("\n[TEST 2] Testing Live Incremental Scraping...")
    safe_print("  Step A: Scrape Batch 1 (3 leads) for 'Dentists in Miami, FL'...")
    scraper = GoogleMapsScraper(headless=True)
    batch1 = await scraper.scrape(
        niche="Dentists",
        location="Miami, FL",
        limit=3,
        skip_duplicates=False # Initial fresh scrape
    )
    assert len(batch1) == 3, f"Expected 3 leads in batch 1, got {len(batch1)}"
    safe_print("  Batch 1 Leads Collected:")
    for b in batch1:
        safe_print(f"    - {b['title']} | {b['maps_url'][:60]}...")

    # Insert batch 1 into DB
    insert_leads(test_job_id, batch1, check_duplicates=True)
    db_identifiers = get_existing_lead_identifiers()

    safe_print("\n  Step B: Run Scrape Batch 2 with skip_duplicates=True (Simulating Day 2 run)...")
    safe_print("  Verifying scraper skips all Batch 1 leads and scrolls to collect fresh ones...")
    batch2 = await scraper.scrape(
        niche="Dentists",
        location="Miami, FL",
        limit=3,
        existing_identifiers=db_identifiers,
        skip_duplicates=True
    )
    assert len(batch2) >= 2, f"Expected at least 2 fresh leads in batch 2, got {len(batch2)}"
    safe_print(f"  Batch 2 Fresh Leads Collected ({len(batch2)} leads):")
    for b in batch2:
        safe_print(f"    - {b['title']} | {b['maps_url'][:60]}...")

    # Ensure zero overlap between Batch 1 and Batch 2
    b1_titles = {b["title"].lower().strip() for b in batch1}
    b2_titles = {b["title"].lower().strip() for b in batch2}
    intersection = b1_titles.intersection(b2_titles)
    assert len(intersection) == 0, f"Found duplicate leads across runs: {intersection}"
    safe_print(f"  [OK] ZERO DUPLICATE LEADS! Batch 2 is 100% distinct from Batch 1.")

    # -------------------------------------------------------------
    # TEST 3: API Endpoint Integration
    # -------------------------------------------------------------
    safe_print("\n[TEST 3] Testing API Job Creation with skip_duplicates parameter...")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post("/api/v1/jobs", json={
            "niche": "Dentists",
            "location": "Miami, FL",
            "target_count": 5,
            "skip_duplicates": True
        })
        assert res.status_code == 200, f"API error: {res.text}"
        data = res.json()
        job_id = data["job_id"]
        job_row = get_job(job_id)
        assert job_row["skip_duplicates"] == 1
        safe_print(f"  [OK] Job #{job_id} successfully stored in DB with skip_duplicates=1.")

    safe_print("\n" + "=" * 65)
    safe_print("ALL INCREMENTAL DEDUPLICATION TESTS PASSED!")
    safe_print("=" * 65 + "\n")

if __name__ == "__main__":
    asyncio.run(test_deduplication())
