/**
 * Lightweight YANG parser that extracts container/list/leaf node names and
 * enumeration values for use in YAML code completion.
 */

import * as fs from "fs";
import * as path from "path";

// ─── Tokeniser ────────────────────────────────────────────────────────────────

interface Tok {
  t: "w" | "s" | "{" | "}" | ";"; // word, string, brace, rbrace, semi
  v: string;
}

function tokenize(src: string): Tok[] {
  const result: Tok[] = [];
  let i = 0;
  const n = src.length;

  function skipWs(): void {
    while (i < n && (src[i] === " " || src[i] === "\t" || src[i] === "\r" || src[i] === "\n"))
      i++;
  }

  function readQuoted(): string {
    const q = src[i++]; // consume opening quote
    let v = "";
    while (i < n && src[i] !== q) {
      if (src[i] === "\\" && i + 1 < n) {
        i++;
        v += src[i++];
      } else {
        v += src[i++];
      }
    }
    if (i < n) i++; // consume closing quote
    return v;
  }

  while (i < n) {
    skipWs();
    if (i >= n) break;
    const c = src[i];

    // line comment
    if (c === "/" && src[i + 1] === "/") {
      while (i < n && src[i] !== "\n") i++;
      continue;
    }
    // block comment
    if (c === "/" && src[i + 1] === "*") {
      i += 2;
      while (i < n - 1 && !(src[i] === "*" && src[i + 1] === "/")) i++;
      i += 2;
      continue;
    }

    if (c === "{") { result.push({ t: "{", v: "{" }); i++; continue; }
    if (c === "}") { result.push({ t: "}", v: "}" }); i++; continue; }
    if (c === ";") { result.push({ t: ";", v: ";" }); i++; continue; }

    // quoted string with optional + concatenation
    if (c === '"' || c === "'") {
      let val = readQuoted();
      while (true) {
        const saved = i;
        skipWs();
        if (i < n && src[i] === "+") {
          i++;
          skipWs();
          if (i < n && (src[i] === '"' || src[i] === "'")) {
            val += readQuoted();
            continue;
          }
        }
        i = saved; // no concatenation found, restore position
        break;
      }
      result.push({ t: "s", v: val });
      continue;
    }

    // unquoted word
    let val = "";
    while (
      i < n &&
      src[i] !== " " &&
      src[i] !== "\t" &&
      src[i] !== "\r" &&
      src[i] !== "\n" &&
      src[i] !== "{" &&
      src[i] !== "}" &&
      src[i] !== ";"
    ) {
      val += src[i++];
    }
    if (val) result.push({ t: "w", v: val });
  }

  return result;
}

// ─── Parser ───────────────────────────────────────────────────────────────────

interface Stmt {
  kw: string;
  arg: string;
  body: Stmt[];
}

function parseBody(toks: Tok[], pos: { i: number }): Stmt[] {
  const stmts: Stmt[] = [];
  while (pos.i < toks.length) {
    const t = toks[pos.i];
    if (t.t === "}") { pos.i++; break; }
    if (t.t !== "w" && t.t !== "s") { pos.i++; continue; }

    const kw = t.v;
    pos.i++;

    let arg = "";
    if (pos.i < toks.length && (toks[pos.i].t === "w" || toks[pos.i].t === "s")) {
      arg = toks[pos.i].v;
      pos.i++;
    }

    let body: Stmt[] = [];
    if (pos.i < toks.length && toks[pos.i].t === "{") {
      pos.i++; // consume {
      body = parseBody(toks, pos); // recursive; stops at }
    } else if (pos.i < toks.length && toks[pos.i].t === ";") {
      pos.i++; // consume ;
    }

    stmts.push({ kw, arg, body });
  }
  return stmts;
}

// ─── Schema Builder ───────────────────────────────────────────────────────────

export interface YangSchema {
  /** Parent YAML path → child node names (e.g., "" → ["network-model"]) */
  children: Map<string, string[]>;
  /** Leaf path → enum values (e.g., "network-model/.../device-type" → ["router",...]) */
  enums: Map<string, string[]>;
  /** Node path → description string */
  descriptions: Map<string, string>;
}

function collectGroupings(stmts: Stmt[], into: Map<string, Stmt[]>): void {
  for (const s of stmts) {
    if (s.kw === "grouping") into.set(s.arg, s.body);
    if (s.body.length) collectGroupings(s.body, into);
  }
}

function resolveUses(stmts: Stmt[], groupings: Map<string, Stmt[]>): Stmt[] {
  const out: Stmt[] = [];
  for (const s of stmts) {
    if (s.kw === "uses") {
      // strip prefix (e.g., "nnm:group-name" → "group-name")
      const name = s.arg.includes(":") ? s.arg.split(":").pop()! : s.arg;
      const grp = groupings.get(name);
      if (grp) out.push(...resolveUses(grp, groupings));
    } else {
      out.push({ kw: s.kw, arg: s.arg, body: resolveUses(s.body, groupings) });
    }
  }
  return out;
}

const DATA_KWS = new Set(["container", "list", "leaf", "leaf-list", "anydata", "anyxml"]);

function indexStmts(stmts: Stmt[], parentPath: string, schema: YangSchema): void {
  for (const s of stmts) {
    // flatten choice/case: index data nodes inside case bodies at current level
    if (s.kw === "choice") {
      for (const cs of s.body) {
        if (cs.kw === "case") indexStmts(cs.body, parentPath, schema);
        else if (DATA_KWS.has(cs.kw)) indexStmts([cs], parentPath, schema);
      }
      continue;
    }

    if (!DATA_KWS.has(s.kw)) continue;

    const name = s.arg;
    const nodePath = parentPath ? `${parentPath}/${name}` : name;

    // register node under parent
    const siblings = schema.children.get(parentPath) ?? [];
    if (!siblings.includes(name)) {
      siblings.push(name);
      schema.children.set(parentPath, siblings);
    }

    // extract description and enum values
    for (const ch of s.body) {
      if (ch.kw === "description" && ch.arg) {
        schema.descriptions.set(nodePath, ch.arg);
      }
      if (ch.kw === "type" && ch.arg === "enumeration") {
        const vals = ch.body.filter((e) => e.kw === "enum").map((e) => e.arg);
        if (vals.length) schema.enums.set(nodePath, vals);
      }
    }

    // recurse into children (container/list bodies)
    indexStmts(s.body, nodePath, schema);
  }
}

/** Strip YANG XPath prefixes: "/nnm:network-model/nnm:layer3-layer" → "network-model/layer3-layer" */
function stripPrefixes(rawPath: string): string {
  return rawPath.replace(/^\//, "").replace(/[a-zA-Z0-9_-]+:/g, "");
}

export function loadYangSchema(yangDir: string): YangSchema {
  const schema: YangSchema = {
    children: new Map(),
    enums: new Map(),
    descriptions: new Map(),
  };

  let files: string[];
  try {
    files = fs.readdirSync(yangDir).filter((f) => f.endsWith(".yang"));
  } catch {
    return schema; // yang/ directory not found — return empty schema
  }

  // Parse all YANG files
  const allTop: Stmt[] = [];
  for (const f of files) {
    try {
      const src = fs.readFileSync(path.join(yangDir, f), "utf-8");
      allTop.push(...parseBody(tokenize(src), { i: 0 }));
    } catch {
      /* skip unreadable files */
    }
  }

  // Collect all groupings from every module/submodule
  const groupings = new Map<string, Stmt[]>();
  for (const s of allTop) {
    if (s.kw === "module" || s.kw === "submodule") collectGroupings(s.body, groupings);
  }

  // Index the main module's data tree (groupings resolved inline)
  for (const s of allTop) {
    if (s.kw === "module") {
      indexStmts(resolveUses(s.body, groupings), "", schema);
    }
  }

  // Index augments from all modules (e.g., acl, nat, qos)
  for (const s of allTop) {
    if (s.kw === "module" || s.kw === "submodule") {
      for (const aug of s.body) {
        if (aug.kw === "augment") {
          const augPath = stripPrefixes(aug.arg);
          indexStmts(resolveUses(aug.body, groupings), augPath, schema);
        }
      }
    }
  }

  return schema;
}
