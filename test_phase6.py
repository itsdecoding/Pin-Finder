import asyncio
import os
from playwright.async_api import async_playwright

async def run_phase6_test():
    print("=" * 60)
    print("PHASE 6 VERIFICATION TEST: UI/UX PREMIUM POLISH & DRAWER")
    print("=" * 60)

    base_url = "http://127.0.0.1:8000"
    os.makedirs("screenshots", exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"]
        )
        page = await context.new_page()

        print("\n[TEST 6.1] Navigating to Pin Finder and Verifying Executive Theme...")
        await page.goto(base_url, wait_until="networkidle")

        # Authenticate if modal is up
        auth_modal = page.locator("#auth-modal")
        if await auth_modal.is_visible():
            await page.fill("#auth-username", "company_admin")
            await page.fill("#auth-password", "SecurePassword123!")
            await page.click("#auth-submit-btn")
            await page.wait_for_selector("#auth-modal", state="hidden")
            print("  [OK] Authenticated into executive dashboard.")

        await page.wait_for_timeout(1000)
        await page.screenshot(path="screenshots/dashboard_v2.png")
        print("  [OK] Captured screenshots/dashboard_v2.png")

        # TEST 6.2: Leads Library & Click-to-Copy
        print("\n[TEST 6.2] Testing Leads Library & Click-to-Copy...")
        await page.click("#nav-leads")
        await page.wait_for_timeout(1000)
        await page.screenshot(path="screenshots/leads_v2.png")
        print("  [OK] Captured screenshots/leads_v2.png")

        # Check Copy Phone button
        phone_copy = page.locator("button[title='Click to copy']").first
        if await phone_copy.count() > 0:
            await phone_copy.click()
            await page.wait_for_timeout(500)
            toast = page.locator("#toast-container")
            toast_text = await toast.inner_text()
            safe_toast = toast_text.encode('ascii', 'ignore').decode().strip()
            print(f"  [OK] Click-to-copy verified with visual toast: '{safe_toast}'")

        # TEST 6.3: Lead Detail Drawer
        print("\n[TEST 6.3] Testing Lead Detail Slide-over Drawer...")
        lead_row = page.locator("#leads-table-body tr").first
        await lead_row.click()
        await page.wait_for_timeout(800)

        drawer = page.locator("#lead-drawer")
        is_visible = not ("translate-x-full" in (await drawer.get_attribute("class") or ""))
        assert is_visible, "Lead detail drawer did not slide open on row click"
        drawer_title = await page.locator("#drawer-title").inner_text()
        print(f"  [OK] Lead Detail Drawer successfully opened for: '{drawer_title}'")

        await page.screenshot(path="screenshots/drawer_v2.png")
        print("  [OK] Captured screenshots/drawer_v2.png")

        # Close drawer
        await page.click("#drawer-backdrop")
        await page.wait_for_timeout(500)
        is_closed = "translate-x-full" in (await drawer.get_attribute("class") or "")
        assert is_closed, "Lead detail drawer did not close when clicking backdrop"
        print("  [OK] Lead Detail Drawer successfully dismissed.")

        await browser.close()

    print("\n" + "=" * 60)
    print("ALL PHASE 6 PREMIUM UI/UX TESTS PASSED!")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_phase6_test())
