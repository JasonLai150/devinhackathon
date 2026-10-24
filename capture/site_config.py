"""Site configuration for the real target site.

Locked at kickoff (Teammate A):
  - Site: https://practicesoftwaretesting.com ("Practice Software Testing" toolshop,
    purpose-built for automation practice; no login, no CAPTCHA)
  - Task: add the cheapest product in the store to the cart
  - Failure mode: pagination — the global argmin price sits on a later page,
    so an agent that only reads page 1 picks the wrong product reliably.
"""

SITE_URL = "https://practicesoftwaretesting.com"
API_URL = "https://api.practicesoftwaretesting.com"

TASK = "Add the cheapest product in the store to your cart."

# Selectors observed on the real site (Angular SPA, data-test hooks).
SEL = {
    "product_card": "a.card[data-test^='product-']",
    "product_name": "[data-test='product-name']",
    "product_price": "[data-test='product-price']",
    "pagination": "ul.pagination",
    "pagination_link": "ul.pagination .page-link",
    "next_page": "button[data-test='pagination-next']",
    "add_to_cart": "button[data-test='add-to-cart'], #btn-add-to-cart, button:has-text('Add to cart')",
    "cart_nav": "a[href='/checkout']",
    "checkout_rows": "table tbody tr",
}

# Fallback task variants A tested in hour 1 (kept for the devlog narrative).
CANDIDATE_TASKS = [
    TASK,
    "Add the cheapest product in the 'Hand Tools' category to your cart.",
    "Add the most expensive product to your cart.",
    "Find 'Combination Pliers' and add it to your cart.",
]
