/* Failure-to-Env dashboard — static, artifact-driven, data-viz first.
   Serve repo root (dashboard/serve.py) and open /dashboard/.
   Sources: a run manifest and its matched artifacts; ?artifacts= selects a run. */

const A = "../";
const ARTIFACTS = new URLSearchParams(location.search).get("artifacts") || "loop/artifacts";

async function fetchJson(path) {
  try { const r = await fetch(A + path, {cache: "no-store"});
        return r.ok ? await r.json() : null; } catch { return null; }
}
async function fetchText(path) {
  try { const r = await fetch(A + path, {cache: "no-store"});
        return r.ok ? await r.text() : null; } catch { return null; }
}
async function fetchJsonl(...paths) {
  for (const p of paths) {
    const t = await fetchText(p);
    if (t) {
      const rows = t.trim().split("\n").filter(Boolean).map(JSON.parse);
      if (rows.length && !String(rows[0].reason).startsWith("<")) return rows;
    }
  }
  return null;
}
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const pct = r => `${(r * 100).toFixed(0)}%`;

/* ============================ charts ============================ */

function axes(c, W, H, pad) {
  c.strokeStyle = "#30363d"; c.fillStyle = "#8b949e"; c.font = "11px Menlo";
  for (let y = 0; y <= 4; y++) {
    const py = pad + (H - 2 * pad) * y / 4;
    c.beginPath(); c.moveTo(pad, py); c.lineTo(W - pad, py); c.stroke();
    c.fillText(`${100 - y * 25}%`, 6, py + 4);
  }
}

function drawRateChart(cv, ms) {
  const c = cv.getContext("2d"), W = cv.width, H = cv.height, pad = 38;
  c.fillStyle = "#0b0e13"; c.fillRect(0, 0, W, H);
  axes(c, W, H, pad);
  const x = i => ms.length === 1 ? W / 2 : pad + (W - 2 * pad) * i / (ms.length - 1);
  const y = r => pad + (H - 2 * pad) * (1 - r.success_rate);
  const phaseColor = m => ({repro: "#f85149", eval: "#58a6ff",
                            transfer: "#3fb950"}[m.phase] || "#8b949e");
  c.strokeStyle = "#58a6ff"; c.lineWidth = 2; c.beginPath();
  ms.forEach((m, i) => i ? c.lineTo(x(i), y(m)) : c.moveTo(x(i), y(m)));
  c.stroke();
  ms.forEach((m, i) => {
    if (m.ci95) {
      c.strokeStyle = "#8b949e"; c.lineWidth = 1;
      c.beginPath();
      c.moveTo(x(i), y({success_rate: m.ci95[0]}));
      c.lineTo(x(i), y({success_rate: m.ci95[1]}));
      c.stroke();
    }
    c.fillStyle = phaseColor(m);
    c.beginPath(); c.arc(x(i), y(m), 5, 0, 7); c.fill();
    c.fillStyle = "#e6edf3";
    c.fillText(`r${m.round}`, x(i) - 8, H - 10);
    c.fillText(`${pct(m.success_rate)} · n=${m.n}`, x(i) - 24, Math.max(14, y(m) - 10));
    c.fillStyle = "#8b949e"; c.font = "9px Menlo";
    if (m.phase) c.fillText(m.phase, x(i) - 12, H - 22);
    c.font = "11px Menlo";
  });
}

function drawBars(cv, entries, color = "#58a6ff") {
  const c = cv.getContext("2d"), W = cv.width, H = cv.height;
  c.fillStyle = "#0b0e13"; c.fillRect(0, 0, W, H);
  const max = Math.max(1, ...entries.map(e => e.v));
  const rowH = (H - 30) / entries.length;
  c.font = "11px Menlo";
  entries.forEach((e, i) => {
    const y = 10 + i * rowH;
    const bw = (W - 170) * e.v / max;
    c.fillStyle = e.color || color;
    c.fillRect(150, y, Math.max(2, bw), rowH - 10);
    c.fillStyle = "#8b949e";
    c.fillText(e.label.slice(0, 20), 6, y + rowH / 2 + 3);
    c.fillStyle = "#e6edf3";
    c.fillText(String(e.v), 154 + bw, y + rowH / 2 + 3);
  });
}

function drawHistogram(cv, vals) {
  const c = cv.getContext("2d"), W = cv.width, H = cv.height, pad = 26;
  c.fillStyle = "#0b0e13"; c.fillRect(0, 0, W, H);
  const buckets = {};
  vals.forEach(v => buckets[v] = (buckets[v] || 0) + 1);
  const ks = Object.keys(buckets).map(Number).sort((a, b) => a - b);
  const max = Math.max(...Object.values(buckets));
  const bw = (W - 2 * pad) / Math.max(1, ks.length);
  c.font = "10px Menlo";
  ks.forEach((k, i) => {
    const h = (H - 2 * pad) * buckets[k] / max;
    c.fillStyle = "#58a6ff";
    c.fillRect(pad + i * bw + 3, H - pad - h, bw - 6, h);
    c.fillStyle = "#e6edf3";
    c.fillText(buckets[k], pad + i * bw + bw / 2 - 4, H - pad - h - 5);
    c.fillStyle = "#8b949e";
    c.fillText(`${k}st`, pad + i * bw + bw / 2 - 8, H - 8);
  });
}

/* ============================ render ============================ */

function kpi(label, value, note, cls) {
  const k = el("div", "kpi " + (cls || ""));
  k.append(el("div", "k-label", label), el("div", "k-value", value));
  if (note) k.append(el("div", "k-note", note));
  return k;
}

function filmstrip(host, steps, failureStep) {
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
  host.appendChild(strip);
}

function replay(host, trajectory, videoPath) {
  const video = videoPath || trajectory?.video_path;
  if (video) {
    const player = el("video");
    player.controls = true; player.preload = "metadata"; player.playsInline = true;
    player.src = A + video;
    host.appendChild(player);
    host.appendChild(el("p", "muted", "Recorded browser interaction"));
  } else if (trajectory?.steps?.length) {
    filmstrip(host, trajectory.steps, trajectory.failure_step);
    host.appendChild(el("p", "muted", "Recorded screenshots · no video attached"));
  } else {
    host.appendChild(el("p", "missing", "No recording available for this run"));
  }
  if (trajectory?.checker)
    host.appendChild(el("p", "verdict " + (trajectory.checker.passed ? "ok" : "bad"),
      `${trajectory.checker.passed ? "PASS" : "FAIL"} — ${trajectory.checker.reason}`));
}

async function main() {
  const manifest = await fetchJson(`${ARTIFACTS}/run_manifest.json`);
  const [traj, spec, repro, metrics, notes, transferR, rollouts] = await Promise.all([
    manifest ? (manifest.trajectory_path ? fetchJson(manifest.trajectory_path) : Promise.resolve(null))
             : fetchJson("fixtures/trajectory.json"),
    manifest ? Promise.resolve(manifest.spec) : fetchJson("fixtures/env_spec.json"),
    manifest ? fetchJson(`${ARTIFACTS}/repro_report.json`) : Promise.resolve(null),
    manifest ? fetchJson(`${ARTIFACTS}/metrics.json`) : Promise.resolve(null),
    manifest ? fetchText(`${ARTIFACTS}/skill_notes.md`) : Promise.resolve(null),
    manifest ? fetchJson(`${ARTIFACTS}/transfer_result.json`) : Promise.resolve(null),
    manifest ? fetchJsonl(`${ARTIFACTS}/rollouts.jsonl`) : Promise.resolve(null),
  ]);
  const report = manifest?.generation_report;
  const r0 = (rollouts || []).filter(r => r.phase === "repro");
  const fails = r0.filter(r => !r.passed);
  const sigFails = fails.filter(r => r.signature_failure);
  const evals = (metrics?.metrics || []).filter(m => m.phase === "eval");
  const lastEval = evals[evals.length - 1];
  const transferTrace = transferR?.trajectory_path ? await fetchJson(transferR.trajectory_path) : null;
  replay(document.getElementById("baseline-player"), traj);
  replay(document.getElementById("transfer-player"), transferTrace, transferR?.video_path);
  const model = manifest?.agent_metadata;
  document.getElementById("run-status").textContent =
    `${manifest?.status || "archived"} · ${manifest?.reason || "Inspect the recorded outcomes"} ` +
    (model ? `· ${model.provider}/${model.model || model.strategy} · ${model.max_steps} steps` : "");

  /* ---- KPI strip ---- */
  const kpis = document.getElementById("kpis");
  kpis.append(
    kpi("real-site baseline", traj ? traj.outcome.toUpperCase() : "—",
        traj ? `@ step ${traj.failure_step}` : "no trajectory",
        traj && traj.outcome !== "passed" ? "bad" : "ok"),
    kpi("env repro (r0)", r0.length
        ? `${sigFails.length}/${fails.length} signature` : "—",
        `${fails.length}/${r0.length} episodes failed`,
        repro?.reproduced ? "ok" : ""),
    kpi("eval success", lastEval ? pct(lastEval.success_rate) : "—",
        lastEval ? `round ${lastEval.round} · n=${lastEval.n}` : "no metrics",
        lastEval && lastEval.success_rate > .5 ? "ok" : ""),
    kpi("transfer", transferR ? `${transferR.passed ? "PASS" : "FAIL"}` : "—",
        transferR?.mode === "real" ? "real-site transfer" : "sandbox rehearsal",
        transferR?.passed ? "ok" : ""),
    kpi("episodes", rollouts ? String(rollouts.length) : "—",
        rollouts ? `${new Set(rollouts.map(r => r.session_id)).size} sessions` : "",
        ""),
  );

  /* ---- pipeline strip ---- */
  const stages = [
    ["1", "capture", traj ? (traj.outcome === "failed" ? "bad" : "ok") : "dim",
     traj ? `${traj.outcome} @${traj.failure_step}` : "no artifact"],
    ["2", "localize", spec ? "ok" : "dim", spec ? spec.name : "no spec"],
    ["3", "generate env", report ? (report.path === "failed" ? "bad" : "ok") : "warn",
     report ? `${report.path} ×${report.attempts}` : "spec ready"],
    ["4", "reproduce", r0.length ? (repro?.reproduced ? "ok" : "warn") : "dim",
     r0.length ? `${sigFails.length} sig-failures` : "no rollouts"],
    ["5", "improve", lastEval ? "ok" : "dim",
     lastEval ? pct(lastEval.success_rate) : "no metrics"],
    ["6", "transfer", transferR?.passed ? "ok" : "dim",
     transferR ? `${transferR.mode}` : "pending"],
    ["7", "demo", "dim", manifest?.mode || "no run manifest"],
  ];
  const pipe = document.getElementById("pipeline");
  for (const [n, name, cls, note] of stages) {
    const s = el("div", "pipe-stage");
    s.append(el("div", "p-num", `stage ${n}`), el("div", "p-name", name),
             el("div", "p-dot " + cls, note));
    pipe.appendChild(s);
  }

  /* ---- success-rate chart ---- */
  const comparable = evals.length && evals[0].round === 0 && evals[0].evaluation_id &&
    evals.every(m => m.evaluation_id === evals[0].evaluation_id);
  if (comparable) {
    drawRateChart(document.getElementById("chart-rate"), evals);
    document.getElementById("rate-legend").innerHTML =
      '<span><i class="sw" style="background:#58a6ff"></i>held-out evaluation</span>';
    document.getElementById("curve-notes").textContent =
      "Identical seeds, task and perturbations each round. Bars: 95% Wilson intervals. " +
      "Reproduction and real-site transfer are separate results.";
  } else {
    document.getElementById("curve-notes").textContent =
      "No comparable evaluation baseline yet. Repro and transfer results are not a learning curve.";
  }

  /* ---- failure-signature chart ---- */
  if (fails.length) {
    drawBars(document.getElementById("chart-sig"), [
      {label: "never_paginated",
       v: fails.filter(f => f.never_paginated).length, color: "#da7bff"},
      {label: "booked_from_page1",
       v: fails.filter(f => f.booked_from_page1).length, color: "#da7bff"},
      {label: "signature_failure",
       v: sigFails.length, color: "#da7bff"},
      {label: "total failures", v: fails.length, color: "#f85149"},
    ]);
  } else {
    drawBars(document.getElementById("chart-sig"), [{label: "no data", v: 0}]);
  }

  /* ---- steps histogram ---- */
  if (rollouts?.length)
    drawHistogram(document.getElementById("chart-steps"),
                  rollouts.map(r => r.steps).filter(s => s != null));

  /* ---- episode matrix ---- */
  const matrix = document.getElementById("matrix");
  const detail = document.getElementById("episode-detail");
  if (rollouts) {
    const groups = {};
    for (const r of rollouts) {
      const k = `r${r.round} ${r.phase || ""} ${r.agent || ""}`.trim();
      (groups[k] = groups[k] || []).push(r);
    }
    for (const [label, rows] of Object.entries(groups)) {
      const row = el("div", "mrow");
      row.appendChild(el("div", "mlabel", label));
      const cells = el("div", "cells");
      for (const r of rows) {
        const cell = el("div",
          "cell " + (r.signature_failure ? "sig" : r.passed ? "pass" : "fail"));
        cell.title = `seed ${r.seed} — ${r.passed ? "PASS" : "FAIL"}`;
        cell.onclick = async () => {
          matrix.querySelectorAll(".cell.sel")
            .forEach(c => c.classList.remove("sel"));
          cell.classList.add("sel");
          detail.classList.remove("hidden");
          detail.innerHTML = "";
          detail.append(
            el("div", null,
               `${r.task_id} · seed ${r.seed} · ${r.agent} · ${r.steps} steps`),
            el("div", "verdict " + (r.passed ? "ok" : "bad"),
               `${r.passed ? "PASS" : "FAIL"} — ${r.reason || ""}`));
          if (r.actions) {
            const trace = el("div", "trace");
            for (const a of r.actions)
              trace.appendChild(el("span",
                "hop" + (a.startsWith("POST") ? " post" : ""), a));
            detail.appendChild(trace);
          }
          if (r.trajectory_path || r.video_path) {
            const recording = r.trajectory_path ? await fetchJson(r.trajectory_path) : null;
            if (cell.classList.contains("sel")) replay(detail, recording, r.video_path);
          }
        };
        cells.appendChild(cell);
      }
      row.appendChild(cells);
      matrix.appendChild(row);
    }
  } else {
    matrix.appendChild(el("p", "missing", "no rollouts yet"));
  }

  /* ---- real-site trajectory ---- */
  const rt = document.getElementById("real-traj");
  if (traj) {
    rt.appendChild(el("p", "muted", `${traj.site_url} — “${traj.task}”`));
    filmstrip(rt, traj.steps, traj.failure_step);
    rt.appendChild(el("p", "verdict " + (traj.outcome === "passed" ? "ok" : "bad"),
      `${traj.outcome.toUpperCase()} — ${traj.checker?.reason || ""}`));
  } else rt.appendChild(el("p", "missing", "no trajectory.json"));

  /* ---- spec + side-by-side ---- */
  const si = document.getElementById("spec-info");
  if (spec) {
    const t = el("table", "data");
    t.innerHTML =
      `<tr><td>entities</td><td>${Object.keys(spec.entities || {}).join(", ")}</td></tr>` +
      `<tr><td>mutations</td><td>${Object.keys(spec.mutations || {}).join(", ")}</td></tr>` +
      `<tr><td>stub api</td><td>${(spec.backend_calls || []).map(c => c.path).join(", ")}</td></tr>` +
      `<tr><td>component</td><td>${spec.component?.name || "—"}</td></tr>`;
    si.appendChild(t);
    const sbs = document.getElementById("sbs");
    const f1 = el("figure");
    if (traj?.steps?.[0]?.screenshot_path) {
      const i1 = el("img"); i1.src = A + traj.steps[0].screenshot_path;
      f1.appendChild(i1);
    }
    f1.appendChild(el("figcaption", null, "real site"));
    const f2 = el("figure");
    const envShot = el("img");
    if (manifest?.env_screenshot) envShot.src = A + manifest.env_screenshot;
    else if (!manifest) envShot.src = A + "fixtures/shots/env/index.png";
    envShot.onerror = () => envShot.replaceWith(el("p", "missing",
      "env screenshot not captured yet"));
    f2.appendChild(manifest && !manifest.env_screenshot ?
      el("p", "missing", "No env screenshot attached to this run") : envShot);
    f2.appendChild(el("figcaption", null, "generated mini-env"));
    sbs.append(f1, f2);
  } else si.appendChild(el("p", "missing", "no env_spec.json"));

  /* ---- skill notes ---- */
  document.getElementById("skill-notes").textContent =
    notes || "no skill_notes.md yet";

  /* ---- sessions ---- */
  const sess = document.getElementById("sessions");
  const envUrl = manifest?.env_url || "http://localhost:5001";
  if (manifest?.env_running === false) {
    sess.appendChild(el("p", "muted", "Experiment sandbox is stopped. Browser recordings and results remain available."));
  } else fetch(envUrl + "/sessions").then(r => r.json()).then(d => {
    const list = d.sessions || d || [];
    const t = el("table", "data");
    t.innerHTML = "<tr><th>session</th><th>seed</th></tr>";
    for (const s of list)
      t.innerHTML += `<tr><td>${s.session_id || s.sid}</td><td>${s.seed ?? ""}</td></tr>`;
    sess.appendChild(t);
  }).catch(() => {
    sess.appendChild(el("p", "missing",
      `env offline — start with: ENV_SPEC=$PWD/fixtures/env_spec.json ` +
      `ENV_COMPONENT_DIR=$PWD/fixtures/captured .venv/bin/python -m env.app`));
  });

  /* ---- status line ---- */
  document.getElementById("artifact-status").textContent =
    `${manifest ? `${manifest.mode} run (${manifest.agent}) · ${ARTIFACTS}` :
       "No run manifest: run loop.demo for matched metrics"} · ` +
    `${rollouts?.length || 0} episodes · ` +
    `${metrics?.metrics?.length || 0} rounds · ` +
    `env ${spec ? "spec: " + spec.name : "no spec"}`;
}

main();
