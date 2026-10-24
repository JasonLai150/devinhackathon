// failure-to-env fetch shim: reroute captured backend calls to env stubs.
(function () {
  var MAP = {"/api/products": "api/products", "https://practicesoftwaretesting.com/api/products": "api/products"};
  function rewrite(u) {
    u = String(u);
    for (var k in MAP) if (u.indexOf(k) === 0) return MAP[k] + u.slice(k.length);
    return u;
  }
  var _fetch = window.fetch.bind(window);
  window.fetch = function (u) {
    var rest = Array.prototype.slice.call(arguments, 1);
    return _fetch.apply(null, [rewrite(u)].concat(rest));
  };
  var _open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) {
    var rest = Array.prototype.slice.call(arguments, 2);
    return _open.apply(this, [m, rewrite(u)].concat(rest));
  };
})();

/* product listing + pagination — reproduces the real site's interaction.
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
      a.setAttribute("data-test", "product-" + p.id);
      a.setAttribute("href", "products/" + p.id);
      a.innerHTML =
        '<div class="card-body">' +
        '<h5 class="product-name"></h5>' +
        '<p class="product-price"></p>' +
        '<p class="product-category text-muted"></p>' +
        "</div>";
      a.querySelector(".product-name").textContent = p.name;
      a.querySelector(".product-name").setAttribute("data-test", "product-name");
      a.querySelector(".product-price").setAttribute("data-test", "product-price");
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
    nb.textContent = "\u00bb";
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
