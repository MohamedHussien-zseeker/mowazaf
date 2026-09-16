import json
import sqlite3
from datetime import datetime

from app.auth import create_access_token

DB_PATH = "mowazaf.db"
TENANT_SLUGS = ["sika", "hassan"]


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute(
        "SELECT tenant_id, slug, name, config FROM tenants WHERE slug IN (?, ?) ORDER BY id",
        (TENANT_SLUGS[0], TENANT_SLUGS[1]),
    )
    rows = cur.fetchall()
    con.close()

    if not rows:
        raise SystemExit("No tenants found for slugs: " + ", ".join(TENANT_SLUGS))

    for r in rows:
        tenant_id = r["tenant_id"]
        slug = r["slug"]
        print("===", slug, "===")
        # For MVP testing we don't need a users table; endpoints only validate JWT signature
        # and read tenant_id from the token.
        for role in ["agent", "admin"]:
            token = create_access_token(data={"role": role}, tenant_id=tenant_id)
            print(f"role={role}")
            print(token)
        # separate block
        print()

    print("Generated at:", datetime.utcnow().isoformat() + "Z")


if __name__ == "__main__":
    main()
