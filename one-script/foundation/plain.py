"""The cases file, read back as plain data only. Stdlib, Python 3.9+.

`gen.py` writes the cases as a pickle, on purpose: lone surrogates, big ints
and odd floats come back exactly as made, which JSON can't promise. But a
plain `pickle.load` runs whatever a file names, so a `cases.pkl` someone else
put here could run code. This reader names no class or function at all:
dicts, lists, tuples, sets, strings, bytes, numbers, True, False and None
load; anything that needs a class or function is refused.
"""

import pickle


class _Plain(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError(
            f"refused: the cases file names {module}.{name}; only plain data loads"
        )


def load(path):
    with open(path, "rb") as f:
        return _Plain(f).load()
