import asyncio
import os
import httpx
from playwright.async_api import async_playwright
import uvicorn
import multiprocessing
import time

def start_server():
    from main import app
    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="warning")

async def run_phase4_test():
    print("=" * 60)
    print("PHASE 4 VERIFICATION TEST: COMPLETE SAAS WEB INTERFACE")
    print("=" * 60)

    # 1. Start test server on port 8899
    proc = multiprocessing.Process(target=start_server, daemon=True)
    proc.start()
    time.sleep(2)  # Wait for uvicorn to bind

    base_url = "http://127.0.0.1:8899"

    try:
        # TEST 4.1: Root endpoint serving UI
        print("\n[TEST 4.1] Verifying Static Web App Hosting...")
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{base_url}/")
            assert res.status_code == 200, f"Root did not return 200: {res.status_code}"
            assert "Pin Finder" in res.text, "Page HTML missing Pin Finder brand"
            assert "view-dashboard" in res.text, "Dashboard container missing"
            assert "view-leads" in res.text, "Leads container missing"
            print("  [OK] Web Application successfully served at http://127.0.0.1:8899/")

        # TEST 4.2: End-to-End Headless Browser Walkthrough via Playwright
        print("\n[TEST 4.2] Running End-to-End Browser UI Walkthrough...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # Navigate to web app
            await page.goto(base_url, wait_until="networkidle")
            print("  [OK] Page loaded in Chromium browser.")

            # Check if Auth modal appears
            auth_modal = page.locator("#auth-modal")
            if await auth_modal.is_visible():
                print("  [OK] Auth Modal visible. Performing login...")
                await page.fill("#auth-username", "company_admin")
                await page.fill("#auth-password", "SecurePassword123!")
                await page.click("#auth-submit-btn")
                await page.wait_for_selector("#auth-modal", state="hidden", timeout=5000)
                print("  [OK] Login successful and auth modal dismissed.")

            # Check Dashboard statistics
            await page.wait_for_timeout(1000)
            total_stat = await page.locator("#stat-total-leads").inner_text()
            print(f"  [OK] Dashboard rendered live stats: Total Leads = {total_stat}")

            # Switch to Leads Library
            print("  Navigating to Leads Library...")
            await page.click("#nav-leads")
            await page.wait_for_timeout(1000)

            # Check Segregated Tabs
            assert await page.locator("#tab-with_website").is_visible()
            assert await page.locator("#tab-no_website").is_visible()
            print("  [OK] Segregation tabs ('With Website' & 'No Website') rendered.")

            # Filter 'With Website'
            await page.click("#tab-with_website")
            await page.wait_for_timeout(1000)
            rows = await page.locator("#leads-table-body tr").count()
            print(f"  [OK] Filtered 'With Website' view shows {rows} businesses.")

            # Filter 'No Website'
            await page.click("#tab-no_website")
            await page.wait_for_timeout(1000)
            no_web_rows = await page.locator("#leads-table-body tr").count()
            print(f"  [OK] Filtered 'No Website' view shows {no_web_rows} businesses.")

            # Check on-demand action button presence
            await page.click("#tab-all")
            await page.wait_for_timeout(1000)
            enrich_btn = page.locator("button[id^='btn-enrich-']").first
            if await enrich_btn.count() > 0:
                btn_text = await enrich_btn.inner_text()
                safe_text = btn_text.encode('ascii', 'ignore').decode().strip()
                print(f"  [OK] On-Demand Action Button verified in table: '{safe_text}'")

            # Check New Scrape Form
            await page.click("#nav-new-scrape")
            await page.wait_for_timeout(500)
            assert await page.locator("#form-niche").is_visible()
            assert await page.locator("#form-location").is_visible()
            print("  [OK] New Scrape campaign creator rendered and responsive.")

            # Check Activities Screen
            await page.click("#nav-activities")
            await page.wait_for_timeout(500)
            assert await page.locator("#activities-job-list").is_visible()
            print("  [OK] Activities & Task Manager rendered.")

            await browser.close()

        print("\n" + "=" * 60)
        print("ALL PHASE 4 SAAS WEB INTERFACE TESTS PASSED!")
        print("=" * 60 + "\n")

    finally:
        proc.terminate()
        proc.join()

if __name__ == "__main__":
    asyncio.run(run_phase4_test())
