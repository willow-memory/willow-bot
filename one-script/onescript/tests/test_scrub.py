"""scrub.py (flatten-2 F12): the union of what ten copies each took out.

The rule this file holds: **the union may never catch less than any copy did**
("a scrubber may never catch less than any copy did"). Each retired copy's own
positive fixtures are here, copied from its tests (serve, willow-mcp
``secret_scan`` and its journal set, ``nest/secrets``, safe-app-store's
nest-pipeline, ratatosk ``redact``, Grove ``seat_record.scrub``, willow-mcp
``observability`` and ``push_executor``, willow-bot ``ingress.redact_url``),
and the new scrub must take every one out. Every fixture is a fake shape built
from parts; there is no real secret in this file.

The second thing it holds is output: a caller keeps the token it printed
(``[hash withheld]``, ``[REDACTED:<kind>]``, ``[withheld]``,
``[redacted-by-willow-membrane]``, ``<redacted>``), so the pins below are the
exact strings the callers' own suites read.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from onescript import scrub

# -- fake shapes, the ones the copies' tests use -------------------------------

AWS = "AKIA" + "Q" * 16
PROVIDER = "sk-ant-" + "a1B2c3D4" * 4
OPENAI = "sk-" + "a1B2c3D4e5" * 3
GITHUB = "ghp_" + "b" * 36
SLACK = "xoxb-123456789012-abcdefABCDEF"
GOOGLE = "AIza" + "C" * 35
STRIPE = "sk_live_" + "d" * 24
JWT = "eyJhbGciOiJI.eyJzdWIiOiIx.QsWn3kF9aa"
JWT_LONG = ".".join(["eyJ" + "a" * 12, "eyJ" + "b" * 12, "c" * 20])
DISCORD = ".".join(["M" + "q" * 24, "aB3dEf", "z" * 30])
GROQ = "gsk_" + "a" * 40
HF = "hf_" + "b" * 30
CEREBRAS = "csk-" + "c" * 30
XAI = "xai-" + "d" * 30
#: A fine-grained personal access token: `github_pat_`, 22, `_`, 59.
GITHUB_PAT = "github_pat_" + "1A" * 11 + "_" + "b2" * 29 + "c"
PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ==\n"
    "-----END OPENSSH PRIVATE KEY-----"
)
PEM_CUT = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\nabcdEFGH5678"


def gone(text, secret, sets=("credentials",), **kw):
    """Is ``secret`` out of ``text`` after the named sets?"""
    out = scrub.apply(text, sets, **kw)
    return secret not in out, out


# -- 1. one test per copy: every positive fixture is taken out ------------------

#: willow-mcp tests/test_secret_scan.py (the funnel) and ratatosk's
#: tests/test_key_custody.py: shape -> kind the funnel reported.
FUNNEL = [
    (AWS, "aws_access_key_id"),
    (PROVIDER, "provider_api_key"),
    (GITHUB, "github_token"),
    (GITHUB_PAT, "github_pat"),
    (SLACK, "slack_token"),
    (GOOGLE, "google_api_key"),
    (STRIPE, "stripe_key"),
    (JWT, "jwt"),
    ("sk-ant-" + "A" * 40, "provider_api_key"),
]


@pytest.mark.parametrize("secret,kind", FUNNEL)
def test_secret_scan_and_ratatosk_shapes_each_taken_out_with_their_kind(secret, kind):
    found: set = set()
    out = scrub.apply(f"x {secret} y", ("credentials",), found=found)
    assert secret not in out
    assert out == f"x [REDACTED:{kind}] y"
    assert found == {kind}  # the kind, never the value


def test_secret_scan_private_key_block_is_taken_out_whole():
    found: set = set()
    out = scrub.apply(PEM, ("credentials",), found=found)
    assert out == "[REDACTED:private_key]"  # ratatosk reads exactly this
    assert found == {"private_key"}


def test_a_pem_block_cut_by_a_window_is_taken_out_to_the_end():
    # willow-mcp journal: "the -n window cut it off before any END line"
    out = scrub.apply(f"leaked key:\n{PEM_CUT}", ("credentials",))
    assert "MIIEow" not in out and "abcdEFGH" not in out and "PRIVATE KEY" not in out
    # and a header with nothing after it (nest/secrets matched only the header)
    assert "PRIVATE KEY" not in scrub.apply(
        "-----BEGIN PRIVATE KEY-----", ("credentials",)
    )


#: willow-mcp tests/test_unit_status.py: the journal set. (kind, line).
JOURNAL = [
    ("groq_api_key", "GROQ_API_KEY=" + GROQ),
    ("hf_token", "HF_API_KEY=" + HF),
    ("cerebras_api_key", "CEREBRAS_API_KEY=" + CEREBRAS),
    ("xai_api_key", "XAI_API_KEY=" + XAI),
    ("authorization_header", "Authorization: Bearer sekret.token.value"),
    ("authorization_header", "Authorization: ApiKey abcdef123 secret"),
    ("pg_dsn_password", "DSN=postgresql://willow:hunter2@127.0.0.1/willow"),
    ("pg_dsn_password", "DSN=postgresql://willow:p@ss@127.0.0.1/willow"),
    ("password_kv", "password=hunter2plusmore"),
    ("labelled_secret_kv", "NESTOR_SEAL_KEY=" + "ab" * 32),
    ("labelled_secret_kv", "NESTOR_SEAL_KEY: " + "ab" * 32),
    ("labelled_secret_kv", "DB_PASSWORD=" + "s3cr3t!" * 3),
    ("labelled_secret_kv", "PGPASSWORD=" + "s3cr3t!" * 3),
    ("labelled_secret_kv", '{"NESTOR_SEAL_KEY": "' + "ab" * 32 + '"}'),
    ("labelled_secret_kv", "API_KEY=null-9f8e7d6c5b4a"),
    ("labelled_secret_kv", "DB_PASSWORD=vaultpass123secret"),
    ("labelled_secret_kv", 'SECRET_KEY="two words"'),
]
JOURNAL_VALUE = {
    "GROQ_API_KEY=": "gsk_",
    "hunter2": "hunter2",
    "sekret.token.value": "sekret.token.value",
    "abcdef123": "abcdef123",
}


@pytest.mark.parametrize("kind,line", JOURNAL)
def test_journal_set_takes_each_line_out_with_its_kind(kind, line):
    found: set = set()
    out = scrub.apply(line, ("credentials", "journal_kv"), found=found)
    assert kind in found, (out, found)
    value = line.split("=", 1)[-1].split(": ", 1)[-1].strip('"}')
    for frag in (
        "gsk_",
        "hf_b",
        "csk-",
        "xai-",
        "hunter2",
        "sekret.token",
        "abcdef123",
        "p@ss",
        "ab" * 32,
        "s3cr3t",
        "null-9f8e",
        "vaultpass",
        "two words",
    ):
        if frag in line:
            assert frag not in out, (frag, out)
    assert value not in out or value == ""


def test_journal_keeps_what_it_always_kept():
    # Authorization keeps its scheme; a DSN keeps scheme and user; kv keeps the name
    j = ("credentials", "journal_kv")
    assert scrub.apply("Authorization: ApiKey abcdef123 secret", j) == (
        "Authorization: ApiKey [REDACTED:authorization_header]"
    )
    assert scrub.apply("DSN=postgresql://willow:hunter2@127.0.0.1/willow tail", j) == (
        "DSN=postgresql://willow:[REDACTED:pg_dsn_password]@127.0.0.1/willow tail"
    )
    assert scrub.apply("NESTOR_SEAL_KEY: " + "ab" * 32, j) == (
        "NESTOR_SEAL_KEY=[REDACTED:labelled_secret_kv]"
    )
    # a bare header never eats the next line
    two = "Authorization: Bearer\n2026-09-27T10:00:01 next line unrelated"
    assert scrub.apply(two, j) == two


@pytest.mark.parametrize(
    "line",
    [
        "Column(Integer, primary_key=True)",
        "order_by(sort_key=lambda r: r.id)",
        "password=None",
        "OPENAI_API_KEY=env:OPENAI_API_KEY",
        'idempotency_key="req-42"',
        "public_key=ssh-ed25519-AAAA",
    ],
)
def test_journal_still_does_not_over_redact(line):
    assert scrub.apply(line, ("credentials", "journal_kv")) == line


def test_the_funnel_set_has_no_label_heuristics():
    # Loki 0DFFEFA6 B3: the funnel's set is `credentials` alone
    for line in (
        "primary_key=True",
        "password=None",
        "DB_PASSWORD=hunter2",
        "OPENAI_API_KEY=env:OPENAI_API_KEY",
    ):
        assert scrub.apply(line, ("credentials",)) == line
    assert scrub.apply(
        {"id": "9f8c2b10-4e3a-4d21-bb0e-2a1c9d6e7f00"}["id"], ("credentials",)
    ) == ("9f8c2b10-4e3a-4d21-bb0e-2a1c9d6e7f00")
    assert scrub.apply("a" * 40, ("credentials",)) == "a" * 40  # a sha is not a key


#: willow-mcp tests/test_nest_secrets.py and safe-app-store apps/nest-seed
#: test_secrets.py: the Nest's finder (kind, value), bounded form.
def test_nest_finder_fixtures():
    f = lambda t: scrub.find(t, ("credentials_bounded", "assigned_credential"))  # noqa: E731
    assert ("aws_access_key_id", AWS) in f(f"wrap{AWS}wrap")
    assert ("aws_access_key_id", AWS) in f(f"the key is {AWS} ok")
    assert ("github_token", GITHUB) in f(f"prefix{GITHUB}suffix")
    assert ("google_api_key", GOOGLE) in f(f"leading{GOOGLE}trailing")
    junk = "AKIA" + "Q" * 30
    assert ("aws_access_key_id", junk) in f(junk)
    for glued in (
        "AKIAIOSFODNN7EXAMPLEQRST",
        "AKIAIOSFODNN7EXAMPLE0",
        "AKIAIOSFODNN7EXAMPLE" + "MMNNPPQQ==",
    ):
        assert any(
            k == "aws_access_key_id" and v.startswith("AKIAIOSFODNN7EXAMPLE")
            for k, v in f(f"payload: {glued} end")
        ), glued
    assert not any(
        v == AWS + "w" for _, v in f(f"wrap{AWS}wrap")
    )  # stops at the lowercase
    kinds = {k for k, _ in f(f"token is {DISCORD} and {JWT_LONG}")}
    assert {"discord_token", "jwt"} <= kinds
    got = f("password: hunter2_supersecret_value")
    assert got and got[0][0] == "credential"
    assert f('api_key = "REPLACE_your_key_here_xxxx"') == []
    assert f("api_key = 'your-key-here'") == []
    assert f("Her name badge read AKIA, short for Akiako, and nothing else.") == []
    prose = (
        "The secret to a good journal entry is honesty. I left my house key "
        "on the token booth counter again and had to ask the desk clerk for "
        "a spare. Note to self: buy a keyring so this stops happening."
    )
    assert f(prose) == []
    caps = "the IRS sent a letter in ALL CAPS ABOUT AN OVERDUE FORM, and the DMV"
    assert f(caps) == []


def test_nest_redact_by_value_keeps_its_marker():
    text = f"here {DISCORD} ok"
    pairs = scrub.find(text, ("credentials_bounded",))
    assert scrub.replace_found(text, pairs) == "here [REDACTED:discord_token] ok"
    renamed = scrub.replace_found(
        f"wrap{AWS}wrap",
        scrub.find(f"wrap{AWS}wrap"),
        kind_of={"aws_access_key_id": "aws_access_key"}.get,
    )
    assert renamed == "wrap[REDACTED:aws_access_key]wrap"


def test_bounded_form_stops_where_the_old_nest_stopped_but_the_redactors_do_not():
    longer = "ghp_" + "b" * 40
    assert scrub.find(longer, ("credentials_bounded",))[0][1] == "ghp_" + "b" * 36
    assert (
        scrub.apply(longer, ("credentials",)) == "[REDACTED:github_token]"
    )  # the funnel took it whole


# -- 2. the glued-key evasion (the safe-app-store copy lacked the fix) ---------

GLUE = [
    AWS,
    GITHUB,
    GOOGLE,
    SLACK,
    STRIPE,
    OPENAI,
    PROVIDER,
    JWT,
    DISCORD,
    GROQ,
    HF,
    CEREBRAS,
    XAI,
]


@pytest.mark.parametrize("secret", GLUE)
def test_a_key_glued_to_letters_on_either_side_is_still_taken_out(secret):
    for text in (f"wrap{secret}wrap", f"lead{secret}", f"{secret}trail"):
        out = scrub.apply(text, ("credentials",))
        # the shape's own charset may swallow trailing glue; what matters is the key is gone
        assert secret[: len(secret) - 3] not in out, (text, out)
        assert "[REDACTED:" in out


def test_the_old_anchored_shapes_missed_what_this_catches():
    import re

    old_aws = re.compile(
        r"\bAKIA[0-9A-Z]{16}\b"
    )  # safe-app-store nest_pipeline, until F12
    text = f"wrap{AWS}wrap"
    assert old_aws.search(text) is None
    assert AWS not in scrub.apply(text, ("credentials",))
    assert scrub.find(text, ("credentials_bounded",))[0] == ("aws_access_key_id", AWS)


def test_every_credential_shape_has_no_word_boundary_anchor():
    # the structural test of the same rule: no credential pattern starts or ends in \b
    for r in scrub.SETS["credentials"]:
        assert not r.rx.pattern.startswith(r"\b") and not r.rx.pattern.endswith(
            r"\b"
        ), r.kind


# -- 3. serve._scrub: 64-hex -> [hash withheld] ---------------------------------

H64 = "ab" * 32


def test_hash64_set_is_serves_rule():
    assert scrub.apply(
        f"cites {H64} and {H64.upper()}", ("hash64",), marker="[hash withheld]"
    ) == ("cites [hash withheld] and [hash withheld]")
    assert scrub.apply("a" * 63, ("hash64",), marker="[hash withheld]") == "a" * 63
    # a 128-hex is two hashes, as it was
    assert (
        scrub.apply(H64 * 2, ("hash64",), marker="[hash withheld]")
        == "[hash withheld]" * 2
    )


# -- 4. Grove seat_record.scrub: the generic set, every LEAK row -----------------

K32 = hashlib.sha256(b"k").hexdigest()[:32]
K64 = hashlib.sha256(b"k").hexdigest()
K40 = hashlib.sha1(b"k").hexdigest()
SEED64 = hashlib.sha256(b"w4:surface").hexdigest()
B64 = base64.b64encode(hashlib.sha256(b"k").digest()).decode()
B64URL = base64.urlsafe_b64encode(hashlib.sha256(b"k").digest()).decode().rstrip("=")
W = "wager f1 escalate to human · "
LEAKS = {
    "32-hex bare": f"{W}{K32}",
    "64-hex bare": f"{W}{K64}",
    "64-hex seed bare": f"table verdict session s · 3 rows · seed {SEED64} · waiting on your tip seal",
    "40-hex bare": f"{W}{K40}",
    "seed: 64-hex": f"{W}seed: {SEED64}",
    "seed 64-hex, no colon": f"{W}seed {SEED64}",
    "key= base64": f"{W}key={B64}",
    "base64 bare": f"{W}{B64}",
    "base64url bare": f"{W}{B64URL}",
    "HUMAN_KEY=64hex": f"{W}GROVE_SEAT_HUMAN_KEY={K64}",
    "HUMAN_KEY=b64": f"{W}HUMAN_KEY={B64}",
    "key split across fields": f"{W}key · {K32[:16]} · {K32[16:]}",
    "hex split in 20s": f"{W}{K64[:20]} {K64[20:40]} {K64[40:]}",
    "key: with spaces inside": f"{W}key: {K32[:8]} {K32[8:16]} {K32[16:]}",
    "uppercase SEED=": f"{W}SEED={SEED64}",
    "passphrase in words": f"{W}passphrase: correct horse battery staple",
    "7-char key=": f"{W}key=abc1234",
    "secret in parentheses": f"{W}(secret) {K32}",
    "0x hex": f"{W}0x{K32}",
    "hex inside a word": f"{W}id{K32}x",
    "two 32s glued": f"{W}{K32}{K32}",
    "bare 64-hex in a cite bracket not served": f"{W}[{K64}_0]",
}
GROVE_SECRETS = (K32, K64, K40, SEED64, B64, B64URL, "abc1234", "correct horse")


@pytest.mark.parametrize("label", list(LEAKS))
def test_grove_leak_rows_each_taken_out_by_the_generic_set(label):
    out = scrub.apply(LEAKS[label], ("generic",), marker="[withheld]")
    assert [x for x in GROVE_SECRETS if x in LEAKS[label] and x in out] == [], out
    assert "[withheld]" in out
    # and with the credential shapes after it (Grove's order), the same
    both = scrub.apply(LEAKS[label], ("generic", "credentials"), marker="[withheld]")
    assert both == out


def test_grove_keeps_a_record_hash_in_its_place():
    h = "d" * 64
    ok = f"proposal:{h} · tip:12:{h} · serve:{h} · run {h} · commit {'e' * 40} · green on {'f' * 40}"

    def g(t, **kw):
        return scrub.apply(t, ("generic", "credentials"), marker="[withheld]", **kw)

    assert g(ok) == ok
    assert g(f"proposal · x · {h}") != f"proposal · x · {h}"
    assert g(f"x {h}", known=(h,)) == f"x {h}"
    assert g(f"seen {'e' * 40}") != f"seen {'e' * 40}"
    assert g("x ab y", secrets=("ab",)) == "x ab y"
    assert g("x abcd y", secrets=("abcd",)) == "x [withheld] y"
    line = "wager f1 escalate to human · tests/test_a_very_long_function_name_for_the_seat.py"
    assert g(line) == line
    h1, h2 = "a1" * 32, "b2" * 32
    agm = f"table agm_head failing · the ledger is at (4, {h1}), anchored (3, {h2})"
    assert g(agm) == agm
    assert g(f"seed_id {'c3' * 8} · subject {h1} · head {h2}") == (
        f"seed_id {'c3' * 8} · subject {h1} · head {h2}"
    )
    assert g(f"seed {h1}") != f"seed {h1}"


def test_grove_gains_the_credential_shapes_after_its_own_rules():
    line = f"{W}note {GITHUB} done"
    assert (
        scrub.apply(line, ("generic", "credentials"), marker="[withheld]").count(
            "[withheld]"
        )
        == 1
    )
    assert GITHUB not in scrub.apply(
        line, ("generic", "credentials"), marker="[withheld]"
    )
    # a split secret (up to three non-word characters between its characters)
    sec = "correcthorsebatterystaple"
    cut = "correct · horse · battery · staple"
    assert (
        scrub.apply(cut, ("generic",), marker="[withheld]", secrets=(sec,))
        == "[withheld]"
    )


# -- 5. observability, push_executor, ingress -----------------------------------


def test_observability_keys_and_paths():
    for key in (
        "lease_token",
        "vault_path",
        "consent",
        "dsn",
        "Authorization",
        "cookie",
        "api-key",
        "apiKey"[:3] + "_key",
        "passwd",
        "session_id",
        "seed",
        "private_key",
        "credentials",
    ):
        assert scrub.sensitive_key(key), key
    for key in ("filename", "function", "lineno", "app_id"):
        assert not scrub.sensitive_key(key), key
    m = "[redacted-by-willow-membrane]"
    sets = ("secret_path", "credentials")
    assert (
        scrub.apply(
            "no such file: /home/sean/sean-data-vault/secret.md", sets, marker=m
        )
        == m
    )
    assert scrub.apply("/home/x/.willow/vault/k", sets, marker=m) == m
    assert scrub.apply("WILLOW_HOME unset", sets, marker=m) == m
    assert scrub.apply("ordinary frame text", sets, marker=m) == "ordinary frame text"
    # added: a credential shape inside a string that names no path
    assert scrub.apply(f"boom {AWS}", sets, marker=m) == f"boom {m}"


def test_git_trace_marks_are_unchanged():
    s = scrub.apply(
        "Authorization: basic abc123== and ghs_AbC123 and AUTHORIZATION: Bearer zz",
        ("git_trace", "credentials"),
    )
    assert (
        s
        == "Authorization: <redacted> and gh?_<redacted> and AUTHORIZATION: <redacted>"
    )
    assert (
        scrub.apply("x-github-request-id: AB12:34", ("git_trace",))
        == "x-github-request-id: AB12:34"
    )


SECRET_URL = "https://hookuser:hookpass@hooks.example.invalid:8443/webhook?token=QUERYSECRET#frag"


def test_redact_url_is_the_stewards():
    assert scrub.redact_url(SECRET_URL) == "https://hooks.example.invalid:8443/webhook"
    assert scrub.redact_url("") == "" and scrub.redact_url("not a url") == ""
    assert scrub.redact_url("http://[::1]:9000/webhook") == "http://[::1]:9000/webhook"
    assert scrub.redact_url("https://smee.io/AbCdEfGhIj") == "https://smee.io/…"
    assert (
        scrub.redact_url("https://h.example.invalid/ghp_abc/hooks")
        == "https://h.example.invalid/…/hooks"
    )
    assert scrub.redact_url("https://h.example.invalid/github-app/webhook") == (
        "https://h.example.invalid/github-app/webhook"
    )


# -- 6. the apply itself ---------------------------------------------------------


def test_apply_is_ordered_and_passes_non_strings_through():
    assert scrub.apply(5, ("credentials",)) == 5
    assert scrub.apply("", ("credentials",)) == ""
    assert scrub.apply(None, ("credentials",)) is None
    # serve's order: a hash inside a `sk-` run is the hash's, as before F12
    mk = lambda kind: "[hash withheld]" if kind == "hash64" else f"[REDACTED:{kind}]"  # noqa: E731
    assert (
        scrub.apply("sk-" + H64, ("hash64", "credentials"), marker=mk)
        == "sk-[hash withheld]"
    )
    assert (
        scrub.apply("sk-" + H64, ("credentials", "hash64"), marker=mk)
        == "[REDACTED:provider_api_key]"
    )


def test_a_marker_may_be_a_string_or_a_function_of_the_kind():
    assert scrub.apply(f"a {AWS}", ("credentials",), marker="<x>") == "a <x>"
    assert (
        scrub.apply(f"a {AWS}", ("credentials",), marker=lambda k: f"<{k}>")
        == "a <aws_access_key_id>"
    )


def test_found_kinds_reports_kinds_never_values():
    assert scrub.found_kinds(f"{AWS} {GITHUB}", ("credentials",)) == [
        "aws_access_key_id",
        "github_token",
    ]
    assert scrub.found_kinds("plain", ("credentials",)) == []


def test_walk_strings_reaches_every_string_and_bounds_depth():
    seen = scrub.walk_strings({"a": ["x", ("y", {"b": "z"})], "n": 1}, str.upper)
    assert seen == {"a": ["X", ("Y", {"b": "Z"})], "n": 1}  # keys are not payload
    deep: object = "leaf"
    for _ in range(60):
        deep = [deep]
    out = scrub.walk_strings(deep, str.upper)
    assert "LEAF" not in repr(out) and "leaf" in repr(
        out
    )  # past the cap: left as it was
    shallow = scrub.walk_strings([["leaf"]], str.upper)
    assert shallow == [["LEAF"]]


def test_every_set_named_in_the_docstring_exists():
    for name in (
        "hash64",
        "credentials",
        "credentials_bounded",
        "journal_kv",
        "assigned_credential",
        "generic",
        "secret_path",
        "git_trace",
    ):
        assert name in scrub.SETS and name in scrub.__doc__


# -- pii (IDEAS 119): each copy's own positives, homestead's negatives ---------
# Every value is a test value from its copy's suite (555 numbers, example.com,
# the card networks' test numbers); none is anyone's.

PII_POSITIVES = [
    # homestead keep/advise.py, tests/test_invariants_advise.py POSITIVES
    ("ssn", "school form needs SSN 123-45-6789 before Friday"),
    ("credit_card", "retainer paid on card 4111 1111 1111 1111"),
    ("dob", "child DOB: 2018-03-04, enrolled since"),
    ("bank", "support deposits to routing 021000021 account 123456789"),
    ("phone", "reach the other parent at 415-555-1234 after 6"),
    ("email", "correspondence went to jordan.rivera@example.com twice"),
    ("ein", "the business EIN 12-3456789 is on the filing"),
    # willow-2.0 core/pii_detect.py, tests/test_pii_detect.py
    ("ssn", "my ssn is 123-45-6789"),
    ("ssn", "ssn: 123456789"),
    ("credit_card", "card: 4532015112830366"),
    ("email", "email me at test@example.com please"),
    ("phone", "call 555-867-5309"),
    ("phone", "reach me at (555) 867-5309"),
    # willow-2.0 apps/nest/pipeline/scrub.py PII_PATTERNS
    ("bank", "account number: 4417"),
    ("case_no", "see claim no: WC-2026-0042"),
    ("case_no", "Case No. 2024-CV-1234 was filed"),
    ("case_no", "case #A12 is set"),
    ("dob", "born 03/04/2018"),
    # corpus-lens egress_shapes.py, tests/test_egress_shapes.py
    ("home_path", "read from /home/jordan/.claude/projects"),
    ("home_path", "/Users/jordan/logs"),
    ("home_path", r"C:\Users\jordan\logs"),
    # hermes-agent agent/redact.py
    ("phone", "signal +15558675309 tonight"),
    ("discord_user", "ping <@!123456789012345678> about it"),
    # the baseline kinds no copy had (documentation and test values only:
    # RFC 5737/3849 addresses, the IBAN registry's example, test wallets)
    ("ip", "[client 10.0.0.68] File does not exist"),
    ("ip", "the node at 192.0.2.44 answered"),
    ("ip", "v6 peer 2001:db8:85a3::8a2e:370:7334 joined"),
    ("itin", "ITIN on file: 912-70-1234"),
    ("iban", "wire to GB82 WEST 1234 5698 7654 32 today"),
    ("passport", "passport no. X1234567 expires in May"),
    ("driver_license", "driver's license: D1234567"),
    ("medical_record", "MRN: 00123456 at intake"),
    ("mac", "the printer is 00:1A:2B:3C:4D:5E on the LAN"),
    ("wallet", "tip jar 0x52908400098527886E0F7030069857D2E4169EE7"),
    ("location", "meet at 45.5231, -122.6765 at noon"),
]

#: homestead's NEGATIVES: what a PII pattern must not fire on.
PII_NEGATIVES = [
    "1420 Maple 87501",
    "88 Ridgeline 90210",
    "347 F.3d 1120",
    "Hearing Aug 15, 2026 at 8:30am, Dept 3",
    "2026-09-15 08:30 · Dept 4",
    "Entry 14 — response filed 2026-08-01",
    "Case FL-2026-00123, Superior Court",
    # 2026-10-31: "no" read as "No." and the next word as the number
    "the claim no longer open was closed",
    "a case number pending review",
    "mailing ZIP is 87501-1234",
    "Tue/Thu 3-7pm, alternating weekends",
    "took that into account when scheduling 3 visits",
    "the minor is 8 years old",
    "Dept 4, Superior Court of California",
    "card: 4532015112830360",  # willow-2.0: a failed Luhn check is no card
    # the baseline kinds' own negatives
    "listening on 127.0.0.1:8080 and 0.0.0.0",
    "Apache/1.3.33 (Unix) PHP/4.3.10 configured",
    "the call at 12:30:45 ran long",
    "std::vector<int> and a::b in C++",
    "commit 3f786850e387550fdab836ed7e6dc881de23001b landed",
    "GB82 WEST 1234 5698 7654 33",  # an IBAN that fails mod 97
    "ITINERARY for 912 travellers",
]


@pytest.mark.parametrize("kind,text", PII_POSITIVES)
def test_the_pii_set_catches_every_copys_positives(kind, text):
    assert kind in scrub.found_kinds(text, ("pii",))
    out = scrub.withhold(text, 0)
    assert f"[withheld: {kind}, L{scrub.RUNGS[kind]}]" in out


@pytest.mark.parametrize("text", PII_NEGATIVES)
def test_the_pii_set_leaves_homesteads_negatives_alone(text):
    assert scrub.found_kinds(text, ("pii",)) == []


def test_a_labelled_kind_runs_first_so_a_bank_number_is_not_also_an_ssn():
    got = scrub.withhold("routing 021000021 account 123456789", 0)
    assert got.count("[withheld: bank, L4]") == 2 and "ssn" not in got


def test_the_ceiling_says_what_is_allowed_where():
    text = "SSN 123-45-6789, DOB: 2018-03-04, mail jordan@example.com"
    found: set = set()
    model = scrub.withhold(text, 2, found=found)  # bound for a model
    assert "123-45" not in model and "2018-03-04" not in model
    assert "jordan@" not in model and found == {"ssn", "dob", "email"}
    screen = scrub.withhold(text, 4)  # the person's own screen
    assert "[withheld: ssn, L5]" in screen
    assert "DOB: 2018-03-04" in screen and "jordan@example.com" in screen
    assert scrub.withhold(text, 5) == text
    assert set(scrub.RUNGS) - {"name"} == set(scrub.kinds_of(("pii",)))


def test_a_served_chunk_carries_no_pii_over_the_serve_ceiling():
    from onescript import chunk

    text = "Case no: FL-2 · SSN 123-45-6789 · write to jordan@example.com"
    got = chunk._scrub({"rows": [{"text": text}]})["rows"][0]["text"]
    assert chunk.SERVE_CEILING == 2 and "Case no: FL-2" in got  # L2 passes
    assert "[withheld: ssn, L5]" in got and "[withheld: email, L3]" in got
    assert "6789" not in got and "jordan@" not in got


# -- typed_password: a password a person wrote ---------------------------------

TYPED = [
    ('$password = "password";  // DO NOT CHANGE', "$password = [REDACTED:password];"),
    ("(user: cwcftp pw: widgets1998)", "pw: [REDACTED:password])"),
    ("PASSWD=hunter2 ./run", "PASSWD=[REDACTED:password] ./run"),
    ("pwd: 'cornflower blue'", "pwd: [REDACTED:password]"),
]


@pytest.mark.parametrize("text,kept", TYPED)
def test_a_typed_password_loses_its_value_and_keeps_its_label(text, kept):
    out = scrub.apply(text, ("typed_password",))
    assert kept in out
    assert scrub.found_kinds(text, ("typed_password",)) == ["password"]


@pytest.mark.parametrize(
    "text",
    [
        "return ($u == $username && $p == $password);",
        "global $username, $password;",
        "send a password reset link",
        "password=None",
        "password: env:DB_PASSWORD",
        "pass the salt: please",
        "password = your-password-here",
        "PGPASSWORD=$DB_PW psql",
    ],
)
def test_a_password_word_with_no_value_is_left_alone(text):
    assert scrub.apply(text, ("typed_password",)) == text


def test_serve_takes_the_typed_password_and_the_ip():
    from onescript import chunk

    got = chunk._scrub("ftp (user: cwcftp pw: widgets1998) from 10.0.0.68")
    assert "widgets1998" not in got and "pw: [REDACTED:password]" in got
    assert "[withheld: ip, L3]" in got


# -- names: a list the person gives, never a pattern ---------------------------
#: The demos' cast (Nestor's shoebox and Big Jim, safe-app-store's acorns, the
#: Forge's first bite, the Grove's June): made-up people, used as the fixture.
CAST = ["Jose Aguirre", "Nieves Aguirre", "Hazel Acorn", "Rosalind Ffoulkes",
        "June Akiyama", "Big Jim", "Nieves"]  # fmt: skip


def test_a_given_name_is_withheld_whole_and_in_any_case():
    text = "Nieves Aguirre met BIG  JIM; Nieves paid. Big Jimmy did not."
    got = scrub.withhold(text, 2, names=CAST)
    assert got.count("[withheld: name, L3]") == 3
    assert "Big Jimmy" in got  # a longer word is not the name
    assert "Aguirre" not in got  # the full name goes before the first name


def test_without_names_no_name_is_found_and_the_ceiling_still_rules():
    text = "Rosalind Ffoulkes wants a tool for her rally photos"
    assert scrub.withhold(text, 2) == text
    assert scrub.withhold(text, 3, names=CAST) == text  # names are L3
    assert scrub.names_rule([" ", ""]) is None


def test_a_fine_grained_github_token_is_found_at_its_length_and_taken_out_glued():
    """2026-10-31: `github_pat_` is not a `gh?_` shape, and no rule saw one."""
    assert len(GITHUB_PAT) == len("github_pat_") + 22 + 1 + 59
    assert scrub.find(f"x{GITHUB_PAT}TRAIL", ("credentials_bounded",)) == [
        ("github_pat", GITHUB_PAT)
    ]
    ok, out = gone(f"prefix{GITHUB_PAT}suffix", GITHUB_PAT)
    assert ok and "github_pat_" not in out
