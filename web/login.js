(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const ui = {
    button: $("#login-button"),
    confirm: $("#confirm-wrap"),
    confirmInput: $("#password-confirm"),
    description: $("#login-description"),
    eyebrow: $("#login-eyebrow"),
    form: $("#login-form"),
    message: $("#login-message"),
    title: $("#login-title"),
  };
  let setupMode = false;

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json();
    if (!response.ok || payload.code !== 0) throw new Error(payload.msg || "请求失败");
    return payload.data;
  }

  function nextUrl() {
    const target = new URLSearchParams(window.location.search).get("next") || "/";
    return target.startsWith("/") && !target.startsWith("//") ? target : "/";
  }

  function render(mode) {
    setupMode = mode;
    ui.eyebrow.textContent = mode ? "首次部署" : "安全访问";
    ui.title.textContent = mode ? "创建管理员" : "登录";
    ui.description.textContent = mode
      ? "这是第一次访问，请创建服务器管理员账号。"
      : "使用服务器管理员账号继续。";
    ui.confirm.hidden = !mode;
    ui.confirmInput.required = mode;
    ui.button.textContent = mode ? "创建并进入" : "登录";
  }

  async function init() {
    try {
      const info = await request("/api/auth/session");
      if (info.authenticated || (!info.require_login && info.has_users)) {
        window.location.replace(nextUrl());
        return;
      }
      render(!info.has_users);
    } catch (error) {
      ui.message.textContent = error.message;
    }
  }

  ui.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("#username").value.trim();
    const password = $("#password").value;
    if (setupMode && password !== ui.confirmInput.value) {
      ui.message.textContent = "两次输入的密码不一致。";
      return;
    }
    ui.button.disabled = true;
    ui.message.classList.remove("is-ok");
    ui.message.textContent = setupMode ? "正在创建账号…" : "正在登录…";
    try {
      await request(setupMode ? "/api/auth/setup" : "/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password, require_login: true }),
      });
      ui.message.classList.add("is-ok");
      ui.message.textContent = "成功，正在进入…";
      window.location.replace(nextUrl());
    } catch (error) {
      ui.message.textContent = error.message;
      ui.button.disabled = false;
    }
  });
  init();
})();
