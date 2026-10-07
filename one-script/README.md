# The one script

The code drives; the model reads what it served; the human seals. This is the
skeleton: the seven parts (boot, predict, record, gate, resolve, view,
reverse), `run`, serve, xref, the one hook, and the scripts they lean on.

Moved here from willows-grove (`docs/design/one-script/` at `008c7f9`) under
D2, sealed 2026-10-02: "All is the things should live inside the bot." Its
history stays in willows-grove, and so do the design docs, which are the
reasoning behind every part: the one-script README, `next-pile.md`,
`workflow.md`, `incoming/`, and `../one-box/`.

It sits outside the `willow_bot` package on purpose: it's a proposal, not a
sealed build, so it isn't in the wheel and moving it cut no release.

## What's here

| Path | What it is |
|---|---|
| `onescript/` | The parts, `run`, serve and xref, and the CLI (`__main__.py`) |
| `onescript/tests/` | Its tests |
| `hook.py` | The one hook (PreToolUse): Write asks, everything else denied, Read included |
| `prompt.py` | The served tables, put into each prompt as framed data (UserPromptSubmit), so the model never holds a path |
| `foundation/` | The hooks' tests run on a bare interpreter, without pytest |
| `scripts/scan/` | `script_match.py` (the gate's script index) and the table scans |
| `scripts/flow/` | The view drafts: session maps and their merge |
| `deep_thought.py` | The morning screen |

## The law comes from willows-grove

The one script's law is willows-grove's constitution (its Trace IDs), and its
toolchain pin is willows-grove's CI. willows-grove is found at
`ONESCRIPT_GROVE`, else beside this repo (`../willows-grove`). Without it
there's no law, and every command refuses.

## How the model gets what was served

The model has no tools but Write, and Write asks the human. It doesn't read a
file: at every prompt, `prompt.py` reads what serve wrote (`ONESCRIPT_SERVED`)
and puts it into the turn inside a `<served-data>` frame that says it is data,
not instructions. The model never sees a path, so it never learns that a box
or a file exists (2026-10-07, the operator: "Sounds good").

| What `prompt.py` finds | What the model gets |
|---|---|
| A served document | The document, inside the frame |
| Serve's own `empty` | That, with serve's reason |
| A served file over 64 KiB | `empty`: "narrow the stack". Never truncated |
| No served file, a missing or unreadable one, or one that isn't a served document | `unreachable`, with a reason that names no path |
| An error in `prompt.py` itself | Exit 2: Claude Code blocks the prompt |

On Claude Code, wiring is two hooks (still the operator's to turn on, N6):

```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 one-script/hook.py"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 one-script/prompt.py"}]}]
  }
}
```

Other front ends: whether each can add context at prompt time isn't verified
(the 22-CLI research confirmed Claude Code only). Until one is, the fallback
on file is a path in its instruction file, which tells the model the box is
there; it is not built.

## Running it

```bash
cd one-script
python3 -m onescript checkin            # boot: record, probes, the four gates
python3 -m onescript turn "the bite"    # one turn as the desk
python3 -m onescript checkout           # reverse, then the morning screen

python3 -m pytest -q onescript/tests    # its tests (willows-grove beside this repo)
python3 foundation/run_hook_tests.py    # the hook's tests on any Python
```

The box defaults to `.flow/onescript/` in this repo, which git ignores.
`ruff.toml` here carries willows-grove's lint floor, so the move didn't
change what the code is held to.
