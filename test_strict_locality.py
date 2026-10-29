import asyncio
import httpx
from scraper import GoogleMapsScraper, is_locality_match, extract_coords_from_url, haversine_distance
from database import init_db, get_job, list_leads
from main import app

def safe_print(*args):
    text = " ".join(str(a) for a in args)
    print(text.encode("ascii", errors="replace").decode("ascii"))

async def run_strict_locality_tests():
    safe_print("=" * 65)
    safe_print("TESTING STRICT HYPER-LOCAL FILTER: SAYYED NAGAR, HADAPSAR")
    safe_print("=" * 65)

    init_db()

    # -------------------------------------------------------------
    # TEST 1: Unit Level Verification of Locality Filtering Logic
    # -------------------------------------------------------------
    safe_print("\n[TEST 1] Unit Test: Locality & Geofence Filter Verification...")
    
    target_center = (18.4998, 73.9248) # Sayyed Nagar, Hadapsar, Pune

    # Known sample clinics extracted in previous run
    test_leads = [
        {
            "name": "Dr Nitin Sapat's Shri Sai (Satavwadi)",
            "item": {
                "title": "Dr Nitin Sapat's Shri Sai",
                "address": "Pune Saswad Road, Satavwadi, Hadapsar, Pune",
                "maps_url": "https://www.google.com/maps/place/.../!3d18.4955664!4d73.9454677"
            },
            "should_match": False
        },
        {
            "name": "Avhad Dental (Solapur Road)",
            "item": {
                "title": "Avhad Dental & Multi Speciality Clinic",
                "address": "1st Floor, Ratan Park, main, Pune - Solapur Rd",
                "maps_url": "https://www.google.com/maps/place/.../!3d18.5005217!4d73.9437661"
            },
            "should_match": False
        },
        {
            "name": "Dr Pharande (Utkarshnagar)",
            "item": {
                "title": "Dr Pharande's Dental Clinic and Implant Centre",
                "address": "Utkarshnagar, Sant Tukaram Palkhi Visava, Solapur Rd",
                "maps_url": "https://www.google.com/maps/place/.../!3d18.5002604!4d73.9402527"
            },
            "should_match": False
        },
        {
            "name": "Dr. Kunal Pawar (Vaibhav Theatre)",
            "item": {
                "title": "Dr. Kunal Pawar's Dental Clinic",
                "address": "4th floor, City Center, 412, Pune - Solapur Rd, opposite vaibhav theatre",
                "maps_url": "https://www.google.com/maps/place/.../!3d18.5020511!4d73.9298797"
            },
            "should_match": False # Outside Sayyed Nagar
        },
        {
            "name": "Genuine Sayyed Nagar Clinic (Hypothetical Match)",
            "item": {
                "title": "Sayyed Nagar Dental Care",
                "address": "Galli No. 12, Sayyed Nagar, Hadapsar, Pune 411028",
                "maps_url": "https://www.google.com/maps/place/.../!3d18.4997!4d73.9250"
            },
            "should_match": True # Exact match
        }
    ]

    for t in test_leads:
        match, reason = is_locality_match(
            item=t["item"],
            card_text=t["item"]["address"],
            strict_locality="Sayyed Nagar",
            target_coords=target_center,
            max_radius_km=0.5
        )
        assert match == t["should_match"], f"Expected {t['should_match']} for {t['name']} but got {match}: {reason}"
        status_label = "[OK] ACCEPTED" if match else "[OK] REJECTED (OUT-OF-BOUNDS)"
        safe_print(f"  {status_label}: {t['name']} -> {reason}")

    safe_print("  [OK] Unit filter successfully isolated Sayyed Nagar and rejected all Satavwadi/Solapur Rd clinics!")

    # -------------------------------------------------------------
    # TEST 2: Live Scraper Execution with Strict Locality Filter
    # -------------------------------------------------------------
    safe_print("\n[TEST 2] Live Scraper Test: Running 'Dentist in Sayyed Nagar, Hadapsar'...")
    safe_print("  Enforcing strict_locality='Sayyed Nagar' with max_radius_km=0.8 km...")

    scraper = GoogleMapsScraper(headless=True)
    leads = await scraper.scrape(
        niche="Dentist",
        location="Sayyed Nagar, Hadapsar",
        limit=10,
        strict_locality="Sayyed Nagar",
        max_radius_km=0.8
    )

    safe_print(f"\n  Scrape Results Count: {len(leads)}")
    for idx, l in enumerate(leads, 1):
        safe_print(f"  [{idx}] {l['title']}")
        safe_print(f"      Address: {l['address'] or 'N/A'}")
        safe_print(f"      URL: {l['maps_url'][:75]}...")

    # Verify that NONE of the returned leads contain forbidden out-of-boundary locations
    forbidden_zones = ["satavwadi", "solapur rd", "solapur road", "utkarshnagar", "malwadi", "gadital"]
    for l in leads:
        addr = (l.get("address") or "").lower()
        for zone in forbidden_zones:
            assert zone not in addr, f"Violation: Found out-of-bounds lead in '{zone}': {l['title']} ({l['address']})"

    safe_print("  [OK] Verified zero leads from Satavwadi, Solapur Road, or outside zones slipped into results!")

    # -------------------------------------------------------------
    # TEST 3: Full API Integration & DB Schema Persistence
    # -------------------------------------------------------------
    safe_print("\n[TEST 3] Testing API Job Creation with Strict Locality Parameters...")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post("/api/v1/jobs", json={
            "niche": "Dentist",
            "location": "Sayyed Nagar, Hadapsar",
            "target_count": 5,
            "strict_locality": "Sayyed Nagar",
            "max_radius_km": 0.8
        })
        assert res.status_code == 200, f"Failed API job create: {res.text}"
        data = res.json()
        job_id = data["job_id"]
        safe_print(f"  [OK] Job created via API with ID #{job_id}")

        # Check DB persistence
        job_row = get_job(job_id)
        assert job_row is not None
        assert job_row["strict_locality"] == "Sayyed Nagar"
        assert job_row["max_radius_km"] == 0.8
        safe_print(f"  [OK] Database verified: strict_locality='{job_row['strict_locality']}', max_radius_km={job_row['max_radius_km']}")

    safe_print("\n" + "=" * 65)
    safe_print("ALL STRICT HYPER-LOCAL FILTER TESTS PASSED!")
    safe_print("=" * 65 + "\n")

if __name__ == "__main__":
    asyncio.run(run_strict_locality_tests())
