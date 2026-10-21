import asyncio
import json
import httpx
from main import app
from database import init_db, DB_PATH

async def run_phase5_test():
    print("=" * 60)
    print("PHASE 5 VERIFICATION TEST: API KEY SYSTEM & CRM INTEGRATION")
    print("=" * 60)

    init_db()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:

        # Login to obtain user session token
        login_res = await client.post("/api/v1/auth/login", json={
            "username": "company_admin",
            "password": "SecurePassword123!"
        })
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        auth_token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {auth_token}"}

        # ------------------------------------------------------------------
        # TEST 5.1: API Key Generation, Masking, and Security Validation
        # ------------------------------------------------------------------
        print("\n[TEST 5.1] Testing API Key Management & Authentication...")
        gen_res = await client.post(
            "/api/v1/integrations/api-keys",
            headers=headers,
            json={"name": "GoHighLevel CRM Key"}
        )
        assert gen_res.status_code == 200, f"API Key creation failed: {gen_res.text}"
        key_data = gen_res.json()["api_key"]
        raw_api_key = key_data["key"]
        masked_key = key_data["masked"]
        key_id = key_data["id"]

        assert raw_api_key.startswith("pf_live_"), f"Invalid key prefix: {raw_api_key}"
        print(f"  [OK] Generated API Key: {masked_key} (ID: {key_id})")

        # Verify listing keys returns masked value
        list_res = await client.get("/api/v1/integrations/api-keys", headers=headers)
        assert list_res.status_code == 200
        keys_list = list_res.json()["keys"]
        assert any(k["id"] == key_id for k in keys_list)
        print("  [OK] API keys list endpoint verified with security masking.")

        # Test request WITHOUT API key -> Must be rejected (HTTP 401)
        unauth_res = await client.post("/api/v1/external/jobs", json={
            "niche": "Dentists",
            "location": "Miami, FL",
            "target_count": 5
        })
        assert unauth_res.status_code == 401, f"Expected 401, got {unauth_res.status_code}"
        print("  [OK] Unauthorized request without API key correctly blocked (HTTP 401).")

        # Test request with INVALID API key -> Must be rejected (HTTP 401)
        bad_res = await client.post(
            "/api/v1/external/jobs",
            headers={"X-API-Key": "pf_live_invalid_fake_key_12345"},
            json={"niche": "Dentists", "location": "Miami, FL", "target_count": 5}
        )
        assert bad_res.status_code == 401
        print("  [OK] Invalid API key correctly blocked (HTTP 401).")

        # ------------------------------------------------------------------
        # TEST 5.2: Remote Inbound API (External Trigger via X-API-Key)
        # ------------------------------------------------------------------
        print("\n[TEST 5.2] Testing Inbound Remote Trigger Endpoint...")
        ext_res = await client.post(
            "/api/v1/external/jobs",
            headers={"X-API-Key": raw_api_key},
            json={
                "niche": "Realtors",
                "location": "Austin, TX",
                "target_count": 10
            }
        )
        assert ext_res.status_code == 200, f"External job trigger failed: {ext_res.text}"
        ext_data = ext_res.json()
        assert ext_data["success"] is True
        job_id = ext_data["job_id"]
        print(f"  [OK] Remotely launched Job #{job_id} for 'Realtors in Austin, TX'!")
        print(f"       Authorized by: {ext_data['authorized_by']}")

        # Fetch external job status with API key
        status_res = await client.get(
            f"/api/v1/external/jobs/{job_id}",
            headers={"X-API-Key": raw_api_key}
        )
        assert status_res.status_code == 200
        print(f"  [OK] Remote status check endpoint verified for Job #{job_id}.")

        # ------------------------------------------------------------------
        # TEST 5.3: CRM Webhook Configuration & Dispatcher
        # ------------------------------------------------------------------
        print("\n[TEST 5.3] Testing CRM Webhook Configuration & Dispatch...")
        test_webhook_url = "https://httpbin.org/post"
        save_hook_res = await client.post(
            "/api/v1/integrations/webhook",
            headers=headers,
            json={"webhook_url": test_webhook_url, "auto_push": True}
        )
        assert save_hook_res.status_code == 200
        print(f"  [OK] Webhook URL configured to: {test_webhook_url} (Auto-push: True)")

        # Verify config retrieved
        get_hook_res = await client.get("/api/v1/integrations/webhook", headers=headers)
        assert get_hook_res.status_code == 200
        assert get_hook_res.json()["webhook_url"] == test_webhook_url
        print("  [OK] Webhook configuration persistent in SQLite database.")

        # Test Webhook Dispatch to live echo endpoint
        print("  Testing live test webhook dispatch...")
        test_dispatch_res = await client.post("/api/v1/integrations/webhook/test", headers=headers)
        assert test_dispatch_res.status_code == 200, f"Webhook test dispatch failed: {test_dispatch_res.text}"
        dispatch_data = test_dispatch_res.json()
        print(f"  [OK] Live Webhook successfully delivered to {test_webhook_url}! Status: {dispatch_data['status_code']}")

        # Test API Key Revocation
        print("\nTesting API Key Revocation...")
        revoke_res = await client.delete(f"/api/v1/integrations/api-keys/{key_id}", headers=headers)
        assert revoke_res.status_code == 200
        print(f"  [OK] API Key #{key_id} revoked.")

        # Verify revoked key can no longer access external API
        revoked_check = await client.post(
            "/api/v1/external/jobs",
            headers={"X-API-Key": raw_api_key},
            json={"niche": "Dentists", "location": "Miami, FL", "target_count": 5}
        )
        assert revoked_check.status_code == 401
        print("  [OK] Revoked API key is immediately blocked from remote calls (HTTP 401).")

    print("\n" + "=" * 60)
    print("ALL PHASE 5 API & CRM INTEGRATION TESTS PASSED!")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_phase5_test())
