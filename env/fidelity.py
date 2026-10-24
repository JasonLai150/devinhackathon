"""Mechanical browser checks of the toolshop interaction, not an agent benchmark."""
import time
import re

from .runtime import running_env


def validate(spec_path, component_dir, seeds=(42, 101)):
    from playwright.sync_api import sync_playwright
    from capture.agents import _cards, _dismiss_overlays, _next_page_link

    started = time.monotonic()
    report = {"kind": "mechanical toolshop browser probe", "passed": False,
              "checks": [], "errors": []}
    try:
        with running_env(spec_path, component_dir) as (url, app):
            spec = app.config["SPEC"]
            if spec["routes"]["detail"]["entity"] != "products":
                raise ValueError("browser fidelity probe currently supports the toolshop only")
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                try:
                    for seed in seeds:
                        client = app.test_client()
                        session = client.post(f"/reset?seed={seed}&popup=1&labels=3").json
                        sid = session["session_id"]
                        state = client.get(f"/state?session={sid}").json
                        expected = state["tables"]["products"]
                        page = browser.new_page()
                        page.set_default_timeout(5000)
                        page.on("pageerror", lambda error: report["errors"].append(str(error)))
                        prefix = f"{url}/s/{sid}/"
                        page.route("**/*", lambda route: route.continue_()
                                   if route.request.url.startswith(prefix) else route.abort())
                        page.goto(url + session["entry_url"])
                        _dismiss_overlays(page)
                        seen, pages = [], 0
                        while True:
                            page.wait_for_selector("a.card[data-test^='product-']")
                            cards = _cards(page)
                            pages += 1
                            for card in cards:
                                card["page"] = pages
                            if any(c["price"] is None for c in cards):
                                raise AssertionError("rendered product price is missing")
                            expected_ids = [str(p["id"]) for p in expected[len(seen):len(seen) + len(cards)]]
                            if [c["id"] for c in cards] != expected_ids:
                                raise AssertionError("component changed seeded listing order or duplicated a page")
                            seen.extend(cards)
                            nxt = _next_page_link(page)
                            if nxt is None:
                                break
                            if pages >= 20:
                                raise AssertionError("pagination never ended")
                            previous = cards[0]["id"]
                            nxt.click()
                            page.wait_for_function(
                                "id => document.querySelector(\"a.card[data-test^='product-']\")"
                                "?.getAttribute('data-test') !== 'product-' + id", arg=previous)
                        if pages < 2 or len(seen) != len(expected):
                            raise AssertionError("pagination did not expose all seeded products")
                        target = min(seen, key=lambda p: p["price"])
                        if target["page"] != pages:
                            page.locator("ul.pagination .page-link").filter(
                                has_text=re.compile(f"^{target['page']}$")).click()
                        link = page.locator(f"a[data-test='product-{target['id']}']")
                        link.wait_for()
                        link.click()
                        _dismiss_overlays(page)
                        with page.expect_response(lambda r: r.request.method == "POST"):
                            page.locator("[data-test='add-to-cart']").click()
                        verdict = client.post("/verify", json={
                            "task_id": "shop-cheapest", "session_id": sid}).json
                        if not verdict["passed"]:
                            raise AssertionError(verdict["reason"])
                        report["checks"].append({"seed": seed, "pages": pages,
                                                 "items": len(seen), "mutation_verified": True})
                        page.close()
                finally:
                    browser.close()
    except Exception as error:
        report["errors"].append(str(error))
    report["passed"] = len(report["checks"]) == len(seeds) and not report["errors"]
    report["elapsed_s"] = round(time.monotonic() - started, 2)
    return report
