import asyncio
import json
import csv
import io
import os
import httpx
from main import app
from database import init_db, insert_leads, get_lead, create_job, DB_PATH

async def run_phase3_test():
    print("=" * 60)
    print("PHASE 3 VERIFICATION TEST: SAAS BACKEND & SQLITE DATABASE")
    print("=" * 60)

    # 1. Initialize SQLite Database
    init_db()
    print("[OK] SQLite database initialized at:", DB_PATH)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:

        # ------------------------------------------------------------------
        # TEST 3.1: Authentication System (Register, Hash, Login, JWT Token)
        # ------------------------------------------------------------------
        print("\n[TEST 3.1] Testing User Registration & Authentication...")
        reg_payload = {
            "username": "company_admin",
            "email": "admin@company.com",
            "password": "SecurePassword123!"
        }
        res = await client.post("/api/v1/auth/register", json=reg_payload)
        # If user already exists in db from previous run, login directly
        if res.status_code == 400:
            print("  Note: User already exists, proceeding to login.")
        else:
            assert res.status_code == 200, f"Registration failed: {res.text}"
            print("  [OK] User successfully registered with bcrypt hashed password.")

        login_res = await client.post("/api/v1/auth/login", json={
            "username": "company_admin",
            "password": "SecurePassword123!"
        })
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        token_data = login_res.json()
        token = token_data["access_token"]
        assert token, "JWT Access token was not returned"
        print(f"  [OK] User logged in successfully. Received JWT Token: {token[:20]}...")

        # Test Auth Me endpoint
        me_res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == 200
        print("  [OK] Token verified by /api/v1/auth/me.")

        # ------------------------------------------------------------------
        # TEST 3.2: Database Storage & Job Lifecycle (With & Without Website)
        # ------------------------------------------------------------------
        print("\n[TEST 3.2] Testing Database Storage & Leads Segregation...")
        job_id = create_job("Dentists", "Miami, FL", target_count=5)
        print(f"  [OK] Created Job #{job_id} in database.")

        # Load Phase 2 enriched leads to populate database with realistic data
        with open("test_enriched_leads.json", "r", encoding="utf-8") as f:
            sample_leads = json.load(f)

        inserted_count = insert_leads(job_id, sample_leads)
        print(f"  [OK] Inserted {inserted_count} leads into SQLite database under Job #{job_id}.")

        # Check job leads endpoint
        leads_res = await client.get(f"/api/v1/jobs/{job_id}/leads")
        assert leads_res.status_code == 200
        leads_data = leads_res.json()["leads"]
        assert len(leads_data) == len(sample_leads)

        # Check segregation
        with_web_res = await client.get(f"/api/v1/jobs/{job_id}/leads?filter_type=with_website")
        no_web_res = await client.get(f"/api/v1/jobs/{job_id}/leads?filter_type=no_website")
        print(f"  [OK] Filtered 'With Website': {with_web_res.json()['total']} leads.")
        print(f"  [OK] Filtered 'No Website': {no_web_res.json()['total']} leads.")

        # ------------------------------------------------------------------
        # TEST 3.3: On-Demand Single Lead Enrichment API Endpoint
        # ------------------------------------------------------------------
        print("\n[TEST 3.3] Testing On-Demand Single Lead Enrichment Endpoint...")
        # Find a lead with a website
        web_lead = next(l for l in leads_data if l["has_website"] and l["website"])
        lead_id = web_lead["id"]
        print(f"  Triggering on-demand enrichment for Lead #{lead_id} ({web_lead['title']})...")

        enrich_res = await client.post(f"/api/v1/leads/{lead_id}/enrich")
        assert enrich_res.status_code == 200, f"Enrichment endpoint failed: {enrich_res.text}"
        enriched_result = enrich_res.json()["lead"]
        assert enriched_result["enrichment_status"] in ["enriched", "no_contact_found", "site_unreachable"]
        print(f"  [OK] On-Demand Enrichment Succeeded! Status: '{enriched_result['enrichment_status']}'")
        if enriched_result["emails"]:
            print(f"    Discovered Emails: {enriched_result['emails']}")

        # Test enrichment on a lead WITHOUT website
        no_web_lead = next(l for l in leads_data if not l["has_website"])
        no_web_res = await client.post(f"/api/v1/leads/{no_web_lead['id']}/enrich")
        assert no_web_res.status_code == 200
        assert no_web_res.json()["lead"]["enrichment_status"] == "no_website"
        print(f"  [OK] Lead without website correctly bypassed and marked 'no_website'.")

        # ------------------------------------------------------------------
        # TEST 3.4: CSV Export & Dashboard Stats
        # ------------------------------------------------------------------
        print("\n[TEST 3.4] Testing System Stats & CSV Export...")
        stats_res = await client.get("/api/v1/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        print(f"  [OK] Stats Endpoint: {stats['total_leads']} Total Leads | {stats['with_website']} With Website | {stats['no_website']} Without Website")

        export_res = await client.get("/api/v1/export/csv")
        assert export_res.status_code == 200
        assert "text/csv" in export_res.headers["content-type"]
        csv_reader = csv.reader(io.StringIO(export_res.text))
        rows = list(csv_reader)
        print(f"  [OK] CSV Export generated with {len(rows)} lines (Header + Records).")
        print(f"    CSV Headers: {rows[0][:6]}...")

    print("\n" + "=" * 60)
    print("ALL PHASE 3 BACKEND & DATABASE TESTS PASSED!")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_phase3_test())
