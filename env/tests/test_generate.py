"""Generator tests: codegen path, validation feedback loop, verbatim fallback.

All tests inject a fake llm callable — no API keys needed.
"""
import json
import os

import pytest

from env import generate

SPEC = os.path.join(os.path.dirname(__file__), "..", "spec", "env_spec.json")

VALID_COMPONENT = {
    "component_html": '<h2>{{ labels.results_heading }}</h2>'
                      '<button id="search-btn">{{ labels.search_button }}</button>'
                      '<div id="results"></div>',
    "component_js": """
async function go(p){
  const r = await fetch('api/flights?page='+p);
  const d = await r.json();
  document.getElementById('results').textContent = d.total + ' results';
}
document.getElementById('search-btn').onclick = () => go(1);
""",
}

BAD_COMPONENT = {
    "component_html": "<html><body><div>x</div></body></html>",
    "component_js": "fetch('https://evil.example.com/api');",
}


def fake_llm(payload):
    def llm(prompt):
        return json.dumps(payload)
    return llm


def test_generated_path(tmp_path):
    rep = generate.generate(SPEC, str(tmp_path), llm=fake_llm(VALID_COMPONENT))
    assert rep["path"] == "generated"
    assert (tmp_path / "component.html").exists()
    assert "api/flights" in (tmp_path / "component.js").read_text()


def test_invalid_then_verbatim_fallback(tmp_path):
    rep = generate.generate(SPEC, str(tmp_path), llm=fake_llm(BAD_COMPONENT))
    assert rep["path"] == "verbatim"
    assert rep["attempts"] == generate.MAX_ATTEMPTS
    assert rep["validation_errors"]  # recorded why codegen was rejected
    assert (tmp_path / "component.js").read_text().startswith(
        "// failure-to-env fetch shim")


def test_no_llm_goes_verbatim(tmp_path):
    rep = generate.generate(SPEC, str(tmp_path), llm=None)
    assert rep["path"] == "verbatim"


def test_verbatim_writes_shim_and_raw_wrap(tmp_path):
    rep = generate.generate(SPEC, str(tmp_path), verbatim=True)
    assert rep["path"] == "verbatim"
    html = (tmp_path / "component.html").read_text()
    js = (tmp_path / "component.js").read_text()
    assert html.startswith("{% raw %}")
    assert "api/flights" in js  # stub map reroutes captured calls


def test_static_validate_catches_external_and_undeclared():
    spec = json.load(open(SPEC))
    errs = generate.static_validate(
        "<div>ok</div>", "fetch('api/secret')", spec)
    assert any("not under a declared path" in e for e in errs)
    errs = generate.static_validate(
        "<div>ok</div>", "fetch('api/flights?page=1')", spec)
    assert errs == []


def test_report_written(tmp_path):
    generate.generate(SPEC, str(tmp_path), llm=fake_llm(VALID_COMPONENT))
    rep = json.loads((tmp_path / "generate_report.json").read_text())
    assert rep["path"] == "generated"


def test_a_spec_resolves_repo_relative_capture(tmp_path):
    spec_path = os.path.join(os.path.dirname(SPEC), "..", "..", "fixtures", "env_spec.json")
    report = generate.generate(spec_path, str(tmp_path), llm=None)
    assert report["path"] == "verbatim"
    assert 'id="product-grid"' in (tmp_path / "component.html").read_text()
    assert "function renderItems" in (tmp_path / "component.js").read_text()
    assert "fixtures/captured/component.html" not in (tmp_path / "component.html").read_text()


def test_codegen_prompt_contains_source_and_route_contract(tmp_path):
    prompts = []
    def llm(prompt):
        prompts.append(prompt)
        return json.dumps(VALID_COMPONENT)
    generate.generate(SPEC, str(tmp_path), llm=llm)
    assert "async function loadFlights" in prompts[0]
    assert "/flights/<int:eid>" in prompts[0]
    assert "total_pages" in prompts[0]


def test_missing_capture_is_not_a_successful_fallback(tmp_path):
    spec = json.load(open(SPEC))
    spec["component"]["html"] = "does-not-exist.html"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    with pytest.raises(FileNotFoundError):
        generate.generate(str(path), str(tmp_path / "out"), llm=None)
