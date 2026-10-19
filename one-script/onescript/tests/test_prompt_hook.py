"""Every prompt is a bite (willows-grove one-script/workflow.md §2c): the
UserPromptSubmit hook looks the prompt up in the box and serves what it finds
into the turn through prompt.py's frame. Each guard is tested by the thing it
forbids; the wiring is tested as two halves (Nestor decision 0225): the
settings invoke the hook, and the hook works."""

# ruff: noqa: F811  (the fixtures `box` and `cfg` are imported by name for pytest)
from __future__ import annotations

import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api  # noqa: E402
from onescript.tests.test_api import cfg, rows  # noqa: E402,F401
from onescript.tests.test_store import BITE, box  # noqa: E402,F401

BOT = Path(__file__).resolve().parents[3]  # willow-bot
HOOK = BOT / "one-script" / "scripts" / "prompt-hook"


def served(text: str) -> dict:
    """The served document inside prompt.py's frame."""
    body = text.split("<served-data>\n", 1)[1].split("\n</served-data>", 1)[0]
    return json.loads(body)


class TestTheBite:
    def test_a_prompt_serves_its_chunks_untrusted_and_unsealed(self, box):  # noqa: F811
        res = api.prompt(box["cfg"], BITE)
        doc = res["doc"]
        assert doc["state"] == "populated"
        assert doc["seal"] == "unsealed: this session"
        assert doc["tables"] and all(t["trust"] == "untrusted" for t in doc["tables"])
        assert doc["tables"][0]["rows"][0]["what"] == "chunk"
        assert any(
            "ad breaks" in t["rows"][0]["text"] for t in doc["tables"] if t["rows"]
        )

    def test_the_record_keeps_the_bites_hash_never_its_words(self, box):  # noqa: F811
        api.prompt(box["cfg"], BITE, argv=("prompt",))
        text = (box["cfg"].box / "record.jsonl").read_text()
        assert BITE not in text
        last = rows(box["cfg"])[-1]
        assert last["kind"] == "lookup"
        assert last["bite_hash"] == api.record.h256(BITE)

    def test_ids_are_keyed_never_the_plain_chunk_hash(self, box):  # noqa: F811
        doc = api.prompt(box["cfg"], BITE)["doc"]
        hashes = {h["chunk"] for h in api.lookup(box["cfg"], BITE)["hits"]}
        assert not {t["id"] for t in doc["tables"]} & hashes

    def test_no_words_is_empty_and_looks_nothing_up(self, box):  # noqa: F811
        before = len(rows(box["cfg"]))
        doc = api.prompt(box["cfg"], "   ")["doc"]
        assert (doc["state"], doc["tables"]) == ("empty", [])
        assert doc["why"] == "the prompt has no words to look up"
        assert "lookup" not in [r["kind"] for r in rows(box["cfg"])[before:]]

    def test_a_bite_that_finds_nothing_is_empty_and_says_so(self, box):  # noqa: F811
        doc = api.prompt(box["cfg"], "zzqx wvvk")["doc"]
        assert doc["state"] == "empty" and doc["why"]

    def test_over_the_cap_is_empty_never_truncated(self, box):  # noqa: F811
        doc = api.prompt(box["cfg"], BITE, max_chars=50)["doc"]
        assert (doc["state"], doc["tables"]) == ("empty", [])
        assert "narrow the stack" in doc["why"]

    def test_a_closed_box_is_unreachable_and_says_why(self, cfg):  # noqa: F811
        doc = api.prompt(cfg, BITE)["doc"]
        assert doc["state"] == "unreachable" and doc["why"]
        assert "seal" not in doc
        last = rows(cfg)[-1]
        assert (last["kind"], last["at"]) == ("refused", "prompt")  # said on the record

    def test_a_checked_out_box_serves_nothing(self, box):  # noqa: F811
        assert api.checkout(box["cfg"])["code"] == 0
        before = len(rows(box["cfg"]))
        doc = api.prompt(box["cfg"], BITE)["doc"]
        assert (doc["state"], doc["tables"]) == ("unreachable", [])
        assert "lookup" not in [r["kind"] for r in rows(box["cfg"])[before:]]


class TestTheHook:
    def run(self, cfg, stdin, monkeypatch, capsys, extra=()):  # noqa: F811
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        argv = [
            "--box",
            str(cfg.box),
            "--keys",
            str(cfg.keys),
            "--no-tests",
            "prompt",
            *extra,
        ]
        monkeypatch.setattr(cli, "GROVE", cfg.grove)
        monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
        monkeypatch.setattr(cli, "CI", cfg.ci)
        code = cli.main(argv)
        return code, capsys.readouterr().out

    def test_the_hook_frames_the_bite_into_the_turn(self, box, monkeypatch, capsys):  # noqa: F811
        code, out = self.run(
            box["cfg"], json.dumps({"prompt": BITE}), monkeypatch, capsys
        )
        assert code == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert served(hso["additionalContext"])["state"] == "populated"

    def test_the_prompt_never_reaches_argv(self, box, monkeypatch, capsys):  # noqa: F811
        self.run(box["cfg"], json.dumps({"prompt": BITE}), monkeypatch, capsys)
        inv = [r for r in rows(box["cfg"]) if r["kind"] == "invocation"][-1]
        assert BITE not in json.dumps(inv)

    @pytest.mark.parametrize("stdin", ["not json", ""])
    def test_a_bad_event_blocks_the_prompt(self, box, stdin, monkeypatch, capsys):  # noqa: F811
        code, out = self.run(box["cfg"], stdin, monkeypatch, capsys)
        assert (code, out) == (2, "")

    def test_a_cap_that_isnt_positive_blocks(self, box, monkeypatch, capsys):  # noqa: F811
        code, _ = self.run(
            box["cfg"],
            json.dumps({"prompt": BITE}),
            monkeypatch,
            capsys,
            extra=("--max-chars", "0"),
        )
        assert code == 2


class TestTheWiring:
    """Nestor 0225: implemented is not wired. Both halves, kept apart."""

    def test_the_settings_invoke_the_hook_on_every_prompt(self):
        settings = json.loads((BOT / ".claude" / "settings.json").read_text())
        hooks = settings["hooks"]["UserPromptSubmit"]
        commands = [h["command"] for group in hooks for h in group["hooks"]]
        assert any(c.endswith("one-script/scripts/prompt-hook") for c in commands)

    def test_the_hook_script_is_there_and_runs(self):
        assert HOOK.exists()
        assert os.stat(HOOK).st_mode & stat.S_IXUSR


class TestTheEmbedderRung:
    """Rung 3 in the hook: fused when it loads, said when it doesn't."""

    def cfg_with(self, box, where):  # noqa: F811
        from dataclasses import replace

        return replace(box["cfg"], embed=where)

    def test_no_embedder_set_is_words_and_said(self, box):  # noqa: F811
        doc = api.prompt(box["cfg"], BITE)["doc"]
        assert doc["rung"] == {"used": "words", "why": "no embedder set"}

    def test_an_embedder_that_wont_load_is_words_and_said(
        self,
        box,
        monkeypatch,
        tmp_path,  # noqa: F811
    ):
        monkeypatch.setattr(api.pile_mod, "wordllama_embed", lambda cache: None)
        doc = api.prompt(self.cfg_with(box, tmp_path), BITE)["doc"]
        assert doc["state"] == "populated"
        assert (
            doc["rung"]["used"] == "words" and "can't be loaded" in doc["rung"]["why"]
        )

    def test_a_loaded_embedder_fuses_over_the_pile_candidate_shape(
        self,
        box,
        monkeypatch,
        tmp_path,  # noqa: F811
    ):
        seen = {}

        def fake(cache):
            def order(bite, cands):
                seen["cands"] = cands
                ws = [w for w, _ in cands]
                return ws[1:] + ws[:1]  # the words' second first: RRF promotes it

            return order

        monkeypatch.setattr(api.pile_mod, "wordllama_embed", fake)
        monkeypatch.setattr(api, "PROMPT_TOP", 1)
        words = api.prompt(box["cfg"], BITE)["doc"]
        fused = api.prompt(self.cfg_with(box, tmp_path), BITE)["doc"]
        assert fused["rung"] == {"used": "fused", "why": ""}
        assert len(seen["cands"]) >= 2
        assert all(len(w) == 4 and len(w[3]) == 64 for w, _ in seen["cands"])
        assert all(isinstance(t, str) and t for _, t in seen["cands"])
        assert len(seen["cands"]) <= api.EMBED_POOL
        second = seen["cands"][1][0]
        where = f"{second[0]}:{second[1]}:{second[2]}"
        assert fused["tables"][0]["group"]["where"] == where
        assert words["tables"][0]["group"]["where"] != where

    def test_the_lookup_row_keeps_seed_top_whatever_the_pool(
        self,
        box,
        monkeypatch,
        tmp_path,  # noqa: F811
    ):
        monkeypatch.setattr(api, "SEED_TOP", 1)
        monkeypatch.setattr(
            api.pile_mod,
            "wordllama_embed",
            lambda c: lambda b, cands: [w for w, _ in cands],
        )
        api.prompt(self.cfg_with(box, tmp_path), BITE)
        assert len(rows(box["cfg"])[-1]["top"]) == 1

    def test_the_cli_takes_the_rung_from_flag_or_env(
        self,
        box,
        monkeypatch,
        capsys,
        tmp_path,  # noqa: F811
    ):
        got = []
        monkeypatch.setattr(
            api,
            "prompt",
            lambda c, *a, **k: (
                got.append(c.embed)
                or {"doc": {"state": "empty", "why": "", "return": "", "tables": []}}
            ),
        )
        hook = TestTheHook()
        monkeypatch.setenv("ONESCRIPT_EMBED", str(tmp_path / "env"))
        hook.run(box["cfg"], json.dumps({"prompt": BITE}), monkeypatch, capsys)
        hook.run(
            box["cfg"],
            json.dumps({"prompt": BITE}),
            monkeypatch,
            capsys,
            extra=("--embed", str(tmp_path / "flag")),
        )
        monkeypatch.delenv("ONESCRIPT_EMBED")
        hook.run(box["cfg"], json.dumps({"prompt": BITE}), monkeypatch, capsys)
        assert got == [tmp_path / "env", tmp_path / "flag", None]


class TestBundledTokenizers:
    """WordLlama 0.4 ships its tokenizer where its own lookup doesn't look."""

    def fake(self, tmp_path):
        import types

        pkg = tmp_path / "fakewl"
        (pkg / "tokenizers").mkdir(parents=True)
        (pkg / "tokenizers" / "l2_supercat_tokenizer_config.json").write_text("{}")
        (pkg / "mod.py").write_text("")
        mod = types.ModuleType("fakewl.mod")
        mod.__file__ = str(pkg / "mod.py")
        sys.modules["fakewl.mod"] = mod

        class WL:
            __module__ = "fakewl.mod"

            @staticmethod
            def get_file_path(kind, cache):
                return Path(cache) / "tokenizers"

        return WL

    def test_the_bundled_config_is_copied_into_the_cache(self, tmp_path):
        from onescript import pile

        pile._bundled_tokenizers(self.fake(tmp_path), tmp_path / "cache")
        assert (
            tmp_path / "cache/tokenizers/l2_supercat_tokenizer_config.json"
        ).read_text() == "{}"

    def test_a_config_already_there_is_left_alone(self, tmp_path):
        from onescript import pile

        mine = tmp_path / "cache/tokenizers/l2_supercat_tokenizer_config.json"
        mine.parent.mkdir(parents=True)
        mine.write_text('{"mine": 1}')
        pile._bundled_tokenizers(self.fake(tmp_path), tmp_path / "cache")
        assert mine.read_text() == '{"mine": 1}'
