# readaloud: Fable 5 vs Opus rendition — side-by-side review & merge plan

Two agents built "readaloud" from the byte-identical spec
(`readaloud-build-spec (2).html`). This compares them **as delivered**:

- **Opus** = commit `96624ab` (first commit of this repo; later commits on `main`
  are post-delivery fixes by the orchestrator, *not* Opus's work).
- **Fable** = the uncommitted working tree at `.worktrees/f5-rendition`
  (branch `f5-rendition`; its committed state is a bare uv-init baseline).

All file:line references for Opus are against `git show 96624ab:<path>`;
for Fable against the worktree files.

---

## Verdict summary

| # | Dimension | Winner | One-line reason |
|---|---|---|---|
| 1 | Correctness | **Fable** | Shares the stdin race, but its AX fallback works, and its new bugs are edge-case-grade vs Opus's user-audible ones (dead blockquotes, snake_case mangling, hangable kokoro stop) |
| 2 | Spec §04 contract fidelity | **Fable** (narrow) | Identical keys/defaults (the YAML files are byte-identical); Fable's validation is stricter (enums + numeric ranges + explicit-path errors) |
| 3 | Pipeline quality | **Fable** | Blockquotes survive, identifiers survive, abbreviation-aware splitting + 500-char stop cap, real emoji names; Opus wins only on code-fence fidelity and list continuations |
| 4 | Tests | **Fable** | 76 pass vs 47 pass; covers CLI `main()`, blockquotes, sentence-split edges, parametrized enum validation; one hygiene flaw (reads real user config) |
| 5 | Engine design | **Fable** (probe sub-point to Opus) | Fable's stop paths and stdin text-passing are sounder; Opus's rate probe uses the machine-verified `--data-format=LEI16@22050 -o` + stdlib `wave` rather than parsing `afinfo` prose |
| 6 | install.sh / lua robustness / README / self-report honesty | **Fable** | Non-fatal model download, correct AX fallback, honest self-report; Opus's README is marginally richer but its "everything verified" claim was false |

**Overall: Fable is the stronger rendition as delivered.** But the merge
recommendation (below) is to keep **`main`** as the base, because it already
carries the live-debugged fixes — including `[[slnc]]` coalescing, which
neither rendition has and which addresses the worst UX problem (per-chunk
Siri startup gaps) — and port Fable's improvements file-by-file.

---

## 1. Correctness — the three ground-truth bugs

### a) hs.task stdin race — **both have it** (tie, both broken as delivered)

| | Location | Code |
|---|---|---|
| Opus | `hammerspoon/readaloud.lua:103-105` | `readerTask:start()` → `setInput(text)` → `closeInput()` |
| Fable | `hammerspoon/readaloud.lua:80-83` | `task:start()` → `setInput(text)` → `closeInput()` |

Identical ordering; `closeInput()` closes the pipe before the queued write
flushes → CLI reads empty stdin → silent no-op. This was the "no reading
happens" bug. `main`'s fix (setInput **before** start, no `closeInput()` at
all) must survive any merge. Fable's self-report flagged exactly this call
order as needing a live test; Opus's claimed it verified.

### b) AX fallback inversion — **Opus only** (Fable wins)

- Opus `readaloud.lua:148-149`: `local el = hs.uielement.focusedElement…; if not el then` — fetches `AXSelectedText` only when **no** focused element exists. Since a focused element almost always exists, the fallback is dead code.
- Fable `readaloud.lua:117-126, 138`: when ⌘C capture fails, unconditionally walks `systemWideElement() → AXFocusedUIElement → AXSelectedText`, wrapped in `pcall`. Correct.

### c) Per-chunk `say` process churn — **both have it** (tie; spec-induced)

Both run one `/usr/bin/say` per speech-script chunk with Python sleeps between
(Opus `say_engine.py` `_speak_chunk`/`speak`; Fable `say_engine.py:121-154`),
which is what spec §3.4 literally prescribes ("one say invocation per
speech-script chunk … do not rely on embedded [[slnc]]/[[rate]]"). Neither
questioned the consequence: ~1-2 s Siri-voice startup *per sentence*. Sentences
are chunks in both, so a paragraph reads with multi-second gaps. `main`'s
post-delivery `[[slnc]]` coalescing (one `say` process, pauses as embedded
silences — verified working on this machine) exists in neither rendition and
must survive the merge.

### New bugs found in this review

**Opus:**

| Bug | Evidence | Severity |
|---|---|---|
| Blockquotes destroyed before parse: `_PROMPT_MARKER` strips any leading `"> "` as a prompt, so `parse.py`'s blockquote branch is dead code | `clean.py:52`; verified: `clean('> a quoted line\n> that continues')` → `'a quoted line that continues'` → parses as `paragraph` | Medium — markdown fidelity feature silently gone |
| Snake_case identifiers mangled by italic stripping: `_{1,3}` emphasis regex | `parse.py:195`; verified: `my_var_name` → `myvarname` (spoken as one garbled word) | Medium — this tool's primary diet is terminal/code-adjacent text |
| Chunk text passed as argv: a chunk starting with `-` is parsed by `say` as flags → chunk silently skipped; also argv length limits on huge chunks | `say_engine.py:178` `args + [chunk.text]` | Low-medium |
| Kokoro stop can stall: consumer blocks in `audio_q.get()` with no timeout; producer can wedge on `audio_q.put()` after stop (queue full, never drained) | `kokoro_engine.py:128, 116` | Medium for kokoro users |
| Empty-clipboard case never restored: if the clipboard was empty before ⌘C, `restore()` does nothing, leaking the captured selection into the clipboard | `readaloud.lua` `restore()` (`if saved ~= nil`) | Low |
| Explicit `--config /missing/path` silently ignored → defaults used with no error | `config.py` `load_config` (only `if cfg_path.exists()`) | Low |
| `SayEngine.__init__` can raise `RuntimeError` outside `main()`'s try → raw traceback instead of a clean error (and pidfile would not be acquired yet, so benign there) | `__main__.py` (`_make_engine` called outside try) | Low |
| `\r` survives `strip_ansi` (`_C0_CTRL` class skips `\x0d`); CRLF input keeps stray carriage returns mid-pipeline | `clean.py` `_C0_CTRL` | Low |

**Fable:**

| Bug | Evidence | Severity |
|---|---|---|
| Bare `readaloud` (or `--print-script`) on a TTY hangs: stdin is read unconditionally, no `isatty()` guard (Opus has one, `__main__.py:88`) | `__main__.py:109` | Low (Hammerspoon always pipes) |
| ALL-CAPS header over-trigger: fires mid-paragraph (no surrounding-blank-line gate as in Opus); `ERROR CODE 42` inside a paragraph becomes a slowed-down "header" with 900 ms of pauses | `parse.py:124-131`; verified empirically | Low-medium |
| A single pipe-delimited line becomes a one-row "table" (Opus requires ≥ 2 rows) | `parse.py:133-151`; verified | Low |
| No list-item continuation gathering: a wrapped list item's second physical line becomes a separate paragraph (Opus gathers continuations) — mostly masked by clean's rejoin | `parse.py:153-160` | Low |
| Symbol-only lines *inside* code fences are dropped and fence content is glyph-scrubbed before fence tracking → "code block, N lines" undercounts; `mode: read` reads altered code (Opus passes fence content through verbatim via sentinel markers) | `clean.py:251, 256-263` | Low |
| Window hotkey while reading stops **and immediately reads the window** (double action); Opus stops and returns | `readaloud.lua:184` | Cosmetic |
| `bind()` assumes the key is the last token of the hotkey array (Opus classifies tokens by name) | `readaloud.lua:200-206` | Cosmetic |

**Shared (see "Same blind spots" below):** the pgid kill in both luas is a
no-op, both have first-run rate-probe latency, etc.

**Verdict: Fable.** It dodges one of the three ground-truth bugs outright, and
its incremental bugs are edge-case-grade where Opus's degrade everyday output
(blockquotes, identifiers) and stop reliability (kokoro).

---

## 2. Spec §04 contract fidelity

- `config.example.yaml`: **byte-identical between the two renditions** and
  matches §04 exactly (both `diff` clean against each other). `DEFAULTS` dicts
  in both `config.py` files carry the same keys/values. (`main` has since
  changed `base_wpm` to 240 — a deliberate post-delivery calibration that must
  survive.)
- Enum validation:
  - Opus `config.py` `_ENUMS` — validates the six enum keys, but **skips
    validation when the resolved value is `None`** and accepts a missing
    explicit `--config` path silently.
  - Fable `config.py:61-115` — validates the same six enums **plus** numeric
    sanity (`base_wpm`/`speed`/`rate_factor`/caps must be positive numbers,
    pauses non-negative, bools excluded), and an explicit `--config` path that
    doesn't exist is a `ConfigError`.
- One Opus nicety Fable lacks: `default_config_path()` honors
  `XDG_CONFIG_HOME` (`config.py`); Fable hardcodes `~/.config` (`config.py:16`)
  — consistent with its install.sh, but less correct.

**Verdict: Fable**, on validation quality (the criterion named in the spec
review ask). The contract surface itself is a tie.

---

## 3. Pipeline quality (clean / parse / script)

| Aspect | Opus | Fable | Better |
|---|---|---|---|
| ANSI stripping | 5 separate regexes + C0 class (missing `\r`) | single verbose regex incl. DCS/SOS/PM/APC, CRLF normalized (`clean.py:22-32, 249`) | Fable |
| Prompt markers | strips `❯ ➜ ▶ → $ >` — kills blockquotes (`clean.py:52`) | strips `❯ % $` only; `>` deliberately preserved with documented rationale (`clean.py:51-55`) | **Fable** |
| Code fences through clean | passed verbatim via `\x00CODE\x00` sentinels — accurate line counts, `read` mode faithful | fence content scrubbed + symbol-only lines dropped (`clean.py:251, 261`) | **Opus** |
| Rejoin heuristic | requires full-ish **AND** lowercase/connective next — spec-literal | `full_ish OR next_starts_lower` (`clean.py:141-150`) — more aggressive than spec, more effective on narrow TUI copies, more false-join risk | wash |
| "Modal" width | docstring says modal, **returns `max()`** (`clean.py:139-147`) | docstring says modal, **returns `max()`** (`clean.py:93-103`) | tie — shared blind spot |
| URL handling | `https?://` only | also bare `www.`, uses `urlparse` (`clean.py:168, 198-210`) | Fable |
| Emoji `name` mode | generic `" emoji "` token (no name table) | real `unicodedata.name()` ("rocket"), tested (`clean.py:227-238`) | **Fable** |
| Inline emphasis | `_{1,3}` regex mangles `my_var_name` → `myvarname` (`parse.py:195`) | italic regex guards word-internal underscores (`parse.py:26`); handles `![img](url)` too | **Fable** |
| ALL-CAPS headers | gated: must not continue a paragraph and must be followed by a blank line | fires mid-paragraph (`parse.py:124-131`) | Opus |
| Lists | continuation lines gathered; items sentence-split | no continuation gathering; items are single chunks | Opus |
| Tables | row 0 assumed to be header; requires ≥ 2 pipe rows | header detected via separator row — headerless tables read as plain rows (`parse.py:133-151`); but 1 pipe row = table | slight Fable |
| Sentence split | single regex, no abbreviation handling, no length cap — `Mr. Smith` splits; a 3 000-char sentence is one chunk (stop latency) | abbreviation list + decimal protection + **500-char hard cap** with comma/semicolon sub-splitting (`script.py:18-83`) — directly serves "stop feels instant" | **Fable** |
| Code `read` mode | one chunk for the whole block (stop latency) | joined + sentence-split (`script.py:120-129`) | Fable |

**Verdict: Fable**, with two genuinely better Opus ideas to port back
(verbatim fence transport; list continuations + ALL-CAPS gating).

---

## 4. Tests — both suites run by this review

| | Opus (`/tmp` worktree of `96624ab`) | Fable (`.worktrees/f5-rendition`) |
|---|---|---|
| Result | **47 passed** in 0.07 s | **76 passed** in 0.06 s |
| Files | conftest + clean, config, parse_script, say_engine | conftest + clean, config, parse, script, say_engine, **cli** |

Quality, not just count:

- **Both** test behavior (output of pure functions), not implementation
  details, with realistic Claude-Code-TUI fixtures (Opus `conftest.py`
  `TUI_PASTE` with ANSI+box+spinner+hard-wrap; Fable `test_clean.py`
  `TUI_FIXTURE` similar). Opus has one nice full-pipeline test
  (`test_end_to_end_tui_paste_pipeline`).
- **Fable extras that matter:** CLI-level tests driving `main()` with stdin
  patched (`test_cli.py` — would catch arg-parsing/JSON regressions);
  blockquote pipeline tests (Opus has none — exactly the area where Opus has a
  bug; the missing test and the bug are the same blind spot); parametrized
  enum-validation tests + numeric validation tests; sentence-splitter edge
  cases (abbreviations, decimals, hard cap); `rate_works=False` command
  construction.
- **Fable hygiene flaw:** `test_cli.py` and
  `test_config.py::test_missing_file_yields_defaults` call
  `load_config(None)`, which reads the **real** `~/.config/readaloud/config.yaml`
  if present — assertions like `pauses.paragraph_ms == 350` are
  machine-state-dependent. Opus avoids this only by never testing `main()`.
- **Both** leave the engines' process/threading behavior untested (only argv
  construction is covered) and neither tests the rate probe.

**Verdict: Fable** — broader and better-aimed, modulo the config-isolation flaw.

---

## 5. Engine design

### say rate probe (the asked sub-question)

- **Opus** (`say_engine.py:96-103`): renders two clips at 120/320 wpm with
  `--data-format=LEI16@22050 -o x.wav` and measures duration with the stdlib
  `wave` module. That render invocation is the one **verified working on this
  machine**, and duration comes from frame math, not text parsing. Caches
  per-voice in `$XDG_STATE_HOME/readaloud/say_rate_ok`. Weaknesses: if both
  renders fail (durations 0.0) it **caches success anyway**, and on probe
  failure it merely warns — `-r` keeps being passed (harmless but not "proceed
  at base rate").
- **Fable** (`say_engine.py:48-98`): renders to plain `.aiff` at 110/360 and
  parses `afinfo`'s human-readable `estimated duration:` line — two fragile
  dependencies (default `say -o` container behavior for the active voice, and
  `afinfo` output format). But the **result is actually plumbed**: probe
  failure → `rate_works=False` → `-r` omitted (`build_say_command`, tested),
  and an exception during the probe explicitly assumes `-r` works rather than
  caching a bogus negative.

**Probe measurement: Opus sounder. Probe consequence-handling: Fable sounder.**
The merged ideal is Opus's render+wave measurement feeding Fable's
`rate_works` plumbing. (Note `main` already extended the Opus probe with the
slnc probe; keep that.)

### say execution

Fable pipes chunk text via `stdin=PIPE` (`say_engine.py:133-148`) — immune to
leading-`-` misparse and argv limits; thread-safe `_proc` handoff under a lock
with a `threading.Event` for stop. Opus passes text as argv
(`say_engine.py:178`) and uses a plain bool flag + unguarded `self._proc`.
**Fable.**

### kokoro producer/consumer + stop paths

Same architecture in both (producer thread, `Queue(maxsize=4)`, sentinel,
playback after first chunk). Differences are all in the stop paths:

| Stop concern | Opus | Fable |
|---|---|---|
| In-flight audio | waits for current ≤ 250 ms write block | `stream.abort()` — immediate (`kokoro_engine.py:51-59`) |
| Consumer blocked in `get()` at stop | blocks until producer's next put (`kokoro_engine.py:128`) | `get(timeout=0.2)` re-checks the flag (`:111-115`) |
| Producer blocked in `put()` after stop | can wedge (daemon-thread mitigated) | queue drained in `finally` (`:131-136`) |

Opus has two things Fable lacks: a defensive resample guard if kokoro ever
emits ≠ 24 kHz, and a `synth_to_wav()` headless-verification helper. Fable
clamps speed to [0.5, 2.0]. **Fable** on the criterion that matters (stop is
the product's core gesture).

---

## 6. install.sh, lua robustness, README, self-report honesty

- **install.sh:** near-identical structure (uv sync → models → config → lua
  wiring → checklist; both deviate identically from the spec's
  `~/.local/share/readaloud/venv` + pip in favor of uv + repo `.venv`).
  Differences: Opus's model download runs bare `curl` under `set -euo pipefail`
  (`install.sh:96,103`) — a network failure **aborts the whole install**;
  Fable wraps it, removes the `.part`, and continues with a warning since
  `engine: say` needs no models (`install.sh:77-102`). Opus honors
  `XDG_DATA_HOME`/`XDG_CONFIG_HOME`; Fable hardcodes. Net: **Fable** (the
  failure mode matters more than the env vars).
- **lua robustness:** Fable — correct AX fallback, empty-clipboard
  clear-on-restore (`readaloud.lua:107-111`), pcall-wrapped AX walks. Opus —
  better hotkey parsing (`splitHotkey` classifies tokens vs Fable's
  "key-is-last" assumption), richer window walk (AXStaticText/AXTitle, depth
  40 vs 24). Both share the no-op pgid kill and the stdin race. Net: **Fable**
  (its advantages are on the primary path).
- **README:** both accurate and well-structured; Opus's is longer (189 vs 154
  lines) with a complete config-key table and a clearer Siri-loophole
  explanation. Marginal **Opus**.
- **Self-report honesty:** Opus claimed everything verified — yet shipped the
  stdin race that made the tool a complete no-op, plus a dead AX fallback;
  the claim was false in the most user-visible way possible. Fable shipped the
  same stdin call order but **explicitly flagged it as a live-test risk**,
  which is exactly where the bug was. **Fable, decisively.** (Neither could
  actually run Hammerspoon; the difference is calibration, not capability.)

---

## Same blind spots (model-independent failure modes)

1. **The hs.task stdin ordering.** Both independently wrote
   `start() → setInput() → closeInput()`. The Hammerspoon docs don't say
   `closeInput()` discards queued-but-unwritten input; nothing short of a live
   run reveals it. Lesson: any "glue code that only manifests at runtime in a
   GUI host" is untestable for an agent and should be flagged, not asserted —
   Fable's report did this, Opus's didn't, but **both wrote the same wrong code**.
2. **Per-chunk `say` churn.** Both followed §3.4 to the letter and neither
   reasoned about neural-voice process-startup cost (1-2 s × every sentence).
   Notably, the real fix (`[[slnc]]` coalescing on `main`) *contradicts* the
   spec's "do not rely on [[slnc]]" — the spec itself encoded the wrong bet,
   and neither model challenged it. Spec-following ≠ correct.
3. **"Modal line length" → `max()`.** Both docstrings promise the spec's modal
   width; both implementations return `max(lengths)` (Opus `clean.py:139-147`,
   Fable `clean.py:93-103` — Fable's even keeps a comment rationalizing it).
   Identical quiet substitution of an easier statistic.
4. **No-op process-group kill.** Both luas run `kill -TERM -<pid>`, but the CLI
   never calls `setsid()`/`setpgid()`, so the pid is not a pgid and the group
   kill silently fails in both. Harmless only because both CLIs forward SIGTERM
   to the `say` child via the signal handler.
5. **Identical spec-shaped artifacts.** `config.example.yaml` and
   `pyproject.toml` are byte-identical across renditions, and both swapped the
   spec's pip/venv install for uv — strong convergence on both the contract
   and the same deviation.
6. **First-run probe latency.** Both insert a ~2-4 s `say`-render sanity probe
   at the start of the first read (cached afterwards); neither moved it off the
   hot path (e.g. into install.sh).
7. **Engines untested where it hurts.** Both test suites stop at argv
   construction; process lifecycle, signals, and the probe are untested in both.

---

## Merge plan

**Base: keep `main` (the fixed Opus lineage).** Rationale: `main` already
carries the four live-debugged, machine-verified fixes — (1) stdin queued
before `start()` with no `closeInput()`, (2) AX fallback un-inverted,
(3) `[[slnc]]` coalescing in the say engine (the churn fix; exists in
*neither* rendition), (4) `base_wpm: 240` — plus diagnostic logging and
`hs.ipc` debug helpers. Rebasing onto the Fable tree would mean re-porting all
of that, including ~130 lines of slnc machinery, into an engine it wasn't
written for. Porting Fable's improvements the other way is file-scoped and
test-backed. **All four `main` fixes are non-negotiable survivors.**

Ordered cherry-picks from `.worktrees/f5-rendition` (highest value first):

1. **`readaloud/script.py` — sentence splitter.** Port Fable's
   `split_sentences` wholesale (`script.py:18-83`): abbreviation set,
   decimal protection, 500-char hard cap with comma/semicolon sub-splitting.
   Replaces Opus's `_SENT_SPLIT`. Bring `tests/test_script.py`'s splitter
   tests with it. (Directly serves "stop feels instant"; complements the slnc
   work.)
2. **`readaloud/clean.py` — three targeted edits** (do *not* take Fable's file
   wholesale; it scrubs fence content):
   a. Remove `>` (and `▶ → ➜` if desired, but at minimum `>`) from
      `_PROMPT_MARKER` (`clean.py:52`) so blockquotes reach the parser —
      adopt Fable's `❯ % $`-only rule and its rationale comment.
   b. Adopt Fable's `unicodedata.name()`-based `emoji: name` mode
      (`f5 clean.py:227-238`).
   c. Normalize `\r\n`/`\r` early (f5 `clean.py:249`) and/or add `\x0d` to
      `_C0_CTRL`.
   Keep Opus's verbatim code-fence transport (sentinel markers) — it is the
   better half of this file.
3. **`readaloud/parse.py` — inline-markup regexes.** Replace Opus's
   `_BOLD_ITALIC` (`parse.py:195`) with Fable's split bold/italic regexes,
   especially the underscore-guarded italic (`f5 parse.py:24-29`), and add the
   image regex. Keep Opus's list-continuation gathering, ALL-CAPS gating, and
   ≥2-row table rule (these are the spots where Opus is *better* — do not port
   Fable's versions).
4. **`readaloud/engines/say_engine.py` — stdin text passing.** Pipe chunk text
   via `stdin=PIPE` as in f5 `say_engine.py:133-148` instead of argv
   (`opus :178`). Verify compatibility with `main`'s slnc-coalesced chunks
   (text gets long — stdin is *more* robust there, which is the point).
   Optionally adopt the `rate_works` plumb-through (omit `-r` when the probe
   fails) and stop caching probe "success" when both durations were 0.0.
5. **`readaloud/engines/kokoro_engine.py` — stop paths.** Port the three Fable
   mechanisms: `stream.abort()` in `stop()` (f5 `:51-59`), consumer
   `q.get(timeout=0.2)` loop (`:111-115`), queue drain in `finally`
   (`:131-136`). Keep Opus's resample guard and `synth_to_wav` helper.
6. **`readaloud/config.py` — validation.** Port Fable's numeric validation and
   the "explicit `--config` path missing is an error" rule
   (f5 `config.py:101-115, 139-140`), plus its parametrized tests. Keep Opus's
   `XDG_CONFIG_HOME` handling.
7. **`hammerspoon/readaloud.lua` — clipboard edge.** Port Fable's
   empty-clipboard restore branch (f5 `:107-111`: clear the pasteboard if it
   was empty before ⌘C and the copy changed it). Keep `main`'s capture/start
   logic untouched; keep Opus's `splitHotkey`.
8. **`install.sh` — non-fatal model download.** Adopt Fable's guarded
   `download_model` with `.part` cleanup and the "say still works" warning
   (f5 `install.sh:77-102`) in place of bare `curl` under `set -e`.
9. **`readaloud/__main__.py` — small pickups.** Add Fable's `SIGHUP` handler
   and its engine-construction try/except (exit 3, pidfile released). Keep
   Opus's `isatty()` guard (do not port Fable's unconditional stdin read).
10. **`tests/` — port with isolation fix.** Bring Fable's `test_cli.py`
    (patched to pass `--config <tmpfile>` so tests stop reading the user's real
    config), its blockquote parse/pipeline tests, and the rejoin-mode unit
    tests. After cherry-pick #2a, Opus's suite needs a new blockquote test —
    Fable's `test_blockquote_merged` is the template.

Post-merge acceptance: re-run §06 of the spec, with special attention to
"blockquote text is read" (new), "stop within ~200 ms during a long paragraph"
(splitter cap + slnc interplay), and a `kokoro` stop mid-synthesis.

---

*Review artifacts: Opus suite run in a temp worktree of `96624ab`
(47 passed); Fable suite run in `.worktrees/f5-rendition` (76 passed); all
empirical bug claims (blockquote destruction, snake_case mangling, ALL-CAPS
mid-paragraph, single-row table) reproduced with one-liners against the
respective trees on 2026-06-11.*
