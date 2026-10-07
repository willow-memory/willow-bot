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
| A scope whose prompt text would pass 9,000 characters (Claude Code hands the model a file path past 10,000) | `empty`: "narrow the stack". Never truncated |
| No served file, a missing or unreadable one, or one that isn't a served document | `unreachable`, with a reason that names no path |
| An error in `prompt.py` itself | Exit 2: Claude Code blocks the prompt |

## The seat: where the model runs

The hooks aren't wired into your own Claude Code: managed settings reach every
session on a machine, and every seat but this one needs its tools. The model
gets its own machine instead, a container (`seat/`), and the managed settings
live only there (2026-10-07, the operator: "I agree with you recomendations.").

| Piece | What it holds |
|---|---|
| `seat/Dockerfile` | Python and Node from their official images, Claude Code pinned, no package manager run |
| `seat/managed-settings.json` | Both hooks; `allowManagedHooksOnly` and `allowManagedPermissionRulesOnly`; a deny list of every tool but Write, plus `mcp__*`; `ONESCRIPT_SERVED` |
| `seat/doors.json` | The sha256 of each door (`hook.py`, `prompt.py`, the settings). `python3 onescript/doors.py pin --repo .` rewrites it after a door changes. |
| `onescript/doors.py` | Before Claude Code starts, the container's entrypoint checks every installed door against its pin, its owner (root) and its mode. One open door and the seat doesn't start. At check-in, the same file checks the repo's doors against the pins, and a mismatch is a hard close. |

Two doors, because Claude Code hooks fail open on anything but exit 2. If a
hook can't start or times out, the deny list still holds. If the deny list
misses a tool, the hook denies it, since anything not on its policy is denied.
Write is on neither, so the hook's ask still reaches you.

```bash
cd one-script
docker build -f seat/Dockerfile -t onescript-seat .
docker run --rm -it -v "$SERVED_DIR":/served:ro -v "$WORK_DIR":/work \
    -e ANTHROPIC_API_KEY onescript-seat
```

Serve writes `served.json` into `$SERVED_DIR` on your box; the seat sees only
that directory, read-only, and `/work` for what the model proposes. The box
itself never enters the container.

CI builds the seat and checks what doesn't need a model: the doors are shut at
start, the seat's user can't write them, the hook answers each tool, and a
swapped door stops the seat.

**Not yet checked, because it needs a model run:** that the settings' `env`
reaches the hooks (the docs imply it), that each name on the deny list is a
tool Claude Code still has, and what `ask` does in `dontAsk` mode. The first
run with a key should try each tool, Write, and a prompt, before the seat is
used for anything.

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
