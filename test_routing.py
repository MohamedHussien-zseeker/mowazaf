import json
import asyncio
import httpx

from app.auth import create_access_token

TENANT_SLUG_TO_TID = {
    "sika": "tenant_sika",
    "hassan": "tenant_hassan",
}

HIT = {
    "sika": "hi from sika — reply in one sentence.",
    "hassan": "hi from hassan — reply in one sentence.",
}

URL = "http://127.0.0.1:8000/api/agent/conversations/test"

async def run_for(tenant_id: str, tenant_slug: str):
    token = create_access_token(data={"role": "agent"}, tenant_id=tenant_id)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(URL, headers=headers, json={"message": HIT[tenant_slug]})
        # Don't print token
        text = resp.text
        # Keep output small
        preview = text[:200].replace("\n", " ")
        return tenant_slug, resp.status_code, preview

async def main():
    results = []
    async with httpx.AsyncClient() as _:
        pass
    for slug, tid in TENANT_SLUG_TO_TID.items():
        results.append(await run_for(tid, slug))
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
