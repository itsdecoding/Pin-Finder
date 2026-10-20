import asyncio
import json
import logging
import re
import urllib.parse
from typing import List, Dict, Any
from playwright.async_api import async_playwright, Page

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("PinFinder")

class GoogleMapsScraper:
    def __init__(self, headless: bool = True):
        self.headless = headless

    async def _handle_consent(self, page: Page):
        """Dismiss Google Cookie Consent popups if they appear."""
        try:
            consent_buttons = [
                "button[aria-label*='Accept all']",
                "button:has-text('Accept all')",
                "button:has-text('I agree')",
                "form:last-child button"
            ]
            for btn_sel in consent_buttons:
                btn = page.locator(btn_sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await asyncio.sleep(1)
                    break
        except Exception:
            pass

    async def _extract_place_details(self, page: Page, url: str) -> Dict[str, Any]:
        """Visit a specific place URL and extract full details."""
        data = {
            "title": "",
            "category": "",
            "rating": None,
            "reviews_count": None,
            "address": "",
            "website": "",
            "phone": "",
            "maps_url": url,
        }

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("h1", timeout=10000)

            # Business Name
            h1 = page.locator("h1").first
            if await h1.count() > 0:
                data["title"] = (await h1.inner_text()).strip()

            # Category
            cat_elem = page.locator("button.DkEaL, button[jsaction*='category']").first
            if await cat_elem.count() > 0:
                data["category"] = (await cat_elem.inner_text()).strip()

            # Rating & Reviews
            rating_elem = page.locator(".F7nice span, .F7nice, span.fontDisplayLarge, span[aria-label*='stars']").first
            if await rating_elem.count() > 0:
                rating_text = await rating_elem.inner_text()
                match = re.search(r"(\d+(\.\d+)?)", rating_text)
                if match:
                    data["rating"] = float(match.group(1))

            reviews_elem = page.locator("button[aria-label*='reviews'], span[aria-label*='reviews']").first
            if await reviews_elem.count() > 0:
                rev_text = await reviews_elem.get_attribute("aria-label") or await reviews_elem.inner_text()
                rev_match = re.search(r"([\d,]+)\s+reviews?", rev_text)
                if rev_match:
                    data["reviews_count"] = int(rev_match.group(1).replace(",", ""))

            # Address
            addr_elem = page.locator("button[data-item-id='address'], [aria-label*='Address:']").first
            if await addr_elem.count() > 0:
                addr_text = await addr_elem.get_attribute("aria-label") or await addr_elem.inner_text()
                data["address"] = addr_text.replace("Address:", "").strip()

            # Website
            web_elem = page.locator("a[data-item-id='authority'], [aria-label*='Website:']").first
            if await web_elem.count() > 0:
                web_url = await web_elem.get_attribute("href")
                if web_url:
                    data["website"] = web_url.strip()

            # Phone
            phone_elem = page.locator("button[data-item-id*='phone'], [aria-label*='Phone:']").first
            if await phone_elem.count() > 0:
                phone_text = await phone_elem.get_attribute("aria-label") or await phone_elem.inner_text()
                data["phone"] = phone_text.replace("Phone:", "").strip()

        except Exception as e:
            logger.warning(f"Error extracting details from {url}: {e}")

        return data

    async def scrape(self, niche: str, location: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = f"{niche} in {location}"
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.google.com/maps/search/{encoded_query}"

        logger.info(f"Starting scrape for: '{query}' (Target: {limit} leads)")

        results = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ]
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )

            # Mask webdriver detection
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            page = await context.new_page()

            try:
                logger.info(f"Navigating to {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                await self._handle_consent(page)

                # Wait for feed or place links
                await page.wait_for_timeout(3000)

                # Check if search redirected directly to a single business
                if "/maps/place/" in page.url:
                    logger.info("Single place detected direct landing.")
                    data = await self._extract_place_details(page, page.url)
                    results.append(data)
                    await browser.close()
                    return results

                # Collect place URLs
                place_urls = set()
                feed_selector = "div[role='feed']"

                try:
                    await page.wait_for_selector(feed_selector, timeout=10000)
                    has_feed = True
                except Exception:
                    has_feed = False

                scroll_attempts = 0
                max_scroll_attempts = 25

                while len(place_urls) < limit and scroll_attempts < max_scroll_attempts:
                    links = await page.locator("a[href*='/maps/place/']").all()
                    for link in links:
                        href = await link.get_attribute("href")
                        if href and "/maps/place/" in href:
                            clean_url = href.split("?")[0]
                            place_urls.add(clean_url)
                            if len(place_urls) >= limit:
                                break

                    logger.info(f"Found {len(place_urls)}/{limit} places. Scrolling for more...")

                    if len(place_urls) >= limit:
                        break

                    # Scroll the feed container
                    if has_feed:
                        feed = page.locator(feed_selector)
                        await feed.evaluate("node => node.scrollTop += 1500")
                    else:
                        await page.mouse.wheel(0, 1500)

                    await page.wait_for_timeout(2000)
                    scroll_attempts += 1

                urls_to_scrape = list(place_urls)[:limit]
                logger.info(f"Target URLs collected: {len(urls_to_scrape)}. Now extracting detailed information...")

                # Extract details for each place
                for idx, url in enumerate(urls_to_scrape, start=1):
                    logger.info(f"[{idx}/{len(urls_to_scrape)}] Extracting: {url}")
                    item = await self._extract_place_details(page, url)
                    if item["title"]:
                        results.append(item)
                    await page.wait_for_timeout(1000)

            except Exception as e:
                logger.error(f"Scraper error: {e}")
            finally:
                await browser.close()

        logger.info(f"Scraping completed. Extracted {len(results)} valid leads.")
        return results

if __name__ == "__main__":
    async def main():
        scraper = GoogleMapsScraper(headless=True)
        leads = await scraper.scrape(niche="Dentists", location="Miami, FL", limit=10)
        output_file = "test_leads.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, ensure_ascii=False)
        print(f"\n--- SUCCESS ---")
        print(f"Saved {len(leads)} leads to {output_file}")
        for lead in leads:
            print(f"- {lead['title']} | Phone: {lead['phone']} | Website: {lead['website']} | Rating: {lead['rating']}")

    asyncio.run(main())
