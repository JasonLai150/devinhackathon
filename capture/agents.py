"""Agent strategies. Same interface for scripted and LLM agents:

    agent.run(page, rec, task)

``rec(action_desc, failure_hint=False)`` is called by the strategy after each
meaningful browser action; the harness logs screenshot + DOM + url per call.
``failure_hint`` marks the step that locked in the wrong answer (→ failure_step
in trajectory.json when the run fails).
"""

import json
import time
import re

from .site_config import SEL, SITE_URL

_SETTLE_MS = 1800  # SPA re-render / API latency on the real site
POLICY_VERSION = "toolshop-observe-act-v2"


def _dismiss_overlays(page):
    for sel in (
        "button:has-text('Accept')",
        "button:has-text('No thanks')",
        "a:has-text('No thanks')",
        "#onetrust-accept-btn-handler",
        ".cookie-consent button",
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=300):
                loc.click(timeout=800)
                page.wait_for_timeout(400)
        except Exception:
            pass


def _cards(page):
    """Extract {id, name, price, href} for every product card on the page."""
    return page.evaluate(
        """() => [...document.querySelectorAll("a.card[data-test^='product-']")]
            .map(a => {
                const t = s => { const el = a.querySelector(s);
                                 return el ? el.textContent.trim() : ""; };
                const price = parseFloat(
                    t("[data-test='product-price']").replace(/[^0-9.]/g, ""));
                return {id: a.getAttribute("data-test").replace("product-", ""),
                        name: t("[data-test='product-name']"),
                        price: isNaN(price) ? null : price,
                        href: a.getAttribute("href")};
            })"""
    )


def _wait_cards(page, prev_first_id=None):
    """Wait until the product grid is populated (and changed if prev id given)."""
    page.wait_for_selector(SEL["product_card"], timeout=15000)
    if prev_first_id:
        try:
            page.wait_for_function(
                "pid => document.querySelector(\"a.card[data-test^='product-']\")"
                "    .getAttribute('data-test') !== 'product-' + pid",
                arg=prev_first_id,
                timeout=8000,
            )
        except Exception:
            pass
    page.wait_for_timeout(_SETTLE_MS)


def _goto_listing(page, entry_url=SITE_URL):
    page.goto(entry_url, wait_until="domcontentloaded")
    _dismiss_overlays(page)
    _wait_cards(page)


def _open_product(page, card):
    target = page.evaluate("(href) => new URL(href, document.baseURI).href", card["href"])
    page.goto(target, wait_until="domcontentloaded")
    _dismiss_overlays(page)
    page.wait_for_selector(SEL["add_to_cart"], timeout=15000)
    page.wait_for_timeout(_SETTLE_MS)


def _add_to_cart(page):
    page.locator(SEL["add_to_cart"]).first.click()
    page.wait_for_timeout(_SETTLE_MS)


def _next_page_link(page):
    """The '»' pagination control, or None when disabled/absent."""
    li = page.locator("ul.pagination li:has(.page-link[aria-label='Next'])")
    if not li.count() or "disabled" in (li.get_attribute("class") or ""):
        return None
    return li.locator(".page-link").first


def _current_page(page):
    try:
        return int(page.locator("ul.pagination li.active").inner_text())
    except Exception:
        return -1


# ---------------------------------------------------------------- naive

def run_naive(page, rec, task, entry_url=SITE_URL):
    """Baseline failure: reads only page 1, books cheapest *visible* item."""
    _goto_listing(page, entry_url)
    rec("navigate to product listing (page 1)")
    cards = [c for c in _cards(page) if c["price"] is not None]
    target = min(cards, key=lambda c: c["price"])
    rec(
        f"read page-1 products; picked cheapest visible: {target['name']} "
        f"(${target['price']})",
    )
    _open_product(page, target)
    rec(f"open product detail: {target['name']}")
    _add_to_cart(page)
    rec("click 'Add to cart'", failure_hint=True)


# ---------------------------------------------------------------- smart

def run_smart(page, rec, task, entry_url=SITE_URL):
    """Fixed behaviour: paginate through every page before comparing."""
    _goto_listing(page, entry_url)
    rec("navigate to product listing (page 1)")
    all_cards, seen_ids = [], set()
    for _ in range(20):  # safety bound on pagination depth
        first = (page.locator(SEL["product_card"]).first.get_attribute("data-test") or "")[8:]
        for c in _cards(page):
            if c["id"] not in seen_ids and c["price"] is not None:
                all_cards.append(c)
                seen_ids.add(c["id"])
        rec(f"read products on page {_current_page(page)} "
            f"({len(all_cards)} products seen so far)")
        nxt = _next_page_link(page)
        if nxt is None:
            break
        nxt.click()
        _wait_cards(page, prev_first_id=first)
        rec(f"click next page (now on page {_current_page(page)})")
    target = min(all_cards, key=lambda c: c["price"])
    rec(f"compared {len(all_cards)} products; global cheapest: "
        f"{target['name']} (${target['price']})")
    _open_product(page, target)
    rec(f"open product detail: {target['name']}")
    _add_to_cart(page)
    rec("click 'Add to cart'")


# ------------------------------------------------------------------- LLM

_OBSERVE_JS = """() => {
  const cards = [...document.querySelectorAll("a.card[data-test^='product-']")]
    .map(a => ({id: a.getAttribute("data-test").replace("product-",""),
                name: a.querySelector("[data-test='product-name']")?.textContent.trim(),
                price: a.querySelector("[data-test='product-price']")?.textContent.trim(),
                href: a.getAttribute("href")}));
  const pages = [...document.querySelectorAll("ul.pagination .page-link")]
    .map(x => (x.getAttribute("aria-label") || x.textContent).trim());
  const detailName = document.querySelector("[data-test='product-name']")?.textContent.trim();
  const addBtn = !!document.querySelector("button[data-test='add-to-cart'], #btn-add-to-cart");
  const cartBadge = document.querySelector("[data-test='cart-quantity'], a[href='/checkout']")?.textContent.trim();
  return {url: location.href, title: document.title, cards, pages,
          can_add_to_cart: addBtn, detail: addBtn ? detailName : null, cartBadge,
          visible_text: document.body.innerText.slice(0, 6000)};
}"""

_PROMPT = """You are a web-browsing agent on {site}.
TASK: {task}
{memory}
CURRENT STATE (JSON): {state}

Pick ONE next action, reply with JSON only:
  {{"action":"goto_page","n":<page number visible in pages>}}
  {{"action":"open_product","id":"<product id from cards>"}}
  {{"action":"add_to_cart"}}                    (on a product detail page)
  {{"action":"done"}}                           (task complete)
Choose based on the task and the observations gathered so far."""


def run_llm(page, rec, task, llm, memory="", max_steps=30, entry_url=SITE_URL):
    _goto_listing(page, entry_url)
    rec("navigate to product listing (page 1)")
    history = []
    seen = {}
    action_history = []
    for _ in range(max_steps):
        state = page.evaluate(_OBSERVE_JS)
        history.append(state)
        for card in state["cards"]:
            seen[str(card["id"])] = card
        raw = llm(
            _PROMPT.format(
                site=entry_url, task=task, memory=memory,
                state=json.dumps({"current": state, "recent_observations": history[-3:],
                                  "seen_products": list(seen.values()), "actions": action_history})
            )
        )
        act = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        action_history.append(act)
        a = act.get("action")
        if a == "done":
            rec("agent decided task is done")
            return
        if a == "goto_page":
            previous = state["cards"][0]["id"] if state["cards"] else None
            page.locator("ul.pagination .page-link").filter(
                has_text=re.compile(f"^{int(act['n'])}$")).first.click()
            _wait_cards(page, prev_first_id=previous)
            rec(f"LLM: go to page {act['n']}")
        elif a == "open_product":
            card = seen.get(str(act["id"]))
            if card is None:
                raise ValueError("agent selected an unseen product")
            _open_product(page, card)
            rec(f"LLM: open product {act['id']}")
        elif a == "add_to_cart":
            _add_to_cart(page)
            rec("LLM: click 'Add to cart'")
        else:
            rec(f"LLM: unhandled action {act}")
            time.sleep(0.3)
    rec(f"hit step budget ({max_steps})")


def run_auto(page, rec, task, memory="", entry_url=SITE_URL):
    """Scripted memory-driven rehearsal, not LLM learning."""
    strategy = run_smart if "paginate" in memory.lower() else run_naive
    strategy(page, rec, task, entry_url=entry_url)


STRATEGIES = {"naive": run_naive, "smart": run_smart}
