(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const state = {
    iceServers: [{ urls: ["stun:stun.freeswitch.org:3478"] }],
    pc: null,
    remoteStream: null,
    sessionId: "",
    menus: [],
    connecting: false,
    generation: 0,
  };

  const ui = {
    account: $("#account-name"),
    avatar: $("#avatar-select"),
    avatarHint: $("#avatar-hint"),
    connect: $("#connect-button"),
    connectionDetail: $("#connection-detail"),
    connectionDot: $("#connection-dot"),
    connectionState: $("#connection-state"),
    interrupt: $("#interrupt-button"),
    logout: $("#logout-button"),
    menuList: $("#menu-list"),
    menuMessage: $("#menu-message"),
    placeholder: $("#video-placeholder"),
    serverState: $("#server-state"),
    stage: $("#stage-shell"),
    stageStatus: $("#stage-status"),
    subtitle: $("#subtitle"),
    video: $("#avatar-video"),
  };

  function message(text, kind = "waiting") {
    ui.connectionState.textContent = text;
    ui.stageStatus.textContent = text;
    ui.connectionDot.className = "dot" + (kind === "ready" ? " is-ready" : kind === "error" ? " is-error" : "");
  }

  function setServerState(text, kind = "waiting") {
    ui.serverState.textContent = text;
    ui.serverState.className = "server-state is-" + kind;
  }

  function setDetail(text) {
    ui.connectionDetail.textContent = text || "";
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      headers,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error("服务器返回了非 JSON 响应（HTTP " + response.status + "）");
    }
    if (response.status === 401 || payload.code === 401) {
      window.location.assign("/login.html?next=" + encodeURIComponent(window.location.pathname));
      throw new Error("登录已过期");
    }
    if (!response.ok || (Object.hasOwn(payload, "code") && payload.code !== 0)) {
      throw new Error(payload.msg || "请求失败（HTTP " + response.status + "）");
    }
    // 大多数管理接口使用 {code, data} 信封；/offer（WebRTC 信令）和
    // 声音克隆代理则按协议直接返回原始 JSON。不能把后者错误地解成 undefined。
    return Object.hasOwn(payload, "data") ? payload.data : payload;
  }

  async function loadSession() {
    const info = await api("/api/auth/session");
    if (!info.has_users || (info.require_login && !info.authenticated)) {
      window.location.replace("/login.html?next=" + encodeURIComponent(window.location.pathname));
      return false;
    }
    ui.account.textContent = info.display_name || info.username || "";
    ui.logout.hidden = !info.authenticated;
    return true;
  }

  async function loadStatus() {
    try {
      const info = await api("/api/status");
      const sessions = Number(info.sessions || info.active_sessions || 0);
      setServerState("主服务就绪" + (sessions ? " · " + sessions + " 个会话" : ""), "ready");
    } catch (error) {
      setServerState("主服务不可用", "error");
      setDetail(error.message);
    }
  }

  async function loadAvatars() {
    try {
      const items = await api("/api/avatars");
      const ready = (Array.isArray(items) ? items : []).filter((item) => item && item.id && item.status === "ready");
      ui.avatar.replaceChildren();
      if (!ready.length) {
        ui.avatar.append(new Option("（没有可用形象）", ""));
        ui.avatar.disabled = true;
        ui.connect.disabled = true;
        ui.avatarHint.textContent = "服务器尚未准备好可播放的数字人形象。";
        return;
      }
      for (const item of ready) {
        ui.avatar.append(new Option(item.name || item.id, item.id));
      }
      ui.avatar.disabled = false;
      ui.connect.disabled = false;
      ui.avatarHint.textContent = "连接后如需更换形象，请先断开当前会话。";
    } catch (error) {
      ui.avatar.replaceChildren(new Option("（读取形象失败）", ""));
      ui.avatar.disabled = true;
      ui.connect.disabled = true;
      ui.avatarHint.textContent = error.message;
    }
  }

  async function loadRtcConfig() {
    try {
      const config = await api("/api/web/rtc-config");
      if (config && Array.isArray(config.ice_servers) && config.ice_servers.length) {
        state.iceServers = config.ice_servers;
      }
    } catch (error) {
      // 默认 STUN 与当前桌面端保持一致。配置接口暂时不可用不能妨碍同网段试运行。
      console.warn("Unable to load WebRTC ICE configuration:", error);
    }
  }

  function renderMenus(items) {
    state.menus = Array.isArray(items) ? items : [];
    ui.menuList.replaceChildren();
    if (!state.menus.length) {
      const empty = document.createElement("span");
      empty.className = "menu-empty";
      empty.textContent = "暂无启用菜单";
      ui.menuList.append(empty);
      return;
    }
    for (const item of state.menus) {
      const button = document.createElement("button");
      button.className = "menu-button";
      button.type = "button";
      button.textContent = String(item.title || "菜单");
      button.disabled = !state.sessionId;
      button.addEventListener("click", () => activateMenu(item));
      ui.menuList.append(button);
    }
  }

  async function loadMenus() {
    try {
      const data = await api("/api/kiosk/menus/enabled");
      renderMenus(data && data.items);
    } catch (error) {
      ui.menuList.replaceChildren();
      const empty = document.createElement("span");
      empty.className = "menu-empty";
      empty.textContent = "菜单读取失败";
      ui.menuList.append(empty);
      ui.menuMessage.textContent = error.message;
    }
  }

  function updateMenuAvailability() {
    for (const button of ui.menuList.querySelectorAll(".menu-button")) {
      button.disabled = !state.sessionId;
    }
  }

  function waitForIceGathering(pc, timeoutMs = 8000) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      const timer = window.setTimeout(done, timeoutMs);
      function done() {
        window.clearTimeout(timer);
        pc.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
      function check() {
        if (pc.iceGatheringState === "complete") done();
      }
      pc.addEventListener("icegatheringstatechange", check);
    });
  }

  function createPeerConnection(generation) {
    const pc = new RTCPeerConnection({
      iceServers: state.iceServers,
    });
    const stream = new MediaStream();
    state.remoteStream = stream;
    ui.video.srcObject = stream;
    pc.addEventListener("track", (event) => {
      if (generation !== state.generation) return;
      stream.addTrack(event.track);
      ui.placeholder.hidden = true;
      ui.video.play().catch(() => {
        setDetail("浏览器阻止了自动播放，请再次点击“开始连接”。");
      });
    });
    pc.addEventListener("connectionstatechange", () => {
      if (generation !== state.generation) return;
      const current = pc.connectionState;
      if (current === "connected") {
        message("已连接", "ready");
        setDetail("音视频正在由服务器实时推送。");
      } else if (current === "failed" || current === "disconnected") {
        message("连接已断开", "error");
        setDetail("媒体通道异常，请断开后重新连接；跨网环境需要 TURN 服务。");
      }
    });
    pc.addTransceiver("audio", { direction: "recvonly" });
    pc.addTransceiver("video", { direction: "recvonly" });
    return pc;
  }

  async function connect() {
    if (state.connecting || state.pc) return;
    const avatar = ui.avatar.value;
    if (!avatar) return;
    const generation = ++state.generation;
    state.connecting = true;
    ui.connect.disabled = true;
    message("正在建立 WebRTC 连接");
    setDetail("正在向服务器请求数字人音视频流…");
    try {
      const pc = createPeerConnection(generation);
      state.pc = pc;
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      const data = await api("/offer", {
        method: "POST",
        body: JSON.stringify({
          sdp: pc.localDescription.sdp,
          type: pc.localDescription.type,
          sessionid: "",
          avatar,
        }),
      });
      if (generation !== state.generation || state.pc !== pc) return;
      await pc.setRemoteDescription({ type: data.type, sdp: data.sdp });
      state.sessionId = String(data.sessionid || "");
      ui.connect.textContent = "断开连接";
      ui.avatar.disabled = true;
      ui.interrupt.disabled = false;
      updateMenuAvailability();
      message("已连接", "ready");
      setDetail(state.sessionId ? "会话已建立。" : "会话已建立，等待媒体流。");
    } catch (error) {
      if (generation === state.generation) {
        message("连接失败", "error");
        setDetail(error.message);
        disconnect(false);
      }
    } finally {
      if (generation === state.generation) {
        state.connecting = false;
        ui.connect.disabled = false;
      }
    }
  }

  function disconnect(updateMessage = true) {
    ++state.generation;
    const pc = state.pc;
    state.pc = null;
    state.remoteStream = null;
    state.sessionId = "";
    if (pc) pc.close();
    ui.video.srcObject = null;
    ui.placeholder.hidden = false;
    ui.connect.textContent = "开始连接";
    ui.connect.disabled = !ui.avatar.value;
    ui.avatar.disabled = !ui.avatar.value;
    ui.interrupt.disabled = true;
    ui.subtitle.hidden = true;
    ui.subtitle.textContent = "";
    ui.menuMessage.textContent = "";
    updateMenuAvailability();
    if (updateMessage) {
      message("已断开");
      setDetail("选择形象后可以再次连接。");
    }
  }

  async function toggleConnection() {
    if (state.pc) {
      disconnect();
    } else {
      await connect();
    }
  }

  async function interrupt() {
    if (!state.sessionId) return;
    ui.interrupt.disabled = true;
    ui.menuMessage.textContent = "正在打断朗读…";
    try {
      await api("/interrupt_talk", {
        method: "POST",
        body: JSON.stringify({ sessionid: state.sessionId }),
      });
      ui.menuMessage.textContent = "已打断朗读。";
    } catch (error) {
      ui.menuMessage.textContent = "打断失败：" + error.message;
    } finally {
      ui.interrupt.disabled = !state.sessionId;
    }
  }

  async function activateMenu(item) {
    if (!state.sessionId || !item || !item.id) return;
    const title = String(item.title || "菜单");
    ui.menuMessage.textContent = "正在介绍：" + title + "…";
    try {
      const data = await api("/api/kiosk/menus/" + encodeURIComponent(item.id) + "/activate", {
        method: "POST",
        body: JSON.stringify({ sessionid: state.sessionId, speak: true }),
      });
      const answer = String((data && data.answer) || "").trim();
      ui.subtitle.textContent = answer || "已开始朗读";
      ui.subtitle.hidden = false;
      ui.menuMessage.textContent = "正在朗读：" + title;
    } catch (error) {
      ui.menuMessage.textContent = "介绍失败：" + error.message;
    }
  }

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } finally {
      window.location.assign("/login.html");
    }
  }

  async function init() {
    try {
      if (!await loadSession()) return;
      await Promise.all([loadStatus(), loadRtcConfig(), loadAvatars(), loadMenus()]);
      window.setInterval(loadStatus, 20000);
      window.setInterval(loadMenus, 15000);
    } catch (error) {
      setServerState("页面初始化失败", "error");
      message("无法连接服务器", "error");
      setDetail(error.message);
    }
  }

  // 工作台页面只通过这一层访问连接状态与 API，避免每个页面各自维护一套 WebRTC。
  window.KnowTalk = {
    api,
    getSessionId: () => state.sessionId,
    interrupt,
    refreshMenus: loadMenus,
    setMenuMessage: (text) => { ui.menuMessage.textContent = String(text || ""); },
  };

  ui.connect.addEventListener("click", toggleConnection);
  ui.interrupt.addEventListener("click", interrupt);
  ui.logout.addEventListener("click", logout);
  ui.avatar.addEventListener("change", () => {
    ui.connect.disabled = !ui.avatar.value;
  });
  $("#fullscreen-button").addEventListener("click", () => ui.stage.requestFullscreen());
  $("#stage-fullscreen-button").addEventListener("click", () => ui.stage.requestFullscreen());
  window.addEventListener("beforeunload", () => disconnect(false));
  init();
})();
