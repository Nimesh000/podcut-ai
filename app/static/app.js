// PodCut AI front-end: submit a job, poll its status, show the result.
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
let pollTimer = null;
let currentId = null;
let activeTab = "link";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const fmtBytes = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1e3)) + " KB");

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

// ------------------------------------------------------------------ health
async function loadHealth() {
  try {
    const h = await api("/api/health");
    const pills = [
      `<span class="pill ${h.ffmpeg ? "ok" : "bad"}" title="${esc(h.ffmpeg_message)}">FFmpeg</span>`,
      h.llm_configured
        ? `<span class="pill ok">LLM: ${esc(h.llm_provider)} &middot; ${esc(h.llm_model)}</span>`
        : `<span class="pill bad" title="Add GROQ_API_KEY or XAI_API_KEY to .env">No LLM key &ndash; rule-based mode</span>`,
    ];
    $("#health").innerHTML = pills.join("");
  } catch (_) {
    $("#health").innerHTML = `<span class="pill bad">Server offline</span>`;
  }
}

// ------------------------------------------------------------------ form
$$(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    activeTab = btn.dataset.tab;
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tabpane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== activeTab));
  })
);

const drop = $("#drop");
const fileInput = $("#file");
fileInput.addEventListener("change", () => {
  $("#dropText").textContent = fileInput.files[0] ? `${fileInput.files[0].name} (${fmtBytes(fileInput.files[0].size)})` : "Drop a video or audio file here, or browse";
});
["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
drop.addEventListener("drop", (ev) => {
  ev.preventDefault();
  if (ev.dataTransfer.files.length) { fileInput.files = ev.dataTransfer.files; fileInput.dispatchEvent(new Event("change")); }
});

$("#form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const err = $("#formError");
  err.classList.add("hidden");
  const fd = new FormData();
  if (activeTab === "link") {
    const url = $("#url").value.trim();
    if (!url) return showFormError("Paste a link first.");
    fd.append("url", url);
  } else {
    if (!fileInput.files[0]) return showFormError("Choose a file first.");
    fd.append("file", fileInput.files[0]);
  }
  for (const name of ["title", "show", "guest", "guest_role", "host", "seconds", "model", "language"]) {
    fd.append(name, form.elements[name].value);
  }
  for (const name of ["music", "subtitles", "hook"]) fd.append(name, form.elements[name].checked ? "true" : "false");
  if (form.elements.logo.files[0]) fd.append("logo", form.elements.logo.files[0]);

  const btn = $("#submit");
  btn.disabled = true;
  btn.textContent = activeTab === "upload" ? "Uploading…" : "Starting…";
  try {
    const { id } = await api("/api/jobs", { method: "POST", body: fd });
    location.hash = `#/job/${id}`;
  } catch (e) {
    showFormError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create highlight video";
  }
});

function showFormError(msg) {
  const err = $("#formError");
  err.textContent = msg;
  err.classList.remove("hidden");
}

// ------------------------------------------------------------------ history
async function loadHistory() {
  try {
    const jobs = await api("/api/jobs");
    $("#history").innerHTML = jobs.length
      ? jobs.map((j) => `<li><a href="#/job/${esc(j.id)}"><span class="t">${esc(j.title || j.id)}</span>
          <span class="status ${esc(j.status)}">${esc(j.status === "running" ? Math.round(j.progress * 100) + "%" : j.status)}</span></a></li>`).join("")
      : `<li class="muted">No jobs yet.</li>`;
  } catch (_) {}
}

// ------------------------------------------------------------------ job view
async function pollJob() {
  if (!currentId) return;
  let job;
  try {
    job = await api(`/api/jobs/${currentId}`);
  } catch (e) {
    $("#jobMsg").textContent = e.message;
    return;
  }
  renderJob(job);
  if (!$("#log").classList.contains("hidden")) {
    api(`/api/jobs/${currentId}/log`).then((t) => { const l = $("#log"); l.textContent = t; l.scrollTop = l.scrollHeight; });
  }
  if (job.status === "done" || job.status === "error") {
    clearInterval(pollTimer);
    pollTimer = null;
    if (job.status === "done") showResult(job);
  }
}

function renderJob(job) {
  const pct = Math.round((job.progress || 0) * 100);
  $("#pct").textContent = pct + "%";
  $("#barFill").style.width = pct + "%";
  $("#jobTitle").textContent = job.summary.ai_title || job.meta.title || job.source.name || job.source.value || "Processing…";
  $("#jobMsg").textContent = job.status === "queued" ? "Waiting for the previous job to finish…" : job.message;
  $("#stages").innerHTML = Object.entries(job.stages).map(([key, st]) => {
    const icon = st.status === "done" ? "&#10003;" : st.status === "error" ? "!" : "";
    const right = st.status === "running" ? Math.round(st.progress * 100) + "%" : st.seconds != null ? st.seconds + " s" : "";
    return `<li class="${esc(st.status)}"><span class="dot">${icon}</span><span>${esc(job.stage_labels[key])}</span><span class="sec">${right}</span></li>`;
  }).join("");
  const err = $("#jobError");
  err.classList.toggle("hidden", job.status !== "error");
  err.textContent = job.error || "";
  $("#retry").classList.toggle("hidden", job.status !== "error");
}

async function showResult(job) {
  $("#result").classList.remove("hidden");
  const v = $("#video");
  const src = `/api/jobs/${job.id}/files/final_video.mp4`;
  if (!v.src.endsWith(src)) { v.src = src; v.poster = `/api/jobs/${job.id}/files/thumbnail.jpg`; }
  $("#downloads").innerHTML = job.outputs.map((o) =>
    `<a href="/api/jobs/${job.id}/files/${encodeURIComponent(o.name)}?download=true">${esc(o.name)}<small>${esc(o.description)} &middot; ${fmtBytes(o.bytes)}</small></a>`
  ).join("");
  try {
    const r = await api(`/api/jobs/${job.id}/report`);
    $("#ytTitle").textContent = r.youtube.title;
    $("#ytDesc").textContent = r.youtube.description || "";
    $("#ytTags").innerHTML = (r.youtube.tags || []).map((t) => `<span>${esc(t)}</span>`).join("");
    const s = r.summary;
    const stats = [
      [((s.source_duration || 0) / 60).toFixed(1) + " min", "source length"],
      [Math.round(s.final_seconds || 0) + " s", "final video"],
      [s.cuts, "cuts"],
      [Math.round((s.processing_seconds || 0) / 60 * 10) / 10 + " min", "processing time"],
    ];
    $("#stats").innerHTML = stats.map(([b, l]) => `<div><b>${esc(b)}</b><small>${esc(l)}</small></div>`).join("");
    $("#cuts tbody").innerHTML = r.cuts.map((c, i) => `<tr>
      <td>${i + 1}${c.role === "hook" ? " <small class='muted'>hook</small>" : ""}</td>
      <td>${fmtTime(c.source_start)}&ndash;${fmtTime(c.source_end)}</td>
      <td>${(c.source_end - c.source_start).toFixed(0)}s</td>
      <td class="score"><b>${esc(c.score)}</b>/10</td>
      <td>${esc(c.topic)}</td>
      <td title="${esc(c.text)}">${esc(c.reason)}</td></tr>`).join("");
  } catch (_) {}
}

$("#retry").addEventListener("click", async () => {
  try {
    await api(`/api/jobs/${currentId}/retry`, { method: "POST" });
    startPolling();
  } catch (e) { alertInline(e.message); }
});
function alertInline(msg) { const e = $("#jobError"); e.textContent = msg; e.classList.remove("hidden"); }

$("#toggleLog").addEventListener("click", () => {
  const l = $("#log");
  l.classList.toggle("hidden");
  $("#toggleLog").textContent = l.classList.contains("hidden") ? "Show log" : "Hide log";
  if (!l.classList.contains("hidden")) api(`/api/jobs/${currentId}/log`).then((t) => { l.textContent = t; l.scrollTop = l.scrollHeight; });
});
$("#back").addEventListener("click", () => { location.hash = ""; });

function startPolling() {
  clearInterval(pollTimer);
  pollJob();
  pollTimer = setInterval(pollJob, 1500);
}

// ------------------------------------------------------------------ router
function route() {
  const m = location.hash.match(/^#\/job\/([\w-]+)/);
  clearInterval(pollTimer);
  if (m) {
    currentId = m[1];
    $("#view-new").classList.add("hidden");
    $("#view-job").classList.remove("hidden");
    $("#result").classList.add("hidden");
    $("#video").removeAttribute("src");
    startPolling();
  } else {
    currentId = null;
    $("#view-job").classList.add("hidden");
    $("#view-new").classList.remove("hidden");
    loadHistory();
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
loadHealth();
route();
