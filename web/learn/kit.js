// Pieces every chapter after the first shares: page start-up, tabs, a quiz, replayed program output,
// an annotated input file and a practice terminal driven by recorded ACTINV output.

import {
  sound, soundButton, themeButton, glossary, complete, isDone, markTasks, rail, store, reducedMotion, toast,
} from "./learn.js";

export const $ = (s) => document.querySelector(s);
export const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
export const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion ? 0 : ms));

/* Start a chapter: toolbar buttons, glossary, task boxes and the side panel. */
export function boot({ terms, steps, version }) {
  soundButton($("#sound"));
  themeButton($("#theme"));
  glossary(terms);
  markTasks();
  rail(steps);
  if (version) document.querySelectorAll(".ver").forEach((el) => (el.textContent = version.replace("actinv ", "")));
  const ids = Object.values(steps).flat();
  $("#reset")?.addEventListener("click", () => {
    const done = store.get("done", []).filter((t) => !ids.includes(t));
    store.set("done", done);
    Object.keys(localStorage).filter((k) => k.startsWith("actinv-learn:" + $("body").dataset.chapter + ":"))
      .forEach((k) => { try { localStorage.removeItem(k); } catch { /* private mode */ } });
    location.reload();
  });
  $("#start")?.addEventListener("click", () => {
    complete($("#start").dataset.task);
    document.querySelector("section.step:nth-of-type(2)")?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth" });
  });
}

/* A key in this chapter's part of the store. */
export const key = (name) => `${document.body.dataset.chapter}:${name}`;

/* Tabs made of selectable labels; aria-controls names each panel. */
export function tabs(group) {
  const ts = [...group.querySelectorAll('[role="tab"]')];
  ts.forEach((t) => t.addEventListener("click", () => {
    sound.tick();
    ts.forEach((x) => {
      x.setAttribute("aria-selected", x === t); x.classList.toggle("on", x === t);
      document.getElementById(x.getAttribute("aria-controls")).hidden = x !== t;
    });
  }));
}

/* Questions with one right answer each; a wrong answer shakes, the right one explains itself. */
export function quiz(host, questions, task) {
  const answered = new Set(store.get(key("quiz"), []));
  questions.forEach((q, qi) => {
    const box = document.createElement("div"); box.className = "q";
    box.innerHTML = `<p>${qi + 1}. ${q.q}</p><div class="opts"></div><p class="why" hidden></p>`;
    const opts = box.querySelector(".opts"), why = box.querySelector(".why");
    const order = q.a.map((x, i) => [x, i]).sort((a, b) => ((a[1] * 7 + qi * 3) % 5) - ((b[1] * 7 + qi * 3) % 5));
    order.forEach(([[text, right]]) => {
      const b = document.createElement("button"); b.type = "button"; b.className = "btn opt"; b.textContent = text;
      const reveal = () => { b.classList.add("right"); why.hidden = false; why.innerHTML = q.why; };
      b.addEventListener("click", () => {
        if (right) {
          reveal(); opts.querySelectorAll(".opt").forEach((o) => (o.disabled = true));
          answered.add(qi); store.set(key("quiz"), [...answered]); sound.done();
          if (answered.size === questions.length) complete(task);
        } else { b.classList.add("wrong"); sound.wrong(); setTimeout(() => b.classList.remove("wrong"), 400); }
      });
      if (answered.has(qi)) { b.disabled = true; if (right) reveal(); }
      opts.append(b);
    });
    host.append(box);
  });
}

/* One multiple-choice prediction, kept once made. Calls then(answer) on choosing and on reload. */
export function predict(host, name, then) {
  const opts = [...host.querySelectorAll("[data-a]")];
  const choose = (a, quiet) => {
    opts.forEach((o) => { o.disabled = true; o.classList.toggle("right", o.dataset.a === a); });
    store.set(key(name), a);
    then(a, quiet);
  };
  opts.forEach((o) => o.addEventListener("click", () => { sound.tick(); choose(o.dataset.a, false); }));
  const saved = store.get(key(name), null);
  if (saved) choose(saved, true);
}

/* Python with comments dimmed. */
export const python = (code) => esc(code).replace(/(#.*)$/gm, '<span class="c">$1</span>');

/* Replay a program's recorded output line by line. */
export async function replay(btn, status, out, text, then) {
  btn.disabled = true; status.textContent = "running…";
  for (let i = 0; i < 8; i++) { sound.key(); await wait(120); }
  out.hidden = false; out.textContent = "";
  for (const l of text.split("\n")) { out.textContent += (out.textContent ? "\n" : "") + l; sound.tick(); await wait(160); }
  status.textContent = "done (replayed)"; btn.disabled = false;
  then && then();
}

/* An input file shown as JSON, split into parts that each explain themselves. */
export function fileExplorer({ pre, chips, explain, doc, parts, task, shorten = {} }) {
  const val = (v) => typeof v === "string" ? `<span class="s">${esc(JSON.stringify(v))}</span>` : `<span class="n">${esc(JSON.stringify(v))}</span>`;
  function render(v, ind, k) {
    const pad = "  ".repeat(ind);
    if (k in shorten) return shorten[k](v, ind);
    if (Array.isArray(v)) return v.length === 0 ? "[]" : v.every((x) => typeof x !== "object" || x === null)
      ? "[" + v.map(val).join(", ") + "]" : "[\n" + v.map((x) => pad + "  " + render(x, ind + 1)).join(",\n") + "\n" + pad + "]";
    if (v && typeof v === "object") {
      const ks = Object.keys(v);
      if (ks.length <= 3 && ks.every((x) => typeof v[x] !== "object" || v[x] === null))
        return "{" + ks.map((x) => `<span class="k">"${x}"</span>: ${val(v[x])}`).join(", ") + "}";
      return "{\n" + ks.map((x) => `${pad}  <span class="k">"${x}"</span>: ${render(v[x], ind + 1, x)}`).join(",\n") + "\n" + pad + "}";
    }
    return val(v);
  }
  const seen = new Set(store.get(key("file-seen"), []));
  pre.innerHTML = "{\n" + parts.map((p, i) => `<span class="blk" data-part="${p.id}">` + p.keys.filter((k) => k in doc)
    .map((k) => `  <span class="k">"${k}"</span>: ${render(doc[k], 1, k)}`).join(",\n") + (i < parts.length - 1 ? "," : "") + "</span>").join("") + "}";
  parts.forEach((p) => {
    const c = document.createElement("button");
    c.type = "button"; c.className = "sel chip" + (seen.has(p.id) ? " seen" : ""); c.textContent = p.name;
    c.setAttribute("role", "tab"); c.dataset.part = p.id;
    c.addEventListener("click", () => select(p.id));
    chips.append(c);
  });
  pre.querySelectorAll(".blk").forEach((b) => b.addEventListener("click", () => select(b.dataset.part)));
  function select(id) {
    const p = parts.find((x) => x.id === id);
    seen.add(id); store.set(key("file-seen"), [...seen]); sound.tick();
    chips.querySelectorAll(".chip").forEach((c) => { c.classList.toggle("on", c.dataset.part === id); c.classList.toggle("seen", seen.has(c.dataset.part)); });
    pre.querySelectorAll(".blk").forEach((b) => b.classList.toggle("on", b.dataset.part === id));
    const blk = pre.querySelector(`.blk[data-part="${id}"]`);
    pre.scrollTo({ top: blk.offsetTop - 20, behavior: reducedMotion ? "auto" : "smooth" });
    explain.innerHTML = `<h4>${p.name}</h4>${p.text}`;
    if (parts.every((x) => seen.has(x.id))) complete(task);
  }
  explain.innerHTML = `<h4>Click a part</h4><p>Use the buttons above or click inside the file.</p>`;
}

/* A practice terminal. `script` lists the commands this chapter teaches, in order, with the output they really
   printed; anything else gets an honest answer. */
const SUBS = ["data", "new", "run", "report", "impurities", "budget", "optimize", "decide", "study", "waste", "import-fispact",
  "import-flux", "export-openmc", "export-mcnp", "export-source", "export-openmc-mesh", "export-r2s", "validate", "doctor",
  "inspect", "clearance", "mesh", "reverse", "unfold", "assimilate", "twin", "maintenance", "surrogate", "eval-spread", "help"];
const lev = (a, b) => {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++)
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
};
const norm = (s) => s.trim().replace(/\s+/g, " ");

export function terminal({ screen, cue, folder, files, version, script, onDone }) {
  const st = store.get(key("term"), { cwd: "~", made: [] });
  const dir = { ...Object.fromEntries(files.map((f) => [f, f.endsWith("/") ? "dir" : "file"])) };
  st.made.forEach((f) => (dir[f] = "file"));
  let busy = false, lastExit = 0;
  const hist = []; let hi = 0;
  const line = document.createElement("div"); line.className = "term-input";
  line.innerHTML = `<span class="prompt"></span><input type="text" aria-label="Type a command" autocomplete="off" autocapitalize="off" spellcheck="false">`;
  screen.append(line);
  const input = line.querySelector("input"), prompt = line.querySelector(".prompt");
  const promptText = () => `you@laptop:${st.cwd}$`;
  const print = (html, cls = "") => { const d = document.createElement("div"); if (cls) d.className = cls; d.innerHTML = html; screen.insertBefore(d, line); screen.scrollTop = screen.scrollHeight; return d; };
  const save = () => store.set(key("term"), st);
  const inside = () => st.cwd !== "~";
  prompt.textContent = promptText();
  screen.addEventListener("click", () => input.focus({ preventScroll: true }));
  print(`<span class="dim">Practice terminal: type a command and press Enter, or click the suggestion below. Type <b>help</b> for what it understands.
ACTINV and its nuclear data are already installed here, as at the end of chapter 1.</span>`);

  async function exec(raw) {
    const text = norm(raw), words = text.split(" ").filter(Boolean);
    if (!words.length) return 0;
    const [cmd, ...args] = words;
    if (cmd === "help") { print(`<span class="hint">This practice terminal understands ls, cd, pwd, cat, clear, echo $?,
actinv --version and the commands this chapter suggests. On your computer every command works the same way.</span>`); return 0; }
    if (cmd === "clear") { [...screen.children].forEach((c) => c !== line && c.remove()); return 0; }
    if (cmd === "pwd") { print(inside() ? `/home/you/${folder}` : "/home/you"); return 0; }
    if (cmd === "echo") { print(args.join(" ") === "$?" ? String(lastExit) : esc(args.join(" "))); return 0; }
    if (cmd === "ls" || cmd === "dir") {
      if (!inside()) { print(`<span style="color:#9bb7ff">${folder}/</span>`); print(`<span class="hint">↳ This chapter's files are in ${folder}. Go in with: cd ${folder}</span>`); return 0; }
      print(Object.keys(dir).sort().map((n) => dir[n] === "dir" ? `<span style="color:#9bb7ff">${esc(n.replace(/\/$/, ""))}/</span>` : esc(n)).join("   ")); return 0;
    }
    if (cmd === "cd") {
      const to = (args[0] || "~").replace(/\/$/, "");
      if (to === "~" || to === "..") { st.cwd = "~"; save(); return 0; }
      if (to === folder && !inside()) { st.cwd = "~/" + folder; save(); return 0; }
      if (to === ".") return 0;
      print(`cd: ${esc(to)}: No such file or directory`, "err"); return 1;
    }
    if (cmd === "cat" || cmd === "less" || cmd === "type") {
      if (!inside() || !(args[0] in dir)) { print(`${cmd}: ${esc(args[0] || "")}: No such file or directory`, "err"); return 1; }
      print(`<span class="dim">(${esc(args[0])} is long; the file explorer above shows it part by part)</span>`); return 0;
    }
    if (cmd === "python" || cmd === "python3") { print(`<span class="hint">↳ The practice terminal doesn't run Python. The Python tab shows it.</span>`); return 0; }
    // a scripted command?
    const entry = script.find((e) => norm(e.cmd) === text || (e.accept && e.accept(text)));
    if (entry) {
      if (!inside()) { print(`<span class="hint">↳ You're in your home folder. This chapter's files are in ${folder}: cd ${folder}</span>`); return 2; }
      const missing = (entry.needs || []).find((f) => !(f in dir));
      if (missing) {
        print(`cannot read ${esc(missing)}: No such file or directory (os error 2)`, "err");
        const maker = script.find((e) => (e.makes || []).includes(missing));
        if (maker) print(`<span class="hint">↳ ${esc(missing)} is made by an earlier command: ${esc(maker.cmd)}</span>`);
        return 2;
      }
      busy = true;
      if (entry.ms) { const spin = print(`<span class="dim">${esc(entry.working || "working…")}</span>`); for (let i = 0; i < entry.ms / 120; i++) { sound.key(); await wait(120); } spin.remove(); }
      for (const l of [].concat(entry.out)) print(esc(l));
      if (entry.hint) print(`<span class="hint">↳ ${entry.hint}</span>`);
      (entry.makes || []).forEach((f) => { dir[f] = "file"; if (!st.made.includes(f)) st.made.push(f); });
      save(); busy = false;
      if (entry.task) complete(entry.task);
      if (entry.then) entry.then();
      return 0;
    }
    if (cmd !== "actinv") {
      print(`${esc(cmd)}: command not found`, "err");
      if (lev(cmd, "actinv") <= 2) print(`<span class="hint">↳ Did you mean actinv?</span>`);
      return 127;
    }
    const sub = args[0];
    if (sub === "--version" || sub === "-V") { print(esc(version)); return 0; }
    if (!sub || sub === "help" || sub === "--help") { print(`<span class="hint">↳ 'actinv' alone lists every command (chapter 1 shows it). This chapter's commands are in the suggestions below.</span>`); return 0; }
    if (!SUBS.includes(sub)) {
      const best = SUBS.map((s) => [lev(sub, s), s]).sort((a, b) => a[0] - b[0])[0];
      print(`actinv: unknown command '${esc(sub)}'.${best[0] <= 2 ? ` Did you mean '${best[1]}'?` : ""} See 'actinv help'.`, "err"); return 2;
    }
    const near = script.find((e) => e.cmd.split(" ")[1] === sub);
    print(`<span class="hint">↳ That's a real command, but the practice terminal only has this chapter's recorded runs.${near ? ` Try: ${esc(near.cmd)}` : ""}</span>`);
    return 2;
  }
  async function submit(v) {
    if (busy) return;
    print(`<span class="prompt">${esc(promptText())}</span> <span class="cmd">${esc(v)}</span>`);
    if (v.trim()) { hist.push(v); hi = hist.length; }
    lastExit = await exec(v);
    prompt.textContent = promptText();
    updateCue();
  }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const v = input.value; input.value = ""; submit(v); }
    else if (e.key === "ArrowUp") { if (hi > 0) input.value = hist[--hi]; e.preventDefault(); }
    else if (e.key === "ArrowDown") { input.value = hi < hist.length - 1 ? hist[++hi] : (hi = hist.length, ""); e.preventDefault(); }
    else if (e.key.length === 1) sound.key();
  });
  function next() {
    if (!inside()) return `cd ${folder}`;
    const e = script.find((x) => x.task && !isDone(x.task)) || script[script.length - 1];
    return e.cmd;
  }
  function updateCue() {
    const n = next();
    cue.innerHTML = `Next, try: <code tabindex="0" role="button">${esc(n)}</code> <span>· click it to type it for you</span>`;
    const c = cue.querySelector("code");
    const go = async () => {
      if (busy) return;
      input.value = "";
      for (const ch of n) { input.value += ch; sound.key(); await wait(16); }
      await wait(120); input.value = ""; submit(n);
    };
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    if (onDone && script.filter((x) => x.task).every((x) => isDone(x.task))) onDone();
  }
  updateCue();
}

export { sound, complete, isDone, store, toast, reducedMotion };
