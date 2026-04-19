const qs = (s) => document.querySelector(s);
const qsa = (s) => Array.from(document.querySelectorAll(s));

const state = {
  assets: [],
  folders: ["默认"],
  galleryFolderFilter: "",
  galleryTagFilter: "",
  layerEditor: null,
  layerEditorBound: false,
  gallerySelected: new Set(),
  busy: { opt: false, gen: false, wm: false, bg: false },
};

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

function setDisabled(ids, disabled) {
  ids.forEach((id) => {
    const el = qs(id);
    if (el) el.disabled = !!disabled;
  });
}

function showToast(message, type = "success") {
  let box = qs("#global-toast");
  if (!box) {
    box = document.createElement("div");
    box.id = "global-toast";
    box.className = "toast";
    document.body.appendChild(box);
  }
  box.textContent = message || "操作完成";
  box.className = `toast show ${type}`;
  window.clearTimeout(showToast._timer);
  showToast._timer = window.setTimeout(() => {
    box.classList.remove("show");
  }, 1800);
}

function startProgress(kind, text) {
  const box = qs(`#${kind}-progress`);
  const bar = qs(`#${kind}-progress-bar`);
  const txt = qs(`#${kind}-progress-text`);
  const pct = qs(`#${kind}-progress-percent`);
  if (!box || !bar || !txt || !pct) return null;

  let p = 6;
  box.style.display = "block";
  txt.textContent = text || "处理中...";
  bar.style.width = `${p}%`;
  pct.textContent = `${p}%`;

  const timer = setInterval(() => {
    const inc = p < 40 ? 5 : (p < 70 ? 3 : 1);
    p = Math.min(92, p + inc);
    bar.style.width = `${p}%`;
    pct.textContent = `${p}%`;
  }, 450);
  return { kind, timer };
}

function finishProgress(handle, text) {
  if (!handle) return;
  const kind = handle.kind;
  const box = qs(`#${kind}-progress`);
  const bar = qs(`#${kind}-progress-bar`);
  const txt = qs(`#${kind}-progress-text`);
  const pct = qs(`#${kind}-progress-percent`);
  clearInterval(handle.timer);
  if (!box || !bar || !txt || !pct) return;
  txt.textContent = text || "已完成";
  bar.style.width = "100%";
  pct.textContent = "100%";
  setTimeout(() => {
    box.style.display = "none";
  }, 600);
}

function card(item) {
  const tags = renderTagBadges(item.tags || []);
  const name = getAssetName(item);
  const folder = item.folder || "默认";
  return `<div class="thumb"><img src="${item.url}" alt="${item.asset_id}" /><div class="meta"><b>${name}</b><br>文件夹: ${folder}<br>${tags}<br><a href="${item.url}" download class="mini ok">下载</a></div></div>`;
}

function getAssetName(item) {
  return item?.original_name || item?.filename || item?.asset_id || "";
}

function badgeStyleForTag(tag) {
  const palette = [
    { bg: "#ecf6ee", fg: "#1f8a5a" },
    { bg: "#eaf3ff", fg: "#1f5ba3" },
    { bg: "#fff2e8", fg: "#b35624" },
    { bg: "#f3ecff", fg: "#6b43a6" },
    { bg: "#e9f7f7", fg: "#1d6d70" },
    { bg: "#fef3d8", fg: "#8b5a00" },
  ];
  const s = String(tag || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}

function renderTagBadges(tags) {
  return (tags || []).map((t) => {
    const c = badgeStyleForTag(t);
    return `<span class="badge" style="background:${c.bg};color:${c.fg};">${t}</span>`;
  }).join(" ");
}

function uniqueTags(items) {
  const out = [];
  for (const item of items || []) {
    for (const t of (item.tags || [])) {
      const tag = String(t || "").trim();
      if (tag && !out.includes(tag)) out.push(tag);
    }
  }
  return out;
}

async function refreshFolders() {
  const d = await api("/api/folders");
  const folders = Array.isArray(d.folders) ? d.folders.map(x => String(x).trim()).filter(Boolean) : [];
  state.folders = folders.length ? folders : ["默认"];
  if (state.galleryFolderFilter && !state.folders.includes(state.galleryFolderFilter)) {
    state.galleryFolderFilter = "";
  }
  refreshFolderSelectors();
}

function refreshFolderSelectors() {
  const filterSel = qs("#gallery-folder-filter");
  const tagSel = qs("#gallery-tag-filter");
  const moveSel = qs("#gallery-move-folder");
  if (filterSel) {
    const cur = state.galleryFolderFilter || "";
    filterSel.innerHTML = ["<option value=''>全部文件夹</option>"]
      .concat(state.folders.map(f => `<option value="${f}">${f}</option>`))
      .join("");
    filterSel.value = cur && state.folders.includes(cur) ? cur : "";
  }
  if (tagSel) {
    const allTags = uniqueTags(state.assets);
    const cur = state.galleryTagFilter || "";
    if (cur && !allTags.includes(cur)) state.galleryTagFilter = "";
    tagSel.innerHTML = ["<option value=''>全部标签</option>"]
      .concat(allTags.map(t => `<option value="${t}">${t}</option>`))
      .join("");
    tagSel.value = state.galleryTagFilter || "";
  }
  if (moveSel) {
    const cur = moveSel.value || "默认";
    moveSel.innerHTML = state.folders.map(f => `<option value="${f}">移动到：${f}</option>`).join("");
    moveSel.value = state.folders.includes(cur) ? cur : (state.folders[0] || "默认");
  }
}

function refreshAssetSelectors() {
  const sorted = [...state.assets].sort((a, b) => {
    const fa = String(a.folder || "默认");
    const fb = String(b.folder || "默认");
    if (fa !== fb) return fa.localeCompare(fb, "zh-CN");
    return getAssetName(a).localeCompare(getAssetName(b), "zh-CN");
  });

  const layerSel = qs("#layer-asset");
  const prevLayer = layerSel ? layerSel.value : "";
  if (layerSel) {
    const options = ["<option value=''>请选择素材</option>"]
      .concat(sorted.map(x => {
        const name = getAssetName(x);
        const folder = x.folder || "默认";
        return `<option value="${x.asset_id}">[${folder}] ${name}</option>`;
      }));
    layerSel.innerHTML = options.join("");
    if (prevLayer && sorted.some(x => x.asset_id === prevLayer)) layerSel.value = prevLayer;
  }

  const genOptSel = qs("#gen-opt-asset");
  if (genOptSel) {
    const prev = genOptSel.value;
    genOptSel.innerHTML = ["<option value=''>选择要优化的图片</option>"]
      .concat(sorted.map(x => {
        const name = getAssetName(x);
        const folder = x.folder || "默认";
        return `<option value="${x.asset_id}">[${folder}] ${name}</option>`;
      }))
      .join("");
    if (prev && sorted.some(x => x.asset_id === prev)) genOptSel.value = prev;
  }
  const overlaySel = qs("#layer-overlay-asset");
  if (overlaySel) {
    const prev = overlaySel.value;
    overlaySel.innerHTML = ["<option value=''>选择要叠加的素材</option>"]
      .concat(sorted.map(x => {
        const name = getAssetName(x);
        const folder = x.folder || "默认";
        return `<option value="${x.asset_id}">[${folder}] ${name}</option>`;
      }))
      .join("");
    if (prev && sorted.some(x => x.asset_id === prev)) overlaySel.value = prev;
  }
  renderLayerSelectedAsset();
}

function renderLayerSelectedAsset() {
  const box = qs("#layer-selected-box");
  const sel = qs("#layer-asset");
  if (!box || !sel) return;
  const id = (sel.value || "").trim();
  if (!id) {
    box.textContent = "未选择素材";
    return;
  }
  const item = state.assets.find(x => x.asset_id === id);
  if (!item) {
    box.textContent = `已选素材: ${id}`;
    return;
  }
  const name = getAssetName(item);
  const folder = item.folder || "默认";
  const tags = (item.tags || []).slice(0, 4).join(" / ");
  box.innerHTML = `
    <span style="display:flex;align-items:center;gap:10px;">
      <img src="${item.url}" alt="${item.asset_id}" style="width:46px;height:46px;border-radius:8px;object-fit:cover;border:1px solid #d8e6f7;" />
      <span>
        <b>已选素材</b><br/>
        ${name}<br/>
        <small style="color:#5a6c82;">文件夹: ${folder}${tags ? ` | 标签: ${tags}` : ""}</small>
      </span>
    </span>
  `;
}

async function previewSelectedAssetOnLayerCanvas() {
  const sel = qs("#layer-asset");
  if (!sel) return;
  const assetId = (sel.value || "").trim();
  if (!assetId) return;
  const item = state.assets.find(x => x.asset_id === assetId);
  if (!item) return;

  try {
    const img = await loadImage(item.url);
    await setupLayerEditorFromImage(img, assetId);
  } catch (_) {
    // ignore preview loading failure
  }
}

async function refreshAssets() {
  const d = await api("/api/assets");
  state.assets = d.items || [];
  try {
    await refreshFolders();
  } catch (_) {
    state.folders = ["默认"];
    refreshFolderSelectors();
  }
  renderGallery();
  refreshAssetSelectors();
}

function getVisibleGalleryAssets() {
  return state.assets.filter((x) => {
    const folderOk = !state.galleryFolderFilter || (x.folder || "默认") === state.galleryFolderFilter;
    const tagOk = !state.galleryTagFilter || (x.tags || []).includes(state.galleryTagFilter);
    return folderOk && tagOk;
  });
}

function updateGalleryStatus() {
  const total = state.assets.length;
  const visible = getVisibleGalleryAssets().length;
  const existing = new Set(state.assets.map(x => x.asset_id));
  const selected = Array.from(state.gallerySelected).filter(id => existing.has(id)).length;
  const folder = state.galleryFolderFilter || "全部";

  const f = qs("#gallery-status-folder");
  const v = qs("#gallery-status-visible");
  const s = qs("#gallery-status-selected");
  const t = qs("#gallery-status-total");
  if (f) f.textContent = folder;
  if (v) v.textContent = String(visible);
  if (s) s.textContent = String(selected);
  if (t) t.textContent = String(total);

  const disabled = selected === 0;
  const tagBtn = qs("#gallery-apply-tags");
  const delBtn = qs("#gallery-delete-selected");
  if (tagBtn) tagBtn.disabled = disabled;
  if (delBtn) delBtn.disabled = disabled;
}

function renderGallery() {
  const existing = new Set(state.assets.map(x => x.asset_id));
  state.gallerySelected = new Set(Array.from(state.gallerySelected).filter(id => existing.has(id)));
  const grid = qs("#gallery-grid");
  const shown = getVisibleGalleryAssets();
  if (!shown.length) {
    grid.innerHTML = "<p>暂无素材</p>";
    updateGalleryStatus();
    return;
  }
  grid.innerHTML = shown.map(item => {
    const tags = renderTagBadges(item.tags || []);
    const name = getAssetName(item);
    const checked = state.gallerySelected.has(item.asset_id) ? "checked" : "";
    const folder = item.folder || "默认";
    return `<div class="thumb" data-asset-id="${item.asset_id}">
      <div class="thumb-head">
        <label><input type="checkbox" class="gallery-check" data-id="${item.asset_id}" ${checked} /> 选择</label>
        <div class="thumb-head-actions">
          <a href="${item.url}" download class="mini ok gallery-download">下载</a>
          <button class="mini warn gallery-delete-one" data-id="${item.asset_id}">删除</button>
        </div>
      </div>
      <img src="${item.url}" alt="${item.asset_id}" />
      <div class="meta"><b class="gallery-name" data-id="${item.asset_id}" title="双击可重命名">${name}</b><br>文件夹: ${folder}<br>${tags}</div>
    </div>`;
  }).join("");
  updateGalleryStatus();
}

function setupTabs() {
  qsa(".tab").forEach(btn => {
    btn.onclick = () => {
      qsa(".tab").forEach(x => x.classList.remove("active"));
      qsa(".pane").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      qs(`#pane-${btn.dataset.tab}`).classList.add("active");
    };
  });
}

function setupOptimizePromptBuild() {
  qs("#p-build").onclick = async () => {
    try {
      const size = getSizeByPrefix("gen");
      const d = await api("/api/prompt/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product_name: qs("#p-product-name").value,
          primary_selling: qs("#p-selling-primary").value,
          secondary_selling: qs("#p-selling-secondary").value,
          other_selling: qs("#p-selling-other").value,
          mode: qs("#p-mode").value,
          resolution: size.resolution,
          display_text: qs("#image-overlay-text").value || "",
        }),
      });
      qs("#gen-prompt").value = d.prompt;
      if (d.warning) {
        qs("#p-mode-tip").textContent = `已回退到模板模式：${d.warning}`;
      } else if (d.mode === "hybrid") {
        qs("#p-mode-tip").textContent = `混合模式已生成（模型：${d.llm_model || "N/A"}）`;
      } else {
        qs("#p-mode-tip").textContent = "模板模式已生成，可手动修改后再调用图片API。";
      }
    } catch (e) {
      alert(e.message);
    }
  };
}

function getSizeByPrefix(prefix) {
  const sizeSel = qs(`#${prefix}-size`)?.value || "1024x1024";
  if (sizeSel !== "custom") {
    const [w, h] = sizeSel.split("x").map(Number);
    return { width: w, height: h, resolution: `${w}x${h}` };
  }
  const cw = Number(qs(`#${prefix}-custom-width`)?.value || 1024);
  const ch = Number(qs(`#${prefix}-custom-height`)?.value || 1024);
  const width = Math.min(2048, Math.max(256, Math.round(cw)));
  const height = Math.min(2048, Math.max(256, Math.round(ch)));
  return { width, height, resolution: `${width}x${height}` };
}

function setupSizePickerByPrefix(prefix) {
  const sizeSel = qs(`#${prefix}-size`);
  const wInput = qs(`#${prefix}-custom-width`);
  const hInput = qs(`#${prefix}-custom-height`);
  if (!sizeSel || !wInput || !hInput) return;
  const sync = () => {
    const isCustom = sizeSel.value === "custom";
    wInput.style.display = isCustom ? "block" : "none";
    hInput.style.display = isCustom ? "block" : "none";
  };
  sizeSel.onchange = sync;
  sync();
}

function setupGeneratePromptBuild() {
  qs("#g-build").onclick = async () => {
    try {
      const size = getSizeByPrefix("g");
      const d = await api("/api/prompt/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product_name: qs("#g-product-name").value,
          primary_selling: qs("#g-selling-primary").value,
          secondary_selling: qs("#g-selling-secondary").value,
          other_selling: qs("#g-selling-other").value,
          mode: qs("#g-mode").value,
          resolution: size.resolution,
          display_text: qs("#g-image-overlay-text").value || "",
        }),
      });
      qs("#g-prompt").value = d.prompt;
      if (d.warning) {
        qs("#g-mode-tip").textContent = `已回退到模板模式：${d.warning}`;
      } else if (d.mode === "hybrid") {
        qs("#g-mode-tip").textContent = `混合模式已生成（模型：${d.llm_model || "N/A"}）`;
      } else {
        qs("#g-mode-tip").textContent = "模板模式已生成，可手动修改后再调用图片API。";
      }
    } catch (e) {
      alert(e.message);
    }
  };
}

function setupOptimize() {
  const optBtn = qs("#gen-opt-run");
  if (optBtn) {
    optBtn.onclick = async () => {
      if (state.busy.opt) return;
      state.busy.opt = true;
      setDisabled(["#gen-opt-run"], true);
      const pg = startProgress("opt", "正在优化图片，请稍候...");
      try {
        const asset_id = (qs("#gen-opt-asset")?.value || "").trim();
        const prompt = qs("#gen-prompt").value.trim();
        const strength = Number(qs("#gen-opt-strength")?.value || 0.65);
        const { width: w, height: h } = getSizeByPrefix("gen");

        if (!asset_id) return alert("请选择要优化的图片");
        if (!prompt) return alert("请先生成或填写提示词");

        const d = await api("/api/ai/optimize-by-prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_id,
            prompt,
            strength: Math.min(1, Math.max(0, strength)),
            num_images: 1,
            width: w,
            height: h,
          }),
        });
        const prev = qs("#gen-opt-result").innerHTML || "";
        const cards = (d.items || (d.item ? [d.item] : [])).map(card).join("");
        qs("#gen-opt-result").innerHTML = cards + prev;
        await refreshAssets();
        finishProgress(pg, "图片已优化");
      } catch (e) {
        finishProgress(pg, "优化失败");
        alert(e.message);
      } finally {
        state.busy.opt = false;
        setDisabled(["#gen-opt-run"], false);
      }
    };
  }
}

function setupImageGenerate() {
  qs("#g-run").onclick = async () => {
    if (state.busy.gen) return;
    state.busy.gen = true;
    setDisabled(["#g-run"], true);
    const pg = startProgress("gen", "正在生成图片，请稍候...");
    try {
      const prompt = qs("#g-prompt").value.trim();
      let html = "";
      if (prompt) {
        const { width: w, height: h } = getSizeByPrefix("g");
        const d = await api("/api/ai/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt,
            provider: "qwen",
            width: w,
            height: h,
            display_text: qs("#g-image-overlay-text").value || "",
          }),
        });
        html = card(d.item);
      }
      qs("#g-run-result").innerHTML = html || "<p>请先输入提示词</p>";
      await refreshAssets();
      finishProgress(pg, "图片已生成");
    } catch (e) {
      finishProgress(pg, "生成失败");
      alert(e.message);
    } finally {
      state.busy.gen = false;
      setDisabled(["#g-run"], false);
    }
  };
}

function setupBatchBg() {
  function setupPicker(prefix) {
    const panel = qs(`#${prefix}-picker-panel`);
    const idsInput = qs(`#${prefix}-asset-ids`);
    const namesInput = qs(`#${prefix}-asset-names`);
    const countEl = qs(`#${prefix}-selected-count`);

    function setSelected(ids) {
      const uniqueIds = Array.from(new Set((ids || []).map(x => String(x).trim()).filter(Boolean)));
      idsInput.value = uniqueIds.join(",");
      const names = uniqueIds
        .map(id => state.assets.find(x => x.asset_id === id))
        .filter(Boolean)
        .map(x => `[${x.folder || "默认"}] ${getAssetName(x)}`);
      namesInput.value = names.join("\n");
      countEl.textContent = `${uniqueIds.length} 张`;
    }

    function getSelected() {
      return (idsInput.value || "").split(",").map(s => s.trim()).filter(Boolean);
    }

    function renderPicker() {
      if (!state.assets.length) {
        panel.innerHTML = "<p>素材库为空，请先生成或上传图片。</p>";
        return;
      }
      const selected = new Set(getSelected());
      const allFolders = ["全部文件夹"].concat(state.folders || ["默认"]);
      const curFilter = panel.dataset.folderFilter || "全部文件夹";
      const curKeyword = panel.dataset.keyword || "";
      const curTag = panel.dataset.tag || "";
      const allTagList = uniqueTags(state.assets);
      const source = state.assets
        .filter(x => curFilter === "全部文件夹" ? true : (x.folder || "默认") === curFilter)
        .filter(x => {
          if (!curTag) return true;
          return (x.tags || []).includes(curTag);
        })
        .filter(x => {
          if (!curKeyword) return true;
          const kw = curKeyword.toLowerCase();
          const text = `${getAssetName(x)} ${(x.folder || "默认")} ${(x.tags || []).join(" ")}`.toLowerCase();
          return text.includes(kw);
        })
        .slice(0, 300);
      panel.innerHTML = `
        <div class="picker-toolbar">
          <button class="btn" id="${prefix}-pick-select-all">全选</button>
          <button class="btn" id="${prefix}-pick-clear-all">全不选</button>
          <button class="btn" id="${prefix}-append-pick">追加选择</button>
          <button class="btn primary" id="${prefix}-apply-pick">应用选择</button>
        </div>
        <div class="picker-filter-row">
          <select id="${prefix}-pick-folder-filter">
            ${allFolders.map(f => `<option value="${f}" ${f === curFilter ? "selected" : ""}>${f}</option>`).join("")}
          </select>
          <select id="${prefix}-pick-tag-filter">
            <option value="">全部标签</option>
            ${allTagList.map(t => `<option value="${t}" ${t === curTag ? "selected" : ""}>${t}</option>`).join("")}
          </select>
          <input id="${prefix}-pick-search" placeholder="输入关键词搜索图片名/文件夹/标签" value="${curKeyword.replace(/"/g, "&quot;")}" />
        </div>
        <div class="picker-scroll">
          <div class="picker-grid">
            ${source.map(x => {
              const checked = selected.has(x.asset_id) ? "checked" : "";
              const name = getAssetName(x);
              const folder = x.folder || "默认";
              const tags = (x.tags || []).slice(0, 3).join(" / ");
              return `
                <label class="pick-card">
                  <input type="checkbox" value="${x.asset_id}" ${checked} />
                  <img src="${x.url}" alt="${x.asset_id}" />
                  <span title="${name}">${name}</span>
                  <small class="pick-meta">[${folder}] ${tags || "无标签"}</small>
                </label>
              `;
            }).join("")}
          </div>
        </div>
      `;
      qs(`#${prefix}-pick-select-all`).onclick = () => {
        qsa(`#${prefix}-picker-panel .pick-card input[type='checkbox']`).forEach(el => { el.checked = true; });
      };
      qs(`#${prefix}-pick-clear-all`).onclick = () => {
        qsa(`#${prefix}-picker-panel .pick-card input[type='checkbox']`).forEach(el => { el.checked = false; });
      };
      qs(`#${prefix}-pick-folder-filter`).onchange = (ev) => {
        panel.dataset.folderFilter = ev.target.value || "全部文件夹";
        renderPicker();
      };
      qs(`#${prefix}-pick-tag-filter`).onchange = (ev) => {
        panel.dataset.tag = ev.target.value || "";
        renderPicker();
      };
      qs(`#${prefix}-pick-search`).oninput = (ev) => {
        panel.dataset.keyword = (ev.target.value || "").trim();
        renderPicker();
      };
      qs(`#${prefix}-apply-pick`).onclick = () => {
        const ids = qsa(`#${prefix}-picker-panel .pick-card input[type='checkbox']:checked`).map(x => x.value);
        setSelected(ids);
      };
      qs(`#${prefix}-append-pick`).onclick = () => {
        const ids = qsa(`#${prefix}-picker-panel .pick-card input[type='checkbox']:checked`).map(x => x.value);
        setSelected(getSelected().concat(ids));
      };
    }

    qs(`#${prefix}-pick-assets`).onclick = async () => {
      try {
        await refreshAssets();
      } catch (_) {}
      renderPicker();
    };
    qs(`#${prefix}-clear-assets`).onclick = () => setSelected([]);
    setSelected(getSelected());

    return { getSelected };
  }

  const wmPicker = setupPicker("wm");
  const bgPicker = setupPicker("bg");

  async function runBatch(prefix, doWm, doBg, getSelected) {
    if (state.busy[prefix]) return;
    state.busy[prefix] = true;
    setDisabled([`#${prefix}-run`], true);
    const pg = startProgress(prefix, doWm ? "正在去水印，请稍候..." : "正在换背景，请稍候...");
    try {
      let ids = getSelected();
      if (!ids.length) ids = state.assets.slice(0, 5).map(x => x.asset_id);
      if (!ids.length) return alert("素材库为空，请先生成或上传图片");

      const outItems = [];
      for (const id of ids) {
        const d = await api("/api/ai/batch-process", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_ids: [id],
            do_remove_watermark: doWm,
            do_replace_bg: doBg,
            wm_provider: "qwen",
            bg_prompt: qs("#bg-prompt")?.value || "简洁电商背景",
          }),
        });
        if (d?.items?.length) outItems.push(...d.items);
      }
      qs(`#${prefix}-result`).innerHTML = outItems.map(card).join("");
      await refreshAssets();
      finishProgress(pg, "处理完成");
    } catch (e) {
      finishProgress(pg, "处理失败");
      alert(e.message);
    } finally {
      state.busy[prefix] = false;
      setDisabled([`#${prefix}-run`], false);
    }
  }

  qs("#wm-run").onclick = () => runBatch("wm", true, false, wmPicker.getSelected);
  qs("#bg-run").onclick = () => runBatch("bg", false, true, bgPicker.getSelected);
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`图片加载失败: ${url}`));
    img.src = url;
  });
}

function imageToDataUrl(img) {
  const c = document.createElement("canvas");
  c.width = img.naturalWidth || img.width;
  c.height = img.naturalHeight || img.height;
  const cctx = c.getContext("2d");
  if (!cctx) throw new Error("浏览器不支持画布导出");
  cctx.clearRect(0, 0, c.width, c.height);
  cctx.drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/png");
}

function getEditorTextFontFamily(vRaw = null) {
  const v = String(vRaw ?? (qs("#txt-font")?.value || "noto")).trim();
  if (v === "song") return `"Noto Serif SC","Songti SC","STSong",serif`;
  if (v === "hei") return `"PingFang SC","Microsoft YaHei","Heiti SC","Noto Sans SC",sans-serif`;
  if (v === "kai") return `"Kaiti SC","STKaiti","KaiTi","Noto Serif SC",serif`;
  if (v === "manrope") return `"Manrope","Noto Sans SC",sans-serif`;
  return `"Noto Sans SC",sans-serif`;
}

function drawWrappedTextLayer(ctx, layer) {
  if (!ctx || !layer) return;
  const text = String(layer.content || "").trim();
  if (!text) return;
  const size = Number(layer.size || 36);
  const color = String(layer.color || "#22344a");
  const fontKey = String(layer.font || "noto");
  const noWrap = (layer.noWrap === true) || (layer.noWrap == null && !!qs("#txt-nowrap")?.checked);
  const boxW = Math.max(40, Number(layer.boxW || 260));
  const maxW = Math.max(40, boxW - 12);
  const lineH = Math.max(16, Math.round(Math.max(12, size) * 1.35));
  ctx.font = `700 ${Math.max(12, size)}px ${getEditorTextFontFamily(fontKey)}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";

  const wrapLine = (line) => {
    const out = [];
    let cur = "";
    for (const ch of line) {
      const test = cur + ch;
      if (ctx.measureText(test).width <= maxW || cur.length === 0) cur = test;
      else {
        out.push(cur);
        cur = ch;
      }
    }
    if (cur) out.push(cur);
    if (!out.length) out.push("");
    return out;
  };

  const lines = getWrappedTextLines(ctx, text, maxW, noWrap);

  const startX = Number(layer.x || 0) - boxW / 2 + 6;
  const startY = Number(layer.y || 0) + 4;
  ctx.lineWidth = Math.max(2, Math.round(size * 0.12));
  ctx.strokeStyle = "rgba(255,255,255,0.92)";
  ctx.fillStyle = color;
  for (let i = 0; i < lines.length; i += 1) {
    const y = startY + i * lineH;
    ctx.strokeText(lines[i], startX, y);
    ctx.fillText(lines[i], startX, y);
  }
}

function getWrappedTextLines(ctx, text, maxW, noWrap = false) {
  if (noWrap) {
    const rawLines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    return rawLines.length ? rawLines : [""];
  }
  const wrapLine = (line) => {
    const out = [];
    let cur = "";
    for (const ch of line) {
      const test = cur + ch;
      if (ctx.measureText(test).width <= maxW || cur.length === 0) cur = test;
      else {
        out.push(cur);
        cur = ch;
      }
    }
    if (cur) out.push(cur);
    if (!out.length) out.push("");
    return out;
  };
  const lines = [];
  const rawLines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  for (const ln of rawLines) lines.push(...wrapLine(ln));
  return lines;
}

function getTextStampBounds(editor, layer) {
  if (!editor || !editor.ctx || !layer) return null;
  const text = String(layer.content || "").trim();
  if (!text) return null;
  const size = Number(layer.size || 36);
  const boxW = Math.max(40, Number(layer.boxW || 260));
  const boxH = Math.max(30, Number(layer.boxH || 44));
  const noWrap = (layer.noWrap === true) || (layer.noWrap == null && !!qs("#txt-nowrap")?.checked);
  const maxW = Math.max(40, boxW - 12);
  const lineH = Math.max(16, Math.round(Math.max(12, size) * 1.35));
  const ctx = editor.ctx;
  ctx.save();
  ctx.font = `700 ${Math.max(12, size)}px ${getEditorTextFontFamily(layer.font || "noto")}`;
  const lines = getWrappedTextLines(ctx, text, maxW, noWrap);
  ctx.restore();
  const contentH = Math.max(lineH, lines.length * lineH);
  const h = Math.max(boxH, contentH + 8);
  return {
    x: Number(layer.x || 0) - boxW / 2,
    y: Number(layer.y || 0),
    w: boxW,
    h,
  };
}

function findTextStampAtPoint(editor, px, py) {
  if (!editor || !Array.isArray(editor.textStamps)) return -1;
  for (let i = editor.textStamps.length - 1; i >= 0; i -= 1) {
    const b = getTextStampBounds(editor, editor.textStamps[i]);
    if (!b) continue;
    if (px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h) return i;
  }
  return -1;
}

function syncToolbarFromSelectedTextStamp(ed) {
  if (!ed || !Array.isArray(ed.textStamps)) return;
  const idx = Number(ed.activeTextStamp ?? -1);
  if (idx < 0 || idx >= ed.textStamps.length) return;
  const s = ed.textStamps[idx];
  const txt = qs("#txt-content");
  if (txt) txt.value = String(s.content || "");
  qs("#txt-size").value = String(Number(s.size || 36));
  qs("#txt-nowrap").checked = !!s.noWrap;
  qs("#txt-font").value = String(s.font || "noto");
  qs("#txt-color").value = String(s.color || "#22344a");
  ed.textX = Number(s.x || ed.textX);
  ed.textY = Number(s.y || ed.textY);
  ed.textBoxW = Math.max(80, Number(s.boxW || ed.textBoxW || 260));
  ed.textBoxH = Math.max(30, Number(s.boxH || ed.textBoxH || 44));
}

function refreshTextLayerList(ed = null) {
  const sel = qs("#text-layer-list");
  if (!sel) return;
  const editor = ed || state.layerEditor;
  const stamps = editor && Array.isArray(editor.textStamps) ? editor.textStamps : [];
  if (!stamps.length) {
    sel.innerHTML = '<option value="">暂无文字图层</option>';
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  sel.innerHTML = stamps.map((s, i) => {
    const raw = String(s.content || "").replace(/\s+/g, " ").trim();
    const name = raw ? raw.slice(0, 16) : "(空文字)";
    return `<option value="${i}">#${i + 1} ${name}</option>`;
  }).join("");
  const active = Number(editor.activeTextStamp ?? -1);
  if (active >= 0 && active < stamps.length) sel.value = String(active);
}

function isOverlayTextInput() {
  const input = qs("#txt-content");
  return !!(input && input.classList && input.classList.contains("canvas-text-input"));
}

function syncCanvasTextInputPosition() {
  const ed = state.layerEditor;
  const input = qs("#txt-content");
  const handle = qs("#txt-resize-handle");
  if (!ed || !input || !ed.canvas) return;
  if (!isOverlayTextInput()) return;
  const rect = ed.canvas.getBoundingClientRect();
  const sx = rect.width / Math.max(1, ed.canvas.width);
  const sy = rect.height / Math.max(1, ed.canvas.height);
  const fontPx = Math.max(12, Math.round(Number(qs("#txt-size")?.value || 36) * Math.min(sx, sy)));
  const boxW = Math.max(80, Math.round(Number(ed.textBoxW || 260) * sx));
  const boxH = Math.max(30, Math.round(Number(ed.textBoxH || 44) * sy));
  input.style.left = `${Math.round(Number(ed.textX || 0) * sx)}px`;
  input.style.top = `${Math.round(Number(ed.textY || 0) * sy)}px`;
  input.style.transform = "translate(-50%, 0)";
  input.style.fontSize = `${fontPx}px`;
  input.style.fontFamily = getEditorTextFontFamily();
  input.style.color = qs("#txt-color")?.value || "#22344a";
  input.style.width = `${boxW}px`;
  input.style.height = `${boxH}px`;
  input.style.minHeight = `${boxH}px`;
  if (handle) {
    const hs = 14;
    const left = Math.round(Number(ed.textX || 0) * sx + boxW / 2 - hs / 2);
    const top = Math.round(Number(ed.textY || 0) * sy + boxH - hs / 2);
    handle.style.left = `${left}px`;
    handle.style.top = `${top}px`;
  }
}

function setCanvasTextInputVisible(visible, focusInput = false) {
  const input = qs("#txt-content");
  const handle = qs("#txt-resize-handle");
  if (!input) return;
  if (!isOverlayTextInput()) return;
  if (visible) {
    input.classList.add("show");
    if (handle) handle.classList.add("show");
    if (focusInput) {
      setTimeout(() => {
        input.focus();
        try { input.setSelectionRange(input.value.length, input.value.length); } catch (_) {}
      }, 0);
    }
  } else {
    input.classList.remove("show");
    if (handle) handle.classList.remove("show");
    if (document.activeElement === input) input.blur();
  }
}

function isCanvasTextInputShown() {
  if (!isOverlayTextInput()) return false;
  const input = qs("#txt-content");
  return !!(input && input.classList.contains("show"));
}

function shouldRenderDraftText(ed) {
  const text = (qs("#txt-content")?.value || "").trim();
  if (!text) return false;
  const activeStampIdx = Number(ed?.activeTextStamp ?? -1);
  if (activeStampIdx >= 0) return false;
  // Toolbar-input mode: only preview draft while user is actively editing input.
  if (!isOverlayTextInput()) {
    const input = qs("#txt-content");
    return !!(input && document.activeElement === input);
  }
  // Overlay-input mode: keep previous behavior.
  const input = qs("#txt-content");
  return !!(input && input.classList.contains("show"));
}

function exportLayerCanvasDataUrl(ed) {
  const c = document.createElement("canvas");
  c.width = ed.canvas.width;
  c.height = ed.canvas.height;
  const cctx = c.getContext("2d");
  if (!cctx) return ed.canvas.toDataURL("image/png");
  const filter = `brightness(${ed.brightness}%) contrast(${ed.contrast}%) saturate(${ed.saturation}%)`;
  if (ed.singleMode) {
    cctx.fillStyle = "#ffffff";
    cctx.fillRect(0, 0, c.width, c.height);
  } else {
    cctx.save();
    cctx.filter = filter;
    cctx.drawImage(ed.bgImg, 0, 0, c.width, c.height);
    cctx.restore();
  }

  const subjectImg = ed.fgImg;
  if (subjectImg) {
    cctx.save();
    cctx.translate(ed.fgX, ed.fgY);
    cctx.rotate((ed.rotateDeg * Math.PI) / 180);
    cctx.scale(ed.scale * ed.flipX, ed.scale * ed.flipY);
    cctx.globalAlpha = ed.opacity;
    cctx.filter = filter;
    cctx.drawImage(subjectImg, -subjectImg.width / 2, -subjectImg.height / 2);
    cctx.restore();
  }

  if (ed.cutoutLayer && ed.cutoutLayer.img) {
    const cl = ed.cutoutLayer;
    const cScale = Number(cl.scale || 1);
    const cRotate = Number(cl.rotateDeg || 0);
    const cOpacity = Number(cl.opacity || 1);
    const cFlipX = Number(cl.flipX || 1);
    const cFlipY = Number(cl.flipY || 1);
    cctx.save();
    cctx.translate(cl.x, cl.y);
    cctx.rotate((cRotate * Math.PI) / 180);
    cctx.scale(cScale * cFlipX, cScale * cFlipY);
    cctx.globalAlpha = cOpacity;
    cctx.drawImage(cl.img, -cl.img.width / 2, -cl.img.height / 2);
    cctx.restore();
  }

  if (ed.overlay && ed.overlay.img) {
    const ov = ed.overlay;
    const w = ov.img.width * ov.scale;
    const h = ov.img.height * ov.scale;
    cctx.save();
    cctx.globalAlpha = ov.opacity;
    cctx.drawImage(ov.img, ov.x - w / 2, ov.y - h / 2, w, h);
    cctx.restore();
  }

  const text = (qs("#txt-content")?.value || "").trim();
  const activeStampIdx = Number(ed.activeTextStamp ?? -1);
  for (const layer of (ed.textStamps || [])) drawWrappedTextLayer(cctx, layer);
  const shouldDrawDraftText = !!text && activeStampIdx < 0 && shouldRenderDraftText(ed);
  if (shouldDrawDraftText) {
    drawWrappedTextLayer(cctx, {
      content: text,
      size: Number(qs("#txt-size")?.value || 36),
      noWrap: !!qs("#txt-nowrap")?.checked,
      font: String(qs("#txt-font")?.value || "noto"),
      color: String(qs("#txt-color")?.value || "#22344a"),
      x: Number(ed.textX || 0),
      y: Number(ed.textY || 0),
      boxW: Number(ed.textBoxW || 260),
      boxH: Number(ed.textBoxH || 44),
    });
  }
  return c.toDataURL("image/png");
}

function layerRender() {
  const editor = state.layerEditor;
  if (!editor) return;
  const { canvas, ctx, bgImg } = editor;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const filter = `brightness(${editor.brightness}%) contrast(${editor.contrast}%) saturate(${editor.saturation}%)`;
  if (editor.singleMode) {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  } else {
    ctx.save();
    ctx.filter = filter;
    ctx.drawImage(bgImg, 0, 0, canvas.width, canvas.height);
    ctx.restore();
  }

  const subjectImg = editor.fgImg;
  if (subjectImg) {
    ctx.save();
    ctx.translate(editor.fgX, editor.fgY);
    ctx.rotate((editor.rotateDeg * Math.PI) / 180);
    ctx.scale(editor.scale * editor.flipX, editor.scale * editor.flipY);
    ctx.globalAlpha = editor.opacity;
    ctx.filter = filter;
    ctx.drawImage(subjectImg, -subjectImg.width / 2, -subjectImg.height / 2);
    if (editor.activeTarget === "fg") {
      const w = subjectImg.width;
      const h = subjectImg.height;
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(15,123,255,0.95)";
      ctx.strokeRect(-w / 2 - 4, -h / 2 - 4, w + 8, h + 8);
    }
    ctx.restore();
  }

  if (editor.cutoutLayer && editor.cutoutLayer.img) {
    const cl = editor.cutoutLayer;
    const cScale = Number(cl.scale || 1);
    const cRotate = Number(cl.rotateDeg || 0);
    const cOpacity = Number(cl.opacity || 1);
    const cFlipX = Number(cl.flipX || 1);
    const cFlipY = Number(cl.flipY || 1);
    ctx.save();
    ctx.translate(cl.x, cl.y);
    ctx.rotate((cRotate * Math.PI) / 180);
    ctx.scale(cScale * cFlipX, cScale * cFlipY);
    ctx.globalAlpha = cOpacity;
    ctx.drawImage(cl.img, -cl.img.width / 2, -cl.img.height / 2);
    if (editor.activeTarget === "cutout") {
      const w = cl.img.width;
      const h = cl.img.height;
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(15,123,255,0.95)";
      ctx.strokeRect(-w / 2 - 4, -h / 2 - 4, w + 8, h + 8);
    }
    ctx.restore();
  }

  if (editor.activeTarget === "fg") {
    const b = getFgBounds(editor);
    if (b) drawCornerHandles(ctx, b, true);
  } else if (editor.activeTarget === "cutout") {
    const b = getCutoutBounds(editor);
    if (b) drawCornerHandles(ctx, b, true);
  } else if (editor.activeTarget === "overlay") {
    const b = getOverlayBounds(editor);
    if (b) drawCornerHandles(ctx, b, true);
  }

  if (editor.overlay && editor.overlay.img) {
    const ov = editor.overlay;
    const w = ov.img.width * ov.scale;
    const h = ov.img.height * ov.scale;
    ctx.save();
    ctx.globalAlpha = ov.opacity;
    ctx.drawImage(ov.img, ov.x - w / 2, ov.y - h / 2, w, h);
    if (editor.activeTarget === "overlay") {
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(15,123,255,0.95)";
      ctx.fillStyle = "rgba(15,123,255,0.10)";
      ctx.strokeRect(ov.x - w / 2 - 4, ov.y - h / 2 - 4, w + 8, h + 8);
      ctx.fillRect(ov.x - w / 2 - 4, ov.y - h / 2 - 4, w + 8, h + 8);
    }
    ctx.restore();
  }

  const text = (qs("#txt-content")?.value || "").trim();
  const textShown = isCanvasTextInputShown();
  const activeStampIdx = Number(editor.activeTextStamp ?? -1);
  (editor.textStamps || []).forEach((layer, idx) => {
    drawWrappedTextLayer(ctx, layer);
    if (Number(editor.activeTextStamp ?? -1) === idx) {
      const b = getTextStampBounds(editor, layer);
      if (b) {
        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(15,123,255,0.95)";
        ctx.strokeRect(b.x - 4, b.y - 3, b.w + 8, b.h + 6);
        ctx.restore();
      }
    }
  });
  if (textShown) {
    const tb = getEditorTextBounds(editor, true);
    if (tb) {
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = editor.activeTarget === "text" ? 2 : 1.5;
      ctx.strokeStyle = editor.activeTarget === "text" ? "rgba(15,123,255,0.95)" : "rgba(95,130,170,0.8)";
      ctx.strokeRect(tb.x - 6, tb.y - 4, tb.w + 12, tb.h + 8);
      ctx.restore();
      if (editor.activeTarget === "text" && text) drawCornerHandles(ctx, tb, true);
    }
  }

  if (!isOverlayTextInput() && text && activeStampIdx < 0 && shouldRenderDraftText(editor)) {
    const size = Number(qs("#txt-size")?.value || 36);
    const color = qs("#txt-color")?.value || "#22344a";
    ctx.font = `700 ${Math.max(12, size)}px ${getEditorTextFontFamily()}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const tx = editor.textX;
    const ty = editor.textY;
    ctx.lineWidth = Math.max(2, Math.round(size * 0.12));
    ctx.strokeStyle = "rgba(255,255,255,0.92)";
    ctx.strokeText(text, tx, ty);
    ctx.fillStyle = color;
    ctx.fillText(text, tx, ty);
    if (editor.activeTarget === "text") {
      const tb = getEditorTextBounds(editor, true);
      if (tb) {
        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(15,123,255,0.95)";
        ctx.fillStyle = "rgba(15,123,255,0.10)";
        ctx.strokeRect(tb.x - 6, tb.y - 4, tb.w + 12, tb.h + 8);
        ctx.fillRect(tb.x - 6, tb.y - 4, tb.w + 12, tb.h + 8);
        ctx.restore();
      }
    }
  }

  syncCanvasTextInputPosition();

  if (editor.cutoutBoxMode && editor.cutoutBox) {
    const b = editor.cutoutBox;
    const bx = Math.min(b.x, b.x + b.w);
    const by = Math.min(b.y, b.y + b.h);
    const bw = Math.abs(b.w);
    const bh = Math.abs(b.h);
    ctx.save();
    ctx.setLineDash([8, 4]);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(15,123,255,0.96)";
    ctx.fillStyle = "rgba(15,123,255,0.12)";
    ctx.strokeRect(bx, by, bw, bh);
    ctx.fillRect(bx, by, bw, bh);
    ctx.restore();
  }
  refreshTextLayerList(editor);
}

function getEditorTextBounds(editor, includeEmpty = false) {
  const text = (qs("#txt-content")?.value || "").trim();
  if (!editor) return null;
  if (!text && !includeEmpty) return null;
  if (!isOverlayTextInput()) {
    const size = Number(qs("#txt-size")?.value || 36);
    const ctx = editor.ctx;
    let width = 0;
    if (text) {
      ctx.save();
      ctx.font = `700 ${Math.max(12, size)}px ${getEditorTextFontFamily()}`;
      width = ctx.measureText(text).width;
      ctx.restore();
    } else {
      width = Math.max(180, Math.round(editor.canvas.width * 0.28));
    }
    const height = Math.max(16, Math.round(size * 1.25));
    return {
      x: editor.textX - width / 2,
      y: editor.textY,
      w: width,
      h: height,
    };
  }
  const width = Math.max(80, Number(editor.textBoxW || 260));
  const height = Math.max(30, Number(editor.textBoxH || 44));
  return {
    x: editor.textX - width / 2,
    y: editor.textY,
    w: width,
    h: height,
  };
}

function getOverlayBounds(editor) {
  if (!editor || !editor.overlay || !editor.overlay.img) return null;
  const ov = editor.overlay;
  const w = ov.img.width * ov.scale;
  const h = ov.img.height * ov.scale;
  return { x: ov.x - w / 2, y: ov.y - h / 2, w, h };
}

function getCutoutBounds(editor) {
  if (!editor || !editor.cutoutLayer || !editor.cutoutLayer.img) return null;
  const cl = editor.cutoutLayer;
  const w = cl.img.width * (cl.scale || 1);
  const h = cl.img.height * (cl.scale || 1);
  return { x: cl.x - w / 2, y: cl.y - h / 2, w, h };
}

function getFgBounds(editor) {
  if (!editor || !editor.fgImg) return null;
  const w = editor.fgImg.width * Math.abs(Number(editor.scale || 1));
  const h = editor.fgImg.height * Math.abs(Number(editor.scale || 1));
  return { x: editor.fgX - w / 2, y: editor.fgY - h / 2, w, h };
}

function drawCornerHandles(ctx, b, active = true) {
  if (!b || !ctx) return;
  const hs = 10;
  const corners = [
    { x: b.x, y: b.y },
    { x: b.x + b.w, y: b.y },
    { x: b.x, y: b.y + b.h },
    { x: b.x + b.w, y: b.y + b.h },
  ];
  ctx.save();
  ctx.setLineDash([]);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = active ? "rgba(15,123,255,0.98)" : "rgba(15,123,255,0.65)";
  ctx.fillStyle = "#ffffff";
  for (const c of corners) {
    ctx.beginPath();
    ctx.rect(c.x - hs / 2, c.y - hs / 2, hs, hs);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function hitResizeCorner(p, b) {
  if (!p || !b) return "";
  const hs = 12;
  const tests = [
    { id: "nw", x: b.x, y: b.y },
    { id: "ne", x: b.x + b.w, y: b.y },
    { id: "sw", x: b.x, y: b.y + b.h },
    { id: "se", x: b.x + b.w, y: b.y + b.h },
  ];
  for (const t of tests) {
    if (Math.abs(p.x - t.x) <= hs && Math.abs(p.y - t.y) <= hs) return t.id;
  }
  return "";
}

function layerSnapshot() {
  const ed = state.layerEditor;
  if (!ed) return null;
  return {
    fgX: ed.fgX,
    fgY: ed.fgY,
    scale: ed.scale,
    rotateDeg: ed.rotateDeg,
    flipX: ed.flipX,
    flipY: ed.flipY,
    opacity: ed.opacity,
    brightness: ed.brightness,
    contrast: ed.contrast,
    saturation: ed.saturation,
    textX: ed.textX,
    textY: ed.textY,
    textBoxW: Number(ed.textBoxW || 260),
    textBoxH: Number(ed.textBoxH || 44),
    textStamps: JSON.parse(JSON.stringify(ed.textStamps || [])),
    activeTextStamp: Number(ed.activeTextStamp ?? -1),
    txtContent: qs("#txt-content")?.value || "",
    txtSize: Number(qs("#txt-size")?.value || 36),
    txtNoWrap: !!qs("#txt-nowrap")?.checked,
    txtFont: qs("#txt-font")?.value || "noto",
    txtColor: qs("#txt-color")?.value || "#22344a",
    cutoutLayer: ed.cutoutLayer
      ? {
        x: ed.cutoutLayer.x,
        y: ed.cutoutLayer.y,
        scale: ed.cutoutLayer.scale,
        rotateDeg: ed.cutoutLayer.rotateDeg,
        flipX: ed.cutoutLayer.flipX,
        flipY: ed.cutoutLayer.flipY,
        opacity: ed.cutoutLayer.opacity,
      }
      : null,
    overlayAssetId: ed.overlay?.assetId || "",
    overlayX: ed.overlay?.x || 0,
    overlayY: ed.overlay?.y || 0,
    overlayScale: ed.overlay?.scale || 1,
    overlayOpacity: ed.overlay?.opacity || 1,
  };
}

function layerApplySnapshot(snap) {
  const ed = state.layerEditor;
  if (!ed || !snap) return;
  ed.fgX = snap.fgX;
  ed.fgY = snap.fgY;
  ed.scale = snap.scale;
  ed.rotateDeg = snap.rotateDeg;
  ed.flipX = snap.flipX;
  ed.flipY = snap.flipY;
  ed.opacity = snap.opacity;
  ed.brightness = snap.brightness;
  ed.contrast = snap.contrast;
  ed.saturation = snap.saturation;
  ed.textX = snap.textX;
  ed.textY = snap.textY;
  ed.textBoxW = Number(snap.textBoxW || ed.textBoxW || 260);
  ed.textBoxH = Number(snap.textBoxH || ed.textBoxH || 44);
  ed.textStamps = Array.isArray(snap.textStamps) ? JSON.parse(JSON.stringify(snap.textStamps)) : [];
  ed.activeTextStamp = Number(snap.activeTextStamp ?? -1);
  qs("#layer-scale").value = String(ed.scale);
  qs("#layer-rotate").value = String(ed.rotateDeg);
  qs("#layer-opacity").value = String(ed.opacity);
  qs("#layer-brightness").value = String(ed.brightness);
  qs("#layer-contrast").value = String(ed.contrast);
  qs("#layer-saturation").value = String(ed.saturation);
  qs("#txt-content").value = snap.txtContent || "";
  qs("#txt-size").value = String(snap.txtSize || 36);
  qs("#txt-nowrap").checked = !!snap.txtNoWrap;
  qs("#txt-font").value = snap.txtFont || "noto";
  qs("#txt-color").value = snap.txtColor || "#22344a";
  if (!snap.cutoutLayer) {
    ed.cutoutLayer = null;
  } else if (ed.cutoutLayer) {
    ed.cutoutLayer.x = Number(snap.cutoutLayer.x || ed.cutoutLayer.x);
    ed.cutoutLayer.y = Number(snap.cutoutLayer.y || ed.cutoutLayer.y);
    ed.cutoutLayer.scale = Number(snap.cutoutLayer.scale || ed.cutoutLayer.scale || 1);
    ed.cutoutLayer.rotateDeg = Number(snap.cutoutLayer.rotateDeg || 0);
    ed.cutoutLayer.flipX = Number(snap.cutoutLayer.flipX || 1);
    ed.cutoutLayer.flipY = Number(snap.cutoutLayer.flipY || 1);
    ed.cutoutLayer.opacity = Number(snap.cutoutLayer.opacity || 1);
  }
  if (snap.overlayAssetId && ed.overlayCache[snap.overlayAssetId]) {
    ed.overlay = {
      assetId: snap.overlayAssetId,
      img: ed.overlayCache[snap.overlayAssetId],
      x: snap.overlayX,
      y: snap.overlayY,
      scale: snap.overlayScale,
      opacity: snap.overlayOpacity,
    };
  } else if (!snap.overlayAssetId) {
    ed.overlay = null;
  }
  layerRender();
}

function layerPushHistory() {
  const ed = state.layerEditor;
  const snap = layerSnapshot();
  if (!ed || !snap) return;
  const cur = ed.history[ed.historyIndex];
  if (cur && JSON.stringify(cur) === JSON.stringify(snap)) return;
  ed.history = ed.history.slice(0, ed.historyIndex + 1);
  ed.history.push(snap);
  if (ed.history.length > 80) ed.history.shift();
  ed.historyIndex = ed.history.length - 1;
}

function layerUndo() {
  const ed = state.layerEditor;
  if (!ed || ed.historyIndex <= 0) return;
  ed.historyIndex -= 1;
  layerApplySnapshot(ed.history[ed.historyIndex]);
}

function layerRedo() {
  const ed = state.layerEditor;
  if (!ed || ed.historyIndex >= ed.history.length - 1) return;
  ed.historyIndex += 1;
  layerApplySnapshot(ed.history[ed.historyIndex]);
}

function canvasPoint(ev, canvas) {
  const rect = canvas.getBoundingClientRect();
  const sx = canvas.width / Math.max(1, rect.width);
  const sy = canvas.height / Math.max(1, rect.height);
  return {
    x: (ev.clientX - rect.left) * sx,
    y: (ev.clientY - rect.top) * sy,
  };
}

function canvasPointToSourcePoint(editor, px, py) {
  if (!editor || !editor.fgImg) return null;
  const w = Number(editor.fgImg.width || 0);
  const h = Number(editor.fgImg.height || 0);
  if (w <= 0 || h <= 0) return null;

  const dx = px - Number(editor.fgX || 0);
  const dy = py - Number(editor.fgY || 0);
  const rad = (Number(editor.rotateDeg || 0) * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);

  // Inverse rotation.
  const rx = dx * cos + dy * sin;
  const ry = -dx * sin + dy * cos;

  // Inverse scale + flip.
  const sx = Number(editor.scale || 1) * Number(editor.flipX || 1);
  const sy = Number(editor.scale || 1) * Number(editor.flipY || 1);
  if (Math.abs(sx) < 1e-6 || Math.abs(sy) < 1e-6) return null;
  const lx = rx / sx;
  const ly = ry / sy;

  // Local image coordinates (image was drawn at -w/2,-h/2).
  return { x: lx + w / 2, y: ly + h / 2 };
}

function mapCanvasBoxToSourceBBox(editor, box) {
  if (!editor || !box) return null;
  const x1 = Math.min(box.x, box.x + box.w);
  const y1 = Math.min(box.y, box.y + box.h);
  const x2 = Math.max(box.x, box.x + box.w);
  const y2 = Math.max(box.y, box.y + box.h);
  const pts = [
    canvasPointToSourcePoint(editor, x1, y1),
    canvasPointToSourcePoint(editor, x2, y1),
    canvasPointToSourcePoint(editor, x1, y2),
    canvasPointToSourcePoint(editor, x2, y2),
  ].filter(Boolean);
  if (pts.length !== 4) return null;

  const w = Number(editor.fgImg?.width || 0);
  const h = Number(editor.fgImg?.height || 0);
  if (w <= 0 || h <= 0) return null;

  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  let bx1 = Math.floor(Math.max(0, Math.min(...xs)));
  let by1 = Math.floor(Math.max(0, Math.min(...ys)));
  let bx2 = Math.ceil(Math.min(w, Math.max(...xs)));
  let by2 = Math.ceil(Math.min(h, Math.max(...ys)));

  // Ensure valid, non-empty region.
  bx1 = Math.max(0, Math.min(bx1, w - 1));
  by1 = Math.max(0, Math.min(by1, h - 1));
  bx2 = Math.max(1, Math.min(bx2, w));
  by2 = Math.max(1, Math.min(by2, h));
  if (bx2 <= bx1 || by2 <= by1) return null;
  return { x1: bx1, y1: by1, x2: bx2, y2: by2 };
}

function cropImageByBox(sourceImg, box) {
  return new Promise((resolve, reject) => {
    try {
      const w = Math.max(1, Number(sourceImg?.naturalWidth || sourceImg?.width || 0));
      const h = Math.max(1, Number(sourceImg?.naturalHeight || sourceImg?.height || 0));
      const x1 = Math.max(0, Math.min(w - 1, Number(box?.x1 || 0)));
      const y1 = Math.max(0, Math.min(h - 1, Number(box?.y1 || 0)));
      const x2 = Math.max(1, Math.min(w, Number(box?.x2 || 1)));
      const y2 = Math.max(1, Math.min(h, Number(box?.y2 || 1)));
      if (x2 <= x1 || y2 <= y1) {
        reject(new Error("截取区域无效，请重新框选"));
        return;
      }
      const c = document.createElement("canvas");
      c.width = x2 - x1;
      c.height = y2 - y1;
      const cctx = c.getContext("2d");
      if (!cctx) {
        reject(new Error("浏览器不支持画布截取"));
        return;
      }
      cctx.drawImage(sourceImg, x1, y1, c.width, c.height, 0, 0, c.width, c.height);
      const out = new Image();
      out.onload = () => resolve(out);
      out.onerror = () => reject(new Error("截取结果加载失败"));
      out.src = c.toDataURL("image/png");
    } catch (e) {
      reject(e);
    }
  });
}

function cutImageByBox(sourceImg, box) {
  return new Promise((resolve, reject) => {
    try {
      const w = Math.max(1, Number(sourceImg?.naturalWidth || sourceImg?.width || 0));
      const h = Math.max(1, Number(sourceImg?.naturalHeight || sourceImg?.height || 0));
      const x1 = Math.max(0, Math.min(w - 1, Number(box?.x1 || 0)));
      const y1 = Math.max(0, Math.min(h - 1, Number(box?.y1 || 0)));
      const x2 = Math.max(1, Math.min(w, Number(box?.x2 || 1)));
      const y2 = Math.max(1, Math.min(h, Number(box?.y2 || 1)));
      if (x2 <= x1 || y2 <= y1) {
        reject(new Error("截取区域无效，请重新框选"));
        return;
      }

      const cutCanvas = document.createElement("canvas");
      cutCanvas.width = x2 - x1;
      cutCanvas.height = y2 - y1;
      const cutCtx = cutCanvas.getContext("2d");
      if (!cutCtx) {
        reject(new Error("浏览器不支持画布截取"));
        return;
      }
      cutCtx.drawImage(sourceImg, x1, y1, cutCanvas.width, cutCanvas.height, 0, 0, cutCanvas.width, cutCanvas.height);

      const remainCanvas = document.createElement("canvas");
      remainCanvas.width = w;
      remainCanvas.height = h;
      const remainCtx = remainCanvas.getContext("2d");
      if (!remainCtx) {
        reject(new Error("浏览器不支持画布编辑"));
        return;
      }
      remainCtx.drawImage(sourceImg, 0, 0, w, h);
      remainCtx.clearRect(x1, y1, x2 - x1, y2 - y1);

      const cutImg = new Image();
      const remainImg = new Image();
      let loaded = 0;
      const done = () => {
        loaded += 1;
        if (loaded === 2) resolve({ cutImg, remainImg });
      };
      cutImg.onload = done;
      remainImg.onload = done;
      cutImg.onerror = () => reject(new Error("截取结果加载失败"));
      remainImg.onerror = () => reject(new Error("主图更新失败"));
      cutImg.src = cutCanvas.toDataURL("image/png");
      remainImg.src = remainCanvas.toDataURL("image/png");
    } catch (e) {
      reject(e);
    }
  });
}

function bindLayerEditorControls() {
  if (state.layerEditorBound) return;
  state.layerEditorBound = true;

  const canvas = qs("#layer-canvas");
  const txtInput = qs("#txt-content");
  const txtHandle = qs("#txt-resize-handle");
  let dragging = false;
  let dragMoved = false;
  let dragTarget = "fg";
  let textDragging = false;
  let textResizing = false;
  let textDragLast = { x: 0, y: 0 };
  let textResizeStart = { x: 0, y: 0, w: 260, h: 44 };
  let resizing = false;
  let resizeTarget = "";
  let resizeCenter = { x: 0, y: 0 };
  let resizeStartDist = 1;
  let resizeStartScale = 1;
  let last = { x: 0, y: 0 };

  canvas.addEventListener("pointerdown", (ev) => {
    if (!state.layerEditor) return;
    const p = canvasPoint(ev, canvas);
    if (state.layerEditor.cutoutBoxMode) {
      state.layerEditor.cutoutBox = { x: p.x, y: p.y, w: 0, h: 0 };
      state.layerEditor.activeTarget = null;
      dragging = false;
      dragMoved = true;
      layerRender();
      return;
    }
    const fgB = getFgBounds(state.layerEditor);
    const cutB = getCutoutBounds(state.layerEditor);
    const cb = getCutoutBounds(state.layerEditor);
    const ob = getOverlayBounds(state.layerEditor);
    const tb = getEditorTextBounds(state.layerEditor, true);
    const stampIdx = findTextStampAtPoint(state.layerEditor, p.x, p.y);
    if (stampIdx >= 0) {
      dragTarget = "text-stamp";
      state.layerEditor.activeTextStamp = stampIdx;
      state.layerEditor.activeTarget = "text";
      syncToolbarFromSelectedTextStamp(state.layerEditor);
      dragging = true;
      dragMoved = false;
      canvas.classList.add("dragging");
      last = p;
      layerRender();
      return;
    }
    const hasText = !!(qs("#txt-content")?.value || "").trim();
    if ((isCanvasTextInputShown() || (!isOverlayTextInput() && hasText)) && tb) {
      const pad = 16;
      const inTextZone =
        p.x >= (tb.x - pad) &&
        p.x <= (tb.x + tb.w + pad) &&
        p.y >= (tb.y - pad) &&
        p.y <= (tb.y + tb.h + pad);
      if (inTextZone) {
        dragTarget = "text";
        state.layerEditor.activeTextStamp = -1;
        state.layerEditor.activeTarget = "text";
        dragging = true;
        dragMoved = false;
        canvas.classList.add("dragging");
        last = p;
        layerRender();
        return;
      }
    }
    const cCut = hitResizeCorner(p, cutB);
    const cOv = hitResizeCorner(p, ob);
    const cFg = hitResizeCorner(p, fgB);
    if (cCut && state.layerEditor.cutoutLayer) {
      resizing = true;
      resizeTarget = "cutout";
      resizeCenter = { x: state.layerEditor.cutoutLayer.x, y: state.layerEditor.cutoutLayer.y };
      resizeStartDist = Math.max(1, Math.hypot(p.x - resizeCenter.x, p.y - resizeCenter.y));
      resizeStartScale = Number(state.layerEditor.cutoutLayer.scale || 1);
      state.layerEditor.activeTarget = "cutout";
      dragging = false;
      dragMoved = false;
      canvas.classList.add("dragging");
      return;
    }
    if (cOv && state.layerEditor.overlay) {
      resizing = true;
      resizeTarget = "overlay";
      resizeCenter = { x: state.layerEditor.overlay.x, y: state.layerEditor.overlay.y };
      resizeStartDist = Math.max(1, Math.hypot(p.x - resizeCenter.x, p.y - resizeCenter.y));
      resizeStartScale = Number(state.layerEditor.overlay.scale || 1);
      state.layerEditor.activeTarget = "overlay";
      dragging = false;
      dragMoved = false;
      canvas.classList.add("dragging");
      return;
    }
    if (cFg) {
      resizing = true;
      resizeTarget = "fg";
      resizeCenter = { x: state.layerEditor.fgX, y: state.layerEditor.fgY };
      resizeStartDist = Math.max(1, Math.hypot(p.x - resizeCenter.x, p.y - resizeCenter.y));
      resizeStartScale = Number(state.layerEditor.scale || 1);
      state.layerEditor.activeTarget = "fg";
      dragging = false;
      dragMoved = false;
      canvas.classList.add("dragging");
      return;
    }
    if (cb && p.x >= cb.x && p.x <= cb.x + cb.w && p.y >= cb.y && p.y <= cb.y + cb.h) {
      dragTarget = "cutout";
    } else if (ob && p.x >= ob.x && p.x <= ob.x + ob.w && p.y >= ob.y && p.y <= ob.y + ob.h) {
      dragTarget = "overlay";
    } else if (tb && p.x >= tb.x && p.x <= tb.x + tb.w && p.y >= tb.y && p.y <= tb.y + tb.h) {
      dragTarget = "text";
    } else {
      dragTarget = "fg";
    }
    if (dragTarget !== "text" && dragTarget !== "text-stamp") {
      state.layerEditor.activeTextStamp = -1;
    }
    state.layerEditor.activeTarget = dragTarget;
    dragging = true;
    dragMoved = false;
    canvas.classList.add("dragging");
    last = p;
    layerRender();
  });
  if (txtInput && isOverlayTextInput()) {
    txtInput.addEventListener("pointerdown", (ev) => {
      if (!state.layerEditor) return;
      state.layerEditor.activeTarget = "text";
      setCanvasTextInputVisible(true, false);
      const r = txtInput.getBoundingClientRect();
      const nearRight = (r.right - ev.clientX) <= 20;
      const nearBottom = (r.bottom - ev.clientY) <= 20;
      const nearEdge =
        ev.clientX - r.left <= 14 ||
        r.right - ev.clientX <= 14 ||
        ev.clientY - r.top <= 14 ||
        r.bottom - ev.clientY <= 14;
      const isFocused = document.activeElement === txtInput;
      if (nearRight && nearBottom) {
        textResizing = true;
        textResizeStart = {
          x: ev.clientX,
          y: ev.clientY,
          w: Number(state.layerEditor.textBoxW || 260),
          h: Number(state.layerEditor.textBoxH || 44),
        };
        txtInput.setPointerCapture?.(ev.pointerId);
        ev.preventDefault();
        return;
      }
      // Easier selection/move: if input is not focused, drag from anywhere.
      // If focused (editing), still allow drag via edge or with modifier key.
      if (!isFocused || nearEdge || ev.altKey || ev.metaKey || ev.ctrlKey) {
        textDragging = true;
        textDragLast = { x: ev.clientX, y: ev.clientY };
        txtInput.setPointerCapture?.(ev.pointerId);
        ev.preventDefault();
      }
    });
    txtInput.addEventListener("pointermove", (ev) => {
      const ed = state.layerEditor;
      if (!ed) return;
      const rect = ed.canvas.getBoundingClientRect();
      const sx = ed.canvas.width / Math.max(1, rect.width);
      const sy = ed.canvas.height / Math.max(1, rect.height);
      if (textDragging) {
        ed.textX += (ev.clientX - textDragLast.x) * sx;
        ed.textY += (ev.clientY - textDragLast.y) * sy;
        textDragLast = { x: ev.clientX, y: ev.clientY };
        layerRender();
        ev.preventDefault();
        return;
      }
      if (textResizing) {
        const dx = ev.clientX - textResizeStart.x;
        const dy = ev.clientY - textResizeStart.y;
        ed.textBoxW = Math.min(1200, Math.max(80, Math.round(textResizeStart.w + dx)));
        ed.textBoxH = Math.min(500, Math.max(30, Math.round(textResizeStart.h + dy)));
        layerRender();
        ev.preventDefault();
      }
    });
    txtInput.addEventListener("pointerup", () => {
      if (textDragging || textResizing) layerPushHistory();
      textDragging = false;
      textResizing = false;
    });
    txtInput.addEventListener("click", () => {
      if (!state.layerEditor) return;
      state.layerEditor.activeTarget = "text";
      setCanvasTextInputVisible(true, false);
      layerRender();
    });
    txtInput.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      setCanvasTextInputVisible(true, true);
    });
    txtInput.addEventListener("wheel", (ev) => {
      if (!state.layerEditor) return;
      const cur = Number(qs("#txt-size")?.value || 36);
      const next = ev.deltaY < 0 ? cur + 1 : cur - 1;
      qs("#txt-size").value = String(Math.min(180, Math.max(12, next)));
      layerRender();
      layerPushHistory();
      ev.preventDefault();
    }, { passive: false });
  }
  if (txtHandle && isOverlayTextInput()) {
    txtHandle.addEventListener("pointerdown", (ev) => {
      if (!state.layerEditor) return;
      state.layerEditor.activeTarget = "text";
      setCanvasTextInputVisible(true, false);
      textResizing = true;
      textResizeStart = {
        x: ev.clientX,
        y: ev.clientY,
        w: Number(state.layerEditor.textBoxW || 260),
        h: Number(state.layerEditor.textBoxH || 44),
      };
      txtHandle.setPointerCapture?.(ev.pointerId);
      ev.preventDefault();
      ev.stopPropagation();
    });
  }
  window.addEventListener("pointerup", () => {
    if (textDragging || textResizing) {
      if (textDragging || textResizing) layerPushHistory();
      textDragging = false;
      textResizing = false;
    }
    if (state.layerEditor && state.layerEditor.cutoutBoxMode && state.layerEditor.cutoutBox) {
      const b = state.layerEditor.cutoutBox;
      const x1 = Math.round(Math.min(b.x, b.x + b.w));
      const y1 = Math.round(Math.min(b.y, b.y + b.h));
      const x2 = Math.round(Math.max(b.x, b.x + b.w));
      const y2 = Math.round(Math.max(b.y, b.y + b.h));
      state.layerEditor.cutoutBox = null;
      state.layerEditor.cutoutBoxMode = false;
      const boxBtn = qs("#layer-cutout-box-mode");
      if (boxBtn) boxBtn.textContent = "框选截取区域（拖拽鼠标）";
      layerRender();
      if ((x2 - x1) >= 8 && (y2 - y1) >= 8) {
        (async () => {
          try {
            const srcBox = mapCanvasBoxToSourceBBox(state.layerEditor, b);
            if (!srcBox) throw new Error("框选区域无效，请重新框选主体");
            const sourceImg = state.layerEditor.fgOriginalImg || state.layerEditor.fgImg;
            const out = await cutImageByBox(sourceImg, srcBox);
            state.layerEditor.fgImg = out.remainImg;
            state.layerEditor.fgOriginalImg = out.remainImg;
            state.layerEditor.cutoutLayer = {
              img: out.cutImg,
              bbox: srcBox,
              fromBox: true,
              x: state.layerEditor.canvas.width / 2,
              y: state.layerEditor.canvas.height / 2,
              scale: 1,
              rotateDeg: 0,
              flipX: 1,
              flipY: 1,
              opacity: 1,
            };
            state.layerEditor.activeTarget = "cutout";
            if (srcBox) {
              state.layerEditor.cutoutLayer.x = ((Number(srcBox.x1) + Number(srcBox.x2)) / 2);
              state.layerEditor.cutoutLayer.y = ((Number(srcBox.y1) + Number(srcBox.y2)) / 2);
            }
            layerRender();
            layerPushHistory();
          } catch (e) {
            alert(e.message);
          }
        })();
      }
      dragging = false;
      canvas.classList.remove("dragging");
      return;
    }
    if (resizing) {
      resizing = false;
      resizeTarget = "";
      layerPushHistory();
      canvas.classList.remove("dragging");
      return;
    }
    if (dragMoved) layerPushHistory();
    dragging = false;
    canvas.classList.remove("dragging");
  });
  window.addEventListener("pointermove", (ev) => {
    if (!state.layerEditor) return;
    if (state.layerEditor.cutoutBoxMode && state.layerEditor.cutoutBox) {
      const p = canvasPoint(ev, canvas);
      state.layerEditor.cutoutBox.w = p.x - state.layerEditor.cutoutBox.x;
      state.layerEditor.cutoutBox.h = p.y - state.layerEditor.cutoutBox.y;
      layerRender();
      return;
    }
    if (resizing) {
      const p = canvasPoint(ev, canvas);
      const nowDist = Math.max(1, Math.hypot(p.x - resizeCenter.x, p.y - resizeCenter.y));
      const ratio = nowDist / Math.max(1e-6, resizeStartDist);
      const s = Math.min(6, Math.max(0.05, resizeStartScale * ratio));
      if (resizeTarget === "cutout" && state.layerEditor.cutoutLayer) {
        state.layerEditor.cutoutLayer.scale = s;
      } else if (resizeTarget === "overlay" && state.layerEditor.overlay) {
        state.layerEditor.overlay.scale = s;
      } else if (resizeTarget === "fg") {
        state.layerEditor.scale = Math.min(4, Math.max(0.1, s));
        qs("#layer-scale").value = String(state.layerEditor.scale);
      }
      layerRender();
      return;
    }
    if (textResizing) {
      const ed = state.layerEditor;
      const dx = ev.clientX - textResizeStart.x;
      const dy = ev.clientY - textResizeStart.y;
      ed.textBoxW = Math.min(1200, Math.max(80, Math.round(textResizeStart.w + dx)));
      ed.textBoxH = Math.min(500, Math.max(30, Math.round(textResizeStart.h + dy)));
      layerRender();
      return;
    }
    if (!dragging) return;
    const p = canvasPoint(ev, canvas);
    if (dragTarget === "text") {
      state.layerEditor.textX += p.x - last.x;
      state.layerEditor.textY += p.y - last.y;
    } else if (dragTarget === "text-stamp") {
      const idx = Number(state.layerEditor.activeTextStamp ?? -1);
      if (idx >= 0 && idx < (state.layerEditor.textStamps || []).length) {
        const s = state.layerEditor.textStamps[idx];
        s.x = Number(s.x || 0) + (p.x - last.x);
        s.y = Number(s.y || 0) + (p.y - last.y);
        state.layerEditor.textX = s.x;
        state.layerEditor.textY = s.y;
      }
    } else if (dragTarget === "overlay" && state.layerEditor.overlay) {
      state.layerEditor.overlay.x += p.x - last.x;
      state.layerEditor.overlay.y += p.y - last.y;
    } else if (dragTarget === "cutout" && state.layerEditor.cutoutLayer) {
      state.layerEditor.cutoutLayer.x += p.x - last.x;
      state.layerEditor.cutoutLayer.y += p.y - last.y;
    } else {
      state.layerEditor.fgX += p.x - last.x;
      state.layerEditor.fgY += p.y - last.y;
    }
    dragMoved = true;
    last = p;
    layerRender();
  });

  canvas.addEventListener("dblclick", (ev) => {
    if (!state.layerEditor) return;
    const p = canvasPoint(ev, canvas);
    state.layerEditor.textX = p.x;
    state.layerEditor.textY = p.y;
    layerRender();
    layerPushHistory();
  });
  canvas.addEventListener("wheel", (ev) => {
    if (!state.layerEditor) return;
    const ed = state.layerEditor;
    const factor = ev.deltaY < 0 ? 1.06 : 0.94;
    if (ed.activeTarget === "overlay" && ed.overlay) {
      ed.overlay.scale = Math.min(6, Math.max(0.05, Number(ed.overlay.scale || 1) * factor));
    } else if (ed.activeTarget === "cutout" && ed.cutoutLayer) {
      ed.cutoutLayer.scale = Math.min(6, Math.max(0.05, Number(ed.cutoutLayer.scale || 1) * factor));
    } else {
      ed.scale = Math.min(4, Math.max(0.1, Number(ed.scale || 1) * factor));
      qs("#layer-scale").value = String(ed.scale);
    }
    layerRender();
    layerPushHistory();
    ev.preventDefault();
  }, { passive: false });

  qs("#layer-scale").addEventListener("input", () => {
    if (!state.layerEditor) return;
    state.layerEditor.scale = Number(qs("#layer-scale").value || 1);
    layerRender();
  });
  qs("#layer-scale").addEventListener("change", layerPushHistory);
  qs("#layer-rotate").addEventListener("input", () => {
    if (!state.layerEditor) return;
    state.layerEditor.rotateDeg = Number(qs("#layer-rotate").value || 0);
    layerRender();
  });
  qs("#layer-rotate").addEventListener("change", layerPushHistory);
  qs("#layer-opacity").addEventListener("input", () => {
    if (!state.layerEditor) return;
    state.layerEditor.opacity = Number(qs("#layer-opacity").value || 1);
    layerRender();
  });
  qs("#layer-opacity").addEventListener("change", layerPushHistory);
  qs("#layer-brightness").addEventListener("input", () => {
    if (!state.layerEditor) return;
    state.layerEditor.brightness = Number(qs("#layer-brightness").value || 100);
    layerRender();
  });
  qs("#layer-brightness").addEventListener("change", layerPushHistory);
  qs("#layer-contrast").addEventListener("input", () => {
    if (!state.layerEditor) return;
    state.layerEditor.contrast = Number(qs("#layer-contrast").value || 100);
    layerRender();
  });
  qs("#layer-contrast").addEventListener("change", layerPushHistory);
  qs("#layer-saturation").addEventListener("input", () => {
    if (!state.layerEditor) return;
    state.layerEditor.saturation = Number(qs("#layer-saturation").value || 100);
    layerRender();
  });
  qs("#layer-saturation").addEventListener("change", layerPushHistory);
  qs("#txt-content").addEventListener("input", layerRender);
  qs("#txt-content").addEventListener("change", layerPushHistory);
  qs("#txt-content").addEventListener("input", () => {
    const ed = state.layerEditor;
    if (!ed) return;
    const idx = Number(ed.activeTextStamp ?? -1);
    if (idx >= 0 && idx < (ed.textStamps || []).length) {
      ed.textStamps[idx].content = qs("#txt-content").value || "";
    }
    layerRender();
  });
  qs("#txt-content").addEventListener("focus", () => {
    if (!state.layerEditor) return;
    state.layerEditor.activeTarget = "text";
    setCanvasTextInputVisible(true, false);
    layerRender();
  });
  qs("#txt-content").addEventListener("click", () => {
    if (!state.layerEditor) return;
    state.layerEditor.activeTarget = "text";
    setCanvasTextInputVisible(true, false);
    layerRender();
  });
  qs("#txt-size").addEventListener("input", layerRender);
  qs("#txt-size").addEventListener("input", () => {
    const ed = state.layerEditor;
    if (!ed) return;
    const idx = Number(ed.activeTextStamp ?? -1);
    if (idx >= 0 && idx < (ed.textStamps || []).length) {
      ed.textStamps[idx].size = Number(qs("#txt-size").value || 36);
    }
    layerRender();
  });
  qs("#txt-size").addEventListener("change", layerPushHistory);
  qs("#txt-nowrap").addEventListener("input", layerRender);
  qs("#txt-nowrap").addEventListener("input", () => {
    const ed = state.layerEditor;
    if (!ed) return;
    const idx = Number(ed.activeTextStamp ?? -1);
    if (idx >= 0 && idx < (ed.textStamps || []).length) {
      ed.textStamps[idx].noWrap = !!qs("#txt-nowrap").checked;
    } else if (Array.isArray(ed.textStamps) && ed.textStamps.length) {
      const v = !!qs("#txt-nowrap").checked;
      ed.textStamps.forEach((s) => { s.noWrap = v; });
    }
    layerRender();
  });
  qs("#txt-nowrap").addEventListener("change", layerPushHistory);
  qs("#txt-font").addEventListener("input", layerRender);
  qs("#txt-font").addEventListener("input", () => {
    const ed = state.layerEditor;
    if (!ed) return;
    const idx = Number(ed.activeTextStamp ?? -1);
    if (idx >= 0 && idx < (ed.textStamps || []).length) {
      ed.textStamps[idx].font = String(qs("#txt-font").value || "noto");
    }
    layerRender();
  });
  qs("#txt-font").addEventListener("change", layerPushHistory);
  qs("#txt-color").addEventListener("input", layerRender);
  qs("#txt-color").addEventListener("input", () => {
    const ed = state.layerEditor;
    if (!ed) return;
    const idx = Number(ed.activeTextStamp ?? -1);
    if (idx >= 0 && idx < (ed.textStamps || []).length) {
      ed.textStamps[idx].color = String(qs("#txt-color").value || "#22344a");
    }
    layerRender();
  });
  qs("#txt-color").addEventListener("change", layerPushHistory);
  window.addEventListener("resize", () => {
    syncCanvasTextInputPosition();
  });

  qs("#layer-clear-text").onclick = () => {
    const ed = state.layerEditor;
    if (ed) {
      const idx = Number(ed.activeTextStamp ?? -1);
      if (idx >= 0 && idx < (ed.textStamps || []).length) {
        ed.textStamps.splice(idx, 1);
        ed.activeTextStamp = -1;
      } else {
        qs("#txt-content").value = "";
      }
    } else {
      qs("#txt-content").value = "";
    }
    layerRender();
    layerPushHistory();
  };
  qs("#layer-insert-text").onclick = () => {
    if (!state.layerEditor) return alert("请先选择素材");
    const ed = state.layerEditor;
    const txtEl = qs("#txt-content");
    const txt = (txtEl?.value || "").trim();
    if (txt) {
      ed.textStamps = ed.textStamps || [];
      ed.textStamps.push({
        content: txt,
        size: Number(qs("#txt-size")?.value || 36),
        noWrap: !!qs("#txt-nowrap")?.checked,
        font: String(qs("#txt-font")?.value || "noto"),
        color: String(qs("#txt-color")?.value || "#22344a"),
        x: Number(ed.textX || ed.canvas.width / 2),
        y: Number(ed.textY || Math.round(ed.canvas.height * 0.08)),
        boxW: Number(ed.textBoxW || Math.max(180, Math.round(ed.canvas.width * 0.3))),
        boxH: Number(ed.textBoxH || 44),
      });
      ed.activeTextStamp = -1;
      txtEl.value = "";
      ed.textX = Math.min(ed.canvas.width - 20, Number(ed.textX || 0) + 22);
      ed.textY = Math.min(ed.canvas.height - 20, Number(ed.textY || 0) + 22);
      ed.activeTarget = "text";
      setCanvasTextInputVisible(true, true);
      layerPushHistory();
      layerRender();
      return;
    }
    ed.activeTarget = "text";
    setCanvasTextInputVisible(true, true);
    layerRender();
    layerPushHistory();
  };
  const selectTextLayer = () => {
    const ed = state.layerEditor;
    const sel = qs("#text-layer-list");
    if (!ed || !sel || sel.disabled) return;
    const idx = Number(sel.value);
    if (!Number.isFinite(idx) || idx < 0 || idx >= (ed.textStamps || []).length) return;
    ed.activeTextStamp = idx;
    ed.activeTarget = "text";
    syncToolbarFromSelectedTextStamp(ed);
    layerRender();
  };
  qs("#text-layer-list").addEventListener("change", selectTextLayer);
  qs("#text-layer-select").onclick = () => {
    selectTextLayer();
  };
  qs("#text-layer-delete").onclick = () => {
    const ed = state.layerEditor;
    const sel = qs("#text-layer-list");
    if (!ed || !sel || sel.disabled) return;
    const idx = Number(sel.value);
    if (!Number.isFinite(idx) || idx < 0 || idx >= (ed.textStamps || []).length) return;
    ed.textStamps.splice(idx, 1);
    if (ed.activeTextStamp === idx) ed.activeTextStamp = -1;
    else if (ed.activeTextStamp > idx) ed.activeTextStamp -= 1;
    layerRender();
    layerPushHistory();
  };
  qs("#layer-add-overlay").onclick = async () => {
    const ed = state.layerEditor;
    if (!ed) return alert("请先选择素材");
    const aid = (qs("#layer-overlay-asset")?.value || "").trim();
    if (!aid) return alert("请选择要叠加的素材");
    const item = state.assets.find(x => x.asset_id === aid);
    if (!item) return alert("素材不存在");
    try {
      const img = await loadImage(item.url);
      ed.overlayCache[aid] = img;
      const maxW = ed.canvas.width * 0.42;
      const s = Math.min(1.2, Math.max(0.08, maxW / Math.max(1, img.width)));
      ed.overlay = {
        assetId: aid,
        img,
        x: ed.canvas.width / 2,
        y: ed.canvas.height / 2,
        scale: s,
        opacity: 1,
      };
      ed.activeTarget = "overlay";
      layerRender();
      layerPushHistory();
    } catch (e) {
      alert(e.message);
    }
  };
  qs("#layer-remove-overlay").onclick = () => {
    const ed = state.layerEditor;
    if (!ed || !ed.overlay) return;
    ed.overlay = null;
    if (ed.activeTarget === "overlay") ed.activeTarget = null;
    layerRender();
    layerPushHistory();
  };
  qs("#layer-cutout").onclick = async () => {
    const ed = state.layerEditor;
    if (!ed) return alert("请先选择素材");
    const assetId = (ed.baseAssetId || qs("#layer-asset")?.value || "").trim();
    if (!assetId) return alert("请先选择素材");
    const btn = qs("#layer-cutout");
    if (btn) btn.disabled = true;
    try {
      const sourceImageBase64 = imageToDataUrl(ed.fgOriginalImg || ed.fgImg);
      const d = await api("/api/layer/cutout-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_id: assetId, image_base64: sourceImageBase64 }),
      });
      const fg = await loadImage(d.image_data_url);
      ed.cutoutLayer = {
        img: fg,
        bbox: d.bbox,
        fromBox: false,
        x: ed.canvas.width / 2,
        y: ed.canvas.height / 2,
        scale: 1,
        rotateDeg: 0,
        flipX: 1,
        flipY: 1,
        opacity: 1,
      };
      ed.activeTarget = "cutout";
      if (d.bbox) {
        ed.cutoutLayer.x = ((Number(d.bbox.x1) + Number(d.bbox.x2)) / 2);
        ed.cutoutLayer.y = ((Number(d.bbox.y1) + Number(d.bbox.y2)) / 2);
      }
      layerRender();
      layerPushHistory();
    } catch (e) {
      alert(e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  };
  qs("#layer-cutout-save").onclick = async () => {
    const ed = state.layerEditor;
    if (!ed || !ed.cutoutLayer || !ed.cutoutLayer.img) return alert("当前没有抠图主体可保存");
    try {
      const selectedId = (qs("#layer-asset")?.value || "").trim();
      const selectedItem = state.assets.find((x) => x.asset_id === selectedId);
      const baseName = selectedItem ? getAssetName(selectedItem) : "image.png";
      const ext = (baseName.includes(".") ? `.${baseName.split(".").pop()}` : ".png");
      const stem = baseName ? baseName.replace(/\.[^.]+$/, "") : "image";
      const d = await api("/api/assets/upload-base64", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_base64: imageToDataUrl(ed.cutoutLayer.img),
          original_name: `${stem}-抠图主体${ext}`,
        }),
      });
      qs("#layer-result").innerHTML += card(d.item);
      const keepLayerAssetId = (ed.baseAssetId || qs("#layer-asset")?.value || "").trim();
      await refreshAssets();
      if (keepLayerAssetId) {
        const layerSel = qs("#layer-asset");
        if (layerSel && Array.from(layerSel.options).some(opt => opt.value === keepLayerAssetId)) {
          layerSel.value = keepLayerAssetId;
          renderLayerSelectedAsset();
        }
      }
      alert("已保存到素材库");
    } catch (e) {
      alert(e.message);
    }
  };
  qs("#layer-cutout-box-mode").onclick = () => {
    const ed = state.layerEditor;
    if (!ed) return alert("请先选择素材");
    ed.cutoutBoxMode = !ed.cutoutBoxMode;
    ed.cutoutBox = null;
    const btn = qs("#layer-cutout-box-mode");
    if (btn) {
      btn.textContent = ed.cutoutBoxMode ? "框选中...拖拽鼠标后截取区域" : "框选截取区域（拖拽鼠标）";
    }
    layerRender();
  };
  qs("#layer-flip-x").onclick = () => {
    if (!state.layerEditor) return;
    state.layerEditor.flipX *= -1;
    layerRender();
    layerPushHistory();
  };
  qs("#layer-flip-y").onclick = () => {
    if (!state.layerEditor) return;
    state.layerEditor.flipY *= -1;
    layerRender();
    layerPushHistory();
  };
  qs("#layer-undo").onclick = () => {
    layerUndo();
  };
  qs("#layer-redo").onclick = () => {
    layerRedo();
  };
  const resetEditorForNextRound = (toastText = "请重新选择素材，开始下一轮编辑") => {
    const canvasEl = qs("#layer-canvas");
    const ctx = canvasEl?.getContext?.("2d");
    if (ctx && canvasEl) {
      ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvasEl.width, canvasEl.height);
    }
    state.layerEditor = null;
    const layerSel = qs("#layer-asset");
    if (layerSel) layerSel.value = "";
    const boxBtn = qs("#layer-cutout-box-mode");
    if (boxBtn) boxBtn.textContent = "框选截取区域（拖拽鼠标）";
    const txt = qs("#txt-content");
    if (txt) txt.value = "";
    renderLayerSelectedAsset();
    refreshTextLayerList(null);
    showToast(toastText);
  };
  qs("#layer-next-round").onclick = () => {
    resetEditorForNextRound("请重新选择素材，开始下一轮编辑");
  };

  window.addEventListener("keydown", (ev) => {
    if (!state.layerEditor) return;
    const mod = !!(ev.ctrlKey || ev.metaKey);
    const key = String(ev.key || "").toLowerCase();
    if (mod && key === "z") {
      if (ev.shiftKey) layerRedo();
      else layerUndo();
      ev.preventDefault();
      return;
    }
    if (mod && key === "y") {
      layerRedo();
      ev.preventDefault();
      return;
    }

    const target = state.layerEditor.activeTarget;
    const canDeleteCutout = target === "cutout" && !!state.layerEditor.cutoutLayer;
    if (target !== "text" && target !== "overlay" && !canDeleteCutout) return;
    const tag = (ev.target && ev.target.tagName ? ev.target.tagName.toLowerCase() : "");
    const txtEl = qs("#txt-content");
    if (target === "text" && txtEl && document.activeElement === txtEl && !mod) return;
    if ((tag === "input" || tag === "textarea" || tag === "select") && target !== "text") return;
    if (ev.key === "Delete" || ev.key === "Backspace") {
      if (target === "overlay") {
        if (!state.layerEditor.overlay) return;
        state.layerEditor.overlay = null;
      } else if (target === "cutout") {
        if (!state.layerEditor.cutoutLayer) return;
        state.layerEditor.cutoutLayer = null;
        state.layerEditor.activeTarget = "fg";
      } else if (target === "text") {
        const idx = Number(state.layerEditor.activeTextStamp ?? -1);
        if (idx >= 0 && idx < (state.layerEditor.textStamps || []).length) {
          state.layerEditor.textStamps.splice(idx, 1);
          state.layerEditor.activeTextStamp = -1;
          qs("#txt-content").value = "";
        } else {
          const txt = qs("#txt-content");
          if (!txt) return;
          if ((txt.value || "").trim()) {
            txt.value = "";
          } else if (Array.isArray(state.layerEditor.textStamps) && state.layerEditor.textStamps.length) {
            state.layerEditor.textStamps.pop();
          } else {
            return;
          }
        }
      } else {
        const txt = qs("#txt-content");
        if (!txt || !txt.value.trim()) return;
        txt.value = "";
      }
      layerRender();
      layerPushHistory();
      ev.preventDefault();
    }
  });

  qs("#layer-reset").onclick = () => {
    const ed = state.layerEditor;
    if (!ed) return;
    ed.fgX = ed.canvas.width / 2;
    ed.fgY = ed.canvas.height / 2;
    ed.scale = 1;
    ed.rotateDeg = 0;
    ed.flipX = 1;
    ed.flipY = 1;
    ed.opacity = 1;
    ed.brightness = 100;
    ed.contrast = 100;
    ed.saturation = 100;
    ed.textX = ed.canvas.width / 2;
    ed.textY = Math.round(ed.canvas.height * 0.08);
    ed.textBoxW = Math.max(180, Math.round(ed.canvas.width * 0.3));
    ed.textBoxH = 44;
    ed.textStamps = [];
    qs("#layer-scale").value = "1";
    qs("#layer-rotate").value = "0";
    qs("#layer-opacity").value = "1";
    qs("#layer-brightness").value = "100";
    qs("#layer-contrast").value = "100";
    qs("#layer-saturation").value = "100";
    layerRender();
    layerPushHistory();
  };

  qs("#layer-download").onclick = () => {
    const ed = state.layerEditor;
    if (!ed) return alert("请先选择素材");
    const a = document.createElement("a");
    a.href = exportLayerCanvasDataUrl(ed);
    a.download = `layer_edit_${Date.now()}.png`;
    a.click();
    showToast("下载已开始");
  };
}

async function setupLayerEditorFromImage(img, assetId = "") {
  const canvas = qs("#layer-canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("当前浏览器不支持画布编辑");
  const source = img;
  canvas.width = source.naturalWidth || source.width;
  canvas.height = source.naturalHeight || source.height;

  state.layerEditor = {
    canvas,
    ctx,
    bgImg: source,
    fgImg: source,
    fgOriginalImg: source,
    fgX: canvas.width / 2,
    fgY: canvas.height / 2,
    scale: Number(qs("#layer-scale")?.value || 1),
    rotateDeg: Number(qs("#layer-rotate")?.value || 0),
    flipX: 1,
    flipY: 1,
    opacity: Number(qs("#layer-opacity")?.value || 1),
    brightness: Number(qs("#layer-brightness")?.value || 100),
    contrast: Number(qs("#layer-contrast")?.value || 100),
    saturation: Number(qs("#layer-saturation")?.value || 100),
    textX: canvas.width / 2,
    textY: Math.round(canvas.height * 0.08),
    textBoxW: Math.max(180, Math.round(canvas.width * 0.3)),
    textBoxH: 44,
    textStamps: [],
    activeTextStamp: -1,
    history: [],
    historyIndex: -1,
    singleMode: true,
    activeTarget: null,
    cutoutLayer: null,
    cutoutBoxMode: false,
    cutoutBox: null,
    overlay: null,
    overlayCache: {},
    baseAssetId: String(assetId || "").trim(),
  };

  bindLayerEditorControls();
  setCanvasTextInputVisible(false, false);
  layerRender();
  layerPushHistory();
}

function setupLayer() {
  qs("#layer-asset").addEventListener("change", async () => {
    renderLayerSelectedAsset();
    await previewSelectedAssetOnLayerCanvas();
  });

  qs("#layer-compose").onclick = async () => {
    if (!state.layerEditor) return alert("请先选择素材");
    try {
      const selectedId = (qs("#layer-asset")?.value || "").trim();
      const selectedItem = state.assets.find((x) => x.asset_id === selectedId);
      const baseName = selectedItem ? getAssetName(selectedItem) : "";
      const ext = (baseName.includes(".") ? `.${baseName.split(".").pop()}` : ".png");
      const stem = baseName ? baseName.replace(/\.[^.]+$/, "") : "image";
      const d = await api("/api/assets/upload-base64", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_base64: exportLayerCanvasDataUrl(state.layerEditor),
          original_name: `${stem}-编辑${ext}`,
        }),
      });
      qs("#layer-result").innerHTML += card(d.item);
      await refreshAssets();
      showToast("保存编辑结果成功");
    } catch (e) {
      alert(e.message);
    }
  };
}

function setupGallery() {
  const visibleAssets = () => getVisibleGalleryAssets();
  const selectedIds = () => {
    const fromDom = qsa("#gallery-grid .gallery-check:checked")
      .map((el) => el.dataset.id || "")
      .filter(Boolean);
    const merged = new Set([...Array.from(state.gallerySelected), ...fromDom]);
    return Array.from(merged);
  };
  const beginInlineRename = (el) => {
    if (!el || el.isContentEditable) return;
    const oldName = (el.textContent || "").trim();
    el.dataset.prevName = oldName;
    el.contentEditable = "true";
    el.classList.add("editing");
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(range);
    }
  };
  const finishInlineRename = async (el, cancel = false) => {
    if (!el || !el.classList.contains("gallery-name")) return;
    const id = el.dataset.id || "";
    const oldName = (el.dataset.prevName || "").trim();
    const newName = (el.textContent || "").trim();
    el.contentEditable = "false";
    el.classList.remove("editing");
    delete el.dataset.prevName;
    if (cancel || !newName) {
      el.textContent = oldName;
      return;
    }
    if (newName === oldName) return;
    try {
      await api("/api/assets/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_id: id, new_name: newName }),
      });
      await refreshAssets();
    } catch (e) {
      el.textContent = oldName;
      alert(e.message);
    }
  };

  qs("#gallery-refresh").onclick = refreshAssets;
  qs("#gallery-folder-filter").onchange = () => {
    state.galleryFolderFilter = qs("#gallery-folder-filter").value || "";
    state.gallerySelected.clear();
    renderGallery();
  };
  qs("#gallery-tag-filter").onchange = () => {
    state.galleryTagFilter = qs("#gallery-tag-filter").value || "";
    state.gallerySelected.clear();
    renderGallery();
  };
  qs("#gallery-upload").onclick = async () => {
    try {
      const files = Array.from(qs("#gallery-upload-files").files || []);
      if (!files.length) return alert("请先选择图片文件");
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      const res = await fetch("/api/assets/upload-batch", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "上传失败");
      qs("#gallery-upload-files").value = "";
      await refreshAssets();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("#gallery-create-folder").onclick = async () => {
    try {
      const name = (qs("#gallery-new-folder").value || "").trim();
      if (!name) return alert("请输入文件夹名称");
      const d = await api("/api/folders/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      state.folders = d.folders || state.folders;
      qs("#gallery-new-folder").value = "";
      refreshFolderSelectors();
      alert(`已创建文件夹：${name}`);
    } catch (e) {
      alert(e.message);
    }
  };

  qs("#gallery-move-folder").onchange = async () => {
    try {
      const ids = selectedIds();
      if (!ids.length) return;
      const folder = (qs("#gallery-move-folder").value || "").trim();
      if (!folder) return;
      await api("/api/assets/move-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: ids, folder }),
      });
      await refreshAssets();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("#gallery-apply-tags").onclick = async () => {
    try {
      const ids = selectedIds();
      if (!ids.length) return alert("请先勾选要打标签的素材");
      const tagsRaw = (qs("#gallery-tags-input").value || "").trim();
      if (!tagsRaw) return alert("请输入标签（逗号分隔）");
      const mode = qs("#gallery-tags-mode").value || "add";
      await api("/api/assets/set-tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: ids, tags: tagsRaw, mode }),
      });
      await refreshAssets();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("#gallery-select-all").onclick = () => {
    const items = visibleAssets();
    if (!items.length) return;
    const allSelected = items.every(x => state.gallerySelected.has(x.asset_id));
    if (allSelected) {
      items.forEach(x => state.gallerySelected.delete(x.asset_id));
    } else {
      items.forEach(x => state.gallerySelected.add(x.asset_id));
    }
    renderGallery();
  };

  qs("#gallery-delete-selected").onclick = async () => {
    try {
      const ids = selectedIds();
      if (!ids.length) return alert("请先勾选要删除的素材");
      if (!window.confirm(`确认删除选中的 ${ids.length} 张素材吗？此操作不可恢复。`)) return;
      const d = await api("/api/assets/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: ids }),
      });
      alert(`已删除 ${d.total_deleted} 个素材`);
      state.gallerySelected.clear();
      await refreshAssets();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("#gallery-grid").addEventListener("change", (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLInputElement)) return;
    if (!t.classList.contains("gallery-check")) return;
    const id = t.dataset.id;
    if (!id) return;
    if (t.checked) state.gallerySelected.add(id);
    else state.gallerySelected.delete(id);
    updateGalleryStatus();
  });

  qs("#gallery-grid").onclick = async (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;
    const check = t.closest(".gallery-check");
    if (check instanceof HTMLInputElement) {
      const id = check.dataset.id;
      if (!id) return;
      if (check.checked) state.gallerySelected.add(id);
      else state.gallerySelected.delete(id);
      updateGalleryStatus();
      return;
    }
    const delBtn = t.closest(".gallery-delete-one");
    if (delBtn instanceof HTMLElement) {
      const id = delBtn.dataset.id;
      if (!id) return;
      if (!window.confirm("确认删除这张素材吗？此操作不可恢复。")) return;
      try {
        await api("/api/assets/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_ids: [id] }),
        });
        state.gallerySelected.delete(id);
        await refreshAssets();
      } catch (e) {
        alert(e.message);
      }
    }
  };

  qs("#gallery-grid").addEventListener("dblclick", (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;
    const nameEl = t.closest(".gallery-name");
    if (!(nameEl instanceof HTMLElement)) return;
    beginInlineRename(nameEl);
  });

  qs("#gallery-grid").addEventListener("keydown", (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;
    if (!t.classList.contains("gallery-name") || !t.isContentEditable) return;
    if (ev.key === "Enter") {
      ev.preventDefault();
      t.blur();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      t.dataset.cancelRename = "1";
      t.blur();
    }
  });

  qs("#gallery-grid").addEventListener("blur", (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;
    if (!t.classList.contains("gallery-name")) return;
    const cancel = t.dataset.cancelRename === "1";
    delete t.dataset.cancelRename;
    finishInlineRename(t, cancel);
  }, true);
}

(async function init() {
  setupTabs();
  setupSizePickerByPrefix("gen");
  setupSizePickerByPrefix("g");
  setupOptimizePromptBuild();
  setupGeneratePromptBuild();
  setupOptimize();
  setupImageGenerate();
  setupBatchBg();
  setupLayer();
  setupGallery();
  await refreshAssets();
})();
