"use strict";

(() => {
  const GPIO_STATE_URL = "/api/gpio";
  const GPIO_PULSE_URL = "/api/gpio/pulse";
  const STREAM_STATE_URL = "/api/streamer";
  const SWITCH_TIMEOUT_MS = 10000;
  const VIDEO_CHECK_MS = 12000;
  const VIDEO_POLL_MS = 250;
  const VIDEO_SETTLE_MS = 750;
  const STREAM_READ_TIMEOUT_MS = 1200;
  const AUTH_POLL_INTERVAL_MS = 3000;

  const navEl = document.getElementById("host-nav");
  const statusEl = document.getElementById("status");
  const statusTextEl = document.getElementById("status-text");
  const frameEl = document.getElementById("console-frame");
  const buttons = Array.from(document.querySelectorAll(".host-btn"));
  const startVideoEl = document.getElementById("start-video");
  const playbackEl = document.getElementById("playback-status");
  const playbackTextEl = document.getElementById("playback-text");
  const VIDEO_EVENTS = ["loadeddata", "canplay", "play", "playing", "pause", "waiting", "emptied", "resize", "error"];
  const MEDIA_CONTROL_EVENTS = ["pointerdown", "pointerup", "mousedown", "mouseup", "mousemove", "click", "dblclick", "contextmenu", "wheel", "touchstart", "touchmove", "touchend", "keydown", "keyup"];

  let isAuthenticated = false;
  let consoleReady = false;
  let isSwitching = false;
  let isFailedClosed = false;
  let authCheckInFlight = false;
  let authPollTimer = null;
  let lastRequestedHost = null;
  let lastSwitchFeedback = null;
  let observedVideo = null;
  let inputLayer = null;
  let frameObserver = null;
  let playAttempt = 0;

  function setStatus(state, message) {
    statusEl.dataset.state = state;
    statusEl.title = message;
    statusTextEl.textContent = message;
  }

  function setButtonsDisabled(disabled) {
    for (const btn of buttons) {
      btn.disabled = disabled || isFailedClosed || !consoleReady;
    }
  }

  function reportAuthenticatedStatus() {
    if (!consoleReady) {
      setStatus("checking", "Loading console…");
    } else if (lastRequestedHost) {
      setStatus(lastSwitchFeedback?.state || "success", lastSwitchFeedback?.message || `Requested ${lastRequestedHost} · Confirm host on screen`);
    } else {
      setStatus("ready", "Ready · Choose a host, then confirm it on screen");
    }
  }

  function setConsoleReady(ready) {
    consoleReady = ready;
    setButtonsDisabled(!isAuthenticated || isSwitching);
    if (isAuthenticated && !isSwitching && !isFailedClosed) {
      reportAuthenticatedStatus();
    }
  }

  function stopAuthPoll() {
    if (authPollTimer !== null) {
      window.clearInterval(authPollTimer);
      authPollTimer = null;
    }
  }

  function startAuthPoll() {
    if (isAuthenticated || isFailedClosed || authPollTimer !== null) {
      return;
    }
    authPollTimer = window.setInterval(() => {
      if (isAuthenticated || isFailedClosed) {
        stopAuthPoll();
        return;
      }
      void checkAuth();
    }, AUTH_POLL_INTERVAL_MS);
  }

  function markLastRequested(targetButton, label = "last requested") {
    for (const btn of buttons) {
      const badge = btn.querySelector(".host-badge");
      if (btn === targetButton) {
        btn.dataset.lastRequested = "true";
        if (badge) {
          badge.textContent = label;
          badge.hidden = false;
        }
      } else {
        delete btn.dataset.lastRequested;
        if (badge) {
          badge.hidden = true;
        }
      }
    }
  }

  async function readStreamState(signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) {
      controller.abort();
    }
    const timeout = window.setTimeout(abort, STREAM_READ_TIMEOUT_MS);
    try {
      const response = await fetch(STREAM_STATE_URL, {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return null;
      }
      const parsed = await response.json();
      const stream = parsed?.result?.streamer;
      if (parsed?.ok !== true || typeof stream?.hdmi?.signal !== "boolean") {
        return null;
      }
      const source = stream.source;
      const width = Number(source?.resolution?.width);
      const height = Number(source?.resolution?.height);
      const hasSize = Number.isInteger(width) && width > 0 && Number.isInteger(height) && height > 0;
      const rawMode = source?.real_resolution;
      const parsedMode = typeof rawMode === "string" ? rawMode.match(/^(\d+)x(\d+)@(\d+(?:\.\d+)?)$/) : null;
      const modeValid = rawMode == null || (parsedMode && Number(parsedMode[1]) === width && Number(parsedMode[2]) === height);
      const frameRate = parsedMode ? Number(parsedMode[3]) : null;
      const mode = `${width}x${height}${frameRate === null ? "" : `@${frameRate}`}`;
      return { signal: stream.hdmi.signal, online: source?.online === true && hasSize && !!modeValid, width, height, frameRate, mode };
    } catch (_) {
      // A failed status read is never evidence that HDMI dropped.
      return null;
    } finally {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  }

  function watchVideoChange(baseline) {
    const controller = new AbortController();
    const deadline = performance.now() + VIDEO_CHECK_MS;
    let lastSignal = baseline?.signal;
    let sawLoss = false;
    let candidate = null;
    let stableSince = 0;
    let validReads = 0;
    let lastValidAt = 0;
    let latest = null;
    const promise = (async () => {
      while (!controller.signal.aborted && performance.now() < deadline) {
        const current = await readStreamState(controller.signal);
        const now = performance.now();
        if (controller.signal.aborted || now >= deadline) {
          break;
        }
        let kind = null;
        if (current) {
          validReads += 1;
          lastValidAt = now;
          latest = current;
          if (lastSignal === true && current.signal === false) {
            sawLoss = true;
          }
          lastSignal = current.signal;
          if (current.signal && current.online) {
            if (sawLoss) {
              kind = "returned";
            } else if (baseline?.signal === false) {
              kind = "appeared";
            } else if (baseline?.signal && baseline.online && (current.width !== baseline.width || current.height !== baseline.height || (current.frameRate !== null && baseline.frameRate !== null && current.frameRate !== baseline.frameRate))) {
              kind = "changed";
            }
          }
        }
        const key = kind ? `${kind}:${current.mode}` : null;
        if (key !== candidate || key === null) {
          candidate = key;
          stableSince = now;
        } else if (now - stableSince >= VIDEO_SETTLE_MS) {
          return { kind, width: current.width, height: current.height, frameRate: current.frameRate };
        }
        await new Promise((resolve) => window.setTimeout(resolve, VIDEO_POLL_MS));
      }
      const recent = validReads && performance.now() - lastValidAt < STREAM_READ_TIMEOUT_MS + VIDEO_POLL_MS * 2;
      return { kind: !recent ? "unavailable" : !latest.signal ? "no-signal" : latest.online ? "unchanged" : "not-ready" };
    })();
    return { promise, cancel: () => controller.abort() };
  }

  function reportSwitchResult(button, hostName, observation) {
    const labels = { returned: "Video returned", appeared: "Video detected", changed: "Video changed" };
    const label = labels[observation.kind];
    if (label) {
      markLastRequested(button, label.toLowerCase());
      const mode = `${observation.width}×${observation.height}${observation.frameRate === null ? "" : ` @ ${observation.frameRate} Hz`}`;
      lastSwitchFeedback = { state: "success", message: `${label} after requesting ${hostName} · ${mode}` };
    } else {
      const details = { unchanged: "No video change seen; this input may already be selected", "no-signal": "No HDMI signal", "not-ready": "HDMI present; capture is not ready", unavailable: "Video change could not be verified" };
      const detail = details[observation.kind];
      lastSwitchFeedback = { state: "unconfirmed", message: `Requested ${hostName} · ${detail}` };
    }
    reportAuthenticatedStatus();
  }

  function failClosed(hostName, detail) {
    isFailedClosed = true;
    isSwitching = false;
    stopAuthPoll();
    markLastRequested(null);
    navEl.setAttribute("aria-busy", "false");
    for (const btn of buttons) {
      delete btn.dataset.pending;
      btn.disabled = true;
    }
    setStatus(
      "halted",
      `${detail} for ${hostName} · Check the console, then reload this page before switching again`
    );
  }

  async function checkAuth() {
    if (isFailedClosed || isSwitching || authCheckInFlight) {
      return;
    }
    authCheckInFlight = true;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), SWITCH_TIMEOUT_MS);

    try {
      const response = await fetch(GPIO_STATE_URL, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
        },
      });

      if (isFailedClosed || isSwitching) {
        return;
      }

      if (response.status === 401 || response.status === 403) {
        isAuthenticated = false;
        setButtonsDisabled(true);
        setStatus("unauthenticated", "Sign in inside the stock console below to enable host switching");
        startAuthPoll();
        return;
      }

      if (!response.ok) {
        isAuthenticated = false;
        setButtonsDisabled(true);
        setStatus("error", `GPIO check returned HTTP ${response.status} · Sign in or check console`);
        startAuthPoll();
        return;
      }

      const parsed = await response.json();
      if (isFailedClosed || isSwitching) {
        return;
      }
      if (parsed?.ok !== true) {
        throw new Error("Invalid GPIO response");
      }

      isAuthenticated = true;
      stopAuthPoll();
      setButtonsDisabled(false);

      reportAuthenticatedStatus();
    } catch (_) {
      if (isFailedClosed || isSwitching) {
        return;
      }
      isAuthenticated = false;
      setButtonsDisabled(true);
      setStatus("error", "Unable to reach /api/gpio · Check appliance connection");
      startAuthPoll();
    } finally {
      window.clearTimeout(timeoutId);
      authCheckInFlight = false;
    }
  }

  async function requestSwitch(button) {
    if (isFailedClosed || isSwitching || !isAuthenticated || !consoleReady) {
      return;
    }

    const channel = button.dataset.channel;
    const hostName = button.dataset.host;
    const port = button.dataset.port;
    if (!channel || !hostName) {
      return;
    }

    isSwitching = true;
    navEl.setAttribute("aria-busy", "true");
    button.dataset.pending = "true";
    setButtonsDisabled(true);
    setStatus("pending", `Requesting ${hostName} (port ${port})…`);

    const controller = new AbortController();
    let videoWatch = null;
    let timedOut = false;
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, SWITCH_TIMEOUT_MS);

    try {
      const baseline = await readStreamState();
      const url = `${GPIO_PULSE_URL}?channel=${encodeURIComponent(channel)}&wait=true`;
      const responsePromise = fetch(url, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
        },
      });
      videoWatch = watchVideoChange(baseline);
      const response = await responsePromise;

      if (response.status === 401 || response.status === 403) {
        isSwitching = false;
        isAuthenticated = false;
        delete button.dataset.pending;
        navEl.setAttribute("aria-busy", "false");
        setButtonsDisabled(true);
        setStatus("unauthenticated", "Session expired · Sign in inside the stock console below");
        startAuthPoll();
        return;
      }

      if (response.status === 504) {
        failClosed(hostName, "Switch request timed out (HTTP 504)");
        return;
      }

      if (!response.ok) {
        failClosed(hostName, `Switch failed (HTTP ${response.status})`);
        return;
      }

      const parsed = await response.json();
      if (parsed?.ok !== true) {
        failClosed(hostName, "Switch result unavailable");
        return;
      }

      // The command finished. Observation has its own deadline and cannot resend it.
      window.clearTimeout(timeoutId);
      lastRequestedHost = hostName;
      lastSwitchFeedback = null;
      markLastRequested(button);
      setStatus("pending", `Requested ${hostName} · Waiting for video…`);
      const observation = await videoWatch.promise;
      isSwitching = false;
      delete button.dataset.pending;
      navEl.setAttribute("aria-busy", "false");
      setButtonsDisabled(false);
      reportSwitchResult(button, hostName, observation);
    } catch (err) {
      if (timedOut || (err && err.name === "AbortError")) {
        failClosed(hostName, "Switch timed out after 10s");
        return;
      }
      failClosed(hostName, "Switch request interrupted");
    } finally {
      videoWatch?.cancel();
      window.clearTimeout(timeoutId);
    }
  }

  // Playback status is separate from host-switch status and never touches switch state.
  function setPlayback(state, message) {
    playbackEl.hidden = false;
    playbackEl.dataset.state = state;
    playbackTextEl.textContent = message;
  }

  function hasVideoFrame(video) {
    return !video.paused && video.readyState >= 2 && video.videoWidth > 0;
  }

  function syncInputLayer(video) {
    // WebKit's native Play button is inside the video. The stock HID layer covers it.
    inputLayer?.toggleAttribute("data-hosts-playback-paused", !hasVideoFrame(video));
  }

  function isolateMediaControl(event) {
    // Bubble phase: native controls must receive the event before we stop it from
    // reaching the stock stream-box mouse/keyboard handlers. Keep default actions.
    event.stopPropagation();
  }

  function reportVideoState(video) {
    syncInputLayer(video);
    if (video.error) {
      setPlayback("error", `Video error (code ${video.error.code}) · Try Start video`);
    } else if (video.paused) {
      setPlayback("checking", "Video paused · Click Play in the console or choose a host");
    } else if (hasVideoFrame(video)) {
      setPlayback("ready", `Playback started · ${video.videoWidth}×${video.videoHeight}`);
    } else {
      setPlayback("checking", "Waiting for video frames");
    }
  }

  function onVideoEvent(event) {
    if (event.target === observedVideo) {
      syncInputLayer(observedVideo);
      if (event.type === "waiting" || event.type === "emptied") {
        setPlayback("checking", "Waiting for video frames");
      } else {
        reportVideoState(observedVideo);
      }
    }
  }

  function observeVideo(video) {
    if (observedVideo === video) {
      return;
    }
    if (observedVideo) {
      for (const name of VIDEO_EVENTS) {
        observedVideo.removeEventListener(name, onVideoEvent);
      }
      for (const name of MEDIA_CONTROL_EVENTS) {
        observedVideo.removeEventListener(name, isolateMediaControl);
      }
    }
    inputLayer?.removeAttribute("data-hosts-playback-paused");
    observedVideo = video;
    inputLayer = video?.ownerDocument.getElementById("video-wrapper") || null;
    if (video) {
      for (const name of VIDEO_EVENTS) {
        video.addEventListener(name, onVideoEvent);
      }
      for (const name of MEDIA_CONTROL_EVENTS) {
        video.addEventListener(name, isolateMediaControl);
      }
      reportVideoState(video);
    }
  }

  function bindConsoleDocument() {
    frameObserver?.disconnect();
    frameObserver = null;
    observeVideo(null);
    setConsoleReady(false);
    let doc;
    try {
      doc = frameEl.contentDocument;
    } catch (_) {
      return;
    }
    if (!doc?.documentElement) {
      return;
    }
    if (!doc.getElementById("hosts-playback-style")) {
      const style = doc.createElement("style");
      style.id = "hosts-playback-style";
      style.textContent = "#video-wrapper[data-hosts-playback-paused] { pointer-events: none !important; }";
      (doc.head || doc.documentElement).append(style);
    }
    const discoverVideo = () => {
      const video = doc.getElementById("stream-video");
      if (video !== observedVideo) {
        playAttempt += 1;
        observeVideo(video);
        if (!video) {
          playbackEl.hidden = true;
        }
      }
      const ready = !!(video || doc.getElementById("stream-canvas"));
      if (ready !== consoleReady) {
        setConsoleReady(ready);
      }
    };
    frameObserver = new MutationObserver(discoverVideo);
    frameObserver.observe(doc.documentElement, { childList: true, subtree: true });
    discoverVideo();
  }

  function findConsoleVideo() {
    let doc = null;
    try {
      doc = frameEl.contentDocument;
    } catch (_) {
      return { error: "Console frame is not accessible from this page" };
    }
    if (!doc) {
      return { error: "Console frame is not accessible from this page" };
    }
    const video = doc.getElementById("stream-video") || doc.querySelector("video");
    if (!video) {
      return { error: "No console video yet · Sign in or wait for the console to load" };
    }
    return { video };
  }

  // Preserve WebKit's click gesture when autoplay is blocked: no async work before play().
  function startVideo() {
    const attempt = ++playAttempt;
    const found = findConsoleVideo();
    if (!found.video) {
      observeVideo(null);
      setPlayback("error", found.error);
      return;
    }
    const video = found.video;
    let playPromise;
    try {
      playPromise = video.play();
    } catch (err) {
      observeVideo(null);
      setPlayback("error", `Playback failed (${err?.name || "Error"})`);
      return;
    }
    observeVideo(video);
    if (!playPromise || typeof playPromise.then !== "function") {
      reportVideoState(video);
      return;
    }
    setPlayback("pending", "Starting playback…");
    playPromise.then(
      () => {
        if (attempt === playAttempt && video === observedVideo) {
          reportVideoState(video);
        }
      },
      (err) => {
        if (attempt !== playAttempt || video !== observedVideo) {
          return;
        }
        const name = err?.name || "Error";
        const hint = name === "NotAllowedError" ? "Browser blocked playback · Click Start video again" : "Try Start video again";
        setPlayback("error", `Playback rejected (${name}) · ${hint}`);
      }
    );
  }

  if (startVideoEl) {
    startVideoEl.addEventListener("click", startVideo);
  }

  for (const btn of buttons) {
    btn.addEventListener("click", () => {
      // The host choice is already a trusted gesture. Unlock playback before
      // requestSwitch reaches its first await; switching itself still runs once.
      const found = findConsoleVideo();
      if (found.video?.paused) {
        startVideo();
      }
      void requestSwitch(btn);
    });
  }

  if (frameEl) {
    frameEl.addEventListener("load", () => {
      observeVideo(null);
      playAttempt += 1;
      playbackEl.hidden = true;
      bindConsoleDocument();
      if (!isFailedClosed && !isSwitching) {
        void checkAuth();
      }
    });
    bindConsoleDocument();
  }

  void checkAuth();
})();
