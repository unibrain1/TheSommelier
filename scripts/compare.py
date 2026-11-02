#!/usr/bin/env python3
"""Compare CellarTracker inventory against the wine drinking plan.

Input:  inventory.json (from parse_inventory.py), plan.json (from parse_plan.py)
Output: report.json to stdout with four sections:
  1. consumed    — in plan but no longer in inventory
  2. urgent_new  — in inventory, not in plan, EndConsume ≤ current_year + 1
  3. mismatches  — quantity differs between plan and inventory
  4. unplanned   — in inventory but nowhere in the plan
"""

import json
import sys
from datetime import date
from pathlib import Path

from wine_utils import CURRENT_YEAR, normalize

# Common abbreviations used in the plan vs full names in CellarTracker
ALIASES = {
    "ddo": "domaine drouhin oregon",
    "ddo laurène": "domaine drouhin oregon pinot noir laurène",
    "ddo laurene": "domaine drouhin oregon pinot noir laurène",
    "ddo louise": "domaine drouhin oregon pinot noir louise",
    "ddo roserock": "drouhin oregon roserock",
    "ddo arthur": "domaine drouhin oregon chardonnay arthur",
    "ddo edition limitée": "domaine drouhin oregon pinot noir edition limitée",
    "ddo edition limitee": "domaine drouhin oregon pinot noir edition limitée",
    "ddo origine": "domaine drouhin oregon pinot noir origine",
    "adamant": "adamant cellars",
    "dion": "dion vineyard",
}


def expand_aliases(name_norm: str) -> str:
    """Expand known abbreviations in a normalized plan name."""
    for abbr, full in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if abbr in name_norm:
            name_norm = name_norm.replace(abbr, normalize(full))
    return name_norm


def tokenize(name: str) -> set[str]:
    return set(name.split())


def match_score(
    plan_name: str, inv_name: str, plan_vintage: int | None, inv_vintage: int | None
) -> float:
    """Score how well a plan bottle matches an inventory wine. Higher = better. 0 = no match."""
    if plan_vintage != inv_vintage:
        return 0

    pn = normalize(plan_name)
    pn_expanded = expand_aliases(pn)
    inv_n = normalize(inv_name)

    # Exact match after normalization
    if pn_expanded == inv_n or pn == inv_n:
        return 100

    # Token overlap
    p_tokens = tokenize(pn_expanded)
    i_tokens = tokenize(inv_n)
    # Remove very common words
    stopwords = {
        "the",
        "a",
        "an",
        "de",
        "du",
        "des",
        "le",
        "la",
        "les",
        "et",
        "vineyard",
        "cellars",
        "winery",
        "estate",
    }
    p_tokens -= stopwords
    i_tokens -= stopwords

    if not p_tokens:
        return 0

    overlap = p_tokens & i_tokens
    score = len(overlap) / max(len(p_tokens), len(i_tokens)) * 100

    # Bonus if producer name matches
    if len(overlap) >= 2:
        score += 10

    return score


def find_best_match(
    plan_bottle: dict, inventory: list[dict]
) -> tuple[dict | None, float]:
    """Find the best matching inventory wine for a plan bottle."""
    pv = plan_bottle.get("vintage")
    if isinstance(pv, str):
        try:
            pv = int(pv)
        except ValueError:
            pv = None

    best = None
    best_score = 0
    for inv_wine in inventory:
        sc = match_score(plan_bottle["name"], inv_wine["Wine"], pv, inv_wine["Vintage"])
        if sc > best_score:
            best_score = sc
            best = inv_wine
    return (best, best_score) if best_score >= 40 else (None, 0)


def compare(inventory: list[dict], plan: list[dict]) -> dict:
    # Track which inventory wines are matched and how many times
    inv_plan_count = {}  # iWine -> count of plan references

    # Match each plan bottle to inventory
    plan_matches = []  # (plan_bottle, inv_wine_or_None, score)
    for pb in plan:
        inv_wine, score = find_best_match(pb, inventory)
        plan_matches.append((pb, inv_wine, score))
        if inv_wine:
            key = (inv_wine["iWine"], inv_wine["Vintage"])
            inv_plan_count[key] = inv_plan_count.get(key, 0) + 1

    matched_inv_keys = set(inv_plan_count.keys())

    # 1. Consumed: in plan but not in inventory
    consumed = []
    for pb, inv_wine, score in plan_matches:
        if inv_wine is None:
            consumed.append(
                {
                    "plan_name": pb["name"],
                    "plan_vintage": pb.get("vintage"),
                    "plan_date": pb.get("date"),
                    "plan_week": pb.get("week"),
                }
            )

    # 2. New urgent: in inventory, not in plan, EndConsume ≤ current_year + 1
    urgent_new = []
    for inv_wine in inventory:
        key = (inv_wine["iWine"], inv_wine["Vintage"])
        if key not in matched_inv_keys:
            ec = inv_wine["EndConsume"]
            if ec is not None and ec <= CURRENT_YEAR + 1:
                urgent_new.append(
                    {
                        "wine": inv_wine["Wine"],
                        "vintage": inv_wine["Vintage"],
                        "quantity": inv_wine["Quantity"],
                        "begin_consume": inv_wine["BeginConsume"],
                        "end_consume": inv_wine["EndConsume"],
                        "ct_score": inv_wine["CT"],
                        "type": inv_wine["Type"],
                        "varietal": inv_wine["Varietal"],
                        "region": inv_wine["Region"],
                    }
                )

    # 3. Quantity mismatches
    mismatches = []
    for key, plan_count in inv_plan_count.items():
        for inv_wine in inventory:
            if (inv_wine["iWine"], inv_wine["Vintage"]) == key:
                if plan_count != inv_wine["Quantity"]:
                    mismatches.append(
                        {
                            "wine": inv_wine["Wine"],
                            "vintage": inv_wine["Vintage"],
                            "inventory_qty": inv_wine["Quantity"],
                            "plan_qty": plan_count,
                            "end_consume": inv_wine["EndConsume"],
                        }
                    )
                break

    # 4. Unplanned: in inventory but not in plan at all (excluding urgent already reported)
    urgent_keys = {(w["wine"], w["vintage"]) for w in urgent_new}
    unplanned = []
    for inv_wine in inventory:
        key = (inv_wine["iWine"], inv_wine["Vintage"])
        if key not in matched_inv_keys:
            ident = (inv_wine["Wine"], inv_wine["Vintage"])
            if ident not in urgent_keys:
                unplanned.append(
                    {
                        "wine": inv_wine["Wine"],
                        "vintage": inv_wine["Vintage"],
                        "quantity": inv_wine["Quantity"],
                        "begin_consume": inv_wine["BeginConsume"],
                        "end_consume": inv_wine["EndConsume"],
                        "ct_score": inv_wine["CT"],
                        "type": inv_wine["Type"],
                        "varietal": inv_wine["Varietal"],
                        "region": inv_wine["Region"],
                    }
                )

    report = {
        "generated": str(date.today()),
        "current_year": CURRENT_YEAR,
        "summary": {
            "inventory_unique_wines": len(inventory),
            "inventory_total_bottles": sum(w["Quantity"] for w in inventory),
            "plan_total_bottles": len(plan),
            "consumed_count": len(consumed),
            "urgent_new_count": len(urgent_new),
            "mismatch_count": len(mismatches),
            "unplanned_count": len(unplanned),
            "cellar_total_valuation": sum(
                w.get("TotalValuation") or 0.0 for w in inventory
            ),
        },
        "consumed": consumed,
        "urgent_new": urgent_new,
        "mismatches": mismatches,
        "unplanned": unplanned,
    }
    return report


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: compare.py <inventory.json> <plan.json>", file=sys.stderr)
        sys.exit(2)
    inv_path = sys.argv[1]
    plan_path = sys.argv[2]

    inventory = json.loads(Path(inv_path).read_text())
    plan_data = json.loads(Path(plan_path).read_text())
    # Support both flat list (legacy) and wrapped {allWeeks: [...]} format
    plan = (
        plan_data.get("allWeeks", plan_data)
        if isinstance(plan_data, dict)
        else plan_data
    )

    report = compare(inventory, plan)
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    print()
