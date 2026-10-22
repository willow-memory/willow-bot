# adapters

Things built on top of `kernel.py`. The kernel is the base, and it runs alone:
delete this folder and it behaves the same, byte for byte.

**The one rule: the arrow points one way.** An adapter uses the kernel; the
kernel never imports, loads or names an adapter. An adapter that writes to a
box writes only through the kernel's own `append` (or its verbs), so the chain
holds; anything that writes around it, `verify` catches.

What an adapter can rely on:

- **the record:** rows, canon, the hash chain from genesis
- **the functions:** `chunks`, `rank`, `look`, `read`, `append`, `verify`,
  `bite_key`, `newest`, `words`, `holds`, `held_by`, `config`, `read_words`,
  held to `kernel_vectors/`
- **the command line:** exit 0 a human said it, 42 not enough to say, 2 refused

What a folder holds: the code, a card (a README: what it's for, which of the
kernel's surface it uses, what it never does), and its own tests, which CI
runs.

| Adapter | What it adds |
|---|---|
| [readings/](readings/) | three hints beside a bite: what isn't there, numbers over time, a date gone by |
