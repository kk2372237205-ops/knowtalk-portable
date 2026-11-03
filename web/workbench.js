(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
  const short = (value, length = 72) => {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text.length > length ? text.slice(0, length) + "…" : text;
  };
  const pretty = (data) => JSON.stringify(data ?? {}, null, 2);
  const workbench = $("#management-page");
  const broadcast = $("#broadcast-page");
  const nav = $("#page-nav");
  const modes = document.querySelectorAll(".mode-button");
  const api = (...args) => window.KnowTalk.api(...args);
  let activePage = "broadcast";
  let activeMode = "speech";

  function page(title, description, content, actions = "") {
    workbench.innerHTML = `
      <main class="workbench">
        <header class="page-head">
          <div><h1>${esc(title)}</h1><p class="subtle">${esc(description)}</p></div>
          ${actions}
        </header>
        ${content}
      </main>`;
  }

  function message(host, text, kind = "") {
    if (!host) return;
    host.textContent = String(text || "");
    host.className = "notice" + (kind ? " is-" + kind : "");
  }

  function errorMessage(host, error) {
    message(host, error instanceof Error ? error.message : String(error), "error");
  }

  function apiButton(button, handler) {
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      try {
        await handler();
      } catch (error) {
        const notice = $(".notice", workbench);
        errorMessage(notice, error);
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    });
  }

  function requireSession(notice) {
    const sessionId = window.KnowTalk.getSessionId();
    if (sessionId) return sessionId;
    message(notice, "请先进入“开播”连接数字人。", "error");
    return "";
  }

  function toJsonInput(input, label) {
    try {
      const value = JSON.parse(input.value);
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("必须是 JSON 对象");
      }
      return value;
    } catch (error) {
      throw new Error((label || "配置") + "不是合法 JSON：" + error.message);
    }
  }

  function bindModes() {
    modes.forEach((button) => {
      button.addEventListener("click", () => {
        activeMode = button.dataset.mode;
        modes.forEach((item) => item.classList.toggle("is-active", item === button));
        document.querySelectorAll("[data-mode-section]").forEach((section) => {
          section.hidden = section.dataset.modeSection !== activeMode;
        });
        showPage("broadcast");
      });
    });
  }

  function updateNav(pageKey) {
    nav.querySelectorAll(".nav-button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.page === pageKey);
    });
  }

  function showPage(pageKey) {
    activePage = pageKey;
    updateNav(pageKey);
    const isBroadcast = pageKey === "broadcast";
    broadcast.hidden = !isBroadcast;
    workbench.hidden = isBroadcast;
    if (isBroadcast) return;
    workbench.hidden = false;
    const renderer = PAGE_RENDERERS[pageKey];
    if (renderer) renderer();
  }

  function bindNavigation() {
    nav.addEventListener("click", (event) => {
      const button = event.target.closest(".nav-button");
      if (button) showPage(button.dataset.page);
    });
  }

  // ── 讲稿 ────────────────────────────────────────────────────────
  function renderScript() {
    page("讲稿", "支持 [PPT:N] 和 [GESTURE:动作ID]：标签在文字前表示边说边做，写在一句末尾表示说完后再做。保存于当前浏览器，可直接让已连接数字人朗读。", `
      <section class="page-grid single"><article class="work-card">
        <textarea id="script-text" aria-label="讲稿内容" placeholder="例如：大家好，我是陈老师[GESTURE:wave_one]">${esc(localStorage.getItem("knowtalk.script") || "")}</textarea>
        <div class="button-bar">
          <button id="script-save" class="button secondary" type="button">保存讲稿</button>
          <button id="script-speak" class="button primary" type="button">朗读讲稿</button>
          <button id="script-interrupt" class="button secondary" type="button">打断朗读</button>
        </div>
        <p id="script-notice" class="notice"></p>
      </article></section>`);
    const text = $("#script-text", workbench);
    const notice = $("#script-notice", workbench);
    $("#script-save", workbench).addEventListener("click", () => {
      localStorage.setItem("knowtalk.script", text.value);
      message(notice, "讲稿已保存在此浏览器。", "ok");
    });
    apiButton($("#script-speak", workbench), async () => {
      const sessionid = requireSession(notice);
      const value = text.value.trim();
      if (!sessionid || !value) throw new Error("请先输入讲稿内容。");
      await api("/human", { method: "POST", body: JSON.stringify({ sessionid, type: "echo", text: value, interrupt: true }) });
      localStorage.setItem("knowtalk.script", value);
      message(notice, "讲稿已送入数字人朗读队列。", "ok");
    });
    apiButton($("#script-interrupt", workbench), async () => {
      const sessionid = requireSession(notice);
      if (!sessionid) return;
      await api("/interrupt_talk", { method: "POST", body: JSON.stringify({ sessionid }) });
      message(notice, "已打断朗读。", "ok");
    });
  }

  // ── 课件 ────────────────────────────────────────────────────────
  function renderCourse() {
    page("课件", "上传 PPT 或单张图片，管理幻灯片；讲稿中的 [PPT:N] 标签会在朗读时切换对应页面。", `
      <section class="page-grid">
        <article class="work-card">
          <h2>导入课件</h2><p>上传 PPT 后会转换并逐页加入列表；上传图片则作为单页课件。</p>
          <div class="form-stack"><label class="field-label">PPT 文件<input id="ppt-file" class="file-input" type="file" accept=".ppt,.pptx"></label>
          <label class="field-label">图片文件<input id="image-file" class="file-input" type="file" accept="image/*"></label></div>
          <div class="button-bar"><button id="upload-ppt" class="button primary" type="button">导入 PPT</button><button id="upload-image" class="button secondary" type="button">导入图片</button></div>
          <p id="course-notice" class="notice"></p>
        </article>
        <article class="work-card"><h2>当前播放页</h2><p id="current-slide">尚未连接数字人。</p><button id="refresh-course" class="button secondary small-button" type="button">刷新课件列表</button></article>
      </section>
      <section class="work-card" style="margin-top:16px"><h2>课件列表</h2><div id="slides-table"></div></section>`);
    const notice = $("#course-notice", workbench);
    const draw = async () => {
      const slides = await api("/api/slides");
      const rows = Array.isArray(slides) ? slides : [];
      $("#slides-table", workbench).innerHTML = rows.length ? `<table class="data-table"><thead><tr><th>名称</th><th>来源</th><th>关键词</th><th>操作</th></tr></thead><tbody>${rows.map((item) => `<tr><td>${esc(item.name)}</td><td>${esc(item.ppt_name || "单页")}</td><td>${esc((item.keywords || []).join("、"))}</td><td class="actions"><button class="button secondary small-button" data-rename-slide="${esc(item.id)}">重命名</button><button class="button secondary small-button danger-button" data-delete-slide="${esc(item.id)}">删除</button></td></tr>`).join("")}</tbody></table>` : '<p class="empty-row">还没有课件。</p>';
      $("#slides-table", workbench).querySelectorAll("[data-rename-slide]").forEach((button) => apiButton(button, async () => {
        const item = rows.find((slide) => String(slide.id) === button.dataset.renameSlide);
        const name = window.prompt("新的课件名称", item?.name || "");
        if (!name?.trim()) return;
        await api("/api/slides/" + encodeURIComponent(button.dataset.renameSlide), { method: "PUT", body: JSON.stringify({ name: name.trim(), keywords: item?.keywords || [], ppt_name: item?.ppt_name || "" }) });
        await draw();
      }));
      $("#slides-table", workbench).querySelectorAll("[data-delete-slide]").forEach((button) => apiButton(button, async () => {
        if (!window.confirm("确定删除这张课件吗？")) return;
        await api("/api/slides/" + encodeURIComponent(button.dataset.deleteSlide), { method: "DELETE" });
        await draw();
      }));
      const sessionid = window.KnowTalk.getSessionId();
      if (sessionid) {
        const current = await api("/api/current_slide?sessionid=" + encodeURIComponent(sessionid));
        const slide = current?.slide;
        $("#current-slide", workbench).textContent = slide ? `第 ${current.slide_id} 页 · ${slide.name}` : "当前尚未切换到课件页。";
      }
    };
    const upload = async (input, endpoint, afterUpload) => {
      const file = input.files[0];
      if (!file) throw new Error("请先选择文件。");
      const form = new FormData(); form.append("file", file, file.name);
      message(notice, "正在上传，请勿关闭页面…");
      const result = await api(endpoint, { method: "POST", body: form });
      await afterUpload(result, file);
      message(notice, "导入完成。", "ok");
      await draw();
    };
    apiButton($("#upload-image", workbench), () => upload($("#image-file", workbench), "/api/materials/upload-image", async (result, file) => {
      await api("/api/slides", { method: "POST", body: JSON.stringify({ name: file.name.replace(/\.[^.]+$/, ""), image: result.path, keywords: [], ppt_id: "", ppt_name: "" }) });
    }));
    apiButton($("#upload-ppt", workbench), () => upload($("#ppt-file", workbench), "/api/materials/upload-ppt", async (result, file) => {
      const title = file.name.replace(/\.[^.]+$/, "");
      for (const [index, image] of (result.pages || []).entries()) {
        await api("/api/slides", { method: "POST", body: JSON.stringify({ name: `第${index + 1}页`, image, keywords: [], ppt_id: title, ppt_name: title }) });
      }
    }));
    apiButton($("#refresh-course", workbench), draw);
    draw().catch((error) => errorMessage(notice, error));
  }

  // ── 知识库 ──────────────────────────────────────────────────────
  function renderKnowledge() {
    page("知识库管理", "创建课程知识库、上传 PDF、查看索引状态并重建或删除知识库中的文档。", `
      <section class="page-grid"><article class="work-card"><h2>新建知识库</h2><div class="inline-form"><label>知识库 ID<input id="kb-id" placeholder="python-base"></label><label>显示名称<input id="kb-title" placeholder="Python 基础"></label></div><div class="button-bar"><button id="kb-create" class="button primary" type="button">创建知识库</button><button id="kb-refresh" class="button secondary" type="button">刷新</button></div><p id="kb-notice" class="notice"></p></article>
      <article class="work-card"><h2>上传文档</h2><label class="field-label">目标知识库<select id="kb-course"></select></label><label class="field-label">PDF 文档<input id="kb-file" class="file-input" type="file" accept="application/pdf,.pdf"></label><div class="button-bar"><button id="kb-upload" class="button primary" type="button">上传并建立索引</button></div></article></section>
      <section class="work-card" style="margin-top:16px"><h2>知识库与文档</h2><div id="kb-list"></div></section>`);
    const notice = $("#kb-notice", workbench);
    const refresh = async () => {
      const courses = await api("/rag/admin/courses");
      const items = Array.isArray(courses) ? courses : [];
      const select = $("#kb-course", workbench);
      select.replaceChildren(...items.map((course) => new Option(course.title || course.course_id, course.course_id)));
      const host = $("#kb-list", workbench);
      host.innerHTML = items.length ? items.map((course) => `<article class="work-card" style="margin-top:12px"><div class="page-head"><div><h2>${esc(course.title || course.course_id)}</h2><p>${esc(course.description || "无说明")} · ${Number(course.document_count || 0)} 个文档 · ${Number(course.chunk_count || 0)} 个切片</p></div><div class="button-bar"><button class="button secondary small-button" data-reindex-course="${esc(course.course_id)}">重建索引</button><button class="button secondary small-button danger-button" data-delete-course="${esc(course.course_id)}">删除知识库</button></div></div><div data-documents="${esc(course.course_id)}"><p class="subtle">正在读取文档…</p></div></article>`).join("") : '<p class="empty-row">还没有知识库。</p>';
      await Promise.all(items.map(async (course) => {
        const docs = await api("/rag/admin/courses/" + encodeURIComponent(course.course_id) + "/documents");
        const target = host.querySelector(`[data-documents="${CSS.escape(course.course_id)}"]`);
        if (!target) return;
        target.innerHTML = (docs || []).length ? `<table class="data-table"><thead><tr><th>文档</th><th>状态</th><th>切片</th><th>操作</th></tr></thead><tbody>${docs.map((doc) => `<tr><td>${esc(doc.title || doc.file_name)}</td><td>${esc(doc.index_status || "pending")}</td><td>${esc(doc.chunk_count || 0)}</td><td class="actions"><a class="button secondary small-button" href="/rag/admin/documents/${encodeURIComponent(doc.document_id)}/preview" target="_blank" rel="noopener">预览</a><button class="button secondary small-button" data-reindex-doc="${esc(doc.document_id)}">重建</button><button class="button secondary small-button danger-button" data-delete-doc="${esc(doc.document_id)}">删除</button></td></tr>`).join("")}</tbody></table>` : '<p class="subtle">还没有文档。</p>';
      }));
      host.querySelectorAll("[data-reindex-course]").forEach((button) => apiButton(button, async () => { await api("/rag/admin/courses/" + encodeURIComponent(button.dataset.reindexCourse) + "/reindex", { method: "POST", body: "{}" }); message(notice, "已提交重建索引任务。", "ok"); }));
      host.querySelectorAll("[data-delete-course]").forEach((button) => apiButton(button, async () => { if (!window.confirm("删除知识库会同时删除其中所有文档和索引，确定继续吗？")) return; await api("/rag/admin/courses/" + encodeURIComponent(button.dataset.deleteCourse), { method: "DELETE" }); await refresh(); }));
      host.querySelectorAll("[data-reindex-doc]").forEach((button) => apiButton(button, async () => { await api("/rag/admin/documents/" + encodeURIComponent(button.dataset.reindexDoc) + "/reindex", { method: "POST", body: "{}" }); message(notice, "已提交文档重建任务。", "ok"); }));
      host.querySelectorAll("[data-delete-doc]").forEach((button) => apiButton(button, async () => { if (!window.confirm("确定从知识库删除此文档吗？")) return; await api("/rag/admin/documents/" + encodeURIComponent(button.dataset.deleteDoc), { method: "DELETE" }); await refresh(); }));
    };
    apiButton($("#kb-create", workbench), async () => { const course_id = $("#kb-id", workbench).value.trim(); const title = $("#kb-title", workbench).value.trim(); if (!course_id) throw new Error("请填写知识库 ID。"); await api("/rag/admin/courses", { method: "POST", body: JSON.stringify({ course_id, title: title || course_id }) }); await refresh(); message(notice, "知识库已创建。", "ok"); });
    apiButton($("#kb-upload", workbench), async () => { const file = $("#kb-file", workbench).files[0]; const courseId = $("#kb-course", workbench).value; if (!courseId || !file) throw new Error("请选择知识库和 PDF 文件。"); const form = new FormData(); form.append("file", file, file.name); message(notice, "正在上传并建立索引，大文件可能需要几分钟…"); await api("/rag/admin/courses/" + encodeURIComponent(courseId) + "/documents/upload", { method: "POST", body: form }); await refresh(); message(notice, "文档已提交，索引在后台进行。", "ok"); });
    apiButton($("#kb-refresh", workbench), refresh);
    refresh().catch((error) => errorMessage(notice, error));
  }

  // ── 背景 ────────────────────────────────────────────────────────
  function renderBackground() {
    page("背景设置", "选择纯色、已有图片或上传新图片。背景合成在服务器端进行，换完背景后重新连接数字人即可看到效果。", `
      <section class="page-grid"><article class="work-card"><h2>背景状态</h2><p id="background-state">读取中…</p><div id="background-colors" class="color-options"></div><div class="button-bar"><button id="background-none" class="button secondary" type="button">保留原背景</button></div><p id="background-notice" class="notice"></p></article>
      <article class="work-card"><h2>图片背景</h2><label class="field-label">上传图片<input id="background-file" class="file-input" type="file" accept="image/png,image/jpeg,image/webp,image/bmp"></label><div class="button-bar"><button id="background-upload" class="button primary" type="button">上传背景图片</button><button id="background-preview" class="button secondary" type="button">打开合成预览</button></div><div id="background-images" class="preview-list" style="margin-top:12px"></div></article></section>
      <section class="work-card" style="margin-top:16px"><h2>虚拟投影参数</h2><p>保持完整 JSON 结构，避免网页端遗漏服务器新增的阴影字段。</p><textarea id="shadow-json" aria-label="虚拟投影参数"></textarea><div class="button-bar"><button id="shadow-save" class="button secondary" type="button">保存投影参数</button></div></section>`);
    const notice = $("#background-notice", workbench);
    const refresh = async () => {
      const data = await api("/api/avatar/background");
      const config = data.config || { type: "none", value: "" };
      $("#background-state", workbench).textContent = data.matte_ready ? `当前：${config.type} ${config.value || ""}。形象 ${data.avatar || "-"} 已具备抠像素材。` : "当前形象尚无完整抠像素材；背景保存后需完成抠像才会在画面上生效。";
      $("#shadow-json", workbench).value = pretty(data.shadow || {});
      const colors = $("#background-colors", workbench); colors.replaceChildren();
      (data.presets || []).forEach((item) => { const button = document.createElement("button"); button.className = "color-option"; button.type = "button"; button.innerHTML = `<span class="color-chip" style="background:${esc(item.value)}"></span>${esc(item.name)}`; apiButton(button, async () => { await api("/api/avatar/background", { method: "PUT", body: JSON.stringify({ type: "color", value: item.value }) }); await refresh(); message(notice, "背景已保存；重新连接数字人后生效。", "ok"); }); colors.append(button); });
      const images = $("#background-images", workbench); images.replaceChildren();
      (data.backgrounds || []).forEach((item) => { const tile = document.createElement("button"); tile.className = "preview-tile"; tile.type = "button"; tile.innerHTML = `<img src="/backgrounds/${encodeURIComponent(item.name)}" alt="${esc(item.label || item.name)}"><div>${esc(item.label || item.name)}</div>`; apiButton(tile, async () => { await api("/api/avatar/background", { method: "PUT", body: JSON.stringify({ type: "image", value: item.path }) }); await refresh(); message(notice, "背景已保存；重新连接数字人后生效。", "ok"); }); images.append(tile); });
    };
    apiButton($("#background-none", workbench), async () => { await api("/api/avatar/background", { method: "PUT", body: JSON.stringify({ type: "none", value: "" }) }); await refresh(); });
    apiButton($("#background-upload", workbench), async () => { const file = $("#background-file", workbench).files[0]; if (!file) throw new Error("请先选择背景图片。"); const form = new FormData(); form.append("file", file, file.name); await api("/api/avatar/background/upload", { method: "POST", body: form }); await refresh(); message(notice, "图片已上传，点击缩略图即可应用。", "ok"); });
    apiButton($("#background-preview", workbench), async () => { window.open("/api/avatar/background/preview", "_blank", "noopener"); });
    apiButton($("#shadow-save", workbench), async () => { await api("/api/avatar/shadow", { method: "PUT", body: JSON.stringify(toJsonInput($("#shadow-json", workbench), "投影参数")) }); message(notice, "虚拟投影参数已保存。", "ok"); });
    refresh().catch((error) => errorMessage(notice, error));
  }

  // ── 全息舱配置：菜单与唤醒词 ────────────────────────────────────
  function renderMenu() {
    page("菜单库", "展项屏幕上的按钮来自此处。可配置固定答复、知识库问题、素材页和显示顺序。", `
      <section class="page-grid"><article class="work-card"><h2 id="menu-form-title">新增菜单</h2><div class="form-stack"><label class="field-label">菜单名称<input id="menu-title"></label><label class="field-label">固定答复<textarea id="menu-answer" style="min-height:90px"></textarea></label><label class="field-label">知识库预置问题<input id="menu-question"></label><label class="field-label">链接（可选）<input id="menu-link"></label><label class="field-label">显示素材页<select id="menu-material"><option value="">不切换课件</option></select></label><label class="field-label"><input id="menu-enabled" type="checkbox" checked> 启用此菜单项</label></div><div class="button-bar"><button id="menu-save" class="button primary" type="button">保存菜单</button><button id="menu-cancel" class="button secondary" type="button">新建菜单</button></div><p id="menu-notice" class="notice"></p></article><article class="work-card"><h2>端侧说明</h2><p>作答优先级：固定答复 → 知识库预置问题 → 菜单标题。调整顺序会同步影响开播页按钮顺序。</p></article></section><section class="work-card" style="margin-top:16px"><h2>菜单列表</h2><div id="menu-table"></div></section>`);
    const notice = $("#menu-notice", workbench); let selected = null;
    const form = () => ({ title: $("#menu-title", workbench).value.trim(), answer: $("#menu-answer", workbench).value.trim(), question: $("#menu-question", workbench).value.trim(), link: $("#menu-link", workbench).value.trim(), material: $("#menu-material", workbench).value, enabled: $("#menu-enabled", workbench).checked });
    const reset = () => { selected = null; $("#menu-form-title", workbench).textContent = "新增菜单"; ["#menu-title", "#menu-answer", "#menu-question", "#menu-link"].forEach((id) => { $(id, workbench).value = ""; }); $("#menu-material", workbench).value = ""; $("#menu-enabled", workbench).checked = true; };
    const refresh = async () => {
      const [menuData, slides] = await Promise.all([api("/api/kiosk/menus"), api("/api/slides")]);
      const items = menuData.items || []; const material = $("#menu-material", workbench); material.replaceChildren(new Option("不切换课件", ""), ...(slides || []).map((slide) => new Option(slide.name || `第${slide.id}页`, String(slide.id))));
      const host = $("#menu-table", workbench); host.innerHTML = items.length ? `<table class="data-table"><thead><tr><th>顺序</th><th>名称</th><th>答复/问题</th><th>状态</th><th>操作</th></tr></thead><tbody>${items.map((item) => `<tr><td>${esc(item.order)}</td><td>${esc(item.title)}</td><td>${esc(short(item.answer || item.question || "使用标题"))}</td><td>${item.enabled ? "已启用" : "已停用"}</td><td class="actions"><button class="button secondary small-button" data-edit-menu="${esc(item.id)}">编辑</button><button class="button secondary small-button" data-move-menu="${esc(item.id)}" data-delta="-1">上移</button><button class="button secondary small-button" data-move-menu="${esc(item.id)}" data-delta="1">下移</button><button class="button secondary small-button danger-button" data-delete-menu="${esc(item.id)}">删除</button></td></tr>`).join("")}</tbody></table>` : '<p class="empty-row">还没有菜单项。</p>';
      host.querySelectorAll("[data-edit-menu]").forEach((button) => button.addEventListener("click", () => { selected = items.find((item) => item.id === button.dataset.editMenu); if (!selected) return; $("#menu-form-title", workbench).textContent = "编辑菜单"; $("#menu-title", workbench).value = selected.title || ""; $("#menu-answer", workbench).value = selected.answer || ""; $("#menu-question", workbench).value = selected.question || ""; $("#menu-link", workbench).value = selected.link || ""; $("#menu-material", workbench).value = selected.material || ""; $("#menu-enabled", workbench).checked = Boolean(selected.enabled); window.scrollTo({ top: 0, behavior: "smooth" }); }));
      host.querySelectorAll("[data-move-menu]").forEach((button) => apiButton(button, async () => { await api("/api/kiosk/menus/" + encodeURIComponent(button.dataset.moveMenu) + "/move", { method: "POST", body: JSON.stringify({ delta: Number(button.dataset.delta) }) }); await refresh(); window.KnowTalk.refreshMenus(); }));
      host.querySelectorAll("[data-delete-menu]").forEach((button) => apiButton(button, async () => { if (!window.confirm("确定删除此菜单项吗？")) return; await api("/api/kiosk/menus/" + encodeURIComponent(button.dataset.deleteMenu), { method: "DELETE" }); await refresh(); window.KnowTalk.refreshMenus(); }));
    };
    apiButton($("#menu-save", workbench), async () => { const data = form(); if (!data.title) throw new Error("菜单名称不能为空。"); const url = selected ? "/api/kiosk/menus/" + encodeURIComponent(selected.id) : "/api/kiosk/menus"; await api(url, { method: selected ? "PUT" : "POST", body: JSON.stringify(data) }); reset(); await refresh(); window.KnowTalk.refreshMenus(); message(notice, "菜单已保存。", "ok"); });
    $("#menu-cancel", workbench).addEventListener("click", reset);
    refresh().catch((error) => errorMessage(notice, error));
  }

  function renderWake() {
    page("唤醒与打断", "维护展项的唤醒词和打断词。回复语支持动作标签，启用状态会立即保存到主服务。", `
      <section class="page-grid"><article class="work-card"><h2 id="wake-form-title">新增词条</h2><div class="form-stack"><label class="field-label">类型<select id="wake-kind"><option value="wake">唤醒词</option><option value="interrupt">打断词</option></select></label><label class="field-label">词条<input id="wake-word"></label><label class="field-label">回复语（支持动作标签）<textarea id="wake-reply" style="min-height:90px" placeholder="例如：您好，欢迎来到我的课堂[GESTURE:wave_one]"></textarea></label><p class="subtle">写在文字前：<code>[GESTURE:wave_one]您好</code>，边说边做；写在一句末尾：<code>您好[GESTURE:wave_one]</code>，说完后再做。动作 ID 请在“形象与设置 → 动作素材”查看。</p><label class="field-label"><input id="wake-enabled" type="checkbox" checked> 启用此词条</label></div><div class="button-bar"><button id="wake-save" class="button primary" type="button">保存词条</button><button id="wake-cancel" class="button secondary" type="button">新建词条</button></div><p id="wake-notice" class="notice"></p></article><article class="work-card"><h2>使用说明</h2><p>唤醒词会让数字人进入交互状态并朗读回复；打断词会清空当前朗读。回复语中的有效动作标签会随音频播放触发；修改只影响后续识别。</p></article></section><section class="work-card" style="margin-top:16px"><h2>词条列表</h2><div id="wake-table"></div></section>`);
    const notice = $("#wake-notice", workbench); let selected = null;
    const reset = () => { selected = null; $("#wake-form-title", workbench).textContent = "新增词条"; $("#wake-kind", workbench).value = "wake"; $("#wake-word", workbench).value = ""; $("#wake-reply", workbench).value = ""; $("#wake-enabled", workbench).checked = true; };
    const refresh = async () => {
      const data = await api("/api/kiosk/wakewords"); const items = [ ...(data.wake || []).map((item) => ({ ...item, kind: "wake" })), ...(data.interrupt || []).map((item) => ({ ...item, kind: "interrupt" })) ];
      const host = $("#wake-table", workbench); host.innerHTML = items.length ? `<table class="data-table"><thead><tr><th>类型</th><th>词条</th><th>回复</th><th>状态</th><th>操作</th></tr></thead><tbody>${items.map((item) => `<tr><td>${item.kind === "wake" ? "唤醒" : "打断"}</td><td>${esc(item.word)}</td><td>${esc(short(item.reply))}</td><td>${item.enabled ? "已启用" : "已停用"}</td><td class="actions"><button class="button secondary small-button" data-edit-wake="${esc(item.id)}">编辑</button><button class="button secondary small-button danger-button" data-delete-wake="${esc(item.id)}" data-kind="${item.kind}">删除</button></td></tr>`).join("")}</tbody></table>` : '<p class="empty-row">还没有词条。</p>';
      host.querySelectorAll("[data-edit-wake]").forEach((button) => button.addEventListener("click", () => { selected = items.find((item) => item.id === button.dataset.editWake); if (!selected) return; $("#wake-form-title", workbench).textContent = "编辑词条"; $("#wake-kind", workbench).value = selected.kind; $("#wake-word", workbench).value = selected.word || ""; $("#wake-reply", workbench).value = selected.reply || ""; $("#wake-enabled", workbench).checked = Boolean(selected.enabled); window.scrollTo({ top: 0, behavior: "smooth" }); }));
      host.querySelectorAll("[data-delete-wake]").forEach((button) => apiButton(button, async () => { if (!window.confirm("确定删除此词条吗？")) return; await api("/api/kiosk/wakewords/" + encodeURIComponent(button.dataset.deleteWake) + "?kind=" + encodeURIComponent(button.dataset.kind), { method: "DELETE" }); await refresh(); }));
    };
    apiButton($("#wake-save", workbench), async () => { const payload = { kind: $("#wake-kind", workbench).value, word: $("#wake-word", workbench).value.trim(), reply: $("#wake-reply", workbench).value.trim(), enabled: $("#wake-enabled", workbench).checked }; if (!payload.word) throw new Error("词条不能为空。"); const url = selected ? "/api/kiosk/wakewords/" + encodeURIComponent(selected.id) : "/api/kiosk/wakewords"; await api(url, { method: selected ? "PUT" : "POST", body: JSON.stringify(payload) }); reset(); await refresh(); message(notice, "词条已保存。", "ok"); });
    $("#wake-cancel", workbench).addEventListener("click", reset); refresh().catch((error) => errorMessage(notice, error));
  }

  // ── 待机、感应与问答记录 ───────────────────────────────────────
  function renderIdle() {
    page("待机与迎宾", "将人体感应的 enabled 设为 true 后，已连接的应用端会请求其本机摄像头并上报活动检测；浏览器摄像头必须通过 HTTPS 访问。JSON 编辑器会保留服务端新增字段。", `
      <section class="page-grid"><article class="work-card"><h2>闲时动作与迎宾语</h2><textarea id="idle-json" aria-label="闲时配置"></textarea><div class="button-bar"><button id="idle-save" class="button primary" type="button">保存闲时配置</button></div></article><article class="work-card"><h2>应用端人体接近检测</h2><textarea id="proximity-json" aria-label="感应配置"></textarea><div class="button-bar"><button id="proximity-save" class="button primary" type="button">保存感应参数</button><button id="camera-probe" class="button secondary" type="button">探测服务器摄像头（备用）</button></div><p id="camera-result" class="notice">应用端启用后请断开并重新连接，以申请该设备的摄像头权限。</p></article></section><section class="work-card" style="margin-top:16px"><h2>当前全息舱状态</h2><pre id="kiosk-state" class="status-json">读取中…</pre><p id="idle-notice" class="notice"></p></section>`);
    const notice = $("#idle-notice", workbench);
    const refresh = async () => { const [idle, proximity, kiosk] = await Promise.all([api("/api/kiosk/idle"), api("/api/kiosk/proximity"), api("/api/kiosk/state")]); $("#idle-json", workbench).value = pretty(idle); $("#proximity-json", workbench).value = pretty(proximity); $("#kiosk-state", workbench).textContent = pretty(kiosk); };
    apiButton($("#idle-save", workbench), async () => { await api("/api/kiosk/idle", { method: "PUT", body: JSON.stringify(toJsonInput($("#idle-json", workbench), "闲时配置")) }); message(notice, "闲时配置已保存。", "ok"); });
    apiButton($("#proximity-save", workbench), async () => { await api("/api/kiosk/proximity", { method: "PUT", body: JSON.stringify(toJsonInput($("#proximity-json", workbench), "感应配置")) }); message(notice, "感应参数已保存。", "ok"); });
    apiButton($("#camera-probe", workbench), async () => { const data = await api("/api/kiosk/proximity/cameras"); message($("#camera-result", workbench), data.cameras?.length ? "服务器可用摄像头：" + data.cameras.join("、") : (data.error || "未发现可用摄像头")); });
    refresh().catch((error) => errorMessage(notice, error));
  }

  function renderQaLog() {
    page("问答记录", "查看知识库命中情况与回答来源；未命中记录是补充课程资料的重点。", `
      <section class="work-card"><div class="inline-form"><label>关键词<input id="qa-keyword" placeholder="问题或回复中的文字"></label><label>匹配状态<select id="qa-matched"><option value="">全部</option><option value="true">已匹配</option><option value="false">未匹配</option></select></label><button id="qa-search" class="button primary" type="button">查询</button><button id="qa-clear" class="button secondary danger-button" type="button">清空全部记录</button></div><p id="qa-notice" class="notice"></p><div id="qa-stats"></div><div id="qa-table"></div></section>`);
    const notice = $("#qa-notice", workbench);
    const refresh = async () => {
      const params = new URLSearchParams({ limit: "200" }); const keyword = $("#qa-keyword", workbench).value.trim(); const matched = $("#qa-matched", workbench).value; if (keyword) params.set("kw", keyword); if (matched) params.set("matched", matched);
      const data = await api("/api/kiosk/qa_log?" + params); const stats = data.stats || {}; $("#qa-stats", workbench).innerHTML = `<p>共 ${Number(stats.total || 0)} 条 · 已匹配 ${Number(stats.matched || 0)} 条 · 未匹配 ${Number(stats.unmatched || 0)} 条 · 命中率 ${(Number(stats.hit_rate || 0) * 100).toFixed(1)}%</p>`;
      const rows = data.rows || []; const host = $("#qa-table", workbench); host.innerHTML = rows.length ? `<table class="data-table"><thead><tr><th>问题</th><th>回答</th><th>匹配</th><th>来源</th><th>时间</th><th></th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(short(row.question, 55))}</td><td>${esc(short(row.answer, 90))}</td><td>${row.matched ? "已匹配" : "未匹配"}</td><td>${esc(row.source || "-")}</td><td>${esc(row.time || "-")}</td><td><button class="button secondary small-button danger-button" data-delete-qa="${esc(row.id)}">删除</button></td></tr>`).join("")}</tbody></table>` : '<p class="empty-row">没有符合条件的记录。</p>';
      host.querySelectorAll("[data-delete-qa]").forEach((button) => apiButton(button, async () => { if (!window.confirm("确定删除这条问答记录吗？")) return; await api("/api/kiosk/qa_log/" + encodeURIComponent(button.dataset.deleteQa), { method: "DELETE" }); await refresh(); }));
    };
    apiButton($("#qa-search", workbench), refresh); apiButton($("#qa-clear", workbench), async () => { if (!window.confirm("确定清空全部问答记录吗？此操作不可恢复。")) return; await api("/api/kiosk/qa_log", { method: "DELETE" }); await refresh(); message(notice, "问答记录已清空。", "ok"); }); refresh().catch((error) => errorMessage(notice, error));
  }

  // ── 形象、音色与服务设置 ───────────────────────────────────────
  function renderStudioBasic() {
    page("形象与设置", "管理现有数字人形象、提交新形象生成任务，并保存对新建会话生效的音色和数字人设置。", `
      <section class="page-grid"><article class="work-card"><h2>现有形象</h2><div id="avatar-list"></div><p id="studio-notice" class="notice"></p></article><article class="work-card"><h2>生成新形象</h2><div class="form-stack"><label class="field-label">模型<select id="avatar-model"><option value="musetalk">MuseTalk</option><option value="wav2lip">Wav2Lip</option></select></label><label class="field-label">形象 ID<input id="new-avatar-id" placeholder="teacher_01"></label><label class="field-label">视频或图片素材<input id="avatar-source" class="file-input" type="file" accept="video/*,image/*"></label></div><div class="button-bar"><button id="avatar-create" class="button primary" type="button">提交生成任务</button><button id="avatar-tasks" class="button secondary" type="button">刷新任务</button></div><pre id="avatar-task-list" class="status-json" style="margin-top:12px"></pre></article></section>
      <section class="page-grid" style="margin-top:16px"><article class="work-card"><h2>新建会话默认设置</h2><div class="form-stack"><label class="field-label">默认形象 ID<input id="setting-avatar"></label><label class="field-label">TTS 引擎<input id="setting-tts" placeholder="edgetts"></label><label class="field-label">语速<input id="setting-rate" placeholder="+0%"></label><label class="field-label">参考音色<input id="setting-voice"></label></div><div class="button-bar"><button id="settings-save" class="button primary" type="button">保存新建会话设置</button></div></article><article class="work-card"><h2>可选音色</h2><div id="voice-list"></div></article></section>`);
    const notice = $("#studio-notice", workbench);
    const refreshTasks = async () => { const data = await api("/api/avatar/tasks"); $("#avatar-task-list", workbench).textContent = pretty(data.tasks || []); };
    const refresh = async () => {
      const [avatars, settings, voices] = await Promise.all([api("/api/avatars"), api("/api/settings"), api("/api/voices")]);
      const list = Array.isArray(avatars) ? avatars : []; const host = $("#avatar-list", workbench); host.innerHTML = list.length ? list.map((avatar) => `<div class="avatar-row"><img src="${esc(avatar.thumbnail)}" alt="${esc(avatar.name || avatar.id)}"><div><strong>${esc(avatar.name || avatar.id)}</strong><p>${avatar.ready ? "素材完整" : "素材不完整"}</p></div><div class="actions"><button class="button secondary small-button" data-rename-avatar="${esc(avatar.id)}">重命名</button><button class="button secondary small-button danger-button" data-delete-avatar="${esc(avatar.id)}">删除</button></div></div>`).join("") : '<p class="empty-row">还没有可用形象。</p>';
      host.querySelectorAll("[data-rename-avatar]").forEach((button) => apiButton(button, async () => { const new_id = window.prompt("新的形象 ID（字母、数字、下划线或短横线）", button.dataset.renameAvatar); if (!new_id || new_id === button.dataset.renameAvatar) return; await api("/api/avatars/" + encodeURIComponent(button.dataset.renameAvatar), { method: "PUT", body: JSON.stringify({ new_id }) }); await refresh(); }));
      host.querySelectorAll("[data-delete-avatar]").forEach((button) => apiButton(button, async () => { if (!window.confirm("确定删除该形象及其全部素材吗？此操作不可恢复。")) return; await api("/api/avatars/" + encodeURIComponent(button.dataset.deleteAvatar), { method: "DELETE" }); await refresh(); }));
      $("#setting-avatar", workbench).value = settings?.avatar?.id || ""; $("#setting-tts", workbench).value = settings?.tts?.engine || ""; $("#setting-rate", workbench).value = settings?.tts?.rate || ""; $("#setting-voice", workbench).value = settings?.tts?.ref_file || "";
      $("#voice-list", workbench).innerHTML = (Array.isArray(voices) ? voices : []).map((voice) => `<span class="tag">${esc(voice.name || voice.id || voice)}</span>`).join("") || '<p class="subtle">未读取到额外音色。</p>';
      await refreshTasks();
    };
    apiButton($("#avatar-create", workbench), async () => { const file = $("#avatar-source", workbench).files[0]; const avatar_id = $("#new-avatar-id", workbench).value.trim(); if (!file || !avatar_id) throw new Error("请填写形象 ID 并选择视频或图片素材。"); const form = new FormData(); form.append("model", $("#avatar-model", workbench).value); form.append("avatar_id", avatar_id); form.append("video_file", file, file.name); const data = await api("/api/avatar/task", { method: "POST", body: form }); message(notice, "生成任务已提交：" + (data.task_id || ""), "ok"); await refreshTasks(); });
    apiButton($("#avatar-tasks", workbench), refreshTasks);
    apiButton($("#settings-save", workbench), async () => { await api("/api/settings", { method: "PUT", body: JSON.stringify({ avatar: { id: $("#setting-avatar", workbench).value.trim() }, tts: { engine: $("#setting-tts", workbench).value.trim(), rate: $("#setting-rate", workbench).value.trim(), ref_file: $("#setting-voice", workbench).value.trim() } }) }); message(notice, "设置已保存，对新建会话生效。", "ok"); });
    refresh().catch((error) => errorMessage(notice, error));
  }

  // ── 提问式讲课 ──────────────────────────────────────────────────
  function renderQuizBasic() {
    page("提问", "以当前开播会话启动提问式讲课；可提交答案、跳题、查看状态并停止编排。", `
      <section class="page-grid"><article class="work-card"><h2>启动提问式讲课</h2><label class="field-label">带 [PPT:N] 标签的讲稿<textarea id="quiz-text" style="min-height:160px">${esc(localStorage.getItem("knowtalk.script") || "")}</textarea></label><div class="inline-form"><label>题目数量<input id="quiz-count" type="number" value="3" min="1"></label><label>选点方式<select id="quiz-mode"><option value="random">随机</option><option value="sequential">顺序</option></select></label></div><div class="button-bar"><button id="quiz-start" class="button primary" type="button">开始提问式讲课</button><button id="quiz-status-button" class="button secondary" type="button">刷新状态</button><button id="quiz-stop" class="button secondary danger-button" type="button">结束提问</button></div><p id="quiz-notice" class="notice"></p></article><article class="work-card"><h2>当前题目</h2><pre id="quiz-status" class="status-json">尚未查询。</pre><label class="field-label">学生作答<textarea id="quiz-answer" style="min-height:90px"></textarea></label><div class="button-bar"><button id="quiz-answer-submit" class="button primary" type="button">提交作答</button><button id="quiz-skip" class="button secondary" type="button">跳过当前题</button></div></article></section><section class="work-card" style="margin-top:16px"><h2>题库（原始 JSON 新增）</h2><p>保留后端题库的全部字段；填写一条 JSON 对象后可直接加入题库。</p><textarea id="quiz-bank-json" style="min-height:100px" placeholder='{"question":"问题","answer":"答案"}'></textarea><div class="button-bar"><button id="quiz-bank-add" class="button secondary" type="button">新增题目</button><button id="quiz-bank-refresh" class="button secondary" type="button">刷新题库</button></div><pre id="quiz-bank" class="status-json"></pre></section>`);
    const notice = $("#quiz-notice", workbench);
    const sid = () => requireSession(notice);
    const refreshStatus = async () => { const sessionid = sid(); if (!sessionid) return; const data = await api("/api/quiz/status?sessionid=" + encodeURIComponent(sessionid)); $("#quiz-status", workbench).textContent = pretty(data); };
    const refreshBank = async () => { const data = await api("/api/quiz/bank"); $("#quiz-bank", workbench).textContent = pretty(data); };
    apiButton($("#quiz-start", workbench), async () => { const sessionid = sid(); const text = $("#quiz-text", workbench).value.trim(); if (!sessionid || !text) throw new Error("请先连接数字人并输入讲稿。"); await api("/api/quiz/lecture", { method: "POST", body: JSON.stringify({ sessionid, text, count: Number($("#quiz-count", workbench).value) || 1, mode: $("#quiz-mode", workbench).value }) }); localStorage.setItem("knowtalk.script", text); await refreshStatus(); message(notice, "提问式讲课已启动。", "ok"); });
    apiButton($("#quiz-status-button", workbench), refreshStatus);
    apiButton($("#quiz-answer-submit", workbench), async () => { const sessionid = sid(); const answer = $("#quiz-answer", workbench).value.trim(); if (!sessionid || !answer) throw new Error("请先输入作答内容。"); await api("/api/quiz/answer", { method: "POST", body: JSON.stringify({ sessionid, answer }) }); $("#quiz-answer", workbench).value = ""; await refreshStatus(); });
    apiButton($("#quiz-skip", workbench), async () => { const sessionid = sid(); if (!sessionid) return; await api("/api/quiz/skip", { method: "POST", body: JSON.stringify({ sessionid }) }); await refreshStatus(); });
    apiButton($("#quiz-stop", workbench), async () => { const sessionid = sid(); if (!sessionid || !window.confirm("确定结束本次提问式讲课吗？")) return; await api("/api/quiz/stop", { method: "POST", body: JSON.stringify({ sessionid }) }); await refreshStatus(); message(notice, "提问式讲课已结束。", "ok"); });
    apiButton($("#quiz-bank-add", workbench), async () => { await api("/api/quiz/bank", { method: "POST", body: JSON.stringify(toJsonInput($("#quiz-bank-json", workbench), "题目")) }); await refreshBank(); message(notice, "题目已加入题库。", "ok"); }); apiButton($("#quiz-bank-refresh", workbench), refreshBank); refreshBank().catch((error) => errorMessage(notice, error));
  }

  // ── 形象、音色与提问的完整浏览器操作面 ─────────────────────────
  // Basic 页面保留了最小可用配置；这里补齐桌面端已有的素材管理接口，
  // 并且所有删除仍要求浏览器二次确认。
  function renderStudio() {
    renderStudioBasic();
    const notice = $("#studio-notice", workbench);
    const container = $(".workbench", workbench);
    const extra = document.createElement("section");
    extra.className = "page-grid studio-extra";
    extra.innerHTML = `
      <article class="work-card">
        <h2>动作素材</h2>
        <p>动作可插入讲稿：<code>[GESTURE:动作ID]</code>。首次使用时可预处理形象素材。</p>
        <div class="inline-form"><label>形象<select id="gesture-avatar"></select></label><button id="gesture-refresh" class="button secondary" type="button">刷新动作</button></div>
        <div id="gesture-list" class="gesture-list"></div>
      </article>
      <article class="work-card">
        <h2>制作新动作</h2>
        <p>使用已选形象的首帧生成短动作视频并切分素材。该操作会调用已配置的 MiniMax 服务，通常需要 2–4 分钟并可能产生费用。</p>
        <div class="form-stack"><label class="field-label">动作预设<select id="gesture-preset"></select></label><label class="field-label">动作显示名<input id="gesture-name" placeholder="例如：自然挥手"></label></div>
        <div class="button-bar"><button id="gesture-make" class="button primary" type="button">提交动作生成</button><button id="gesture-maker-refresh" class="button secondary" type="button">刷新作业状态</button></div>
        <p id="gesture-maker-status" class="notice"></p>
      </article>
      <article class="work-card">
        <h2>参考音色文件</h2>
        <p>上传的文件保存在服务器的 <code>data/voices</code>；选择“设为 XTTS 音色”会保存为新会话默认设置。</p>
        <div class="inline-form"><label>名称<input id="voice-name" placeholder="例如：教师样音"></label><label>音频<input id="voice-file" class="file-input" type="file" accept=".wav,.mp3,.ogg,.m4a,.flac,audio/*"></label></div>
        <div class="button-bar"><button id="voice-upload" class="button primary" type="button">上传音色文件</button><button id="voice-refresh" class="button secondary" type="button">刷新音色</button></div>
        <div id="uploaded-voices" class="compact-list"></div>
      </article>
      <article class="work-card">
        <h2>系统音色</h2>
        <p>Edge 音色需要服务器可访问微软语音服务；SAPI 仅适用于 Windows 服务器。</p>
        <label class="field-label">可用音色<select id="system-voice"></select></label>
        <div class="button-bar"><button id="system-voice-apply" class="button secondary" type="button">设为默认音色</button></div>
      </article>
      <article class="work-card">
        <h2>XTTS 声音克隆</h2>
        <p id="clone-status" class="subtle">正在检查克隆服务…</p>
        <p class="subtle">网页端只使用服务器已经运行的克隆服务；远程部署时请由服务器管理员以服务方式启动它，避免网页拥有启动进程的权限。</p>
        <label class="field-label">3–10 秒人声样本<input id="clone-file" class="file-input" type="file" accept=".wav,.mp3,.ogg,.m4a,.flac,audio/*"></label>
        <div class="button-bar"><button id="clone-upload" class="button primary" type="button">上传并克隆</button><button id="clone-refresh" class="button secondary" type="button">刷新状态</button></div>
        <div id="clone-voices" class="compact-list"></div>
      </article>`;
    container.append(extra);

    const saveTts = async ({ engine, refFile, refText = "" }) => {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ tts: { engine, ref_file: refFile, ref_text: refText } }) });
      const currentSession = window.KnowTalk.getSessionId();
      if (currentSession && refFile) {
        await api("/api/voice/apply", { method: "POST", body: JSON.stringify({ sessionid: currentSession, engine, ref_file: refFile, ref_text: refText }) });
      }
      const engineField = $("#setting-tts", workbench);
      const voiceField = $("#setting-voice", workbench);
      if (engineField) engineField.value = engine;
      if (voiceField) voiceField.value = refFile;
      message(notice, currentSession ? "音色已保存，并已应用到当前会话。" : "音色已保存，将用于之后新建的会话。", "ok");
    };

    let gesturePresets = [];
    let gestureJobId = "";
    const showGestureJob = (job, configured = true) => {
      const target = $("#gesture-maker-status", extra);
      if (!configured) {
        message(target, "MiniMax 密钥尚未在服务器配置，无法提交动作生成。", "error");
        return;
      }
      if (!job) {
        message(target, "没有正在进行的动作作业。", "");
        return;
      }
      const stage = String(job.stage || "");
      const text = `${job.stage_text || stage || "作业处理中"}${job.progress !== undefined ? " · " + job.progress + "%" : ""}${job.error ? "：" + job.error : ""}`;
      message(target, text, stage === "failed" ? "error" : stage === "done" ? "ok" : "");
    };
    const refreshGestureMaker = async () => {
      const [presetData, jobData] = await Promise.all([
        api("/api/gestures/presets"),
        $("#gesture-avatar", extra).value ? api("/api/gestures/make?avatar_id=" + encodeURIComponent($("#gesture-avatar", extra).value)) : Promise.resolve({}),
      ]);
      gesturePresets = Array.isArray(presetData?.presets) ? presetData.presets : [];
      const selector = $("#gesture-preset", extra);
      const previous = selector.value || presetData?.default_id || "";
      selector.replaceChildren(...gesturePresets.map((preset) => new Option(`${preset.label || preset.id}${preset.hint ? " · " + preset.hint : ""}`, preset.id)));
      if (previous) selector.value = previous;
      const active = jobData?.active || null;
      const recent = Array.isArray(jobData?.recent) ? jobData.recent : [];
      const job = active || recent[0] || null;
      if (job?.id && !["done", "failed"].includes(String(job.stage || ""))) {
        gestureJobId = String(job.id);
      }
      showGestureJob(job, presetData?.configured !== false);
    };
    const pollGestureJob = async () => {
      if (!gestureJobId || !extra.isConnected) return;
      const data = await api("/api/gestures/make/" + encodeURIComponent(gestureJobId));
      const job = data?.job || {};
      showGestureJob(job);
      const stage = String(job.stage || "");
      if (stage === "done") {
        gestureJobId = "";
        await refreshGestures();
        return;
      }
      if (stage === "failed") { gestureJobId = ""; return; }
      window.setTimeout(() => pollGestureJob().catch((error) => errorMessage(notice, error)), 2500);
    };

    const refreshGestures = async () => {
      const avatars = await api("/api/avatars");
      const avatarSelect = $("#gesture-avatar", extra);
      const previous = avatarSelect.value || $("#setting-avatar", workbench)?.value;
      avatarSelect.replaceChildren(...(Array.isArray(avatars) ? avatars : []).map((avatar) => new Option(avatar.name || avatar.id, avatar.id)));
      if (previous) avatarSelect.value = previous;
      const avatarId = avatarSelect.value;
      const host = $("#gesture-list", extra);
      if (!avatarId) { host.innerHTML = '<p class="empty-row">请先创建或选择一个形象。</p>'; return; }
      const result = await api("/api/avatars/" + encodeURIComponent(avatarId) + "/gestures");
      const gestures = Array.isArray(result?.gestures) ? result.gestures : [];
      host.innerHTML = gestures.length ? gestures.map((gesture) => {
        const id = String(gesture.id || "");
        const preview = "/api/avatars/" + encodeURIComponent(avatarId) + "/gestures/" + encodeURIComponent(id) + "/preview";
        return `<div class="gesture-row"><img loading="lazy" src="${esc(preview)}" alt="${esc(gesture.label || id)} 动作预览"><div><strong>${esc(gesture.label || id)}</strong><p><code>${esc(id)}</code> · ${Number(gesture.frames || 0)} 帧</p><div class="actions"><a class="button secondary small-button" href="${esc(preview)}" target="_blank" rel="noopener">预览</a><button class="button secondary small-button" data-prepare-gesture="${esc(id)}">预处理</button><button class="button secondary small-button" data-rename-gesture="${esc(id)}">重命名</button><button class="button secondary small-button danger-button" data-delete-gesture="${esc(id)}">删除</button></div></div></div>`;
      }).join("") : '<p class="empty-row">该形象还没有动作素材。</p>';
      host.querySelectorAll("[data-prepare-gesture]").forEach((button) => apiButton(button, async () => {
        await api("/api/avatars/" + encodeURIComponent(avatarId) + "/gestures/" + encodeURIComponent(button.dataset.prepareGesture) + "/prepare", { method: "POST", body: "{}" });
        message(notice, "动作素材已预处理。", "ok");
      }));
      host.querySelectorAll("[data-rename-gesture]").forEach((button) => apiButton(button, async () => {
        const value = window.prompt("新的动作显示名", button.dataset.renameGesture);
        if (!value?.trim()) return;
        await api("/api/avatars/" + encodeURIComponent(avatarId) + "/gestures/" + encodeURIComponent(button.dataset.renameGesture), { method: "PUT", body: JSON.stringify({ label: value.trim() }) });
        await refreshGestures();
      }));
      host.querySelectorAll("[data-delete-gesture]").forEach((button) => apiButton(button, async () => {
        if (!window.confirm("确定删除这个动作及其素材吗？此操作不可恢复。")) return;
        await api("/api/avatars/" + encodeURIComponent(avatarId) + "/gestures/" + encodeURIComponent(button.dataset.deleteGesture), { method: "DELETE" });
        await refreshGestures();
      }));
      await refreshGestureMaker();
    };

    const refreshVoices = async () => {
      const [uploaded, systems] = await Promise.all([api("/api/voices"), api("/api/voices/system")]);
      const voiceHost = $("#uploaded-voices", extra);
      const samples = Array.isArray(uploaded) ? uploaded : [];
      voiceHost.innerHTML = samples.length ? samples.map((voice) => `<div class="compact-row"><span>${esc(voice.name || voice.path)}</span><div class="actions"><button class="button secondary small-button" data-use-voice="${esc(voice.path)}">设为 XTTS 音色</button><button class="button secondary small-button danger-button" data-delete-voice="${esc(voice.path)}">删除</button></div></div>`).join("") : '<p class="subtle">暂未上传参考音色。</p>';
      voiceHost.querySelectorAll("[data-use-voice]").forEach((button) => apiButton(button, () => saveTts({ engine: "xtts", refFile: button.dataset.useVoice })));
      voiceHost.querySelectorAll("[data-delete-voice]").forEach((button) => apiButton(button, async () => {
        if (!window.confirm("确定删除这个参考音色文件吗？")) return;
        await api("/api/voice/delete", { method: "POST", body: JSON.stringify({ path: button.dataset.deleteVoice }) });
        await refreshVoices();
      }));
      const systemSelect = $("#system-voice", extra);
      const systemVoices = [ ...(systems?.edge || []), ...(systems?.sapi || []) ];
      systemSelect.replaceChildren(new Option(systemVoices.length ? "请选择系统音色" : "未读取到系统音色", ""), ...systemVoices.map((voice) => new Option(`${voice.name || voice.id} · ${voice.engine || ""}`, JSON.stringify({ id: voice.id, engine: voice.engine || "edgetts" }))));
    };

    const refreshClone = async () => {
      const [status, response] = await Promise.all([api("/api/clone/status"), api("/api/clone/speakers")]);
      const statusText = !status?.running ? "克隆服务未启动" : status.loading ? "克隆模型正在加载" : status.ready ? `克隆服务已就绪（${status.device || "服务器"}）` : `克隆服务不可用：${status.error || "未知原因"}`;
      $("#clone-status", extra).textContent = statusText;
      const speakers = Array.isArray(response?.speakers) ? response.speakers.filter((item) => item?.cached) : [];
      const host = $("#clone-voices", extra);
      host.innerHTML = speakers.length ? speakers.map((speaker) => `<div class="compact-row"><span>${esc(speaker.name)} · ${Math.max(0, Number(speaker.size || 0) / 1048576).toFixed(1)} MB</span><div class="actions"><button class="button secondary small-button" data-preview-clone="${esc(speaker.name)}">试听</button><button class="button secondary small-button" data-use-clone="${esc(speaker.file || (speaker.name + ".wav"))}">设为 XTTS 音色</button><button class="button secondary small-button danger-button" data-delete-clone="${esc(speaker.name)}">删除</button></div></div>`).join("") : '<p class="subtle">暂未完成克隆的音色。</p>';
      host.querySelectorAll("[data-preview-clone]").forEach((button) => apiButton(button, async () => {
        const response = await fetch("/api/clone/preview", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ speaker_id: button.dataset.previewClone, text: "这是用这段声音克隆出来的音色，你觉得像吗？" }) });
        if (!response.ok) throw new Error("试听合成失败（HTTP " + response.status + "）");
        const url = URL.createObjectURL(await response.blob());
        const player = new Audio(url);
        player.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
        await player.play();
      }));
      host.querySelectorAll("[data-use-clone]").forEach((button) => apiButton(button, () => saveTts({ engine: "xtts", refFile: "data/voices/" + button.dataset.useClone })));
      host.querySelectorAll("[data-delete-clone]").forEach((button) => apiButton(button, async () => {
        if (!window.confirm("确定删除这个克隆音色及其缓存吗？")) return;
        await api("/api/clone/speaker/" + encodeURIComponent(button.dataset.deleteClone), { method: "DELETE" });
        await refreshClone();
      }));
    };

    apiButton($("#gesture-refresh", extra), refreshGestures);
    $("#gesture-avatar", extra).addEventListener("change", () => refreshGestures().catch((error) => errorMessage(notice, error)));
    apiButton($("#gesture-make", extra), async () => {
      const avatar_id = $("#gesture-avatar", extra).value;
      const preset_id = $("#gesture-preset", extra).value;
      if (!avatar_id || !preset_id) throw new Error("请先选择形象和动作预设。");
      const preset = gesturePresets.find((item) => item.id === preset_id);
      const data = await api("/api/gestures/make", { method: "POST", body: JSON.stringify({ avatar_id, preset_id, name: $("#gesture-name", extra).value.trim() || preset?.label || preset_id }) });
      gestureJobId = String(data?.job?.id || "");
      showGestureJob(data?.job || null);
      if (gestureJobId) pollGestureJob().catch((error) => errorMessage(notice, error));
    });
    apiButton($("#gesture-maker-refresh", extra), refreshGestureMaker);
    apiButton($("#voice-upload", extra), async () => {
      const file = $("#voice-file", extra).files[0];
      if (!file) throw new Error("请选择音频文件。");
      const form = new FormData(); form.append("file", file, file.name); form.append("name", $("#voice-name", extra).value.trim() || file.name.replace(/\.[^.]+$/, ""));
      await api("/api/voice/upload", { method: "POST", body: form });
      $("#voice-file", extra).value = ""; $("#voice-name", extra).value = "";
      await refreshVoices(); message(notice, "参考音色已上传。", "ok");
    });
    apiButton($("#voice-refresh", extra), refreshVoices);
    apiButton($("#system-voice-apply", extra), async () => {
      const raw = $("#system-voice", extra).value;
      if (!raw) throw new Error("请选择系统音色。");
      const selected = JSON.parse(raw);
      await saveTts({ engine: selected.engine, refFile: selected.id });
    });
    apiButton($("#clone-upload", extra), async () => {
      const file = $("#clone-file", extra).files[0];
      if (!file) throw new Error("请选择人声音频。");
      const form = new FormData(); form.append("wav_file", file, file.name);
      const result = await api("/api/clone/speaker", { method: "POST", body: form });
      $("#clone-file", extra).value = "";
      await refreshClone(); await refreshVoices();
      message(notice, "克隆完成：" + (result.speaker_id || "已登记"), "ok");
    });
    apiButton($("#clone-refresh", extra), refreshClone);
    Promise.all([refreshGestures(), refreshVoices(), refreshClone()]).catch((error) => errorMessage(notice, error));
  }

  function renderQuiz() {
    renderQuizBasic();
    const notice = $("#quiz-notice", workbench);
    const container = $(".workbench", workbench);
    const extra = document.createElement("section");
    extra.className = "work-card quiz-extra";
    extra.innerHTML = `
      <h2>题库与课堂记录</h2>
      <p>题库可逐题新增、修改、删除；课堂记录与报告随当前已连接的数字人会话读取。</p>
      <div class="page-grid"><div><h3 id="bank-form-title">新增题目</h3><div class="form-stack"><label class="field-label">问题<input id="bank-question"></label><label class="field-label">参考答案<textarea id="bank-answer" style="min-height:86px"></textarea></label><label class="field-label">评分要点（每行一个）<textarea id="bank-rubric" style="min-height:76px"></textarea></label><label class="field-label">关联课件页（可选）<input id="bank-slide-id" type="number" min="1"></label></div><div class="button-bar"><button id="bank-save" class="button primary" type="button">保存题目</button><button id="bank-cancel" class="button secondary" type="button">取消编辑</button><button id="bank-generate" class="button secondary" type="button">按讲稿生成 3 题</button></div><p id="bank-notice" class="notice"></p></div><div><h3>题库列表</h3><div id="bank-table"></div></div></div>
      <hr class="section-rule"><div class="page-grid"><div><h3>本次课堂记录</h3><div class="button-bar"><button id="records-refresh" class="button secondary" type="button">刷新记录</button><button id="report-refresh" class="button secondary" type="button">生成本次报告</button></div><pre id="quiz-records" class="status-json">请先连接数字人。</pre></div><div><h3>课堂报告</h3><pre id="quiz-report" class="status-json">请先连接数字人。</pre></div></div>`;
    container.append(extra);
    let editingQuestionId = "";
    let questions = [];
    const resetForm = () => {
      editingQuestionId = "";
      $("#bank-form-title", extra).textContent = "新增题目";
      ["#bank-question", "#bank-answer", "#bank-rubric", "#bank-slide-id"].forEach((selector) => { $(selector, extra).value = ""; });
    };
    const readBankForm = () => {
      const question = $("#bank-question", extra).value.trim();
      const reference_answer = $("#bank-answer", extra).value.trim();
      if (!question || !reference_answer) throw new Error("请填写问题和参考答案。");
      const rawSlideId = $("#bank-slide-id", extra).value.trim();
      return { question, reference_answer, rubric: $("#bank-rubric", extra).value.split("\n").map((item) => item.trim()).filter(Boolean), slide_id: rawSlideId ? Number(rawSlideId) : null, source: "manual" };
    };
    const refreshBankTable = async () => {
      const data = await api("/api/quiz/bank");
      questions = Array.isArray(data?.questions) ? data.questions : [];
      const host = $("#bank-table", extra);
      host.innerHTML = questions.length ? `<table class="data-table"><thead><tr><th>问题</th><th>答案 / 要点</th><th>课件</th><th>操作</th></tr></thead><tbody>${questions.map((item) => `<tr><td>${esc(item.question)}</td><td>${esc(short(item.reference_answer, 70))}${(item.rubric || []).length ? `<br><small>${esc((item.rubric || []).join("；"))}</small>` : ""}</td><td>${esc(item.slide_id || "-")}</td><td class="actions"><button class="button secondary small-button" data-edit-question="${esc(item.id)}">编辑</button><button class="button secondary small-button danger-button" data-delete-question="${esc(item.id)}">删除</button></td></tr>`).join("")}</tbody></table>` : '<p class="empty-row">题库为空。</p>';
      host.querySelectorAll("[data-edit-question]").forEach((button) => button.addEventListener("click", () => {
        const current = questions.find((item) => String(item.id) === button.dataset.editQuestion);
        if (!current) return;
        editingQuestionId = String(current.id); $("#bank-form-title", extra).textContent = "编辑题目";
        $("#bank-question", extra).value = current.question || ""; $("#bank-answer", extra).value = current.reference_answer || "";
        $("#bank-rubric", extra).value = (current.rubric || []).join("\n"); $("#bank-slide-id", extra).value = current.slide_id || "";
        $("#bank-question", extra).focus();
      }));
      host.querySelectorAll("[data-delete-question]").forEach((button) => apiButton(button, async () => {
        if (!window.confirm("确定删除这道题吗？")) return;
        await api("/api/quiz/bank/" + encodeURIComponent(button.dataset.deleteQuestion), { method: "DELETE" });
        if (editingQuestionId === button.dataset.deleteQuestion) resetForm();
        await refreshBankTable();
      }));
    };
    const refreshRecords = async (withReport = false) => {
      const sessionid = window.KnowTalk.getSessionId();
      if (!sessionid) throw new Error("请先在“开播”页连接数字人。");
      const records = await api("/api/quiz/records?sessionid=" + encodeURIComponent(sessionid));
      $("#quiz-records", extra).textContent = pretty(records?.records || []);
      if (withReport) {
        const report = await api("/api/quiz/report?sessionid=" + encodeURIComponent(sessionid));
        $("#quiz-report", extra).textContent = report?.text || pretty(report);
      }
    };
    apiButton($("#bank-save", extra), async () => {
      const payload = readBankForm();
      await api(editingQuestionId ? "/api/quiz/bank/" + encodeURIComponent(editingQuestionId) : "/api/quiz/bank", { method: editingQuestionId ? "PUT" : "POST", body: JSON.stringify(payload) });
      resetForm(); await refreshBankTable(); message($("#bank-notice", extra), "题目已保存。", "ok");
    });
    $("#bank-cancel", extra).addEventListener("click", resetForm);
    apiButton($("#bank-generate", extra), async () => {
      const slide_text = $("#quiz-text", workbench).value.trim();
      if (!slide_text) throw new Error("请先在上方填写讲稿，作为出题素材。");
      await api("/api/quiz/bank/generate", { method: "POST", body: JSON.stringify({ slide_text, count: 3, persist: true }) });
      await refreshBankTable(); message($("#bank-notice", extra), "已生成并写入 3 道题目。", "ok");
    });
    apiButton($("#records-refresh", extra), () => refreshRecords(false));
    apiButton($("#report-refresh", extra), () => refreshRecords(true));
    refreshBankTable().catch((error) => errorMessage($("#bank-notice", extra), error));
  }

  // ── 管理后台与状态 ──────────────────────────────────────────────
  function renderAdmin() {
    page("管理后台", "查看在线会话与启动配置，可踢出异常会话或修改当前管理员密码。", `
      <section class="page-grid"><article class="work-card"><h2>在线会话</h2><div id="admin-sessions"></div><p id="admin-notice" class="notice"></p><div class="button-bar"><button id="admin-refresh" class="button secondary" type="button">刷新会话</button></div></article><article class="work-card"><h2>修改密码</h2><div class="form-stack"><label class="field-label">旧密码<input id="old-password" type="password"></label><label class="field-label">新密码<input id="new-password" type="password" minlength="8"></label></div><div class="button-bar"><button id="password-save" class="button primary" type="button">修改密码</button></div></article></section><section class="work-card" style="margin-top:16px"><h2>服务配置</h2><pre id="admin-config" class="status-json">读取中…</pre></section>`);
    const notice = $("#admin-notice", workbench);
    const refresh = async () => { const [sessionData, config] = await Promise.all([api("/api/admin/sessions"), api("/api/admin/config")]); const sessions = sessionData.sessions || []; const host = $("#admin-sessions", workbench); host.innerHTML = sessions.length ? `<table class="data-table"><thead><tr><th>会话 ID</th><th>模型</th><th>形象</th><th>朗读</th><th></th></tr></thead><tbody>${sessions.map((item) => `<tr><td>${esc(short(item.sessionid, 18))}</td><td>${esc(item.model)}</td><td>${esc(item.avatar_id)}</td><td>${item.speaking ? "是" : "否"}</td><td><button class="button secondary small-button danger-button" data-kick-session="${esc(item.sessionid)}">踢出</button></td></tr>`).join("")}</tbody></table>` : '<p class="empty-row">没有在线会话。</p>'; $("#admin-config", workbench).textContent = pretty(config); host.querySelectorAll("[data-kick-session]").forEach((button) => apiButton(button, async () => { if (!window.confirm("确定关闭这个会话的 WebRTC 连接吗？")) return; await api("/api/admin/sessions/" + encodeURIComponent(button.dataset.kickSession), { method: "DELETE" }); await refresh(); })); };
    apiButton($("#admin-refresh", workbench), refresh); apiButton($("#password-save", workbench), async () => { const old_password = $("#old-password", workbench).value; const new_password = $("#new-password", workbench).value; if (!old_password || !new_password) throw new Error("请填写旧密码和新密码。"); await api("/api/auth/password", { method: "POST", body: JSON.stringify({ old_password, new_password }) }); $("#old-password", workbench).value = ""; $("#new-password", workbench).value = ""; message(notice, "密码已修改，其他旧登录会话已失效。", "ok"); }); refresh().catch((error) => errorMessage(notice, error));
  }

  function renderStatus() {
    page("系统状态", "查看主服务、RAG、模型与硬件资源快照。网页端不会提供远程关机或重启入口，避免误中断正在进行的数字人会话。", `<section class="page-grid"><article class="work-card"><h2>状态快照</h2><pre id="system-status" class="status-json">读取中…</pre><div class="button-bar"><button id="status-refresh" class="button primary" type="button">刷新状态</button></div></article><article class="work-card"><h2>RAG 设置</h2><p>开关和服务地址修改后需按服务器部署流程重启主服务。</p><textarea id="rag-settings" style="min-height:220px"></textarea><div class="button-bar"><button id="rag-save" class="button secondary" type="button">保存 RAG 设置</button></div><p id="status-notice" class="notice"></p></article></section>`);
    const notice = $("#status-notice", workbench);
    const refresh = async () => { const [status, rag] = await Promise.all([api("/api/status"), api("/api/settings/rag")]); $("#system-status", workbench).textContent = pretty(status); $("#rag-settings", workbench).value = pretty(rag); };
    apiButton($("#status-refresh", workbench), refresh); apiButton($("#rag-save", workbench), async () => { await api("/api/settings/rag", { method: "PUT", body: JSON.stringify(toJsonInput($("#rag-settings", workbench), "RAG 设置")) }); message(notice, "RAG 设置已保存；按服务器部署流程重启后生效。", "ok"); }); refresh().catch((error) => errorMessage(notice, error));
  }

  const PAGE_RENDERERS = {
    script: renderScript,
    course: renderCourse,
    knowledge: renderKnowledge,
    background: renderBackground,
    menu: renderMenu,
    wake: renderWake,
    idle: renderIdle,
    qalog: renderQaLog,
    studio: renderStudio,
    quiz: renderQuiz,
    admin: renderAdmin,
    status: renderStatus,
  };

  bindModes();
  bindNavigation();
})();
