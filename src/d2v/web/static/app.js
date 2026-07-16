// d2v Web GUI — フロントエンド制御（Phase 3: d2v 生成フロー）

let META = null;
let CURRENT_JOB = null;
let SCORES = []; // sparkline 用のスコア履歴

// ── 初期化 ────────────────────────────────────────────────────────
function switchTab(target) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === target)
  );
  document.querySelectorAll(".panel").forEach((p) => {
    const match = p.id === `panel-${target}`;
    p.classList.toggle("active", match);
    p.hidden = !match;
  });
}

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
}

async function loadMeta() {
  const badge = document.getElementById("provider-badge");
  try {
    const res = await fetch("/api/meta");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    META = await res.json();
    badge.textContent = `provider: ${META.llm_provider}`;
    // サンプル一覧を反映
    const sel = document.getElementById("example-select");
    sel.innerHTML = "";
    for (const name of META.examples) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    }
    // 検証タブのサンプル一覧も反映
    const vsel = document.getElementById("val-example-select");
    if (vsel) {
      vsel.innerHTML = "";
      for (const name of META.examples) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        vsel.appendChild(opt);
      }
    }
    // diff タブの before/after サンプル一覧
    for (const id of ["diff-before-example", "diff-after-example"]) {
      const dsel = document.getElementById(id);
      if (!dsel) continue;
      dsel.innerHTML = "";
      for (const name of META.examples) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        dsel.appendChild(opt);
      }
    }
    // 既定値を反映
    const d = META.defaults;
    setVal("p-split-threshold", d.split_threshold);
    setVal("p-zone-opacity", d.zone_opacity);
    document.getElementById("opacity-val").textContent = d.zone_opacity;
  } catch (err) {
    badge.textContent = "provider: 取得失敗";
    console.error("メタ情報の取得に失敗しました:", err);
  }
}

function setVal(id, v) {
  const el = document.getElementById(id);
  if (el != null && v != null) el.value = v;
}

// ── 入力ソース切替 ────────────────────────────────────────────────
function initSourceToggle() {
  const radios = document.querySelectorAll('input[name="source"]');
  radios.forEach((r) =>
    r.addEventListener("change", () => {
      const src = document.querySelector('input[name="source"]:checked').value;
      document.querySelectorAll(".src-block").forEach((b) => {
        b.hidden = b.dataset.src !== src;
      });
    })
  );
}

// ── モード別オプション表示 ────────────────────────────────────────
function initModeToggle() {
  const modeSel = document.getElementById("p-mode");
  const apply = () => {
    const mode = modeSel.value;
    document.querySelectorAll(".mode-opt").forEach((b) => {
      const modes = (b.dataset.mode || "").split(/\s+/);
      b.hidden = !modes.includes(mode);
    });
  };
  modeSel.addEventListener("change", apply);
  apply();
}

function initOpacityLabel() {
  const range = document.getElementById("p-zone-opacity");
  range.addEventListener("input", () => {
    document.getElementById("opacity-val").textContent = range.value;
  });
}

// ── 入力 YAML の取得 ──────────────────────────────────────────────
async function getInputPayload() {
  const src = document.querySelector('input[name="source"]:checked').value;
  if (src === "example") {
    return { source: "example", example: document.getElementById("example-select").value };
  }
  if (src === "text") {
    return { source: "text", yaml_text: document.getElementById("yaml-text").value };
  }
  // upload → クライアント側で読み取り text として送る
  const file = document.getElementById("file-input").files[0];
  if (!file) throw new Error("ファイルを選択してください。");
  const text = await file.text();
  return { source: "text", yaml_text: text };
}

// ── プレビュー ────────────────────────────────────────────────────
async function previewInput() {
  const wrap = document.getElementById("input-preview-wrap");
  const pre = document.getElementById("input-preview");
  try {
    const payload = await getInputPayload();
    let content = payload.yaml_text;
    if (payload.source === "example") {
      const res = await fetch(`/api/examples/${encodeURIComponent(payload.example)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      content = (await res.json()).content;
    }
    pre.textContent = content || "(空)";
    wrap.hidden = false;
  } catch (err) {
    pre.textContent = `プレビュー失敗: ${err.message}`;
    wrap.hidden = false;
  }
}

// ── ジョブ投入 ────────────────────────────────────────────────────
function buildRequest(inputPayload) {
  const mode = document.getElementById("p-mode").value;
  const req = {
    ...inputPayload,
    format: document.getElementById("p-format").value,
    max_iter: intVal("p-max-iter"),
    threshold: intVal("p-threshold"),
    patience: intVal("p-patience"),
    split_threshold: intVal("p-split-threshold"),
    no_split: document.getElementById("p-no-split").checked,
    hops: intVal("p-hops"),
    zone_opacity: parseFloat(document.getElementById("p-zone-opacity").value),
    focus: null,
    zone: null,
  };
  // モードに応じてフラグを組み立て（バックエンドは focus/zone/no_split から判別）
  if (mode === "single") {
    req.no_split = true;
  } else if (mode === "focus") {
    req.focus = splitList(document.getElementById("p-focus").value);
  } else if (mode === "zone") {
    req.zone = splitList(document.getElementById("p-zone").value);
  } else if (mode === "split") {
    req.no_split = false;
  }
  return req;
}

function intVal(id) {
  return parseInt(document.getElementById(id).value, 10);
}
function splitList(s) {
  return (s || "").split(",").map((x) => x.trim()).filter(Boolean);
}

async function submitJob(ev) {
  ev.preventDefault();
  const runBtn = document.getElementById("run-btn");
  runBtn.disabled = true;
  resetResultUI();
  try {
    const inputPayload = await getInputPayload();
    const req = buildRequest(inputPayload);
    const res = await fetch("/api/d2v/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    CURRENT_JOB = job_id;
    startProgress(job_id);
  } catch (err) {
    logLine(`✗ 送信エラー: ${err.message}`);
    document.getElementById("progress-wrap").hidden = false;
    runBtn.disabled = false;
  }
}

// ── 進捗（SSE） ───────────────────────────────────────────────────
function resetResultUI() {
  SCORES = [];
  document.getElementById("d2v-idle").hidden = true;
  document.getElementById("result-wrap").hidden = true;
  const log = document.getElementById("progress-log");
  log.textContent = "";
  document.getElementById("sparkline").innerHTML = "";
  document.getElementById("progress-wrap").hidden = false;
  setState("running");
}

function setState(state) {
  const el = document.getElementById("progress-state");
  el.textContent = state;
  el.className = `badge state-${state}`;
}

function logLine(text) {
  const log = document.getElementById("progress-log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

function startProgress(jobId) {
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  const handle = (stage) => (e) => {
    const data = JSON.parse(e.data);
    renderEvent(stage, data);
  };
  [
    "topology", "plan", "diagram_start", "diagram_done",
    "iteration_start", "generate", "render", "render_failed",
    "evaluate", "score", "passed", "early_stop", "pipeline_done",
    "job_done", "error",
  ].forEach((stage) => es.addEventListener(stage, handle(stage)));

  es.addEventListener("end", (e) => {
    es.close();
    const data = JSON.parse(e.data);
    setState(data.state);
    document.getElementById("run-btn").disabled = false;
    if (data.state === "succeeded") {
      loadResults(jobId);
    } else if (data.error) {
      logLine(`✗ ${data.error}`);
    }
  });
  es.onerror = () => {
    // 完了後の自動再接続を防ぐ
    if (document.getElementById("progress-state").textContent !== "running") es.close();
  };
}

function renderEvent(stage, data) {
  switch (stage) {
    case "topology":
      logLine("● トポロジ解析完了");
      break;
    case "plan":
      logLine(`● モード: ${data.extra.mode}（${data.total} 枚）`);
      break;
    case "diagram_start":
      logLine(`\n▼ 図 ${(data.iteration ?? 0) + 1}/${data.total}: ${data.extra.title}`);
      break;
    case "iteration_start":
      logLine(`  ── Iteration ${(data.iteration ?? 0) + 1}/${data.total} ──`);
      break;
    case "generate":
      logLine("    [1/3] DOT 生成中…");
      break;
    case "render":
      logLine("    [2/3] レンダリング中…");
      break;
    case "render_failed":
      logLine(`    ✗ レンダリング失敗: ${data.message}`);
      break;
    case "evaluate":
      logLine("    [3/3] 評価中…");
      break;
    case "score":
      SCORES.push(data.score);
      drawSparkline();
      logLine(`    スコア: ${data.score}/10  ${data.passed ? "✓" : ""}${data.is_best ? " ★BEST" : ""}`);
      break;
    case "passed":
      logLine(`  ✓ ${data.message}`);
      break;
    case "early_stop":
      logLine(`  → ${data.message}`);
      break;
    case "diagram_done":
      logLine(`  完了: ${data.extra.title}  スコア ${data.score}/10`);
      break;
    case "job_done":
      logLine("\n● ジョブ完了");
      break;
    case "error":
      logLine(`✗ ${data.message}`);
      break;
  }
}

function drawSparkline() {
  const box = document.getElementById("sparkline");
  if (SCORES.length === 0) { box.innerHTML = ""; return; }
  const w = 200, h = 40, pad = 4;
  const max = 10, min = 0;
  const step = SCORES.length > 1 ? (w - 2 * pad) / (SCORES.length - 1) : 0;
  const pts = SCORES.map((s, i) => {
    const x = pad + i * step;
    const y = h - pad - ((s - min) / (max - min)) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  box.innerHTML =
    `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<polyline points="${pts}" fill="none" stroke="#38bdf8" stroke-width="2" />` +
    SCORES.map((s, i) => {
      const x = pad + i * step;
      const y = h - pad - ((s - min) / (max - min)) * (h - 2 * pad);
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" fill="#38bdf8" />`;
    }).join("") +
    `</svg><span class="spark-label">スコア推移: ${SCORES.join(" → ")}</span>`;
}

// ── 結果表示 ──────────────────────────────────────────────────────
async function loadResults(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) return;
  const job = await res.json();
  const outputs = job.outputs || [];
  if (outputs.length === 0) return;

  document.getElementById("result-wrap").hidden = false;

  // 複数枚: 図の切替タブ
  const tabsEl = document.getElementById("result-tabs");
  tabsEl.innerHTML = "";
  outputs.forEach((o, i) => {
    const b = document.createElement("button");
    b.className = "rtab" + (i === 0 ? " active" : "");
    b.textContent = `${o.title}（${o.score}/10）`;
    b.addEventListener("click", () => {
      tabsEl.querySelectorAll(".rtab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      showOutput(jobId, o);
    });
    tabsEl.appendChild(b);
  });
  tabsEl.hidden = outputs.length < 2;

  showOutput(jobId, outputs[0]);
}

let DETAIL_MODE = "image";

function showOutput(jobId, output) {
  const key = encodeURIComponent(output.key);
  const bust = Date.now();
  document.getElementById("result-image").src = `/api/jobs/${jobId}/image?key=${key}&t=${bust}`;

  // DOT
  fetch(`/api/jobs/${jobId}/dot?key=${key}`).then((r) => r.text()).then((t) => {
    document.getElementById("detail-dot").textContent = t;
  });
  // 評価
  fetch(`/api/jobs/${jobId}/eval?key=${key}`).then((r) => r.json()).then((ev) => {
    renderEval(ev);
  });
  // ダウンロード
  const dl = document.getElementById("downloads");
  dl.innerHTML =
    `<a href="/api/jobs/${jobId}/image?key=${key}" download>画像をダウンロード</a>` +
    `<a href="/api/jobs/${jobId}/dot?key=${key}" download="${output.key}.dot">DOT をダウンロード</a>`;

  applyDetailMode();
}

function renderEval(ev) {
  const box = document.getElementById("detail-eval");
  const issues = (ev.issues || []).map((i) => `<li>${escapeHtml(i)}</li>`).join("");
  const rc = ev.rule_checks || {};
  const checks = Object.entries(rc)
    .map(([k, v]) => `<span class="chk ${v ? "ok" : "ng"}">${v ? "✓" : "✗"} ${k}</span>`)
    .join(" ");
  box.innerHTML =
    `<p><strong>スコア:</strong> ${ev.score}/10　<strong>合格:</strong> ${ev.passed ? "✓" : "✗"}</p>` +
    `<div class="checks">${checks}</div>` +
    (issues ? `<p><strong>指摘事項:</strong></p><ul>${issues}</ul>` : "<p>指摘事項なし</p>");
}

function initDetailTabs() {
  document.querySelectorAll(".dtab").forEach((b) =>
    b.addEventListener("click", () => {
      DETAIL_MODE = b.dataset.d;
      document.querySelectorAll(".dtab").forEach((x) => x.classList.toggle("active", x === b));
      applyDetailMode();
    })
  );
}

function applyDetailMode() {
  document.querySelector(".img-view").hidden = DETAIL_MODE !== "image";
  document.getElementById("detail-dot").hidden = DETAIL_MODE !== "dot";
  document.getElementById("detail-eval").hidden = DETAIL_MODE !== "eval";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ══════════════════════════════════════════════════════════════════
// v2d（画像 → YAML）
// ══════════════════════════════════════════════════════════════════
let V2D_FILE = null;

function initV2d() {
  const dz = document.getElementById("v2d-dropzone");
  const fileInput = document.getElementById("v2d-file");

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) setV2dFile(fileInput.files[0]);
  });
  ["dragover", "dragenter"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); })
  );
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) setV2dFile(f);
  });

  document.querySelectorAll(".vdtab").forEach((b) =>
    b.addEventListener("click", () => {
      V2D_DETAIL = b.dataset.v;
      document.querySelectorAll(".vdtab").forEach((x) => x.classList.toggle("active", x === b));
      applyV2dDetail();
    })
  );

  document.getElementById("v2d-form").addEventListener("submit", submitV2dJob);
}

function setV2dFile(file) {
  V2D_FILE = file;
  const img = document.getElementById("v2d-preview");
  img.src = URL.createObjectURL(file);
  img.hidden = false;
}

async function submitV2dJob(ev) {
  ev.preventDefault();
  if (!V2D_FILE) { alert("画像を選択してください。"); return; }
  const btn = document.getElementById("v2d-run-btn");
  btn.disabled = true;
  resetV2dUI();
  try {
    const fd = new FormData();
    fd.append("image", V2D_FILE);
    fd.append("rerender", document.getElementById("v2d-rerender").checked);
    fd.append("format", document.getElementById("v2d-format").value);
    const truth = document.getElementById("v2d-truth").value;
    if (truth.trim()) fd.append("truth", truth);

    const res = await fetch("/api/v2d/jobs", { method: "POST", body: fd });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    startV2dProgress(job_id);
  } catch (err) {
    v2dLog(`✗ 送信エラー: ${err.message}`);
    document.getElementById("v2d-progress-wrap").hidden = false;
    btn.disabled = false;
  }
}

function resetV2dUI() {
  document.getElementById("v2d-idle").hidden = true;
  document.getElementById("v2d-result-wrap").hidden = true;
  document.getElementById("v2d-progress-log").textContent = "";
  document.getElementById("v2d-progress-wrap").hidden = false;
  setV2dState("running");
}

function setV2dState(state) {
  const el = document.getElementById("v2d-progress-state");
  el.textContent = state;
  el.className = `badge state-${state}`;
}

function v2dLog(text) {
  const log = document.getElementById("v2d-progress-log");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

function startV2dProgress(jobId) {
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  const stages = {
    v2d_extract: (d) => v2dLog("● " + d.message),
    v2d_extracted: (d) => v2dLog(`● 抽出完了: ノード ${d.extra.nodes} / エッジ ${d.extra.edges} / ゾーン ${d.extra.clusters}（確信度 ${d.extra.confidence.toFixed(2)}）`),
    v2d_metrics_start: (d) => v2dLog("● " + d.message),
    v2d_metrics: () => v2dLog("● 精度計測完了"),
    v2d_rerender_start: (d) => v2dLog("● " + d.message),
    v2d_rerender: (d) => v2dLog(`● 再描画完了（スコア ${d.score}/10）`),
    job_done: () => v2dLog("● ジョブ完了"),
    error: (d) => v2dLog("✗ " + d.message),
  };
  Object.entries(stages).forEach(([stage, fn]) =>
    es.addEventListener(stage, (e) => fn(JSON.parse(e.data)))
  );
  es.addEventListener("end", (e) => {
    es.close();
    const data = JSON.parse(e.data);
    setV2dState(data.state);
    document.getElementById("v2d-run-btn").disabled = false;
    if (data.state === "succeeded") loadV2dResults(jobId);
    else if (data.error) v2dLog(`✗ ${data.error}`);
  });
  es.onerror = () => {
    if (document.getElementById("v2d-progress-state").textContent !== "running") es.close();
  };
}

let V2D_DETAIL = "yaml";

async function loadV2dResults(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) return;
  const job = await res.json();
  const v = job.v2d || {};
  document.getElementById("v2d-result-wrap").hidden = false;
  document.getElementById("v2d-summary").textContent =
    `ノード ${v.node_count} / エッジ ${v.edge_count} / ゾーン ${v.cluster_count}・確信度 ${(v.confidence ?? 0).toFixed(2)}`;

  // YAML
  const yamlText = await (await fetch(`/api/jobs/${jobId}/v2d/yaml`)).text();
  document.getElementById("v2d-yaml").textContent = yamlText;

  // 所見
  const notesBox = document.getElementById("v2d-notes");
  const notes = (v.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  const lowConf = (v.low_confidence_nodes || [])
    .map((n) => `<li>${escapeHtml(n.hostname || n.id)}（確信度 ${n.confidence.toFixed(2)}）</li>`).join("");
  notesBox.innerHTML =
    (notes ? `<p><strong>所見:</strong></p><ul>${notes}</ul>` : "<p>所見なし</p>") +
    (lowConf ? `<p><strong>低確信度ノード:</strong></p><ul>${lowConf}</ul>` : "");

  // 精度
  const metricsTab = document.querySelector('.vdtab[data-v="metrics"]');
  if (v.metrics) {
    metricsTab.hidden = false;
    document.getElementById("v2d-metrics").innerHTML = renderMetrics(v.metrics);
  } else {
    metricsTab.hidden = true;
  }

  // 元画像 / 再描画
  document.getElementById("v2d-original").src = `/api/jobs/${jobId}/v2d/original?t=${Date.now()}`;
  const rrFig = document.getElementById("v2d-rerender-fig");
  if (v.has_rerender) {
    rrFig.hidden = false;
    document.getElementById("v2d-rerender-img").src = `/api/jobs/${jobId}/v2d/rerender?t=${Date.now()}`;
  } else {
    rrFig.hidden = true;
  }

  // ダウンロード
  document.getElementById("v2d-downloads").innerHTML =
    `<a href="/api/jobs/${jobId}/v2d/yaml" download="topology.yaml">YAML をダウンロード</a>` +
    `<a href="/api/jobs/${jobId}/v2d/sidecar" download="topology.v2d.json">サイドカー JSON</a>`;

  // v2d → d2v ワンクリック連携用に抽出 YAML を保持
  V2D_YAML = yamlText;
  document.getElementById("v2d-to-d2v").hidden = false;

  V2D_DETAIL = "yaml";
  document.querySelectorAll(".vdtab").forEach((x) => x.classList.toggle("active", x.dataset.v === "yaml"));
  applyV2dDetail();
}

function renderMetrics(m) {
  const f = (x) => (x ?? 0).toFixed(2);
  return (
    `<table class="metrics"><tbody>` +
    `<tr><th>ノード</th><td>P=${f(m.nodes.precision)} R=${f(m.nodes.recall)} <strong>F1=${f(m.nodes.f1)}</strong></td><td>${m.pred_nodes} 検出 / 正解 ${m.truth_nodes}</td></tr>` +
    `<tr><th>エッジ</th><td>P=${f(m.edges.precision)} R=${f(m.edges.recall)} <strong>F1=${f(m.edges.f1)}</strong></td><td>${m.pred_edges} 検出 / 正解 ${m.truth_edges}</td></tr>` +
    `<tr><th>種別一致</th><td colspan="2">${f(m.device_type_accuracy)}</td></tr>` +
    `<tr><th>ゾーン一致</th><td colspan="2">${f(m.zone_accuracy)}</td></tr>` +
    `<tr><th>loopback一致</th><td colspan="2">${f(m.loopback_accuracy)}</td></tr>` +
    `<tr><th>マッチノード</th><td colspan="2">${m.matched_nodes}</td></tr>` +
    `</tbody></table>`
  );
}

function applyV2dDetail() {
  document.getElementById("v2d-yaml").hidden = V2D_DETAIL !== "yaml";
  document.getElementById("v2d-notes").hidden = V2D_DETAIL !== "notes";
  document.getElementById("v2d-metrics").hidden = V2D_DETAIL !== "metrics";
  document.getElementById("v2d-compare").hidden = V2D_DETAIL !== "compare";
}

// ══════════════════════════════════════════════════════════════════
// 履歴・共有・v2d→d2v 連携（Phase 5）
// ══════════════════════════════════════════════════════════════════
let V2D_YAML = "";

function initHistory() {
  const drawer = document.getElementById("history-drawer");
  document.getElementById("history-btn").addEventListener("click", () => {
    drawer.hidden = !drawer.hidden;
    if (!drawer.hidden) loadHistory();
  });
  document.getElementById("history-close").addEventListener("click", () => {
    drawer.hidden = true;
  });
  document.getElementById("history-refresh").addEventListener("click", loadHistory);
}

async function loadHistory() {
  const list = document.getElementById("history-list");
  try {
    const res = await fetch("/api/jobs");
    const { jobs: items } = await res.json();
    if (!items.length) {
      list.innerHTML = '<li class="dim">（まだありません）</li>';
      return;
    }
    list.innerHTML = "";
    for (const j of items) {
      const li = document.createElement("li");
      li.className = "hist-item";
      const when = new Date(j.created_at).toLocaleTimeString();
      let detail = "";
      let thumb = "";
      if (j.kind === "d2v" && j.best_score != null) {
        detail = `${j.mode} · ${j.diagram_count}枚 · 最高 ${j.best_score}/10`;
        thumb = `/api/jobs/${j.id}/image`;
      } else if (j.kind === "v2d" && j.node_count != null) {
        detail = `node ${j.node_count}/edge ${j.edge_count} · ${(j.confidence ?? 0).toFixed(2)}`;
        thumb = `/api/jobs/${j.id}/v2d/original`;
      }
      li.innerHTML =
        (thumb ? `<img class="hthumb" src="${thumb}" alt="" />` : `<span class="hk hk-${j.kind}">${j.kind}</span>`) +
        `<span class="hlabel">${escapeHtml(j.label)}</span>` +
        `<span class="hstate state-${j.state}">${j.state}</span>` +
        `<span class="hdetail">${escapeHtml(detail)}</span>` +
        `<span class="hwhen">${when}</span>`;
      if (j.state === "succeeded") {
        li.classList.add("clickable");
        li.addEventListener("click", () => openHistoryJob(j));
      }
      list.appendChild(li);
    }
  } catch (err) {
    list.innerHTML = `<li class="dim">読み込み失敗: ${escapeHtml(err.message)}</li>`;
  }
}

function openHistoryJob(j) {
  document.getElementById("history-drawer").hidden = true;
  if (j.kind === "d2v") {
    switchTab("d2v");
    document.getElementById("d2v-idle").hidden = true;
    document.getElementById("progress-wrap").hidden = true;
    loadResults(j.id);
  } else if (j.kind === "v2d") {
    switchTab("v2d");
    document.getElementById("v2d-idle").hidden = true;
    document.getElementById("v2d-progress-wrap").hidden = true;
    loadV2dResults(j.id);
  }
}

// ── 設定を URL に反映（共有リンク） ───────────────────────────────
const SHARE_KEYS = [
  ["p-format", "format", "str"],
  ["p-max-iter", "max_iter", "int"],
  ["p-threshold", "threshold", "int"],
  ["p-patience", "patience", "int"],
  ["p-mode", "mode", "str"],
  ["p-split-threshold", "split_threshold", "int"],
  ["p-hops", "hops", "int"],
  ["p-zone-opacity", "zone_opacity", "str"],
  ["p-focus", "focus", "str"],
  ["p-zone", "zone", "str"],
];

function initShare() {
  document.getElementById("share-btn").addEventListener("click", () => {
    const params = new URLSearchParams();
    // サンプル選択時のみ入力も共有（貼り付け/アップロードは内容を URL に載せない）
    const src = document.querySelector('input[name="source"]:checked').value;
    if (src === "example") {
      params.set("source", "example");
      params.set("example", document.getElementById("example-select").value);
    }
    for (const [id, key] of SHARE_KEYS) {
      const v = document.getElementById(id).value;
      if (v !== "" && v != null) params.set(key, v);
    }
    if (document.getElementById("p-no-split").checked) params.set("no_split", "1");
    const url = `${location.origin}${location.pathname}?${params.toString()}`;
    navigator.clipboard.writeText(url).then(
      () => flash("URL をコピーしました"),
      () => flash(url)
    );
  });
}

function applyQueryParams() {
  const q = new URLSearchParams(location.search);
  if (![...q.keys()].length) return;
  if (q.get("source") === "example" && q.get("example")) {
    setVal("example-select", q.get("example"));
  }
  const setIf = (id, key) => { if (q.has(key)) setVal(id, q.get(key)); };
  for (const [id, key] of SHARE_KEYS) setIf(id, key);
  if (q.get("no_split") === "1") document.getElementById("p-no-split").checked = true;
  document.getElementById("p-mode").dispatchEvent(new Event("change"));
  document.getElementById("opacity-val").textContent =
    document.getElementById("p-zone-opacity").value;
}

function flash(msg) {
  const el = document.getElementById("share-btn");
  const orig = el.textContent;
  el.textContent = msg;
  setTimeout(() => { el.textContent = orig; }, 1800);
}

function initV2dToD2v() {
  document.getElementById("v2d-to-d2v").addEventListener("click", () => {
    if (!V2D_YAML) return;
    switchTab("d2v");
    // 貼り付けソースに切替えて YAML を流し込む
    document.querySelector('input[name="source"][value="text"]').checked = true;
    document.querySelectorAll(".src-block").forEach((b) => {
      b.hidden = b.dataset.src !== "text";
    });
    document.getElementById("yaml-text").value = V2D_YAML;
    document.getElementById("d2v-idle").scrollIntoView({ behavior: "smooth" });
    flash2("d2v タブに YAML を流し込みました。「生成する」で描画できます。");
  });
}

function flash2(msg) {
  const idle = document.getElementById("d2v-idle");
  idle.hidden = false;
  idle.textContent = msg;
}

// ── エントリポイント ──────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════
// 検証（design lint）
// ══════════════════════════════════════════════════════════════════
const VAL_SEV_CLASS = { error: "sev-error", warning: "sev-warning", info: "sev-info" };

function initValidate() {
  document.querySelectorAll('input[name="val-source"]').forEach((r) =>
    r.addEventListener("change", () => {
      const src = document.querySelector('input[name="val-source"]:checked').value;
      document.querySelectorAll(".val-src-block").forEach((b) => {
        b.hidden = b.dataset.src !== src;
      });
    })
  );
  document.getElementById("val-form").addEventListener("submit", submitValidate);
}

async function submitValidate(e) {
  e.preventDefault();
  const btn = document.getElementById("val-run-btn");
  const source = document.querySelector('input[name="val-source"]:checked').value;
  const payload = {
    source,
    strict: document.getElementById("val-strict").checked,
    explain: document.getElementById("val-explain").checked,
  };
  if (source === "example") {
    payload.example = document.getElementById("val-example-select").value;
  } else {
    payload.yaml_text = document.getElementById("val-yaml-text").value;
  }

  btn.disabled = true;
  btn.textContent = "検証中…";
  try {
    const res = await fetch("/api/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderValidate(data);
  } catch (err) {
    renderValidateError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ 検証する";
  }
}

function renderValidate(data) {
  document.getElementById("val-idle").hidden = true;
  document.getElementById("val-result-wrap").hidden = false;

  const c = data.counts || { error: 0, warning: 0, info: 0 };
  const badge = document.getElementById("val-summary");
  const label = data.passed ? "合格" : "不合格";
  badge.textContent = `${label} (error ${c.error} / warning ${c.warning} / info ${c.info})`;
  badge.className = "badge " + (data.passed ? "ok" : "ng");

  const errBox = document.getElementById("val-explain-error");
  if (data.explain_error) {
    errBox.hidden = false;
    errBox.textContent = data.explain_error;
  } else {
    errBox.hidden = true;
  }

  const tbody = document.getElementById("val-tbody");
  const issues = data.issues || [];
  if (issues.length === 0) {
    tbody.innerHTML =
      `<tr><td colspan="4" class="dim">設計上の問題は検出されませんでした。</td></tr>`;
  } else {
    tbody.innerHTML = issues.map((i) =>
      `<tr class="${VAL_SEV_CLASS[i.severity] || ""}">` +
      `<td>${escapeHtml(i.severity)}</td>` +
      `<td>${escapeHtml(i.rule)}</td>` +
      `<td>${escapeHtml(i.message)}</td>` +
      `<td>${escapeHtml((i.targets || []).join(", "))}</td></tr>`
    ).join("");
  }

  const details = document.getElementById("val-details");
  const explained = issues.filter((i) => i.explanation || i.suggestion);
  if (explained.length) {
    details.hidden = false;
    details.innerHTML =
      `<h4>詳細（--explain）</h4>` +
      explained.map((i) =>
        `<div class="val-detail ${VAL_SEV_CLASS[i.severity] || ""}">` +
        `<div class="val-detail-head">${escapeHtml(i.rule)} ` +
        `<span class="dim">[${escapeHtml((i.targets || []).join(", "))}]</span></div>` +
        (i.explanation ? `<div><strong>理由:</strong> ${escapeHtml(i.explanation)}</div>` : "") +
        (i.suggestion ? `<div><strong>修正案:</strong> ${escapeHtml(i.suggestion)}</div>` : "") +
        `</div>`
      ).join("");
  } else {
    details.hidden = true;
    details.innerHTML = "";
  }
}

function renderValidateError(msg) {
  document.getElementById("val-idle").hidden = true;
  document.getElementById("val-result-wrap").hidden = false;
  document.getElementById("val-summary").textContent = "エラー";
  document.getElementById("val-summary").className = "badge ng";
  document.getElementById("val-explain-error").hidden = true;
  document.getElementById("val-details").hidden = true;
  document.getElementById("val-tbody").innerHTML =
    `<tr><td colspan="4" class="sev-error">${escapeHtml(msg)}</td></tr>`;
}


// ══════════════════════════════════════════════════════════════════
// diff（意味的 diff）
// ══════════════════════════════════════════════════════════════════

function initDiff() {
  // before/after の入力ソース切替
  ["before", "after"].forEach((side) => {
    document.querySelectorAll(`input[name="diff-${side}-source"]`).forEach((r) =>
      r.addEventListener("change", () => {
        const src = document.querySelector(`input[name="diff-${side}-source"]:checked`).value;
        document.querySelectorAll(`.diff-src-block[data-side="${side}"]`).forEach((b) => {
          b.hidden = b.dataset.src !== src;
        });
      })
    );
  });
  // 詳細タブ（変更点 / 差分図）切替
  document.querySelectorAll(".ddtab").forEach((t) =>
    t.addEventListener("click", () => {
      document.querySelectorAll(".ddtab").forEach((x) => x.classList.toggle("active", x === t));
      document.getElementById("diff-changes").hidden = t.dataset.dd !== "changes";
      document.getElementById("diff-image-wrap").hidden = t.dataset.dd !== "image";
    })
  );
  document.getElementById("diff-form").addEventListener("submit", submitDiff);
}

function _diffSide(side) {
  const source = document.querySelector(`input[name="diff-${side}-source"]:checked`).value;
  if (source === "example") {
    return { source: "example", example: document.getElementById(`diff-${side}-example`).value };
  }
  return { source: "text", yaml_text: document.getElementById(`diff-${side}-text`).value };
}

async function submitDiff(e) {
  e.preventDefault();
  const btn = document.getElementById("diff-run-btn");
  const payload = {
    before: _diffSide("before"),
    after: _diffSide("after"),
    summarize: document.getElementById("diff-summarize").checked,
    format: document.getElementById("diff-format").value,
    image: true,
  };
  btn.disabled = true;
  btn.textContent = "比較中…";
  try {
    const res = await fetch("/api/diff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderDiff(data);
  } catch (err) {
    renderDiffError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ 差分を比較";
  }
}

function _diffList(title, items, sign, cls) {
  if (!items || !items.length) return "";
  const lis = items.map((x) => `<li class="${cls}">${sign} ${escapeHtml(x)}</li>`).join("");
  return `<h4>${escapeHtml(title)}</h4><ul class="diff-list">${lis}</ul>`;
}

function renderDiff(data) {
  document.getElementById("diff-idle").hidden = true;
  document.getElementById("diff-result-wrap").hidden = false;

  const d = data.diff || {};
  const badge = document.getElementById("diff-summary-badge");
  if (data.is_empty) {
    badge.textContent = "変化なし";
    badge.className = "badge ok";
  } else {
    badge.textContent =
      `+ノード ${(d.nodes_added || []).length} / -ノード ${(d.nodes_removed || []).length} / ` +
      `~ノード ${(d.nodes_changed || []).length} / +エッジ ${(d.edges_added || []).length} / -エッジ ${(d.edges_removed || []).length}`;
    badge.className = "badge";
  }

  const errBox = document.getElementById("diff-summary-error");
  errBox.hidden = !data.summary_error;
  if (data.summary_error) errBox.textContent = data.summary_error;

  const nl = document.getElementById("diff-nl-summary");
  if (d.summary) {
    nl.hidden = false;
    nl.textContent = d.summary;
  } else {
    nl.hidden = true;
  }

  const changed = (d.nodes_changed || []).map((nc) => {
    const fields = (nc.changes || [])
      .map((c) => `${escapeHtml(c.field)}: ${escapeHtml(String(c.before))} → ${escapeHtml(String(c.after))}`)
      .join("、");
    return `<li class="chg">~ ${escapeHtml(nc.device_id)}（${fields}）</li>`;
  }).join("");

  const changesEl = document.getElementById("diff-changes");
  if (data.is_empty) {
    changesEl.innerHTML = `<p class="dim">構造上の変化はありません。</p>`;
  } else {
    changesEl.innerHTML =
      _diffList("ノード追加", d.nodes_added, "+", "add") +
      _diffList("ノード削除", d.nodes_removed, "-", "del") +
      (changed ? `<h4>ノード変更</h4><ul class="diff-list">${changed}</ul>` : "") +
      _diffList("エッジ追加", d.edges_added, "+", "add") +
      _diffList("エッジ削除", d.edges_removed, "-", "del") +
      _diffList("ゾーン追加", d.zones_added, "+", "add") +
      _diffList("ゾーン削除", d.zones_removed, "-", "del") +
      _diffList("サブネット追加", d.subnets_added, "+", "add") +
      _diffList("サブネット削除", d.subnets_removed, "-", "del");
  }

  const imgWrap = document.getElementById("diff-image-wrap");
  if (data.image_token) {
    const url = `/api/diff/image/${data.image_token}`;
    document.getElementById("diff-image").src = url;
    document.getElementById("diff-image-dl").href = url;
    imgWrap.dataset.available = "1";
  } else {
    document.getElementById("diff-image").removeAttribute("src");
    imgWrap.dataset.available = "0";
  }
  // 既定は「変更点」タブを表示
  document.querySelectorAll(".ddtab").forEach((x) => x.classList.toggle("active", x.dataset.dd === "changes"));
  document.getElementById("diff-changes").hidden = false;
  imgWrap.hidden = true;
}

function renderDiffError(msg) {
  document.getElementById("diff-idle").hidden = true;
  document.getElementById("diff-result-wrap").hidden = false;
  document.getElementById("diff-summary-badge").textContent = "エラー";
  document.getElementById("diff-summary-badge").className = "badge ng";
  document.getElementById("diff-nl-summary").hidden = true;
  document.getElementById("diff-summary-error").hidden = true;
  document.getElementById("diff-changes").innerHTML = `<p class="sev-error">${escapeHtml(msg)}</p>`;
  document.getElementById("diff-image-wrap").hidden = true;
}


document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initSourceToggle();
  initModeToggle();
  initOpacityLabel();
  initDetailTabs();
  initV2d();
  initHistory();
  initShare();
  initV2dToD2v();
  initValidate();
  initDiff();
  loadMeta().then(applyQueryParams);
  document.getElementById("d2v-form").addEventListener("submit", submitJob);
  document.getElementById("preview-btn").addEventListener("click", previewInput);
});
