"""Parity-tested byte copies of one-script modules that the installed package needs.

``willow_bot`` ships as a wheel WITHOUT ``one-script/``, so it must not import the
one script (flatten-2 gate 6, ``willow-bot:wheel``). Operator ruling 2026-10-21,
"carry copies": an installed package carries copies, and a test holds each copy
to the one script's bytes. Nothing here is edited by hand.

  ``clock.py``  = ``one-script/onescript/clock.py``  (``seconds_z``, F8)
  ``scrub.py``  = ``one-script/onescript/scrub.py``  (``redact_url``, F12)

The parity tests are ``tests/test_carried_parity.py``; they fail, never skip,
when the one script is not beside. This follows willow-mcp's ``scrub_local``.
"""
