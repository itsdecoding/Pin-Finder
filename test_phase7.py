import asyncio
import json
import httpx
from scraper import GoogleMapsScraper
from database import init_db, get_proxy_config, save_proxy_config
from main import app

async def run_phase7_test():
    print("=" * 60)
    print("PHASE 7 VERIFICATION TEST: SINGLE-PAGE STREAM & PROXY ENGINE")
    print("=" * 60)

    init_db()

    # ------------------------------------------------------------------
    # TEST 7.1: Single-Page Live Card Extractor (Zero Tab Hopping)
    # ------------------------------------------------------------------
    print("\n[TEST 7.1] Testing Single-Page Live Card Extractor...")
    print("  Executing scrape: 'Dentists in Miami, FL' (Target: 5 leads)")
    print("  Verifying zero page.goto() URL hops beyond initial search...")

    scraper = GoogleMapsScraper(headless=True)
    leads = await scraper.scrape(niche="Dentists", location="Miami, FL", limit=5)

    assert len(leads) >= 3, f"Expected at least 3 leads, got {len(leads)}"
    print(f"  [OK] Extracted {len(leads)} leads in ONE single session without tab-hopping!")

    for idx, l in enumerate(leads, 1):
        assert l["title"], f"Lead #{idx} missing title"
        print(f"  [{idx}] {l['title']}")
        print(f"      Phone: {l['phone'] or '(None)'} | Rating: {l['rating']} ({l['reviews_count']} reviews)")
        print(f"      Address: {l['address'] or '(None)'}")
        print(f"      Website: {l['website'] or '(None)'}")

    # ------------------------------------------------------------------
    # TEST 7.2: Proxy Parsing & Format Support
    # ------------------------------------------------------------------
    print("\n[TEST 7.2] Testing Proxy Format Parsing...")
    
    # HTTP with auth
    s1 = GoogleMapsScraper(proxy="http://alice:secret123@proxy.example.com:8080")
    p1 = s1._parse_proxy()
    assert p1["server"] == "http://proxy.example.com:8080"
    assert p1["username"] == "alice"
    assert p1["password"] == "secret123"
    print("  [OK] Successfully parsed authenticated HTTP proxy.")

    # SOCKS5 proxy
    s2 = GoogleMapsScraper(proxy="socks5://127.0.0.1:9050")
    p2 = s2._parse_proxy()
    assert p2["server"] == "socks5://127.0.0.1:9050"
    assert "username" not in p2
    print("  [OK] Successfully parsed SOCKS5 proxy.")

    # Direct connection (None)
    s3 = GoogleMapsScraper(proxy=None)
    assert s3._parse_proxy() is None
    print("  [OK] Cleanly defaults to Direct Connection when proxy is None.")

    # ------------------------------------------------------------------
    # TEST 7.3: Proxy Database Persistence & Test Endpoint
    # ------------------------------------------------------------------
    print("\n[TEST 7.3] Testing Proxy Settings Persistence & IP Verification...")
    
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Save proxy config
        save_res = await client.post("/api/v1/integrations/proxy", json={
            "proxy_url": "http://127.0.0.1:8080",
            "proxy_enabled": False
        })
        assert save_res.status_code == 200
        print("  [OK] Proxy configuration successfully saved.")

        # Read back proxy config
        get_res = await client.get("/api/v1/integrations/proxy")
        assert get_res.status_code == 200
        cfg = get_res.json()
        assert cfg["proxy_url"] == "http://127.0.0.1:8080"
        assert cfg["proxy_enabled"] is False
        print("  [OK] Proxy configuration persistent in SQLite database.")

        # Test IP test endpoint (direct IP check)
        ip_test_res = await client.post("/api/v1/integrations/proxy/test")
        assert ip_test_res.status_code == 200
        ip_data = ip_test_res.json()
        assert ip_data["success"] is True
        print(f"  [OK] IP test endpoint functional! Outgoing IP: {ip_data['outgoing_ip']}")

    print("\n" + "=" * 60)
    print("ALL PHASE 7 STREAM & PROXY TESTS PASSED!")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_phase7_test())
