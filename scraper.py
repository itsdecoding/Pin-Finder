import asyncio
import json
import logging
import math
import re
import urllib.parse
from typing import List, Dict, Any, Optional, Set
from playwright.async_api import async_playwright, Page, Locator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("PinFinder")

PHONE_REGEX = re.compile(r"(\+?\d{1,3}[\s\-\.]?)?(\(?\d{2,4}\)?[\s\-\.]?)?\d{3,4}[\s\-\.]?\d{3,4}")

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in kilometers between two points."""
    R = 6371.0 # Earth's radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def extract_coords_from_url(url: str) -> Optional[tuple[float, float]]:
    """Extract (lat, lng) from Google Maps URL if present."""
    if not url:
        return None
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return None

def is_locality_match(
    item: Dict[str, Any],
    card_text: str,
    strict_locality: Optional[str] = None,
    target_coords: Optional[tuple[float, float]] = None,
    max_radius_km: Optional[float] = None
) -> tuple[bool, str]:
    """
    Check if a scraped listing belongs strictly to the requested micro-locality.
    Returns (is_match, reason).
    """
    if not strict_locality and (max_radius_km is None or max_radius_km <= 0):
        return True, "No locality filter applied"

    strict_lower = strict_locality.lower().strip() if strict_locality else ""
    
    # Common alternate spellings
    variants = set()
    if strict_lower:
        variants.add(strict_lower)
        if "sayyed" in strict_lower:
            variants.add(strict_lower.replace("sayyed", "sayed"))
            variants.add(strict_lower.replace("sayyed", "sayyad"))
        elif "sayed" in strict_lower:
            variants.add(strict_lower.replace("sayed", "sayyed"))
            variants.add(strict_lower.replace("sayed", "sayyad"))

    address_lower = (item.get("address") or "").lower()
    title_lower = (item.get("title") or "").lower()
    card_lower = card_text.lower()

    # 1. Direct text match in address, title, or card snippet
    if variants:
        for v in variants:
            if v in address_lower or v in title_lower or v in card_lower:
                return True, f"Keyword match: '{v}' in listing"

    # 2. Geofence radius check if coordinates are present
    coords = extract_coords_from_url(item.get("maps_url", ""))
    if coords and target_coords and max_radius_km is not None and max_radius_km > 0:
        dist = haversine_distance(target_coords[0], target_coords[1], coords[0], coords[1])
        if dist <= max_radius_km:
            return True, f"Within {dist:.2f} km of target (max: {max_radius_km} km)"
        else:
            return False, f"Out of radius: {dist:.2f} km > {max_radius_km} km limit"

    # If strict locality was requested and neither text nor geofence matched, reject
    if strict_locality:
        return False, f"Address '{item.get('address') or 'N/A'}' does not match locality '{strict_locality}'"

    return True, "Locality verification passed"

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

    async def scrape(
        self,
        niche: str,
        location: str,
        limit: int = 10,
        strict_locality: Optional[str] = None,
        max_radius_km: Optional[float] = None,
        existing_identifiers: Optional[Dict[str, Set[str]]] = None,
        skip_duplicates: bool = True
    ) -> List[Dict[str, Any]]:
        # Formulate search query with phrase emphasis if strict locality specified
        if strict_locality and strict_locality.lower() not in location.lower():
            query = f'{niche} in "{strict_locality}", {location}'
        elif strict_locality and f'"{strict_locality}"' not in location:
            # Wrap the strict locality in quotes for Google Maps ranking
            pattern = re.compile(re.escape(strict_locality), re.IGNORECASE)
            quoted_location = pattern.sub(f'"{strict_locality}"', location, count=1)
            query = f"{niche} in {quoted_location}"
        else:
            query = f"{niche} in {location}"

        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.google.com/maps/search/{encoded_query}"

        proxy_config = self._parse_proxy()
        logger.info(f"Starting Single-Page Stream Scrape: '{query}' (Target: {limit})")
        if strict_locality:
            logger.info(f"Strict Micro-Locality Filter Active: '{strict_locality}' | Max Radius: {max_radius_km or 'strict'} km")
        if skip_duplicates and existing_identifiers:
            logger.info(f"Incremental Deduplication Active: {len(existing_identifiers.get('urls', set()))} known leads will be skipped.")
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
                # 1. Load Google Maps ONCE in this single session (with retry resilience)
                logger.info(f"Opening Single-Page Session: {search_url}")
                for attempt in range(3):
                    try:
                        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                        break
                    except Exception as err:
                        if attempt == 2:
                            raise err
                        logger.warning(f"Connection glitch on attempt {attempt+1} ({err}), retrying in 2s...")
                        await asyncio.sleep(2)
                await self._check_captcha(page)
                await self._handle_consent(page)
                await page.wait_for_timeout(2500)
                await self._check_captcha(page)

                # Attempt to extract target centroid coordinates from resolved URL
                target_coords = extract_coords_from_url(page.url)
                if target_coords:
                    logger.info(f"Target Centroid Coordinates Resolved: {target_coords}")

                feed_selector = "div[role='feed']"
                try:
                    await page.wait_for_selector(feed_selector, timeout=8000)
                    has_feed = True
                except Exception:
                    has_feed = False

                evaluated_titles = set()
                scroll_attempts = 0
                # Scale max scroll attempts dynamically with target limit plus existing leads headroom
                existing_count = len(existing_identifiers.get("urls", set())) if (skip_duplicates and existing_identifiers) else 0
                max_scroll_attempts = max(35, ((limit + existing_count) // 4) + 15)
                prev_card_count = 0
                no_new_cards_count = 0

                # 2. Scroll the feed and parse cards directly from the single page
                while len(results) < limit and scroll_attempts < max_scroll_attempts:
                    cards = await page.locator("div.Nv2PK").all()
                    
                    for card in cards:
                        if len(results) >= limit:
                            break

                        # Quick title check before deep extraction to prevent duplicate processing
                        title_el = card.locator(".qBF1Pd, a.hfpxzc").first
                        temp_title = ""
                        if await title_el.count() > 0:
                            temp_title = (await title_el.inner_text() or await title_el.get_attribute("aria-label") or "").strip()
                        
                        if temp_title and temp_title in evaluated_titles:
                            continue
                        if temp_title:
                            evaluated_titles.add(temp_title)

                        # 1. Historical Database Deduplication Check
                        if skip_duplicates and existing_identifiers:
                            link_elem = card.locator("a.hfpxzc").first
                            raw_href = ""
                            if await link_elem.count() > 0:
                                raw_href = (await link_elem.get_attribute("href") or "").split("?")[0].rstrip("/").lower()
                            if raw_href and raw_href in existing_identifiers.get("urls", set()):
                                logger.info(f"  [SKIPPED - ALREADY IN DATABASE]: URL match for '{temp_title or raw_href[:40]}'")
                                continue

                            clean_title = temp_title.lower().strip()
                            if clean_title and clean_title in existing_identifiers.get("titles", set()):
                                logger.info(f"  [SKIPPED - ALREADY IN DATABASE]: Title match '{temp_title}'")
                                continue

                        card_text = await card.inner_text()
                        item = await self._extract_card_info(card, page)
                        
                        if not item["title"]:
                            continue

                        # Phone number deduplication check
                        if skip_duplicates and existing_identifiers and item["phone"]:
                            phone_digits = re.sub(r"\D", "", item["phone"])
                            if len(phone_digits) >= 10 and phone_digits[-10:] in existing_identifiers.get("phones", set()):
                                logger.info(f"  [SKIPPED - ALREADY IN DATABASE]: Phone match for '{item['title']}' ({item['phone']})")
                                continue

                        # Strict Micro-Locality Filter Check
                        is_match, reason = is_locality_match(
                            item=item,
                            card_text=card_text,
                            strict_locality=strict_locality,
                            target_coords=target_coords,
                            max_radius_km=max_radius_km
                        )

                        if not is_match:
                            logger.info(f"  [SKIPPED OUT-OF-BOUNDS] '{item['title']}': {reason}")
                            continue

                        if item["title"] not in seen_titles:
                            seen_titles.add(item["title"])
                            results.append(item)
                            logger.info(f"[{len(results)}/{limit}] Extracted (Fresh Lead): {item['title']} | Phone: {item['phone']} | Web: {bool(item['website'])}")

                    if len(results) >= limit:
                        break

                    # Check if reached the end of Google Maps listings
                    end_marker = page.locator("text=\"You've reached the end of the list\"").first
                    if await end_marker.is_visible(timeout=400):
                        logger.info("Reached the end of Google Maps feed.")
                        break

                    if len(cards) == prev_card_count:
                        no_new_cards_count += 1
                        if no_new_cards_count >= 3:
                            logger.info("No new cards loading after 3 scroll attempts. Completing scan.")
                            break
                    else:
                        no_new_cards_count = 0
                    prev_card_count = len(cards)

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
