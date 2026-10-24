"""Real-site success checker — same contract as env verifiers: {passed, reason}.

Primary path is scripted: compute the expected product live from the site's
public API (never hardcoded), then read the cart's contents off the /checkout
page DOM (cart rows link back to /product/<id>, giving us the product id).

Fallback: LLM judge on the final DOM + instruction, only when a key exists.
"""

import json
import re
import time
import urllib.request

from .site_config import API_URL, SITE_URL

_UA = {"User-Agent": "Mozilla/5.0 (compatible; failure-to-env capture)"}


def _get_json(url, retries=4, timeout=20):
    err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:  # flaky practice API — seen timeouts and 500s
            err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {retries} tries: {err}")


def expected_cheapest():
    """argmin(price) over ALL products, computed live from the site API."""
    items, page = [], 1
    while True:
        d = _get_json(f"{API_URL}/products?page={page}")
        items.extend(d["data"])
        if page >= d.get("last_page", page):
            break
        page += 1
        time.sleep(0.4)  # be kind to the shared practice API
    cheapest = min(items, key=lambda p: float(p["price"]))
    return {
        "id": cheapest["id"],
        "name": cheapest["name"],
        "price": float(cheapest["price"]),
        "n_products": len(items),
    }


def cart_contents(page):
    """Read cart rows off the /checkout page: [{name, price}]."""
    page.goto(f"{SITE_URL}/checkout", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("[data-test='product-title']", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    return page.evaluate(
        """() => [...document.querySelectorAll("table tbody tr")].map(tr => {
            const t = tr.querySelector("[data-test='product-title']");
            const cells = [...tr.querySelectorAll("td")].map(td => td.innerText.trim());
            const price = cells.map(c => c.match(/\\$\\s*([0-9.]+)/)).filter(Boolean)
                               .map(m => parseFloat(m[1]));
            return {name: t ? t.textContent.replace(/\\s+/g, " ").trim() : "",
                    price: price.length ? price[0] : null};
        }).filter(r => r.name)"""
    )


def verify(page, expected=None):
    """{passed, reason} — does the cart contain exactly the expected product?

    The checkout table exposes product names (data-test=product-title) but no
    product ids, so we match on normalized name and, when available, price.
    """
    expected = expected or expected_cheapest()
    rows = cart_contents(page)
    hit = [
        r for r in rows
        if r["name"].lower() == expected["name"].lower()
        and (r["price"] is None or abs(r["price"] - expected["price"]) < 0.01)
    ]
    if hit:
        return {
            "passed": True,
            "reason": f"cart contains {expected['name']} "
                      f"(${expected['price']}) — the cheapest of "
                      f"{expected.get('n_products', '?')} products",
        }
    got = ", ".join(r["name"] for r in rows if r["name"]) or "an empty cart"
    return {
        "passed": False,
        "reason": f"expected {expected['name']} (${expected['price']}, id "
                  f"{expected['id']}); cart has: {got}",
    }


def llm_judge(llm, instruction, page):
    """LLM-judge fallback for tasks without a scripted check."""
    dom = page.evaluate(
        "() => document.body.innerText.slice(0, 6000)")
    out = llm(
        "Judge whether this web task succeeded. Reply JSON: "
        '{"passed": true|false, "reason": "..."}\n'
        f"TASK: {instruction}\nPAGE TEXT:\n{dom}"
    )
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else {"passed": False, "reason": out}
