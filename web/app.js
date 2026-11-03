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
    subtitleKey: "",
    subtitleTimer: null,
    subtitleHideTimer: null,
    subtitlePolling: false,
    voiceRecognition: null,
    voiceWakeEnabled: false,
    proximity: null,
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
    voiceWake: $("#voice-wake-button"),
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

  function clearSubtitle() {
    if (state.subtitleHideTimer) {
      window.clearTimeout(state.subtitleHideTimer);
      state.subtitleHideTimer = null;
    }
    state.subtitleKey = "";
    ui.subtitle.hidden = true;
    ui.subtitle.textContent = "";
  }

  function showSubtitle(text, key) {
    if (!text) return;
    if (state.subtitleHideTimer) {
      window.clearTimeout(state.subtitleHideTimer);
      state.subtitleHideTimer = null;
    }
    state.subtitleKey = key || state.subtitleKey;
    ui.subtitle.textContent = text;
    ui.subtitle.hidden = false;
  }

  function scheduleSubtitleHide(key) {
    if (state.subtitleHideTimer || !ui.subtitle.hidden) {
      if (state.subtitleHideTimer) window.clearTimeout(state.subtitleHideTimer);
      state.subtitleHideTimer = window.setTimeout(() => {
        if (!key || state.subtitleKey === key) clearSubtitle();
      }, 1200);
    }
  }

  async function pollSubtitle() {
    if (!state.sessionId || state.subtitlePolling) return;
    state.subtitlePolling = true;
    try {
      const snapshot = await api("/llm_status", {
        method: "POST",
        body: JSON.stringify({ sessionid: state.sessionId }),
      });
      const key = String(snapshot?.bubble_key || "");
      const text = String(snapshot?.text || "").trim();
      if (key && key !== state.subtitleKey) {
        clearSubtitle();
        state.subtitleKey = key;
      }
      // 字幕以服务端“实际进入朗读”为准，而不是菜单接口刚返回就抢先展示。
      // 这能让画面上的文字、声音与口型落在同一段时间轴上。
      if (text && snapshot?.speaking) {
        showSubtitle(text, key);
      } else if (key && !snapshot?.speaking && snapshot?.done) {
        scheduleSubtitleHide(key);
      }
    } catch (error) {
      console.debug("subtitle status unavailable:", error);
    } finally {
      state.subtitlePolling = false;
    }
  }

  function startSubtitlePolling() {
    stopSubtitlePolling();
    pollSubtitle();
    state.subtitleTimer = window.setInterval(pollSubtitle, 350);
  }

  function stopSubtitlePolling() {
    if (state.subtitleTimer) {
      window.clearInterval(state.subtitleTimer);
      state.subtitleTimer = null;
    }
    clearSubtitle();
  }

  function requireSecureMedia(feature) {
    if (!window.isSecureContext) {
      throw new Error(feature + "只能在 HTTPS 页面使用；当前校园网 HTTP 地址不能向浏览器申请设备权限。");
    }
  }

  async function reportClientPresence(presence, force = false) {
    const detector = state.proximity;
    if (!detector || !state.sessionId) return;
    const now = Date.now();
    if (!force && detector.present === presence && now - detector.lastReportAt < 4000) return;
    detector.present = presence;
    detector.lastReportAt = now;
    try {
      await api("/api/kiosk/proximity/event", {
        method: "POST",
        body: JSON.stringify({
          sessionid: state.sessionId,
          present: presence,
          confidence: detector.confidence || 0,
          note: "应用端摄像头帧差检测",
        }),
      });
    } catch (error) {
      console.debug("client proximity report failed:", error);
    }
  }

  async function sampleClientProximity() {
    const detector = state.proximity;
    if (!detector || !detector.video || detector.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    const { context, width, height, config } = detector;
    context.drawImage(detector.video, 0, 0, width, height);
    const pixels = context.getImageData(0, 0, width, height).data;
    let changed = 0;
    let sampled = 0;
    if (detector.previous) {
      for (let index = 0, sample = 0; index < pixels.length; index += 16, sample += 1) {
        const brightness = (pixels[index] * 3 + pixels[index + 1] * 4 + pixels[index + 2]) >> 3;
        if (Math.abs(brightness - detector.previous[sample]) > 24) changed += 1;
        sampled += 1;
      }
    }
    const current = new Uint8Array(Math.ceil(pixels.length / 16));
    for (let index = 0, sample = 0; index < pixels.length; index += 16, sample += 1) {
      current[sample] = (pixels[index] * 3 + pixels[index + 1] * 4 + pixels[index + 2]) >> 3;
    }
    detector.previous = current;
    if (!sampled) return;

    const ratio = changed / sampled;
    const threshold = Math.max(0.004, Number(config.min_area_ratio || 0.02) * (1 - 0.75 * Number(config.sensitivity || 0.5)));
    const hit = ratio >= threshold;
    detector.confidence = Math.min(1, ratio / Math.max(threshold, 0.0001));
    detector.hitStreak = hit ? detector.hitStreak + 1 : 0;
    const now = Date.now();
    const enterFrames = Math.max(1, Number(config.enter_frames || 3));
    if (!detector.present && detector.hitStreak >= enterFrames) {
      detector.lastHitAt = now;
      await reportClientPresence(true, true);
    } else if (hit) {
      detector.lastHitAt = now;
    } else if (detector.present && now - detector.lastHitAt > Number(config.leave_seconds || 8) * 1000) {
      await reportClientPresence(false, true);
    } else {
      await reportClientPresence(detector.present, false);
    }
  }

  async function startClientProximity() {
    stopClientProximity(false);
    const config = await api("/api/kiosk/proximity");
    if (!config?.enabled) return;
    requireSecureMedia("应用端摄像头感应");
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前浏览器不支持摄像头访问。");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { width: { ideal: Number(config.width || 640) }, height: { ideal: Number(config.height || 480) } },
    });
    const video = document.createElement("video");
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play();
    const canvas = document.createElement("canvas");
    const width = 160;
    const height = 120;
    canvas.width = width;
    canvas.height = height;
    state.proximity = {
      stream, video, context: canvas.getContext("2d", { willReadFrequently: true }), width, height, config,
      previous: null, present: false, confidence: 0, hitStreak: 0, lastHitAt: Date.now(), lastReportAt: 0, timer: null,
    };
    const every = Math.max(200, 1000 / Math.max(1, Number(config.fps || 5)));
    state.proximity.timer = window.setInterval(() => { sampleClientProximity().catch(console.debug); }, every);
    setDetail("应用端摄像头感应已启动（活动检测）。");
  }

  function stopClientProximity(reportAbsent = true) {
    const detector = state.proximity;
    if (!detector) return;
    if (detector.timer) window.clearInterval(detector.timer);
    if (reportAbsent && detector.present && state.sessionId) {
      api("/api/kiosk/proximity/event", {
        method: "POST",
        body: JSON.stringify({ sessionid: state.sessionId, present: false, note: "应用端摄像头已停止" }),
      }).catch(console.debug);
    }
    detector.stream?.getTracks().forEach((track) => track.stop());
    state.proximity = null;
  }

  function stopVoiceWake() {
    state.voiceWakeEnabled = false;
    const recognition = state.voiceRecognition;
    state.voiceRecognition = null;
    if (recognition) {
      try { recognition.stop(); } catch (_error) { /* 已停止 */ }
    }
    ui.voiceWake.textContent = "启用语音唤醒";
  }

  async function handleVoiceTranscript(text) {
    const phrase = String(text || "").trim();
    if (!phrase || !state.sessionId) return;
    ui.menuMessage.textContent = "识别到：“" + phrase + "”，正在处理…";
    try {
      const result = await api("/api/kiosk/ask", {
        method: "POST",
        body: JSON.stringify({ sessionid: state.sessionId, text: phrase, speak: true, interrupt: true }),
      });
      ui.menuMessage.textContent = result?.kind === "interrupt" ? "已打断朗读。" : "正在准备回答…";
    } catch (error) {
      ui.menuMessage.textContent = "语音请求失败：" + error.message;
    }
  }

  function startVoiceWake() {
    requireSecureMedia("应用端语音唤醒");
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) throw new Error("当前浏览器不支持语音识别，请使用最新版 Edge 或 Chrome。");
    const recognition = new Recognition();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        if (event.results[index].isFinal) handleVoiceTranscript(event.results[index][0].transcript);
      }
    };
    recognition.onerror = (event) => {
      if (event.error !== "aborted") setDetail("语音唤醒异常：" + event.error);
    };
    recognition.onend = () => {
      if (!state.voiceWakeEnabled || !state.sessionId || state.voiceRecognition !== recognition) return;
      window.setTimeout(() => { try { recognition.start(); } catch (_error) { /* 浏览器正在重启识别 */ } }, 250);
    };
    state.voiceRecognition = recognition;
    state.voiceWakeEnabled = true;
    recognition.start();
    ui.voiceWake.textContent = "关闭语音唤醒";
    ui.menuMessage.textContent = "语音唤醒已开启，请说唤醒词或直接提问。";
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
      ui.voiceWake.disabled = false;
      updateMenuAvailability();
      startSubtitlePolling();
      startClientProximity().catch((error) => {
        // 仅在管理员启用感应后才提示；默认未启用时不打扰正常开播。
        if (error?.message) setDetail(error.message);
      });
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
    stopVoiceWake();
    stopClientProximity(true);
    stopSubtitlePolling();
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
    ui.voiceWake.disabled = true;
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
      clearSubtitle();
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
    clearSubtitle();
    ui.menuMessage.textContent = "正在准备：" + title + "…";
    try {
      const data = await api("/api/kiosk/menus/" + encodeURIComponent(item.id) + "/activate", {
        method: "POST",
        body: JSON.stringify({ sessionid: state.sessionId, speak: true, interrupt: true }),
      });
      ui.menuMessage.textContent = data?.spoken ? "正在合成并朗读：" + title : "正在准备回答：" + title;
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
  ui.voiceWake.addEventListener("click", () => {
    try {
      if (state.voiceWakeEnabled) stopVoiceWake(); else startVoiceWake();
    } catch (error) {
      setDetail(error.message);
    }
  });
  ui.logout.addEventListener("click", logout);
  ui.avatar.addEventListener("change", () => {
    ui.connect.disabled = !ui.avatar.value;
  });
  $("#fullscreen-button").addEventListener("click", () => ui.stage.requestFullscreen());
  $("#stage-fullscreen-button").addEventListener("click", () => ui.stage.requestFullscreen());
  window.addEventListener("beforeunload", () => disconnect(false));
  init();
})();
