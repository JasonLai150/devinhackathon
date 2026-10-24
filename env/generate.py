"""Spec -> component codegen.

Pipeline:
  1. LLM generates component.html + component.js for the component slot,
     against a fixed slot contract (may only call declared stub endpoints,
     no external URLs, Jinja vars limited to labels/meta/sid).
  2. static validation: no external URLs, fetch() only hits declared paths,
     node --check on the JS when available, partial-only markup.
  3. smoke test: boot the app with the candidate component dir, /reset, load
     entry page, hit every backend_call.
  4. N attempts with validation errors fed back into the prompt; on failure
     fall back to verbatim paste of spec.component.html/js + a fetch shim
     that reroutes the captured component's backend calls to env stubs.

Usage:  python -m env.generate [spec_path] [--component-dir DIR] [--verbatim]

LLM provider: ANTHROPIC_API_KEY or OPENAI_API_KEY; model via ENV_LLM_MODEL.
llm callable can be injected for tests: llm(prompt) -> str.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

_BASE = os.path.dirname(__file__)
_DEFAULT_SPEC = os.path.join(_BASE, "spec", "env_spec.json")
_DEFAULT_OUT = os.path.join(_BASE, "component")

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------- prompt ---

SLOT_CONTRACT = """\
You are generating ONE UI component for a sandboxed web environment that
reproduces a real interaction an agent failed on.

The component is dropped into a Jinja page partial (component.html) plus one
JS file (component.js, loaded with a plain <script> tag). Rules:

- component.html is a PARTIAL: no <html>/<head>/<body>, no external resources.
- You may use these Jinja variables in component.html: {{ labels.<key> }},
  {{ meta }}, {{ sid }}. Nothing else. Never use {{ }} in component.js.
- The page sets <base href="/s/<sid>/"> so ALL fetches/links must be relative
  (e.g. fetch('api/flights?page=1'), href='flights/12').
- You may ONLY call these backend stub endpoints (they serve live seeded data):
{stubs}
- No http:// or https:// anywhere. No external CDNs, fonts, or APIs.
- The interaction that must be reproduced: {interaction}

Entities (seeded per-session SQLite):
{entities}

Available label keys: {label_keys}

Captured reference component from the real site (fidelity target — recreate
its behavior against the stubs, do not copy its URLs):
--- component.html ---
{captured_html}
--- component.js ---
{captured_js}

Respond with ONLY a JSON object: {{"component_html": "...", "component_js": "..."}}"""


def build_prompt(spec):
    stubs = "\n".join(
        f"  {c.get('method','GET')} {c['path'].lstrip('/')}  "
        f"(table: {c.get('table')}, paginated: {c.get('paginated', False)})"
        for c in spec.get("backend_calls", []))
    entities = json.dumps(
        {k: {"columns": v["columns"]} for k, v in spec.get("entities", {}).items()},
        indent=2)
    from .seeds import LABEL_SETS
    return SLOT_CONTRACT.format(
        stubs=stubs or "  (none)",
        interaction=spec.get("interaction", "(not described)"),
        entities=entities,
        label_keys=list(LABEL_SETS[0].keys()),
        captured_html=str(spec.get("component", {}).get("html", ""))[:8000],
        captured_js=str(spec.get("component", {}).get("js", ""))[:8000])


# ------------------------------------------------------------------- LLM ---

def _post(url, headers, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def default_llm():
    """Pick a provider from env keys. Returns callable(prompt)->str or None."""
    model = os.environ.get("ENV_LLM_MODEL")

    if os.environ.get("ANTHROPIC_API_KEY"):
        def call(prompt):
            resp = _post(
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"},
                {"model": model or "claude-sonnet-4-5",
                 "max_tokens": 4096,
                 "messages": [{"role": "user", "content": prompt}]})
            return resp["content"][0]["text"]
        return call

    if os.environ.get("OPENAI_API_KEY"):
        def call(prompt):
            resp = _post(
                "https://api.openai.com/v1/chat/completions",
                {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                {"model": model or "gpt-4.1",
                 "messages": [{"role": "user", "content": prompt}]})
            return resp["choices"][0]["message"]["content"]
        return call

    return None


def parse_response(text):
    """Extract {component_html, component_js} from an LLM response."""
    try:
        obj = json.loads(text)
        return obj["component_html"], obj["component_js"]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj["component_html"], obj["component_js"]
        except (json.JSONDecodeError, KeyError):
            pass
    html = re.search(r"```(?:html)?\s*\n(.*?)```", text, re.S)
    js = re.findall(r"```(?:js|javascript)?\s*\n(.*?)```", text, re.S)
    if html and len(js) >= 2:
        return html.group(1), js[-1]
    if html and js:
        return html.group(1), js[0]
    raise ValueError("could not parse component files from LLM response")


# ------------------------------------------------------------- validation ---

_FETCH_RE = re.compile(r"""fetch\(\s*['"`]([^'"`]+)""")


def _allowed_prefixes(spec):
    out = []
    for c in spec.get("backend_calls", []):
        out.append(c["path"].strip("/"))
    for r in spec.get("routes", {}).values():
        p = r["path"] if isinstance(r, dict) else r
        out.append(p.split("<")[0].strip("/"))
    return [p for p in out if p]


def _normalize_fetch_url(url):
    u = url.split("?")[0].strip().lstrip("./").lstrip("/")
    u = re.sub(r"^s/[^/]+/", "", u)  # strip explicit session prefix if present
    return u


def static_validate(html, js, spec):
    errors = []
    if not html or not html.strip():
        errors.append("component.html is empty")
    if not js or not js.strip():
        errors.append("component.js is empty")
    for fname, content in (("component.html", html), ("component.js", js)):
        if not content:
            continue
        for bad in ("http://", "https://"):
            if bad in content:
                errors.append(f"{fname}: external URL '{bad}...' not allowed")
    if re.search(r"</?(html|head|body)[\s>]", html or "", re.I):
        errors.append("component.html: must be a partial (no html/head/body tags)")

    allowed = _allowed_prefixes(spec)
    for url in _FETCH_RE.findall(js or ""):
        n = _normalize_fetch_url(url)
        if not any(n.startswith(p) for p in allowed):
            errors.append(f"component.js: fetch('{url}') not under a declared path "
                          f"(allowed: {allowed})")

    if js and shutil.which("node"):
        p = subprocess.run(["node", "--check", "/dev/stdin"], input=js.encode(),
                           capture_output=True)
        if p.returncode != 0:
            errors.append(f"component.js: node --check failed: "
                          f"{p.stderr.decode()[:300]}")
    return errors


def smoke_test(spec_path, component_dir):
    """Boot the app against the candidate component dir and exercise it."""
    from .app import create_app
    import tempfile

    errors = []
    old = os.environ.get("ENV_SESSIONS_DIR")
    os.environ["ENV_SESSIONS_DIR"] = tempfile.mkdtemp(prefix="envsmoke_")
    try:
        client = create_app(spec_path, component_dir=component_dir).test_client()
        r = client.post("/reset?seed=1&popup=0")
        if r.status_code != 200:
            return [f"reset failed: {r.status_code}"]
        sid = r.get_json()["session_id"]

        spec = json.load(open(spec_path))
        entry = client.get(f"/s/{sid}{spec['routes']['entry']}")
        if entry.status_code != 200:
            errors.append(f"entry page failed: {entry.status_code}")
        else:
            snippet = html_probe(component_dir)
            if snippet and snippet not in entry.get_data(as_text=True):
                errors.append("entry page does not contain generated component")

        jsr = client.get(f"/s/{sid}/component.js")
        if jsr.status_code != 200 or len(jsr.get_data()) < 10:
            errors.append(f"component.js not served: {jsr.status_code}")

        for c in spec.get("backend_calls", []):
            url = f"/s/{sid}{c['path']}" + ("?page=1" if c.get("paginated") else "")
            if client.get(url).status_code != 200:
                errors.append(f"stub {c['path']} failed")
    finally:
        if old is None:
            os.environ.pop("ENV_SESSIONS_DIR", None)
        else:
            os.environ["ENV_SESSIONS_DIR"] = old
    return errors


def html_probe(component_dir):
    """A rendered-marker substring from component.html to look for in the page.
    Strips Jinja syntax so '{{ labels.x }}foo' probes as 'foo'."""
    try:
        src = open(os.path.join(component_dir, "component.html")).read()
    except OSError:
        return None
    text = re.sub(r"\{[{%].*?[%}]\}", "", src)
    m = re.search(r"[A-Za-z0-9][A-Za-z0-9 _-]{5,40}", text)
    return m.group(0).strip() if m else None


# -------------------------------------------------------------- fallback ---

def _stub_map(spec):
    site = spec.get("site_url", "").rstrip("/")
    if not site.startswith("http"):
        site = ""
    m = {}
    for c in spec.get("backend_calls", []):
        p = c["path"]
        rel = p.lstrip("/")
        m[p] = rel
        if site:
            m[site + p] = rel
    return m


FETCH_SHIM = """\
// failure-to-env fetch shim: reroute captured backend calls to env stubs.
(function () {
  var MAP = %s;
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
    return _open.call(this, m, rewrite(u), rest);
  };
})();
"""


def _captured(field, spec, spec_path):
    """spec.component.html may be literal content or a path (relative to the
    spec file or to the env package)."""
    v = spec.get("component", {}).get(field, "")
    if v:
        for base in (os.path.dirname(spec_path), _BASE):
            candidate = os.path.join(base, v)
            if os.path.exists(candidate):
                return open(candidate).read()
    return v


def write_verbatim(spec, out_dir, spec_path):
    os.makedirs(out_dir, exist_ok=True)
    html = _captured("html", spec, spec_path)
    js = _captured("js", spec, spec_path)
    # {% raw %} keeps captured markup safe from the Jinja include.
    with open(os.path.join(out_dir, "component.html"), "w") as f:
        f.write("{% raw %}\n" + html + "\n{% endraw %}\n")
    with open(os.path.join(out_dir, "component.js"), "w") as f:
        f.write(FETCH_SHIM % json.dumps(_stub_map(spec)) + "\n" + js)


# ----------------------------------------------------------------- main ---

def generate(spec_path=_DEFAULT_SPEC, component_dir=_DEFAULT_OUT,
             llm="__default__", max_attempts=MAX_ATTEMPTS, verbatim=False):
    spec = json.load(open(spec_path))
    if llm == "__default__":
        llm = default_llm()
    report = {"spec": spec.get("name"), "attempts": 0, "path": None,
              "validation_errors": [], "smoke_errors": []}

    if not verbatim and llm is not None:
        prompt = build_prompt(spec)
        for attempt in range(1, max_attempts + 1):
            report["attempts"] = attempt
            try:
                html, js = parse_response(llm(prompt))
            except Exception as e:
                report["validation_errors"] = [f"llm/parse: {e}"]
                continue
            errs = static_validate(html, js, spec)
            if errs:
                report["validation_errors"] = errs
                prompt += "\n\nPrevious attempt failed validation:\n" + \
                          "\n".join("- " + e for e in errs) + \
                          "\nFix and resubmit the full JSON object."
                continue
            os.makedirs(component_dir, exist_ok=True)
            with open(os.path.join(component_dir, "component.html"), "w") as f:
                f.write(html)
            with open(os.path.join(component_dir, "component.js"), "w") as f:
                f.write(js)
            errs = smoke_test(spec_path, component_dir)
            if errs:
                report["smoke_errors"] = errs
                prompt += "\n\nPrevious attempt failed smoke test:\n" + \
                          "\n".join("- " + e for e in errs) + \
                          "\nFix and resubmit the full JSON object."
                continue
            report["path"] = "generated"
            break

    if report["path"] is None:
        if spec.get("component", {}).get("html"):
            write_verbatim(spec, component_dir, spec_path)
            report["smoke_errors"] = smoke_test(spec_path, component_dir)
            report["path"] = "verbatim" if not report["smoke_errors"] else "failed"
        else:
            report["path"] = "failed"

    with open(os.path.join(component_dir, "generate_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    spec_path = args[0] if args else _DEFAULT_SPEC
    out = _DEFAULT_OUT
    if "--component-dir" in sys.argv:
        i = sys.argv.index("--component-dir")
        out = sys.argv[i + 1]
    rep = generate(spec_path, out, verbatim="--verbatim" in sys.argv)
    print(json.dumps(rep, indent=2))
    sys.exit(0 if rep["path"] != "failed" else 1)
