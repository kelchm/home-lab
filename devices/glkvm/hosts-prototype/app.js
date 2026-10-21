"use strict";

(() => {
  const GPIO_STATE_URL = "/api/gpio";
  const GPIO_PULSE_URL = "/api/gpio/pulse";
  const SWITCH_TIMEOUT_MS = 10000;
  const AUTH_POLL_INTERVAL_MS = 3000;

  const navEl = document.getElementById("host-nav");
  const statusEl = document.getElementById("status");
  const statusTextEl = document.getElementById("status-text");
  const frameEl = document.getElementById("console-frame");
  const buttons = Array.from(document.querySelectorAll(".host-btn"));

  let isAuthenticated = false;
  let isSwitching = false;
  let isFailedClosed = false;
  let authCheckInFlight = false;
  let authPollTimer = null;
  let lastRequestedHost = null;

  function setStatus(state, message) {
    statusEl.dataset.state = state;
    statusTextEl.textContent = message;
  }

  function setButtonsDisabled(disabled) {
    for (const btn of buttons) {
      btn.disabled = disabled || isFailedClosed;
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

  function markLastRequested(targetButton) {
    for (const btn of buttons) {
      const badge = btn.querySelector(".host-badge");
      if (btn === targetButton) {
        btn.dataset.lastRequested = "true";
        if (badge) {
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

      if (lastRequestedHost) {
        setStatus("success", `Requested ${lastRequestedHost} · Confirm host on screen`);
      } else {
        setStatus("ready", "Ready · Choose a host, then confirm it on screen");
      }
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
    if (isFailedClosed || isSwitching || !isAuthenticated) {
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
    let timedOut = false;
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, SWITCH_TIMEOUT_MS);

    try {
      const url = `${GPIO_PULSE_URL}?channel=${encodeURIComponent(channel)}&wait=true`;
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
        },
      });

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

      isSwitching = false;
      delete button.dataset.pending;
      navEl.setAttribute("aria-busy", "false");
      lastRequestedHost = hostName;
      markLastRequested(button);
      setButtonsDisabled(false);
      setStatus("success", `Requested ${hostName} · Confirm host on screen`);
    } catch (err) {
      if (timedOut || (err && err.name === "AbortError")) {
        failClosed(hostName, "Switch timed out after 10s");
        return;
      }
      failClosed(hostName, "Switch request interrupted");
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  for (const btn of buttons) {
    btn.addEventListener("click", () => {
      void requestSwitch(btn);
    });
  }

  if (frameEl) {
    frameEl.addEventListener("load", () => {
      if (!isFailedClosed && !isSwitching) {
        void checkAuth();
      }
    });
  }

  void checkAuth();
})();
