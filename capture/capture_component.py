"""Capture the failing component from the real site for env_spec.json.

Saves the rendered product-grid + pagination markup (the component the failure
lives in) to fixtures/captured/component.html and writes a component.js that
reproduces the same *observable* interaction — fetch a paginated list and
re-render on page click. The real site's JS is a minified Angular bundle; the
captured markup is verbatim, the JS is a hand-recreated reference (disclosed in
devlog).

    python -m capture.capture_component
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

from .site_config import SITE_URL

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "fixtures" / "captured"

# Grab the smallest ancestor that contains both the product grid and the
# pagination control — i.e. the whole "component" an agent interacts with.
_COMPONENT_JS = """() => {
  const cards = [...document.querySelectorAll("a.card[data-test^='product-']")];
  const pager = document.querySelector("ul.pagination");
  if (!cards.length || !pager) return null;
  let el = cards[0];
  while (el && !(el.contains(pager) && cards.every(c => el.contains(c))))
    el = el.parentElement;
  return el ? el.outerHTML : null;
}"""

# Reference implementation of the same interaction, written against the env
# stub contract (GET api/products?page=N -> {items, page, per_page, total,
# total_pages}). Used verbatim when codegen falls back; also the fidelity
# reference for LLM codegen.
INTERACTION_JS = """/* product listing + pagination — reproduces the real site's interaction.
   Fetches the paginated stub API, renders a card grid + page controls.
   Detail links resolve under the session <base href>. */
(function () {
  const grid = document.getElementById("product-grid");
  const pager = document.getElementById("pagination");
  if (!grid || !pager) return;

  let state = {page: 1, totalPages: 1};

  function renderItems(items) {
    grid.innerHTML = "";
    for (const p of items) {
      const a = document.createElement("a");
      a.className = "card product-card";
      a.setAttribute("href", "products/" + p.id);
      a.innerHTML =
        '<div class="card-body">' +
        '<h5 class="product-name"></h5>' +
        '<p class="product-price"></p>' +
        '<p class="product-category text-muted"></p>' +
        "</div>";
      a.querySelector(".product-name").textContent = p.name;
      a.querySelector(".product-price").textContent =
        "$" + Number(p.price).toFixed(2);
      a.querySelector(".product-category").textContent = p.category || "";
      grid.appendChild(a);
    }
  }

  function renderPager() {
    pager.innerHTML = "";
    for (let n = 1; n <= state.totalPages; n++) {
      const li = document.createElement("li");
      li.className = "page-item" + (n === state.page ? " active" : "");
      const btn = document.createElement("button");
      btn.className = "page-link";
      btn.textContent = n;
      btn.addEventListener("click", () => load(n));
      li.appendChild(btn);
      pager.appendChild(li);
    }
    const next = document.createElement("li");
    next.className =
      "page-item" + (state.page >= state.totalPages ? " disabled" : "");
    const nb = document.createElement("button");
    nb.className = "page-link";
    nb.setAttribute("aria-label", "Next");
    nb.textContent = "\\u00bb";
    nb.addEventListener("click", () => load(state.page + 1));
    next.appendChild(nb);
    pager.appendChild(next);
  }

  function load(page) {
    fetch("api/products?page=" + page)
      .then(r => r.json())
      .then(d => {
        state.page = d.page;
        state.totalPages = d.total_pages;
        renderItems(d.items);
        renderPager();
      });
  }

  load(1);
})();
"""

# Shell the JS drives: keeps the real site's captured markup shape available to
# codegen, with the two mount points the script needs.
COMPONENT_SHELL = """<!-- Captured component shell: product grid + pagination mount points. -->
<div class="product-listing" data-component="product-listing">
  <div id="product-grid" class="row"></div>
  <ul id="pagination" class="pagination"></ul>
</div>
"""


def capture(out_dir=OUT):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        page = p.chromium.launch().new_page()
        page.goto(SITE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("a.card[data-test^='product-']", timeout=15000)
        page.wait_for_timeout(2500)
        markup = page.evaluate(_COMPONENT_JS)
        page.context.close()

    if markup:
        (out_dir / "real_markup.html").write_text(markup)
        print(f"captured real markup -> {out_dir/'real_markup.html'} "
              f"({len(markup)} bytes)")
    else:
        print("WARN: could not isolate component markup")

    (out_dir / "component.html").write_text(COMPONENT_SHELL)
    (out_dir / "component.js").write_text(INTERACTION_JS)
    print(f"component shell + interaction js -> {out_dir}")
    return out_dir


if __name__ == "__main__":
    capture()
