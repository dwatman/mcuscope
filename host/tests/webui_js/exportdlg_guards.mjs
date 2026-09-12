// A stand-in for the export endpoints that refuses what the daemon refuses.
//
// The previous double answered 200 to every URL with no parameter handling at all, so the
// suite certified any URL the dialog could build - and three of them (a comma-joined `chan`,
// `id_to=0`, `changes` without `decode`) were 4xx at the daemon. Mirrors server.py's guards
// for /lines/export, /plot/export and /can/frames; update it beside them.
//
// Note the id_to floor: `ge=0`, so a surface frozen before it held any line exports nothing
// rather than being refused.

const CHANS = ["debug", "cmd", "resp", "event", "marker", "sys"];
const CHAN_MSG = "chan: Input should be 'debug', 'cmd', 'resp', 'event', 'marker' or 'sys'";

// The daemon's refusal message for this URL, or null when it would answer 200.
export function refuse(url) {
  const [path, qs] = String(url).split("?");
  const p = new URLSearchParams(qs || "");
  const num = (k) => Number(p.get(k));
  if (p.has("id_to")) {
    const v = num("id_to");
    if (!Number.isInteger(v) || v < 0) {
      return `id_to: Input should be greater than or equal to 0 (got '${p.get("id_to")}')`;
    }
  }
  if (p.has("since_ts") && p.has("until_ts") && num("until_ts") < num("since_ts")) {
    return "until_ts is before since_ts";
  }
  if (path === "/lines/export") {
    if (!["text", "jsonl", "csv"].includes(p.get("format") || "text")) {
      return "format must be 'text', 'jsonl' or 'csv'";
    }
    // `chan` is a REPEATED parameter (SPEC 3.4), so a comma-joined list is one bad value.
    for (const c of p.getAll("chan")) if (!CHANS.includes(c)) return `${CHAN_MSG} (got '${c}')`;
  } else if (path === "/plot/export") {
    const names = (p.get("names") || "").split(",").filter(Boolean);
    if (!names.length) return "names is required";
    if (!["long", "wide"].includes(p.get("format") || "long")) {
      return "format must be 'long' or 'wide'";
    }
    if (p.has("changes") && !p.has("decode")) return "changes requires decode";
    if (p.has("deadband") && !p.has("changes")) return "deadband requires changes";
    for (const part of (p.get("deadband") || "").split(",").filter(Boolean)) {
      const nm = part.split("=")[0].trim();
      if (!names.includes(nm)) return `no such plot channel in deadband: ${nm}`;
    }
  } else if (path === "/can/frames") {
    if (!["json", "csv"].includes(p.get("format") || "json")) {
      return "format must be 'json' or 'csv'";
    }
    for (const el of (p.get("id") || "").split(",").filter(Boolean)) {
      if (!/^(0[xX])?[0-9a-fA-F]{1,8}$/.test(el)) return `bad can id: ${el}`;
      if (parseInt(el, 16) > 0x1FFFFFFF) return `can id out of range: ${el}`;
    }
  }
  return null;
}

// Install the double. Every export URL the page issues is checked, by whichever road it
// leaves on: a fetch (a token is set) or the `<a download>` navigation state.js uses when
// there is none. `refusals` is what must stay empty - a URL the daemon would not answer.
export function installExportDaemon(env, sessions = []) {
  const seen = { lastUrl: null, refusals: [], fetched: 0, navigated: 0 };
  const record = (url) => {
    seen.lastUrl = String(url);
    const bad = refuse(seen.lastUrl);
    if (bad) seen.refusals.push([seen.lastUrl, bad]);
    return bad;
  };
  const answer = (bad) => ({
    ok: !bad,
    status: bad ? 400 : 200,
    headers: { get: () => null },
    blob: async () => new Blob(["body"]),
    json: async () => (bad ? { error: bad } : {}),
  });

  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith("/sessions")) {
      return { ok: true, status: 200, headers: { get: () => null },
               json: async () => ({ sessions }), blob: async () => new Blob([""]) };
    }
    seen.fetched += 1;
    return answer(record(u));
  };

  const create = env.document.createElement;
  env.document.createElement = (tag) => {
    const el = create(tag);
    if (String(tag).toLowerCase() === "a") {
      // saveBlob's anchor carries an object URL and is not an export request; the download
      // navigation carries the path the dialog built.
      el.click = () => {
        if (!el.href || el.href.startsWith("blob:")) return;
        seen.navigated += 1;
        record(el.href);
      };
    }
    return el;
  };
  return seen;
}
