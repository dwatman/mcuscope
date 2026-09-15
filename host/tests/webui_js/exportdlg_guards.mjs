// A stand-in for GET /lines/export, /plot/export and /can/frames that refuses what server.py
// refuses, in its order and words; test_webui_js.py::test_export_guard_double_agrees_with_the_daemon
// pins every clause below against the real daemon.
//
// Not mirrored: regex compile errors in `match` (Python `regex` syntax), the wide-export
// one-stream check and the label deadband check (both need decoder state).

const MAX_LINE_ID = (1n << 63n) - 1n;
const MAX_MS = 10n ** 15n;
const MAX_MATCH_LEN = 200;
const MAX_DECIMAL_DIGITS = 20;
const CAN_ID_MAX_EXT = 0x1FFFFFFFn;
const CHANS = ["debug", "cmd", "resp", "event", "marker", "sys"];
const BOOLS = ["0", "off", "f", "false", "n", "no", "1", "on", "t", "true", "y", "yes"];

// Python repr() of a str, as _validation_error prints the input.
function pyRepr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ch === "\\" || ch === q) out += "\\" + ch;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch !== " " && /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u.test(ch)) {
      const [w, p] = cp < 0x100 ? [2, "\\x"] : cp < 0x10000 ? [4, "\\u"] : [8, "\\U"];
      out += p + cp.toString(16).padStart(w, "0");
    } else out += ch;
  }
  return q + out + q;
}

const trimWs = (s) => s.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, "");

// pydantic lax str -> int: whitespace, sign, single underscores between digits, a `.000` tail.
function pyInt(s) {
  const m = /^([+-]?)([0-9]+(?:_[0-9]+)*)(?:\.0+)?$/.exec(trimWs(s));
  if (!m) return null;
  const v = BigInt(m[2].replaceAll("_", ""));
  return m[1] === "-" ? -v : v;
}

// pydantic lax str -> float: no leading, trailing or doubled underscore, then Rust's f64 grammar.
function pyFloat(s) {
  const t = trimWs(s);
  if (t.startsWith("_") || t.endsWith("_") || t.includes("__")) return null;
  const u = t.replaceAll("_", "");
  const m = /^([+-]?)(?:(inf|infinity)|(nan))$/i.exec(u);
  if (m) return m[3] ? NaN : m[1] === "-" ? -Infinity : Infinity;
  return /^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(u) ? Number(u) : null;
}

// _parse_deadband's accepted value: protocol.parse_plot_value, the SPEC 2.5 value grammar, finite.
const deadbandNumber = (v) => /^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$/.test(v) && Number.isFinite(Number(v));

// FastAPI's 422 pass: every Query() constraint, in declaration order, joined as
// _validation_error joins them. A repeated scalar parameter takes its last value.
function validate(p, spec) {
  const errs = [];
  const got = (v) => ` (got ${pyRepr(v)})`;
  for (const [name, type, lim = {}] of spec) {
    const all = p.getAll(name);
    if (type === "chan") {
      all.forEach((c, i) => {
        if (!CHANS.includes(c)) {
          errs.push(`chan.${i}: Input should be 'debug', 'cmd', 'resp', 'event', 'marker' or 'sys'${got(c)}`);
        }
      });
      continue;
    }
    if (!all.length) {
      if (lim.required) errs.push(`${name}: Field required`);
      continue;
    }
    const raw = all.at(-1);
    if (type === "int") {
      const v = pyInt(raw);
      if (v === null) errs.push(`${name}: Input should be a valid integer, unable to parse string as an integer${got(raw)}`);
      else if (lim.ge !== undefined && v < lim.ge) errs.push(`${name}: Input should be greater than or equal to ${lim.ge}${got(raw)}`);
      else if (lim.le !== undefined && v > lim.le) errs.push(`${name}: Input should be less than or equal to ${lim.le}${got(raw)}`);
    } else if (type === "float") {
      if (pyFloat(raw) === null) errs.push(`${name}: Input should be a valid number, unable to parse string as a number${got(raw)}`);
    } else if (type === "bool") {
      if (!BOOLS.includes(raw.toLowerCase())) errs.push(`${name}: Input should be a valid boolean, unable to interpret input${got(raw)}`);
    }
  }
  return errs.length ? errs.join("; ") : null;
}

const LINES_SPEC = [
  ["chan", "chan"], ["since_id", "int", { le: MAX_LINE_ID }], ["since_ts", "float"],
  ["until_ts", "float"], ["last_ms", "int", { ge: 0n, le: MAX_MS }],
  ["id_to", "int", { ge: 0n, le: MAX_LINE_ID }],
];
const CAN_SPEC = [
  ["bus", "int", { ge: 1n, le: 9n }], ["last_ms", "int", { ge: 0n, le: MAX_MS }], ["since_ts", "float"],
  ["until_ts", "float"], ["since_id", "int", { le: MAX_LINE_ID }],
  ["id_to", "int", { ge: 0n, le: MAX_LINE_ID }], ["limit", "int", { ge: 0n }],
];
const PLOT_SPEC = [
  ["names", "str", { required: true }], ["last_ms", "int", { ge: 0n, le: MAX_MS }], ["since_ts", "float"],
  ["until_ts", "float"], ["id_to", "int", { ge: 0n, le: MAX_LINE_ID }], ["decode", "bool"],
  ["changes", "bool"],
];

// The daemon's refusal message for this URL, or null when it would answer 200.
// `known.channels` ([{name, port}]) and `known.sessions` ([{id, name}]) model stored state;
// null skips that guard.
export function refuse(url, known = {}) {
  const { channels = null, sessions = null } = known;
  const [path, qs] = String(url).split("?");
  const p = new URLSearchParams(qs || "");
  const last = (k) => (p.has(k) ? p.getAll(k).at(-1) : null);
  const window = () => {
    const s = last("since_ts"), u = last("until_ts");
    for (const [field, v] of [["since_ts", s], ["until_ts", u]]) {
      if (v !== null && !Number.isFinite(pyFloat(v))) return `${field} must be a finite number`;
    }
    return s !== null && u !== null && pyFloat(u) < pyFloat(s) ? "until_ts is before since_ts" : null;
  };
  const session = () => {
    const ref = last("session");
    if (ref === null || sessions === null) return null;
    const byId = /^[0-9]+$/.test(ref) && ref.length <= MAX_DECIMAL_DIGITS
      && sessions.some((s) => BigInt(s.id) === BigInt(ref));
    return byId || sessions.some((s) => s.name === ref) ? null : `no such session: ${ref}`;
  };

  if (path === "/lines/export") {
    const bad = validate(p, LINES_SPEC);
    if (bad) return bad;
    if (!["text", "jsonl", "csv"].includes(last("format") ?? "text")) {
      return "format must be 'text', 'jsonl' or 'csv'";
    }
    const match = last("match");
    if (match !== null && [...match].length > MAX_MATCH_LEN) {
      return `match regex too long (max ${MAX_MATCH_LEN} chars)`;
    }
    return window() || session();
  }

  if (path === "/can/frames") {
    const bad = validate(p, CAN_SPEC);
    if (bad) return bad;
    if (!["json", "csv"].includes(last("format") ?? "json")) return "format must be 'json' or 'csv'";
    const w = window();
    if (w) return w;
    const ids = last("id");
    for (const el of ids === null ? [] : ids.split(",")) {
      if (!el) return "empty can id in list";
      const hex = /^0[xX]/.test(el) ? el.slice(2) : el;
      if (!/^[0-9a-fA-F]{1,16}$/.test(hex)) return `bad can id: ${el}`;
      if (BigInt("0x" + hex) > CAN_ID_MAX_EXT) return `can id out of range: ${el}`;
    }
    return session();
  }

  if (path === "/plot/export") {
    const bad = validate(p, PLOT_SPEC);
    if (bad) return bad;
    const names = last("names").split(",").filter(Boolean);
    if (!names.length) return "names is required";
    const twice = names.find((n, i) => names.indexOf(n) < i);
    if (twice !== undefined) return `names lists ${twice} twice`;
    if (!["long", "wide"].includes(last("format") ?? "long")) return "format must be 'long' or 'wide'";
    const w = window();
    if (w) return w;
    const flag = (k) => ["1", "on", "t", "true", "y", "yes"].includes((last(k) ?? "").toLowerCase());
    if (flag("changes") && !flag("decode")) return "changes requires decode";
    const deadband = last("deadband");
    if (deadband !== null && !flag("changes")) return "deadband requires changes";
    const banded = new Set();
    for (const item of (deadband ?? "").split(",").filter(Boolean)) {
      const eq = item.indexOf("=");
      if (eq < 0) return `deadband needs name=value: ${item}`;
      const name = item.slice(0, eq);
      if (!names.includes(name)) return `deadband names no exported channel: ${item}`;
      if (banded.has(name)) return `deadband names ${name} twice`;
      banded.add(name);
      if (!deadbandNumber(item.slice(eq + 1))) return `deadband value is not a number: ${item}`;
      if (Number(item.slice(eq + 1)) < 0) return `deadband for ${name} must be >= 0`;
    }
    const s = session();
    if (s) return s;
    if (channels !== null) {
      const port = last("port");
      const here = new Set(channels.filter((c) => !port || c.port === port).map((c) => c.name));
      const unknown = names.filter((n) => !here.has(n));
      if (unknown.length) return `no such plot channel: ${unknown.join(", ")}; see /plot/channels`;
    }
    return null;
  }
  return null;
}

// Install the double. Every export URL the page issues is checked, by whichever road it
// leaves on: a fetch (a token is set) or the `<a download>` navigation state.js uses when
// there is none. `refusals` is what must stay empty - a URL the daemon would not answer.
// `sessions` answers /sessions and, when given, backs the session guard.
export function installExportDaemon(env, sessions = null, channels = null) {
  const seen = { lastUrl: null, refusals: [], fetched: 0, navigated: 0 };
  const record = (url) => {
    seen.lastUrl = String(url);
    const bad = refuse(seen.lastUrl, { sessions, channels });
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
               json: async () => ({ sessions: sessions ?? [] }), blob: async () => new Blob([""]) };
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
