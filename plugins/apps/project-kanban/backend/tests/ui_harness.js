// Loads ui/index.js in a sandbox with a stub host and prints the results
// of checks on its pure helpers as JSON. Driven by test_ui.py.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const input = JSON.parse(process.argv[2] || "{}");
const source = fs.readFileSync(
  path.resolve(__dirname, "../../ui/index.js"),
  "utf8",
);

// Any property of the antd / React stubs resolves to a callable stub.
function anything() {
  const fn = function () {
    return {};
  };
  return new Proxy(fn, { get: (target, prop) => (prop in target ? target[prop] : anything()) });
}

const registered = [];
const logs = [];
const window = {
  __PROJECT_KANBAN_TEST__: {},
  QwenPaw: {
    host: {
      React: anything(),
      antd: anything(),
      getApiUrl: (p) => "http://host/api" + p,
      getApiToken: () => "tok",
      useLocale: () => "zh-CN",
    },
    registerRoutes: (pluginId, routes) => registered.push({ pluginId, routes }),
  },
};
const sandbox = {
  window,
  console: {
    info: (...a) => logs.push(["info", a.join(" ")]),
    error: (...a) => logs.push(["error", a.join(" ")]),
    warn: (...a) => logs.push(["warn", a.join(" ")]),
  },
  fetch: async (url, init) => {
    sandbox.lastRequest = { url, init };
    return {
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: "PROJECT_ARCHIVED", message: "x" } }),
    };
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "index.js" });

const ui = window.__PROJECT_KANBAN_TEST__.internals;
const zh = Object.keys(ui.MESSAGES.zh).sort();
const en = Object.keys(ui.MESSAGES.en).sort();
const t = (locale) => (key, values) => ui.tr(locale, key, values);

const moves = {};
const statuses = ["backlog", "todo", "in_progress", "review", "done"];
for (const from of statuses) {
  for (const to of statuses) {
    const issue = { status: from, assignee: "alice" };
    moves[from + ">" + to] = ui.checkMove(issue, to, ["alice"]);
  }
}

const markdown = {};
for (const [name, text] of Object.entries(input.markdown || {})) {
  markdown[name] = ui.renderMarkdown(text);
}

const derived = {};
for (const [name, taken] of input.derive || []) {
  derived[name + "|" + taken.join(",")] = ui.deriveKey(name, taken);
}

// Drives createStreamManager with fake EventSources, a manual timer queue
// and a controllable "is the issue still running?" answer.
async function streamScenarios() {
  const flush = async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  };
  function makeEnv() {
    const env = { sources: [], timers: [], texts: {}, finished: [], running: true, offline: false };
    env.manager = ui.createStreamManager({
      open: (id) => {
        const source = { id, closed: false, close() { this.closed = true; } };
        env.sources.push(source);
        return source;
      },
      isRunning: () => (env.offline ? Promise.reject(new Error("offline")) : Promise.resolve(env.running)),
      onText: (id, text) => { env.texts[id] = text; },
      onFinished: (id) => env.finished.push(id),
      label: (msg) => msg.type + ":" + msg.name,
      schedule: (fn, ms) => { const timer = { fn, ms, cancelled: false }; env.timers.push(timer); return timer; },
      cancel: (timer) => { timer.cancelled = true; },
    });
    env.last = () => env.sources[env.sources.length - 1];
    env.send = (source, msg) => source.onmessage({ data: JSON.stringify(msg) });
    env.fire = async () => {
      const timer = env.timers.shift();
      if (!timer.cancelled) timer.fn();
      await flush();
      return timer.ms;
    };
    return env;
  }
  const out = {};

  // A: transient drop while running -> reconnect, replay rebuilds the text.
  let env = makeEnv();
  env.manager.subscribe("i1");
  const first = env.last();
  env.send(first, { type: "message", text: "a" });
  env.send(first, { type: "tool_start", name: "grep" });
  const beforeDrop = env.texts.i1;
  first.onerror();
  await flush();
  const delay = await env.fire();
  const second = env.last();
  const keptDuringReconnect = env.texts.i1;
  env.send(second, { type: "message", text: "a" });
  env.send(second, { type: "tool_start", name: "grep" });
  env.send(second, { type: "message", text: "b" });
  env.send(first, { type: "message", text: "stale" });  // old source ignored
  out.reconnect = {
    beforeDrop, firstClosed: first.closed, delay, sources: env.sources.length,
    keptDuringReconnect, after: env.texts.i1, finished: env.finished.slice(),
  };
  env.send(second, { type: "done" });
  out.reconnect.finishedAfterDone = env.finished.slice();
  out.reconnect.textAfterDone = env.texts.i1;

  // B: stream closed after the run ended -> finished, no retry.
  env = makeEnv();
  env.manager.subscribe("i2");
  env.running = false;
  env.last().onerror();
  await flush();
  out.ended = { finished: env.finished.slice(), timers: env.timers.length, text: env.texts.i2 };

  // C: offline -> retry with backoff; collapsing cancels the pending retry.
  env = makeEnv();
  env.manager.subscribe("i3");
  env.offline = true;
  env.last().onerror();
  await flush();
  const d1 = await env.fire();
  env.last().onerror();
  await flush();
  const pending = env.timers[0];
  env.manager.unsubscribe("i3");
  out.offline = {
    delays: [d1, pending.ms], cancelled: pending.cancelled, sources: env.sources.length,
    lastClosed: env.last().closed, text: env.texts.i3, finished: env.finished.slice(),
  };

  // D: backoff is capped.
  env = makeEnv();
  env.manager.subscribe("i4");
  const delays = [];
  for (let i = 0; i < 6; i++) {
    env.last().onerror();
    await flush();
    delays.push(await env.fire());
  }
  out.backoff = delays;
  env.manager.closeAll();
  out.closeAll = { closed: env.last().closed, text: env.texts.i4 };
  return out;
}

(async () => {
  let apiError = null;
  try {
    await ui.api.archiveProject("p/1");
  } catch (err) {
    apiError = {
      status: err.status,
      code: err.code,
      text: ui.errorText(t("en"), err),
      url: sandbox.lastRequest.url,
      auth: sandbox.lastRequest.init.headers.Authorization,
    };
  }
  process.stdout.write(
    JSON.stringify({
      registered: registered.map((r) => ({
        pluginId: r.pluginId,
        paths: r.routes.map((x) => x.path),
        labels: r.routes.map((x) => x.label),
      })),
      logs,
      zhKeys: zh,
      enKeys: en,
      missingKeys: ui.missingKeys(),
      errKeys: en.filter((k) => k.startsWith("err.")),
      locale: [ui.normalizeLocale("zh-CN"), ui.normalizeLocale("zh"), ui.normalizeLocale("ja-JP"), ui.normalizeLocale("")],
      fallback: ui.tr("zh", "no.such.key"),
      genericError: ui.errorText(t("zh"), { status: 500 }),
      networkError: ui.errorText(t("en"), new Error("offline")),
      unknownCode: ui.errorText(t("en"), { status: 418, code: "TEAPOT" }),
      moves,
      markdown,
      derived,
      apiError,
      streamUrl: ui.api.streamUrl("p", "i"),
      streams: await streamScenarios(),
    }),
  );
})();
