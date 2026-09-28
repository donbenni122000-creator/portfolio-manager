"""Account types, beneficiaries and account-level permissions (onboarding)."""
from __future__ import annotations

import json

ACCOUNT_TYPES = {
    "Individual": {"tax": "taxable", "margin": True, "sbloc": True, "lending": True},
    "Joint": {"tax": "taxable", "margin": True, "sbloc": True, "lending": True},
    "Trust": {"tax": "taxable", "margin": True, "sbloc": True, "lending": True},
    "Corporate": {"tax": "taxable", "margin": True, "sbloc": True, "lending": True},
    "Traditional IRA": {"tax": "tax-deferred", "margin": False, "sbloc": False, "lending": False},
    "Roth IRA": {"tax": "tax-free", "margin": False, "sbloc": False, "lending": False},
}
OPTIONS_LEVELS = {
    0: "Options not enabled",
    1: "Level 1 - covered calls and cash-secured puts",
    2: "Level 2 - Level 1 + buying calls and puts",
}


def rules(client: dict) -> dict:
    return ACCOUNT_TYPES.get(client.get("account_type") or "Individual", ACCOUNT_TYPES["Individual"])


def is_taxable(client: dict) -> bool:
    return rules(client)["tax"] == "taxable"


def validate(account_type: str | None, beneficiaries) -> tuple[str, str]:
    at = account_type or "Individual"
    if at not in ACCOUNT_TYPES:
        raise ValueError(f"Account type must be one of: {', '.join(ACCOUNT_TYPES)}")
    bens = beneficiaries or []
    if isinstance(bens, str):
        bens = json.loads(bens or "[]")
    clean = []
    for b in bens:
        name = str(b.get("name", "")).strip()
        if not name:
            continue
        clean.append({"name": name, "relationship": str(b.get("relationship", "")).strip(),
                      "share": float(b.get("share", 0)), "type": b.get("type", "primary")})
    prim = [b for b in clean if b["type"] == "primary"]
    if prim and abs(sum(b["share"] for b in prim) - 100) > 0.01:
        raise ValueError("Primary beneficiary shares must add up to 100%")
    return at, json.dumps(clean)
