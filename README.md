# Pin Finder 📍

An internal, standalone Google Maps Lead Generation & Scraping Platform built for company-wide usage with a **$0 budget**. 

Pin Finder allows targeting any niche and geographic area, extracting business listings, auto-enriching leads with emails/socials, and syncing directly with CRMs via custom APIs and webhooks.

---

## 🚀 Features (Roadmap)

- [x] **Phase 1: Core Scraper Engine** — Stealth browser crawler extracting Name, Phone, Address, Website, Rating, and Review count with zero bot blocks or paid proxies.
- [x] **Phase 2: On-Demand Lead Enrichment** — High-speed async crawler visiting websites to discover contact emails, LinkedIn, Instagram, Facebook, and X handles, with automatic "no website" bypass.
- [x] **Phase 3: SaaS Backend & Local DB** — Multi-user management, JWT authentication, background job queue, SQLite relational database, and CSV export.
- [x] **Phase 4: Standalone Web Interface** — Polished UI (Login, Dashboard, New Scrape, Real-time Activities, Leads Library with CSV Export).
- [ ] **Phase 5: API Key System & CRM Webhooks** — Secret API key generator, remote scrape trigger endpoint, and automatic webhook push to CRMs (HubSpot, GoHighLevel, etc.).
- [ ] **Phase 6: UI/UX Premium Polish** — High-end styling, click-to-copy, skeleton loaders, micro-animations, and executive design system refinement.

---

## 💻 Running the Web Application (UI)

Start the local server:
```bash
python -m uvicorn main:app --reload --port 8000
```
Open **`http://localhost:8000`** in any web browser.
* **Default Username**: `company_admin`
* **Default Password**: `SecurePassword123!`

---

## 🛠️ Quick Start (Phase 1)

### 1. Setup Virtual Environment
```bash
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Run the Scraper Test
```bash
python scraper.py
```
Extracted leads are saved in `test_leads.json`.
