"""
MyOB Connection Test Script.

Tests:
1. OAuth token status (loaded from tokens.json)
2. Token refresh
3. Company file discovery
4. Basic API call

Usage:
    cd mcp-myob-server
    python scripts/test_connection.py

First-time setup:
    python scripts/test_connection.py --setup
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from src.myob.auth import MyOBAuth


async def test_token_status(auth: MyOBAuth) -> bool:
    """Check if we have valid tokens."""
    print("\n=== Token Status ===")
    if auth.access_token:
        print(f"  Access token: ***{auth.access_token[-8:]}")
        expired = auth._is_token_expired()
        print(f"  Expired: {expired}")
        if auth.refresh_token:
            print(f"  Refresh token: ***{auth.refresh_token[-8:]}")
        return not expired
    else:
        print("  No access token found.")
        print("  Run with --setup to complete OAuth flow.")
        return False


async def test_token_refresh(auth: MyOBAuth) -> bool:
    """Test token refresh."""
    print("\n=== Token Refresh ===")
    if not auth.refresh_token:
        print("  No refresh token — skipping.")
        return False

    try:
        token = await auth.ensure_valid_token()
        print(f"  Token valid: ***{token[-8:]}")
        return True
    except Exception as e:
        print(f"  Refresh failed: {e}")
        return False


async def test_company_files(auth: MyOBAuth) -> bool:
    """List company files."""
    print("\n=== Company Files ===")
    try:
        files = await auth.discover_company_files()
        if not files:
            print("  No company files found.")
            return False

        for i, f in enumerate(files):
            name = f.get("Name", "Unknown")
            uid = f.get("Id", "?")
            uri = f.get("Uri", "?")
            print(f"  [{i+1}] {name}")
            print(f"      ID:  {uid}")
            print(f"      URI: {uri}")

        # Auto-set first company file if not configured
        if not auth.company_file_id and files:
            first = files[0]
            await auth.set_company_file(first["Id"], first["Uri"])
            print(f"\n  Auto-selected company file: {first['Name']}")

        return True
    except Exception as e:
        print(f"  Failed: {e}")
        return False


async def test_api_call(auth: MyOBAuth) -> bool:
    """Make a test API call."""
    print("\n=== Test API Call (Company Info) ===")
    if not auth.company_file_id:
        print("  No company file configured — skipping.")
        return False

    try:
        from src.myob.client import MyOBClient

        client = MyOBClient(auth=auth)
        result = await client.get("/Info")
        print(f"  Success! Response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        await client.close()
        return True
    except Exception as e:
        print(f"  Failed: {e}")
        return False


async def setup_oauth(auth: MyOBAuth) -> None:
    """Interactive OAuth setup flow."""
    print("\n=== OAuth Setup ===")
    print(f"  Client ID: {auth.client_id or 'NOT SET'}")

    if not auth.client_id or not auth.client_secret:
        print("\n  ERROR: Set MYOB_CLIENT_ID and MYOB_CLIENT_SECRET in .env first.")
        return

    url = auth.get_authorization_url()
    print(f"\n  1. Open this URL in your browser:\n\n     {url}\n")
    print("  2. Log in with your MYOB account and grant access.")
    print("  3. You'll be redirected. Copy the 'code' parameter from the URL.")

    code = input("\n  Paste the authorization code here: ").strip()
    if not code:
        print("  No code entered. Aborting.")
        return

    try:
        result = await auth.exchange_code_for_tokens(code)
        print(f"\n  Success! Token obtained.")
        print(f"  Access token: ***{auth.access_token[-8:]}")
        print(f"  Saved to tokens.json")
    except Exception as e:
        print(f"\n  Failed: {e}")


async def main():
    print("=" * 50)
    print("MyOB Connection Test")
    print("=" * 50)
    print(f"  Client ID: {'***' + settings.MYOB_CLIENT_ID[-4:] if settings.MYOB_CLIENT_ID else 'NOT SET'}")
    print(f"  Company File: {settings.MYOB_COMPANY_FILE_ID or 'NOT SET'}")

    auth = MyOBAuth()

    if "--setup" in sys.argv:
        await setup_oauth(auth)
        return

    has_token = await test_token_status(auth)
    if has_token:
        await test_token_refresh(auth)
        await test_company_files(auth)
        await test_api_call(auth)
    else:
        print("\n  Tip: Run 'python scripts/test_connection.py --setup' for OAuth flow.")

    print("\n" + "=" * 50)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
