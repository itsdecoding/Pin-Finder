import asyncio
import json
import logging
import re
import urllib.parse
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, Page, Locator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("PinFinder")

PHONE_REGEX = re.compile(r"(\+?\d{1,3}[\s\-\.]?)?(\(?\d{2,4}\)?[\s\-\.]?)?\d{3,4}[\s\-\.]?\d{3,4}")

class GoogleMapsScraper:
    def __init__(self, headless: bool = True, proxy: Optional[str] = None):
        self.headless = headless
        self.proxy = proxy

    def _parse_proxy(self) -> Optional[Dict[str, str]]:
        """Format proxy string into Playwright proxy dictionary."""
        if not self.proxy:
            return None
        proxy_str = self.proxy.strip()
        if not proxy_str:
            return None

        # Check for user:pass@host:port
        parsed = urllib.parse.urlparse(proxy_str)
        if not parsed.scheme:
            # Default to http:// if no scheme provided
            parsed = urllib.parse.urlparse("http://" + proxy_str)

        server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        cfg = {"server": server}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg

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
                    await asyncio.sleep(0.5)
                    break
        except Exception:
            pass

    async def _check_captcha(self, page: Page):
        """Detect Google's Unusual traffic / CAPTCHA challenge and abort immediately."""
        content = (await page.content()).lower()
        if "sorry/index" in page.url or "unusual traffic" in content or "recaptcha" in content:
            logger.error("🚨 GOOGLE CAPTCHA / BOT CHALLENGE DETECTED! Aborting to protect IP.")
            raise RuntimeError("GOOGLE_CAPTCHA_DETECTED: Unusual traffic flag encountered.")

    async def _extract_card_info(self, card: Locator, page: Page) -> Dict[str, Any]:
        """Extract all details from a single feed listing card directly on the page."""
        item = {
            "title": "",
            "category": "",
            "rating": None,
            "reviews_count": None,
            "address": "",
            "website": "",
            "phone": "",
            "maps_url": ""
        }

        try:
            # 1. Business Title & URL
            link_elem = card.locator("a.hfpxzc").first
            if await link_elem.count() > 0:
                item["maps_url"] = (await link_elem.get_attribute("href") or "").split("?")[0]
                item["title"] = (await link_elem.get_attribute("aria-label") or "").strip()

            if not item["title"]:
                title_elem = card.locator(".qBF1Pd").first
                if await title_elem.count() > 0:
                    item["title"] = (await title_elem.inner_text()).strip()

            # 2. Rating & Reviews Count
            rating_elem = card.locator(".MW4etd, span[aria-label*='stars']").first
            if await rating_elem.count() > 0:
                rating_text = await rating_elem.inner_text()
                match = re.search(r"(\d+(\.\d+)?)", rating_text)
                if match:
                    item["rating"] = float(match.group(1))

            reviews_elem = card.locator(".UY7F9, span[aria-label*='reviews']").first
            if await reviews_elem.count() > 0:
                rev_text = await reviews_elem.inner_text()
                rev_match = re.search(r"([\d,]+)", rev_text)
                if rev_match:
                    item["reviews_count"] = int(rev_match.group(1).replace(",", ""))

            # 3. Direct Website / Action Buttons on the card
            web_elem = card.locator("a[data-value='Website'], a.lcr4fd, a[aria-label*='Website']").first
            if await web_elem.count() > 0:
                web_url = await web_elem.get_attribute("href")
                if web_url:
                    item["website"] = web_url.strip()

            # Direct action/booking/external link fallback in card
            if not item["website"]:
                ext_links = await card.locator("a[href^='http']").all()
                for el in ext_links:
                    href = await el.get_attribute("href")
                    if href and "google.com" not in href and "gstatic.com" not in href:
                        item["website"] = href.strip()
                        break

            # 4. Text Snippets (Category, Address & Phone)
            card_text = await card.inner_text()
            lines = [l.strip() for l in card_text.split("\n") if l.strip()]

            # Search for phone in card text
            phone_matches = PHONE_REGEX.findall(card_text)
            for m in phone_matches:
                full_phone = "".join(m).strip()
                if len(re.sub(r"\D", "", full_phone)) >= 10:
                    item["phone"] = full_phone
                    break

            # Parse metadata lines (e.g. "Dentist · 350 NE 24th St")
            for line in lines:
                if "·" in line:
                    parts = [p.strip() for p in line.split("·") if p.strip()]
                    if parts and not item["category"]:
                        # First part before dot is often Category
                        if not re.search(r"^\d", parts[0]) and "star" not in parts[0].lower():
                            item["category"] = parts[0]
                    for p in parts[1:]:
                        if re.search(r"\d+.*(st|ave|blvd|rd|way|dr|suite|#|hwy)", p, re.IGNORECASE):
                            if not item["address"]:
                                item["address"] = p
                        elif PHONE_REGEX.search(p) and not item["phone"]:
                            item["phone"] = p.strip()

            # 5. In-Place Inspection Fallback (Clicks card in same view WITHOUT loading a new page)
            if not item["phone"] or not item["website"] or not item["address"]:
                try:
                    await card.click(timeout=1000)
                    await page.wait_for_timeout(350)  # Brief micro-pause for panel update

                    # Address from side panel
                    if not item["address"]:
                        addr_el = page.locator("button[data-item-id='address'], [aria-label*='Address:']").first
                        if await addr_el.count() > 0:
                            addr_text = await addr_el.get_attribute("aria-label") or await addr_el.inner_text()
                            item["address"] = addr_text.replace("Address:", "").strip()

                    # Phone from side panel
                    if not item["phone"]:
                        phone_el = page.locator("button[data-item-id*='phone'], [aria-label*='Phone:']").first
                        if await phone_el.count() > 0:
                            phone_text = await phone_el.get_attribute("aria-label") or await phone_el.inner_text()
                            item["phone"] = phone_text.replace("Phone:", "").strip()

                    # Website from side panel
                    if not item["website"]:
                        web_el = page.locator("a[data-item-id='authority'], [aria-label*='Website:']").first
                        if await web_el.count() > 0:
                            web_url = await web_el.get_attribute("href")
                            if web_url:
                                item["website"] = web_url.strip()
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Error reading card: {e}")

        return item

    async def scrape(self, niche: str, location: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = f"{niche} in {location}"
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.google.com/maps/search/{encoded_query}"

        proxy_config = self._parse_proxy()
        logger.info(f"Starting Single-Page Stream Scrape: '{query}' (Target: {limit})")
        if proxy_config:
            logger.info(f"Using Proxy: {proxy_config.get('server')}")
        else:
            logger.info("Using Direct Connection (No Proxy)")

        results: List[Dict[str, Any]] = []
        seen_titles = set()

        async with async_playwright() as p:
            browser_kwargs = {
                "headless": self.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ]
            }
            if proxy_config:
                browser_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**browser_kwargs)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            page = await context.new_page()

            try:
                # 1. Load Google Maps ONCE in this single session
                logger.info(f"Opening Single-Page Session: {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                await self._check_captcha(page)
                await self._handle_consent(page)
                await page.wait_for_timeout(2500)
                await self._check_captcha(page)

                feed_selector = "div[role='feed']"
                try:
                    await page.wait_for_selector(feed_selector, timeout=8000)
                    has_feed = True
                except Exception:
                    has_feed = False

                scroll_attempts = 0
                max_scroll_attempts = 20

                # 2. Scroll the feed and parse cards directly from the single page
                while len(results) < limit and scroll_attempts < max_scroll_attempts:
                    cards = await page.locator("div.Nv2PK").all()
                    
                    for card in cards:
                        if len(results) >= limit:
                            break
                        item = await self._extract_card_info(card, page)
                        if item["title"] and item["title"] not in seen_titles:
                            seen_titles.add(item["title"])
                            results.append(item)
                            logger.info(f"[{len(results)}/{limit}] Extracted: {item['title']} | Phone: {item['phone']} | Web: {bool(item['website'])}")

                    if len(results) >= limit:
                        break

                    # Smooth humanized scroll
                    if has_feed:
                        feed = page.locator(feed_selector)
                        await feed.evaluate("node => node.scrollTop += 1200")
                    else:
                        await page.mouse.wheel(0, 1200)

                    await page.wait_for_timeout(1500)
                    scroll_attempts += 1

            except Exception as e:
                logger.error(f"Scraper error: {e}")
                raise e
            finally:
                await browser.close()

        logger.info(f"Single-Page Scrape complete. Collected {len(results)} leads without opening separate tabs.")
        return results

if __name__ == "__main__":
    async def main():
        scraper = GoogleMapsScraper(headless=True)
        leads = await scraper.scrape(niche="Dentists", location="Miami, FL", limit=5)
        print(f"\nExtracted {len(leads)} leads in 1 single session:")
        for l in leads:
            print(f"- {l['title']} | Phone: {l['phone']} | Rating: {l['rating']} | Web: {l['website']}")

    asyncio.run(main())
