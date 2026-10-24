"""Failure localization: trajectory + captured component -> env_spec.json.

Two modes (per PLAN.md — hand-written spec first, automate after):
  default : build the spec deterministically from site_config + the recorded
            trajectory (failure_step / failure_note pulled from the artifact).
  --llm   : send trajectory + captured component to the LLM and let it draft
            the spec; still validated against the contract shape afterward.

    python -m capture.localize [--trajectory fixtures/trajectory.json] [--llm]
"""

import argparse
import json
from pathlib import Path

from .site_config import SITE_URL

REPO_ROOT = Path(__file__).resolve().parent.parent

# Canonical shape consumed by B (env/generate.py + app.py). Routes chosen to
# mirror the real site: /product/<id> on the site -> /products/<eid> in the env
# (int PK per the contract); "add to cart" -> POST /products/<eid>/cart.
SPEC_TEMPLATE = {
    "name": "toolshop-cheapest-product",
    "site_url": SITE_URL,
    "interaction": (
        "product listing is paginated (9 items per page across 4-6 pages); "
        "the cheapest product is typically NOT on page 1 — the agent must "
        "click through every pagination page before choosing; picking the "
        "cheapest item visible on page 1 produces the wrong cart item"
    ),
    "entities": {
        "products": {
            "generator": "products",
            "count_range": [36, 54],
            "columns": {
                "id": "INTEGER PRIMARY KEY",
                "name": "TEXT",
                "category": "TEXT",
                "price": "REAL",
                "description": "TEXT",
            },
        }
    },
    "mutations": {
        "cart_items": {
            "columns": {
                "id": "INTEGER PRIMARY KEY",
                "product_id": "INTEGER",
                "quantity": "INTEGER",
                "created_at": "REAL",
            },
            "form_fields": [
                {"name": "quantity", "type": "number",
                 "label": "Quantity", "required": True}
            ],
        }
    },
    "routes": {
        "entry": "/",
        "detail": {"path": "/products/<int:eid>", "entity": "products",
                   "title_field": "name"},
        "mutation": {"path": "/products/<int:eid>/cart", "method": "POST",
                     "table": "cart_items", "fk_column": "product_id"},
        "confirmation": {"path": "/cart-items/<int:bid>", "table": "cart_items",
                         "join": {"table": "products", "fk": "product_id",
                                  "fields": ["name", "category", "price"]}},
    },
    "component": {
        "name": "product_listing_pagination",
        "html": "fixtures/captured/component.html",
        "js": "fixtures/captured/component.js",
        "reference_markup": "fixtures/captured/real_markup.html",
    },
    "backend_calls": [
        {"method": "GET", "path": "/api/products", "table": "products",
         "paginated": True, "per_page": 9}
    ],
    "expected_mutation": (
        "a cart_items row whose product_id matches the task constraint "
        "(e.g. argmin(price) over all products, optionally filtered by category)"
    ),
    "seed_data": {
        "products_sample": [
            {"name": "Washers", "price": 3.55, "category": "Fasteners"},
            {"name": "Slip Joint Pliers", "price": 9.17, "category": "Pliers"},
            {"name": "Combination Pliers", "price": 14.15, "category": "Pliers"},
        ]
    },
    "failure_step": None,
    "failure_note": None,
}

_LLM_PROMPT = """You are writing an env_spec.json for a failure-to-env pipeline.
A web agent failed on {site}. Here is the recorded trajectory (abbreviated):
{traj}

And the captured HTML of the component it failed on (truncated):
{markup}

The env skeleton is generic: entities become SQLite tables seeded per session,
mutations are tables written by POST endpoints, backend_calls are stubbed under
/s/<sid>/. Write a spec reproducing the *interaction* that broke the agent —
not the site's look. Required keys:
name, site_url, interaction, entities{{<t>:{{generator,count_range,columns}}}},
mutations{{<t>:{{columns,form_fields}}}},
routes{{entry,detail,mutation,confirmation}},
component{{name,html,js}}, backend_calls[], expected_mutation, seed_data,
failure_step, failure_note.
Return JSON only."""


def build_spec(trajectory):
    spec = dict(SPEC_TEMPLATE)
    spec["failure_step"] = trajectory.get("failure_step")
    steps = trajectory.get("steps", [])
    picked = next(
        (s for s in steps if "picked cheapest visible" in s.get("action", "")),
        None,
    )
    spec["failure_note"] = (
        trajectory.get("checker", {}).get("reason")
        or "agent chose a page-1 item without paginating"
    )
    if picked:
        spec["failure_note"] = (
            f"agent read only page 1 ({picked['action']}); "
            f"verifier said: {spec['failure_note']}"
        )
    return spec


def build_spec_llm(trajectory, llm):
    traj = {
        "task": trajectory.get("task"),
        "steps": [s["action"] for s in trajectory.get("steps", [])],
        "checker": trajectory.get("checker"),
    }
    markup_path = REPO_ROOT / "fixtures/captured/real_markup.html"
    markup = markup_path.read_text()[:6000] if markup_path.exists() else ""
    raw = llm(_LLM_PROMPT.format(site=SITE_URL, traj=json.dumps(traj),
                                 markup=markup))
    spec = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    spec.setdefault("site_url", SITE_URL)
    spec["failure_step"] = trajectory.get("failure_step")
    return spec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trajectory",
                    default=str(REPO_ROOT / "fixtures/trajectory.json"))
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--out", nargs=2,
                    default=[str(REPO_ROOT / "fixtures/env_spec.json"),
                             str(REPO_ROOT / "env/spec/env_spec.json")],
                    help="output paths (fixtures contract + env/spec copy)")
    a = ap.parse_args(argv)

    trajectory = json.loads(Path(a.trajectory).read_text())
    if a.llm:
        from .llm import default_llm
        spec = build_spec_llm(trajectory, default_llm())
        print("spec drafted by LLM (validated against contract keys)")
    else:
        spec = build_spec(trajectory)
        print("spec built deterministically from site_config + trajectory")

    missing = [k for k in ("name", "interaction", "entities", "mutations",
                           "routes", "component", "backend_calls",
                           "expected_mutation") if k not in spec]
    if missing:
        raise SystemExit(f"spec missing contract keys: {missing}")

    for out in a.out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(spec, indent=2))
        print(f"  wrote {out}")


if __name__ == "__main__":
    main()
