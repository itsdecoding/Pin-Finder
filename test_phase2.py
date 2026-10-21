import asyncio
import json
from enricher import LeadEnricher

async def run_phase2_test():
    print("=" * 60)
    print("PHASE 2 VERIFICATION TEST: ON-DEMAND LEAD ENRICHMENT")
    print("=" * 60)

    # 1. Load leads from Phase 1
    with open("test_leads.json", "r", encoding="utf-8") as f:
        real_leads = json.load(f)

    # Pick 5 leads that have websites
    sample_leads = real_leads[:5]

    # Add 2 leads without websites to test safe bypass & segregation
    sample_leads.append({
        "title": "Biscayne Dental Lab (No Website)",
        "category": "Dental laboratory",
        "rating": 4.5,
        "reviews_count": 12,
        "address": "100 Biscayne Blvd, Miami, FL",
        "website": "",
        "phone": "+1 305-555-0122",
        "maps_url": "https://maps.google.com/?cid=12345"
    })
    sample_leads.append({
        "title": "Dr. Carlos Ortho Clinic (No Website)",
        "category": "Orthodontist",
        "rating": 4.9,
        "reviews_count": 88,
        "address": "400 NW 2nd Ave, Miami, FL",
        "website": None,
        "phone": "+1 305-555-0199",
        "maps_url": "https://maps.google.com/?cid=67890"
    })

    print(f"Testing on mixed batch of {len(sample_leads)} leads (5 with website, 2 without website)...")

    enricher = LeadEnricher(timeout=4.0)
    enriched_leads = await enricher.enrich_batch(sample_leads, concurrency=4)

    # Save output
    with open("test_enriched_leads.json", "w", encoding="utf-8") as f:
        json.dump(enriched_leads, f, indent=2, ensure_ascii=False)

    print("\n--- TEST RESULTS SUMMARY ---")
    no_website_count = sum(1 for l in enriched_leads if not l.get("has_website"))
    enriched_count = sum(1 for l in enriched_leads if l.get("enrichment_status") == "enriched")

    for idx, lead in enumerate(enriched_leads, 1):
        status = lead.get("enrichment_status")
        emails = lead.get("emails", [])
        socials = lead.get("socials", {})
        print(f"[{idx}] {lead['title']}")
        print(f"    Website: {lead.get('website') or '(None)'}")
        print(f"    Has Website: {lead.get('has_website')}")
        print(f"    Status: {status}")
        print(f"    Emails Found: {emails if emails else 'None'}")
        print(f"    Socials Found: {list(socials.keys()) if socials else 'None'}")
        print("-" * 40)

    print(f"\nTotal Processed: {len(enriched_leads)}")
    print(f"Segregated as 'No Website': {no_website_count}")
    print(f"Successfully Enriched: {enriched_count}")
    print(f"Full details saved to: test_enriched_leads.json\n")

if __name__ == "__main__":
    asyncio.run(run_phase2_test())
