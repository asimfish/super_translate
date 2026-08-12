/* Benchmark showcase page logic. Chinese labels are kept as \u escapes so
 * the source stays ASCII (editor toolchains have mangled raw CJK before). */
const L = {
  papers: "\u8bba\u6587\u6570",
  strict: "\u4e25\u683c\u95e8\u7981\u901a\u8fc7",
  errors: "\u5f53\u524d\u9519\u8bef\u603b\u6570",
  vsbase: "vs \u4fee\u590d\u524d\u57fa\u7ebf",
  dropped: "\u9519\u8bef\u51cf\u5c11 ",
  gen: "\u751f\u6210\u65f6\u95f4 ",
  note: " \u30fb \u5b8c\u6574\u9875\u9762\u5bf9\u7167\u4ec5\u5bf9 Creative Commons \u8bb8\u53ef\u8bba\u6587\u5f00\u653e\uff0c\u5176\u4f59\u8bba\u6587\u5c55\u793a\u68c0\u6d4b\u6307\u6807\u3002",
  cc_ok: "CC \u53ef\u5c55\u793a",
  cc_no: "\u8bb8\u53ef\u9650\u5236",
  visual: "\u89c6\u89c9\u76f8\u4f3c\u5ea6 ",
  pages_issues: " \u9875 \u30fb ",
  issues_suffix: " \u4e2a\u95ee\u9898",
  strict_badge: "\u4e25\u683c\u95e8\u7981 ",
  legacy_badge: "\u57fa\u7840\u95e8\u7981 ",
  pass: "\u901a\u8fc7",
  fail: "\u672a\u8fc7",
  no_issues: "\u65e0\u68c0\u51fa\u95ee\u9898",
  view: "\u67e5\u770b\u539f\u6587 / \u8bd1\u6587\u5bf9\u7167",
  view_internal: "\u67e5\u770b\u5bf9\u7167\uff08\u5185\u90e8\u5ba1\u9605\uff09",
  metrics_only: "\u8bb8\u53ef\u9650\u5236\uff0c\u4ec5\u5c55\u793a\u6307\u6807",
  page_cap_prefix: "\u7b2c ",
  page_cap_suffix: " \u9875 \u30fb \u5de6\u539f\u6587 / \u53f3\u8bd1\u6587",
  m_visual: "\u89c6\u89c9\u76f8\u4f3c\u5ea6 ",
  m_issues: " \u4e2a\u68c0\u51fa\u95ee\u9898",
  orig: "\u539f\u6587",
  trans: "\u8bd1\u6587",
};

(async function () {
  const grid = document.getElementById("grid");
  const summary = document.getElementById("summary");
  const note = document.getElementById("note");
  // Reuse the main app's stored credential: authenticated operators may
  // review non-CC papers' page pairs (the server enforces the licence gate
  // for anonymous visitors).
  const token = localStorage.getItem("paperChinaApiToken") || "";
  const authHeaders = token ? { Authorization: "Bearer " + token } : {};
  let data;
  try {
    const res = await fetch("/api/showcase", { headers: authHeaders });
    if (!res.ok) throw new Error("no data");
    data = await res.json();
  } catch (err) {
    document.getElementById("empty").style.display = "block";
    return;
  }
  const papers = data.papers || [];
  const authed = Boolean(data.authenticated);

  const totalErrors = papers.reduce((acc, p) => acc + (p.error_count || 0), 0);
  const strictPass = papers.filter((p) => p.strict_pass).length;
  const stats = [
    { k: L.papers, v: papers.length },
    { k: L.strict, v: strictPass + " / " + papers.length },
    { k: L.errors, v: totalErrors },
  ];
  if (data.comparison) {
    const b = data.comparison.baseline, c = data.comparison.current;
    const drop = b.error_count ? Math.round((1 - c.error_count / b.error_count) * 100) : 0;
    stats.push({ k: L.vsbase, v: b.error_count + " \u2192 " + c.error_count, d: L.dropped + drop + "%", cls: "up" });
  }
  summary.innerHTML = stats.map((s) =>
    '<div class="stat"><div class="k">' + s.k + '</div><div class="v">' + s.v +
    "</div>" + (s.d ? '<div class="d ' + (s.cls || "") + '">' + s.d + "</div>" : "") + "</div>"
  ).join("");
  note.textContent = L.gen + (data.generated_at || "").replace("T", " ").slice(0, 19) + L.note;

  const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

  grid.innerHTML = papers.map((p, i) => {
    const codes = Object.entries(p.issues_by_code || {})
      .sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([code, count]) => esc(code) + " \u00d7 " + count).join("\u3000") || L.no_issues;
    const visual = Math.round((p.visual_score || 0) * 100);
    const lic = p.showcase_ok
      ? '<span class="chip lic">' + L.cc_ok + "</span>"
      : '<span class="chip lic no">' + L.cc_no + "</span>";
    const canPreview = (p.showcase_ok || authed) && (p.previews || []).length;
    return '<div class="card">' +
      '<h3><a href="https://arxiv.org/abs/' + esc(p.arxiv_id) + '" target="_blank" rel="noopener">' + esc(p.title) + "</a></h3>" +
      '<div class="meta">' + (p.tags || []).map((t) => '<span class="chip">' + esc(t) + "</span>").join("") + lic + "</div>" +
      '<div class="row"><span>' + L.visual + visual + "%</span><span>" + p.pages + L.pages_issues + p.error_count + L.issues_suffix + "</span></div>" +
      '<div class="bar"><i style="width:' + visual + '%"></i></div>' +
      '<div class="badges">' +
        '<span class="badge ' + (p.strict_pass ? "pass" : "fail") + '">' + L.strict_badge + (p.strict_pass ? L.pass : L.fail) + "</span>" +
        '<span class="badge ' + (p.legacy_pass ? "pass" : "fail") + '">' + L.legacy_badge + (p.legacy_pass ? L.pass : L.fail) + "</span>" +
      "</div>" +
      '<div class="codes">' + codes + "</div>" +
      '<button class="btn" data-open="' + i + '"' + (canPreview ? "" : " disabled") + ">" +
      (canPreview ? (p.showcase_ok ? L.view : L.view_internal) : L.metrics_only) +
      "</button></div>";
  }).join("");

  const loadPreviewImage = async (img) => {
    try {
      const res = await fetch(img.dataset.src, { headers: authHeaders });
      if (!res.ok) throw new Error(String(res.status));
      img.src = URL.createObjectURL(await res.blob());
    } catch (err) {
      img.alt = "unavailable";
    }
  };

  const modal = document.getElementById("modal");
  document.body.addEventListener("click", (event) => {
    const openBtn = event.target.closest("[data-open]");
    if (openBtn) {
      const p = papers[Number(openBtn.dataset.open)];
      document.getElementById("m-title").textContent = p.title;
      document.getElementById("m-sub").innerHTML =
        "<span>" + L.m_visual + Math.round(p.visual_score * 100) + "%</span><span>" +
        p.error_count + L.m_issues + "</span>";
      document.getElementById("m-pairs").innerHTML = (p.previews || []).map((pair) => {
        const num = String(pair.page).padStart(3, "0");
        const base = "/api/showcase/previews/" + p.id + "/p" + num;
        return '<div class="pair"><div class="cap">' + L.page_cap_prefix + pair.page + L.page_cap_suffix + "</div>" +
          '<div class="imgs"><img loading="lazy" data-src="' + base + '_original.jpg" alt="' + L.orig +
          '"><img loading="lazy" data-src="' + base + '_translated.jpg" alt="' + L.trans + '"></div></div>';
      }).join("");
      document.querySelectorAll("#m-pairs img[data-src]").forEach(loadPreviewImage);
      modal.classList.add("open");
    }
    if (event.target.closest("[data-close]") || event.target === modal) {
      modal.classList.remove("open");
    }
  });
})();
