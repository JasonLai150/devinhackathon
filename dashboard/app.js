/* Failure-to-Env dashboard — static, artifact-driven.
   Serve the repo root (dashboard/serve.py) and open /dashboard/.
   Missing artifacts degrade to notes instead of errors. */

const A = "../";                     // artifacts live at repo root
const ENV_URL = "http://localhost:5001";

async function fetchJson(path) {
  try {
    const r = await fetch(A + path, {cache: "no-store"});
    return r.ok ? await r.json() : null;
  } catch { return null; }
}
async function fetchText(path) {
  try {
    const r = await fetch(A + path, {cache: "no-store"});
    return r.ok ? await r.text() : null;
  } catch { return null; }
}
async function fetchJsonl(path) {
  const t = await fetchText(path);
  return t ? t.trim().split("\n").filter(Boolean).map(JSON.parse) : null;
}

const $ = (sel, el) => (el || document).querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const missing = b => b.appendChild(el("p", "missing",
  "artifact not yet produced — run the pipeline (see capture/record_fallback.sh)"));

function addStage(num, title, build) {
  const li = document.importNode($("#tpl-stage").content, true);
  $(".stage-num", li).textContent = num;
  $(".stage-title", li).textContent = title;
  $("#timeline").appendChild(li);
  const stage = $("#timeline").lastElementChild;
  build($(".stage-body", stage), $(".stage-badge", stage));
}

function badge(b, kind, text) { b.classList.add(kind); b.textContent = text; }

function stepsStrip(body, steps, failureStep) {
  const strip = el("div", "steps");
  for (const s of steps) {
    const d = el("div", "step" + (s.i === failureStep ? " failstep" : ""));
    if (s.screenshot_path) {
      const img = el("img"); img.src = A + s.screenshot_path; img.loading = "lazy";
      d.appendChild(img);
    }
    d.appendChild(el("div", "cap", `#${s.i} ${s.action}`));
    strip.appendChild(d);
  }
  body.appendChild(strip);
}

async function main() {
  const [traj, spec, rollouts, metrics, transfer, report, tasks] =
    await Promise.all([
      fetchJson("fixtures/trajectory.json"),
      fetchJson("fixtures/env_spec.json"),
      fetchJsonl("fixtures/rollouts.jsonl"),
      fetchJson("fixtures/metrics.json"),
      fetchJson("fixtures/trajectory_transfer.json"),
      fetchJson("env/component/generate_report.json"),
      fetchJson("env/tasks.json"),
    ]);
  $("#artifact-status").textContent =
    `loaded: ${["trajectory", "env_spec", "rollouts", "metrics", "transfer"]
      .filter((_, i) => [traj, spec, rollouts, metrics, transfer][i]).join(", ") || "none"}`;

  /* 1 — capture: failure on the real site */
  addStage(1, "Capture — agent fails on the real site", (b, badgeEl) => {
    if (!traj) return missing(b) + badge(badgeEl, "bad", "missing");
    b.appendChild(el("p", null, `${traj.site_url} — “${traj.task}”`));
    stepsStrip(b, traj.steps, traj.failure_step);
    const v = el("p", "verdict " + (traj.outcome === "passed" ? "ok" : "bad"),
      `${traj.outcome.toUpperCase()} — ${traj.checker?.reason || ""}`);
    b.appendChild(v);
    badge(badgeEl, traj.outcome === "passed" ? "ok" : "bad",
      `${traj.outcome} @ step ${traj.failure_step}`);
  });

  /* 2 — localize: env_spec.json */
  addStage(2, "Localize — trajectory → env spec", (b, badgeEl) => {
    if (!spec) return missing(b) + badge(badgeEl, "bad", "missing");
    b.appendChild(el("p", null, `interaction: ${spec.interaction}`));
    const side = el("div", "sidebyside");
    const f1 = el("figure");
    if (traj?.steps?.[0]?.screenshot_path) {
      const i1 = el("img"); i1.src = A + traj.steps[0].screenshot_path;
      f1.appendChild(i1);
    }
    f1.appendChild(el("figcaption", null, "real site (step 0)"));
    const f2 = el("figure");
    const envShot = el("img"); envShot.src = A + "fixtures/shots/env/index.png";
    envShot.onerror = () => envShot.replaceWith(el("p", "missing",
      "env screenshot not captured yet"));
    f2.appendChild(envShot);
    f2.appendChild(el("figcaption", null, "generated mini-env"));
    side.append(f1, f2);
    b.appendChild(side);
    b.appendChild(el("pre", null, JSON.stringify(spec, null, 2)));
    badge(badgeEl, "ok", spec.name || "spec");
  });

  /* 3 — generate env */
  addStage(3, "Generate env — spec → resettable mini-app", (b, badgeEl) => {
    if (!spec) return missing(b) + badge(badgeEl, "bad", "missing");
    const t = el("table", "data");
    t.innerHTML = "<tr><th>entities</th><th>mutations</th><th>stub API</th></tr>" +
      `<tr><td>${Object.keys(spec.entities || {}).join(", ")}</td>` +
      `<td>${Object.keys(spec.mutations || {}).join(", ")}</td>` +
      `<td>${(spec.backend_calls || []).map(c => `${c.method} ${c.path}`).join("<br>")}</td></tr>`;
    b.appendChild(t);
    b.appendChild(el("p", null,
      `component: ${spec.component?.name} (${spec.component?.html}, ${spec.component?.js})`));
    if (report)
      b.appendChild(el("p", "verdict " + (report.path === "failed" ? "bad" : "ok"),
        `codegen: ${report.path} after ${report.attempts} attempt(s)`));
    else
      b.appendChild(el("p", "missing", "no generate_report.json yet (B's generator)"));
    badge(badgeEl, "ok", report ? report.path : "spec ready");
  });

  /* 4 — reproduce: round-0 rollouts should be red */
  addStage(4, "Reproduce — same failure inside the env", (b, badgeEl) => {
    const r0 = (rollouts || []).filter(r => r.round === 0);
    if (!r0.length) return missing(b) + badge(badgeEl, "bad", "missing");
    const t = el("table", "data");
    t.innerHTML = "<tr><th>task</th><th>seed</th><th>steps</th><th>result</th></tr>";
    for (const r of r0) {
      const tr = el("tr", r.passed ? "pass" : "fail");
      tr.innerHTML = `<td>${r.task_id}</td><td>${r.seed}</td>` +
        `<td>${r.steps}</td><td>${r.passed ? "PASS" : "FAIL"} — ${r.reason || ""}</td>`;
      t.appendChild(tr);
    }
    b.appendChild(t);
    const nFail = r0.filter(r => !r.passed).length;
    badge(badgeEl, nFail ? "bad" : "ok",
      `${nFail}/${r0.length} failed (expected: failure reproduced)`);
  });

  /* 5 — improve: success-rate chart */
  addStage(5, "Improve — rejection-sampling loop", (b, badgeEl) => {
    const ms = metrics?.metrics;
    if (!ms || !ms.length) return missing(b) + badge(badgeEl, "bad", "missing");
    const cv = el("canvas"); cv.id = "chart";
    cv.width = 720; cv.height = 240;
    b.appendChild(cv);
    drawChart(cv, ms);
    const last = ms[ms.length - 1];
    badge(badgeEl, "ok",
      `round ${last.round}: ${(last.success_rate * 100).toFixed(0)}% (n=${last.n})`);
  });

  /* 6 — transfer: improved agent back on the real site */
  addStage(6, "Transfer — improved agent on the real site", (b, badgeEl) => {
    if (!transfer) return missing(b) + badge(badgeEl, "bad", "missing");
    stepsStrip(b, transfer.steps, null);
    b.appendChild(el("p", "verdict " + (transfer.outcome === "passed" ? "ok" : "bad"),
      `${transfer.outcome.toUpperCase()} — ${transfer.checker?.reason || ""}`));
    badge(badgeEl, transfer.outcome === "passed" ? "ok" : "bad", transfer.outcome);
  });

  /* 7 — devin session timeline + live env sessions */
  addStage(7, "Sessions — env instances + Devin timeline", (b, badgeEl) => {
    const note = el("p", null,
      "Per-session SQLite files under env/.sessions/ — one isolated DB per rollout.");
    b.appendChild(note);
    fetch(ENV_URL + "/sessions").then(r => r.json()).then(sessions => {
      const t = el("table", "data");
      t.innerHTML = "<tr><th>session</th><th>seed</th></tr>";
      for (const s of sessions.sessions || sessions || [])
        t.innerHTML += `<tr><td>${s.session_id || s.sid}</td><td>${s.seed ?? ""}</td></tr>`;
      b.appendChild(t);
      badge(badgeEl, "ok", `${(sessions.sessions || sessions).length} live sessions`);
    }).catch(() => {
      b.appendChild(el("p", "missing", `env not running (${ENV_URL}) — start with: .venv/bin/python -m env.app`));
      badge(badgeEl, "bad", "env offline");
    });
    if (tasks?.tasks) {
      b.appendChild(el("h3", null, "task variants"));
      b.appendChild(el("pre", null, JSON.stringify(tasks.tasks, null, 2)));
    }
  });
}

function drawChart(cv, ms) {
  const c = cv.getContext("2d"), W = cv.width, H = cv.height, pad = 36;
  c.fillStyle = "#0b0e13"; c.fillRect(0, 0, W, H);
  c.strokeStyle = "#30363d"; c.fillStyle = "#8b949e"; c.font = "11px Menlo";
  for (let y = 0; y <= 4; y++) {
    const py = pad + (H - 2 * pad) * y / 4;
    c.beginPath(); c.moveTo(pad, py); c.lineTo(W - pad, py); c.stroke();
    c.fillText(`${(100 - y * 25)}%`, 6, py + 4);
  }
  const x = i => ms.length === 1 ? W / 2
    : pad + (W - 2 * pad) * i / (ms.length - 1);
  const y = r => pad + (H - 2 * pad) * (1 - r.success_rate);
  c.strokeStyle = "#58a6ff"; c.lineWidth = 2; c.beginPath();
  ms.forEach((m, i) => i ? c.lineTo(x(i), y(m)) : c.moveTo(x(i), y(m)));
  c.stroke();
  c.fillStyle = "#58a6ff";
  ms.forEach((m, i) => {
    c.beginPath(); c.arc(x(i), y(m), 4, 0, 7); c.fill();
    c.fillStyle = "#e6edf3";
    c.fillText(`r${m.round}`, x(i) - 8, H - 10);
    c.fillStyle = "#58a6ff";
  });
  c.fillStyle = "#8b949e";
  c.fillText("success rate on held-out variants", pad, 16);
}

main();
