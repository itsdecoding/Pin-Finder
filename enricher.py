import asyncio
import logging
import re
from typing import Dict, List, Any, Set
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("PinFinder-Enricher")

# Regex pattern for finding email addresses
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE)

# Ignored extensions and junk domains
INVALID_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".pdf"
)

JUNK_DOMAINS = {
    "example.com", "domain.com", "email.com", "yourdomain.com", "sentry.io",
    "wixpress.com", "schema.org", "w3.org", "cloudflare.com", "googleapis.com",
    "github.com", "facebook.com", "instagram.com", "twitter.com", "linkedin.com"
}

JUNK_PREFIXES = {
    "noreply", "no-reply", "donotreply", "privacy", "mailer-daemon", "user", "username"
}

def clean_email(email: str) -> str:
    """Clean and validate an email string, removing junk false-positives."""
    email = email.strip().lower()

    # Drop trailing periods or commas
    email = email.rstrip(".,;")

    # Check for invalid image/code extensions
    for ext in INVALID_EXTENSIONS:
        if email.endswith(ext):
            return ""

    if "@" not in email:
        return ""

    user, domain = email.split("@", 1)

    if user in JUNK_PREFIXES:
        return ""

    if domain in JUNK_DOMAINS:
        return ""

    # Must have a valid dot in domain
    if "." not in domain or len(domain.split(".")[-1]) < 2:
        return ""

    return email

def extract_socials(soup: BeautifulSoup) -> Dict[str, str]:
    """Extract Facebook, Instagram, LinkedIn, and Twitter links from page HTML."""
    socials = {
        "facebook": "",
        "instagram": "",
        "linkedin": "",
        "twitter": ""
    }

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower_href = href.lower()

        if "facebook.com/" in lower_href and not socials["facebook"]:
            if not any(x in lower_href for x in ["/sharer", "/share", "/dialog", "policies"]):
                socials["facebook"] = href.split("?")[0]
        elif "instagram.com/" in lower_href and not socials["instagram"]:
            if not any(x in lower_href for x in ["/p/", "/reel/", "/explore/"]):
                socials["instagram"] = href.split("?")[0]
        elif "linkedin.com/" in lower_href and not socials["linkedin"]:
            if "/company/" in lower_href or "/in/" in lower_href:
                socials["linkedin"] = href.split("?")[0]
        elif ("twitter.com/" in lower_href or "x.com/" in lower_href) and not socials["twitter"]:
            if not any(x in lower_href for x in ["/intent", "/share"]):
                socials["twitter"] = href.split("?")[0]

    return socials

class LeadEnricher:
    def __init__(self, timeout: float = 4.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def _fetch_html(self, client: httpx.AsyncClient, url: str) -> str:
        """Fetch raw HTML with short timeout and redirection handling."""
        try:
            resp = await client.get(url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        return ""

    def _find_contact_links(self, base_url: str, soup: BeautifulSoup) -> List[str]:
        """Discover contact and about page URLs from navigation links."""
        contact_urls: Set[str] = set()
        base_domain = urlparse(base_url).netloc.lower()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text().strip().lower()
            lower_href = href.lower()

            # Target contact/about keywords
            is_candidate = any(keyword in lower_href or keyword in text for keyword in [
                "contact", "about", "team", "touch", "reach"
            ])

            if is_candidate:
                full_url = urljoin(base_url, href)
                # Keep only internal links
                if urlparse(full_url).netloc.lower() == base_domain:
                    # Avoid anchors or direct downloads
                    if not full_url.endswith(INVALID_EXTENSIONS) and "#" not in full_url:
                        contact_urls.add(full_url)
                        if len(contact_urls) >= 2:  # Check up to 2 high-probability subpages
                            break

        return list(contact_urls)

    async def enrich_lead(self, lead: Dict[str, Any], client: httpx.AsyncClient = None) -> Dict[str, Any]:
        """
        On-demand enrichment function for a single lead.
        Safely bypasses leads without websites immediately.
        """
        # Ensure standard keys exist
        website = (lead.get("website") or "").strip()
        lead["emails"] = lead.get("emails") or []
        lead["socials"] = lead.get("socials") or {}

        # 1. Check if lead has a website
        if not website:
            logger.info(f"Skipping enrichment: No website for '{lead.get('title', 'Unknown')}'")
            lead["has_website"] = False
            lead["enrichment_status"] = "no_website"
            return lead

        lead["has_website"] = True
        logger.info(f"Enriching: '{lead.get('title')}' -> {website}")

        created_client = False
        if client is None:
            client = httpx.AsyncClient(verify=False)
            created_client = True

        found_emails: Set[str] = set()
        found_socials: Dict[str, str] = {
            "facebook": "",
            "instagram": "",
            "linkedin": "",
            "twitter": ""
        }

        try:
            # 2. Fetch Homepage
            home_html = await self._fetch_html(client, website)
            if not home_html:
                lead["enrichment_status"] = "site_unreachable"
                return lead

            soup = BeautifulSoup(home_html, "html.parser")

            # Extract mailto links
            for mailto in soup.select('a[href^="mailto:"]'):
                email = mailto.get("href", "").replace("mailto:", "").split("?")[0]
                cleaned = clean_email(email)
                if cleaned:
                    found_emails.add(cleaned)

            # Extract regex emails from homepage text
            for raw in EMAIL_REGEX.findall(home_html):
                cleaned = clean_email(raw)
                if cleaned:
                    found_emails.add(cleaned)

            # Extract social links from homepage
            home_socials = extract_socials(soup)
            for k, v in home_socials.items():
                if v:
                    found_socials[k] = v

            # 3. If no email found on homepage, search Contact / About subpages
            if not found_emails:
                subpage_urls = self._find_contact_links(website, soup)
                for sub_url in subpage_urls:
                    sub_html = await self._fetch_html(client, sub_url)
                    if sub_html:
                        sub_soup = BeautifulSoup(sub_html, "html.parser")
                        for mailto in sub_soup.select('a[href^="mailto:"]'):
                            cleaned = clean_email(mailto.get("href", "").replace("mailto:", "").split("?")[0])
                            if cleaned:
                                found_emails.add(cleaned)

                        for raw in EMAIL_REGEX.findall(sub_html):
                            cleaned = clean_email(raw)
                            if cleaned:
                                found_emails.add(cleaned)

                        # Check socials on subpage if missing
                        sub_socials = extract_socials(sub_soup)
                        for k, v in sub_socials.items():
                            if v and not found_socials[k]:
                                found_socials[k] = v

                        if found_emails:
                            break

            # 4. Save results
            lead["emails"] = sorted(list(found_emails))
            lead["socials"] = {k: v for k, v in found_socials.items() if v}
            
            if lead["emails"] or lead["socials"]:
                lead["enrichment_status"] = "enriched"
            else:
                lead["enrichment_status"] = "no_contact_found"

        except Exception as e:
            logger.warning(f"Error enriching {website}: {e}")
            lead["enrichment_status"] = "failed"
        finally:
            if created_client:
                await client.aclose()

        return lead

    async def enrich_batch(self, leads: List[Dict[str, Any]], concurrency: int = 5) -> List[Dict[str, Any]]:
        """Enrich a batch of leads on-demand with bounded concurrency."""
        sem = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(verify=False) as client:
            async def _task(l):
                async with sem:
                    return await self.enrich_lead(l, client)

            return await asyncio.gather(*[_task(l) for l in leads])
