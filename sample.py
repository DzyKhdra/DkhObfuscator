"""DKH sample file: exercises every obfuscator layer.
Run:  python sample.py            -> prints EXPECT block
Obf:  python dkh3_12.py (pick sample.py) -> obf-sample.py prints the same.
"""
import json
import math
import os
from collections import Counter
from functools import lru_cache
from os.path import join as joinpath


def banner(title="DKH"):
    line = "=" * 24
    print(line)
    print("** " + title + " **")
    print(line)


def deco(fn):
    return fn


@deco
def area(r):
    match r:
        case 0:
            return 0
        case n if n > 100:
            return -1
        case _:
            return 3 * r * r + math.floor(math.sqrt(r))


@lru_cache(maxsize=None)
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


class Vault:
    def __init__(self, tag="vlt", times=2):
        self.tag = tag
        self.times = times

    @property
    def label(self):
        return f"[{self.tag}x{self.times}]"

    def run(self, *items, **opts):
        total = 0
        for it in items:
            total += it
        try:
            total = total // opts.get("div", 1)
        except ZeroDivisionError:
            total = -1
        finally:
            total += 0
        return (self.label, total * self.times)


def pick(xs):
    first, *rest = xs
    return first, rest


def gen(n):
    for i in range(n):
        yield i * i


def describe(n):
    tag = "circle"
    size = n * 2 + 1
    label = tag if n > 0 else "none"
    return label + "-" + str(size)


def main():
    banner()
    print(area(16), area(0))
    print(pick([1, 2, 3]))
    print(fib(10))
    v = Vault()
    print(v.run(1, 2, 3, div=2))
    print(v.label)
    print(describe(5))
    print(joinpath("a", "b"))
    c = Counter([1, 1, 2, 3, 3, 3])
    print(sorted(c.items()))
    print([x * 2 for x in range(4) if x % 2 == 0])
    print({k: k * k for k in range(3)})
    print(sum(gen(4)))
    print(json.dumps({"ok": True}))
    print("CWD:", os.path.basename(os.getcwd()) or ".")
    dbl = lambda x: x * 2
    print(dbl(21))
    print("done")


if __name__ == "__main__":
    main()
