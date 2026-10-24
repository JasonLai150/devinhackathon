"""Seeded data generation + perturbation decisions.

Everything here is driven by random.Random(seed) so a given seed always
produces identical data, label choices, and popup state. Entity rows are
produced by registered generator functions keyed by spec.entities.*.generator —
new domains plug in by registering a generator, no skeleton changes needed.
"""
import json
import random
import time

# Perturbation knob: per-seed UI label dictionary. Agents that memorize
# button text break; agents that read intent don't.
LABEL_SETS = [
    {"site_heading": "Flight Finder", "search_button": "Search",
     "book_button": "Book now", "results_heading": "Results"},
    {"site_heading": "Air Search", "search_button": "Find flights",
     "book_button": "Reserve seat", "results_heading": "Available flights"},
    {"site_heading": "FlyCheap", "search_button": "Go",
     "book_button": "Confirm booking", "results_heading": "Matching flights"},
    {"site_heading": "Ticket Hunt", "search_button": "Show flights",
     "book_button": "Book this flight", "results_heading": "Flights found"},
]

AIRLINES = ["AeroLine", "SkyJet", "BlueAir", "Nimbus", "TransCo", "Vertex Air"]

GENERATORS = {}


def generator(name):
    def deco(fn):
        GENERATORS[name] = fn
        return fn
    return deco


def _depart_time(rng, h_lo, h_hi):
    return f"{rng.randint(h_lo, h_hi):02d}:{rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]):02d}"


@generator("flights")
def gen_flights(rng, cfg):
    """Flights with structured near-misses so verifier constraints bite.

    Guarantees:
      - global argmin(price) is non-direct  → 'cheapest' vs 'cheapest direct' differ
      - argmin over direct flights is distinct
      - argmin over departs>=17:00 is distinct
      - a unique earliest departure exists
    """
    lo, hi = cfg.get("count_range", [22, 45])
    count = rng.randint(lo, hi)
    rows = []

    def mk(price, direct, departs):
        return {"flight_no": "", "airline": rng.choice(AIRLINES),
                "departs_at": departs, "price": price, "direct": direct}

    cheap_nondirect = mk(rng.randint(80, 110), 0, _depart_time(rng, 6, 11))
    cheap_direct = mk(rng.randint(140, 180), 1, _depart_time(rng, 6, 11))
    cheap_late = mk(rng.randint(160, 220), rng.randint(0, 1), _depart_time(rng, 17, 21))
    earliest = mk(rng.randint(300, 450), rng.randint(0, 1), "05:40")
    rows += [cheap_nondirect, cheap_direct, cheap_late, earliest]

    while len(rows) < count:
        dep = _depart_time(rng, 6, 22)
        direct = rng.randint(0, 1)
        price = rng.randint(200, 600)
        if direct:
            price = max(price, cheap_direct["price"] + rng.randint(1, 40))
        if int(dep[:2]) >= 17:
            price = max(price, cheap_late["price"] + rng.randint(1, 40))
        rows.append(mk(price, direct, dep))

    rng.shuffle(rows)  # perturbation knob: reorder — memorized positions break
    for i, r in enumerate(rows):
        r["flight_no"] = f"FL{100 + i}"
    return rows


PRODUCT_NAMES = {
    "Fasteners": ["Washers", "M4 Nuts", "Wood Screws", "Hex Bolts",
                  "Wall Plugs", "Flat Nails", "Anchor Bolts"],
    "Hand Tools": ["Claw Hammer", "Rubber Mallet", "Tape Measure",
                   "Utility Knife", "Spirit Level", "Hand Saw"],
    "Pliers": ["Slip Joint Pliers", "Combination Pliers", "Needle-Nose Pliers",
               "Wire Cutters", "Locking Pliers"],
    "Power Tools": ["Cordless Drill", "Angle Grinder", "Jigsaw",
                    "Orbital Sander", "Heat Gun"],
    "Storage": ["Tool Box", "Parts Organizer", "Magnetic Tray", "Tool Bag"],
}


@generator("products")
def gen_products(rng, cfg):
    """Products with a trap-first argmin so the pagination failure bites.

    Guarantees:
      - a unique global argmin(price) sits in the BACK HALF of the listing
        order — at 9 items/page it is never on page 1 (mirrors the real site,
        where 'Washers' $3.55 was on page 4-5)
      - the cheapest 'Pliers' item is a distinct row (category variant task)
      - remaining prices are clamped above the trap
    """
    lo, hi = cfg.get("count_range", [36, 54])
    count = rng.randint(lo, hi)
    rows = []

    def mk(name, category, price):
        return {"name": name, "category": category, "price": price,
                "description": f"{name} — {category.lower()} product"}

    trap = mk(rng.choice(PRODUCT_NAMES["Fasteners"]), "Fasteners",
              round(rng.uniform(2.0, 4.5), 2))
    pliers_min = mk(rng.choice(PRODUCT_NAMES["Pliers"]), "Pliers",
                    round(rng.uniform(6.0, 9.5), 2))
    rows += [trap, pliers_min]

    used = {r["name"] for r in rows}
    cats = list(PRODUCT_NAMES)
    while len(rows) < count:
        cat = rng.choice(cats)
        name = rng.choice(PRODUCT_NAMES[cat])
        if name in used:
            name = f"{name} ({rng.choice(['Pro', 'XL', 'Mini', 'Set'])})"
        if name in used:
            continue
        used.add(name)
        price = round(rng.uniform(11.0, 90.0), 2)
        if cat == "Pliers":
            price = max(price, pliers_min["price"] + rng.uniform(0.5, 5.0))
        rows.append(mk(name, cat, round(price, 2)))

    # Perturbation knob: display order is shuffled, but the trap lands in the
    # second half so a page-1-only agent provably picks wrong.
    rng.shuffle(rows)
    half = len(rows) // 2
    ti = rows.index(trap)
    j = rng.randrange(half, len(rows))
    rows[ti], rows[j] = rows[j], rows[ti]
    return rows


def seed_db(conn, seed, spec, overrides):
    rng = random.Random(seed)

    for name, ent in spec.get("entities", {}).items():
        cols = ", ".join(f"{c} {t}" for c, t in ent["columns"].items())
        conn.execute(f"CREATE TABLE {name} ({cols})")
        rows = GENERATORS[ent["generator"]](rng, ent)
        col_names = list(rows[0].keys())
        ph = ", ".join("?" for _ in col_names)
        conn.executemany(
            f"INSERT INTO {name} ({', '.join(col_names)}) VALUES ({ph})",
            [[r[c] for c in col_names] for r in rows],
        )

    for name, mut in spec.get("mutations", {}).items():
        cols = ", ".join(f"{c} {t}" for c, t in mut["columns"].items())
        conn.execute(f"CREATE TABLE {name} ({cols})")

    conn.execute(
        "CREATE TABLE request_log (id INTEGER PRIMARY KEY, ts REAL, method TEXT, path TEXT)")

    labels_idx = int(overrides.get("labels", rng.randrange(len(LABEL_SETS)))) % len(LABEL_SETS)
    popup = overrides.get("popup", rng.random() < 0.5)
    meta = {
        "seed": seed,
        "labels": LABEL_SETS[labels_idx],
        "labels_index": labels_idx,
        "popup": bool(int(popup)) if isinstance(popup, str) else bool(popup),
        "created_at": time.time(),
    }
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO meta (k, v) VALUES (?, ?)",
                     [(k, json.dumps(v)) for k, v in meta.items()])
