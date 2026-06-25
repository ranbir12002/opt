"""
Standalone Simpro API credential tester.
Bypasses ALL app code — pure HTTP requests to Simpro API.
Tests both UAT and Production credentials.
"""
import httpx
import asyncio
import json

# ── Hardcoded credentials ──────────────────────────────────────────────
UAT = {
    "token": "ccd1c2bf32782f309c67f014cd937b1b97e90c20",
    "base_url": "https://specialisedplumbing-uat.simprosuite.com/api",
    "company_id": 2,
}

PRODUCTION = {
    "token": "e57d2e5f9818a4ef9fb1c1c3d92b8e8c641b071d",
    "base_url": "https://specialisedplumbing.simprosuite.com/api",
    "company_id": 5,
}

# ── Test endpoints (simple read-only GET calls) ────────────────────────
TESTS = [
    ("List Employees (page 1)", "/v1.0/companies/{cid}/employees/?page=1&pageSize=5"),
    ("List Jobs (page 1)",      "/v1.0/companies/{cid}/jobs/?page=1&pageSize=5"),
    ("Setup Cost Centres",      "/v1.0/companies/{cid}/setup/costCentres/?page=1&pageSize=5"),
    ("Company Info",            "/v1.0/companies/{cid}/"),
]


async def test_creds(label: str, creds: dict):
    print(f"\n{'='*60}")
    print(f"  Testing: {label}")
    print(f"  Base URL: {creds['base_url']}")
    print(f"  Token: {creds['token'][:12]}...{creds['token'][-6:]}")
    print(f"  Company ID: {creds['company_id']}")
    print(f"{'='*60}")

    headers = {
        "Authorization": f"Bearer {creds['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for test_name, endpoint_template in TESTS:
            endpoint = endpoint_template.replace("{cid}", str(creds["company_id"]))
            url = f"{creds['base_url'].rstrip('/')}/{endpoint.lstrip('/')}"

            print(f"\n  📡 {test_name}")
            print(f"     GET {url}")

            try:
                resp = await client.get(url, headers=headers)
                status = resp.status_code
                body_preview = resp.text[:300]

                if status == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        print(f"     ✅ 200 OK — {len(data)} records returned")
                        if data:
                            print(f"     First record keys: {list(data[0].keys())[:8]}")
                    elif isinstance(data, dict):
                        print(f"     ✅ 200 OK — keys: {list(data.keys())[:8]}")
                    else:
                        print(f"     ✅ 200 OK — {type(data).__name__}")
                elif status == 401:
                    print(f"     ❌ 401 UNAUTHORIZED — {body_preview}")
                elif status == 403:
                    print(f"     ❌ 403 FORBIDDEN — {body_preview}")
                elif status == 404:
                    print(f"     ⚠️  404 NOT FOUND — {body_preview}")
                else:
                    print(f"     ⚠️  {status} — {body_preview}")

            except httpx.ConnectError as e:
                print(f"     ❌ CONNECTION ERROR: {e}")
            except httpx.TimeoutException:
                print(f"     ❌ TIMEOUT (15s)")
            except Exception as e:
                print(f"     ❌ ERROR: {type(e).__name__}: {e}")


async def main():
    print("\n🔑 Simpro API Credential Tester")
    print("   (Direct HTTP — no app code involved)\n")

    await test_creds("UAT Credentials", UAT)
    await test_creds("PRODUCTION Credentials", PRODUCTION)

    print(f"\n{'='*60}")
    print("  Done! If both show 401, the tokens may be expired/revoked.")
    print("  If UAT works but Production doesn't (or vice versa), ")
    print("  check which one your DB is using.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
