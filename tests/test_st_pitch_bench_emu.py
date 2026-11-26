#!/usr/bin/env python3
"""The DIAGNOSTIC adapter of core 2.9.0-mt1-bench (branch feat/pitch-bench), in Unicorn.

With a host API 4 profile buffer registered, core_native_pitch times the hook
with the profiler's timer (0xfc07000c) into the words past the buffer's 36
(layout in mods/core-st/native.s). The timer here counts one tick per
instruction run, so the numbers are exact. It checks that:
  - the stock instruction and the registers/flags/stack are replayed as in the
    release adapter in every mode (the release adapter: test_st_native_pitch_emu.py);
  - the block period between two entries (the denominator) is summed (64-bit),
    maxed and counted;
  - mode 0 (patched path) accumulates sum/max/n into 168..176, with the bus
    call inside the timed span and the profile buffer withheld from that call,
    so a subscriber is not charged for profiling; mode 1 (stock instruction)
    into 180..188;
  - the subscriber still edits the pitches in place in mode 0 and is never
    called in mode 1 or without interest.

    python tests/test_st_pitch_bench_emu.py --core <core build folder>
"""
import argparse, json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_st_native_pitch_emu import Pitch, SITE, RESUME, PITCHES, STATUS, SAVED, pack   # noqa: E402
from unicorn import UC_HOOK_CODE   # noqa: E402
from unicorn.m68k_const import *    # noqa: E402,F403

TIMER, BUF = 0xfc07000c, 0x80008000
PREV, PLO, PMAX, PN, MODE, PHI, HSUM, HMAX, HN, SSUM, SMAX, SN = range(144, 192, 4)


class Bench(Pitch):
    def __init__(self, folder):
        super().__init__(folder)
        self.u.mem_map(0xfc070000, 0x1000)
        self.ticks = 1000
        self.u.hook_add(UC_HOOK_CODE, self.tick)
        self.profile = self.sym['core:core_native_profile_buffer']

    def tick(self, u, pc, n, _):
        self.ticks += 1
        u.mem_write(TIMER, struct.pack('>I', self.ticks & 0xffffffff))

    def buffer(self, mode):
        self.u.mem_write(BUF, b'\0' * 192)
        self.w(BUF + MODE, mode)
        self.w(self.profile, BUF)

    def word(self, off):
        return struct.unpack('>I', bytes(self.u.mem_read(BUF + off, 4)))[0]

    def idle(self, n):                 # time MAIN spends elsewhere between two blocks
        self.ticks += n


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('--core', type=Path, required=True)
    args = a.parse_args()
    m = Bench(args.core)
    stock = [(48 + t) << 16 for t in range(12)]

    # mode 0, subscriber: edits kept, the bus call is timed, period measured from the second entry
    m.buffer(0)
    for status in (0, 2):
        m.calls = []
        assert m.run(1, status) == [v + 1000 * (t + 1) for t, v in enumerate(stock)]
        assert m.calls == [(22, PITCHES, 12)] and m.word(MODE) == 0
        assert m.u.mem_read(m.profile, 4) == struct.pack('>I', BUF), 'profile buffer not restored'
        m.idle(500)
    assert m.word(HN) == 2 and m.word(SN) == 0 and m.word(PN) == 1
    patched_sub = m.word(HSUM) // 2
    assert m.word(HMAX) >= patched_sub > 20, (m.word(HSUM), m.word(HMAX))
    period = m.word(PLO)
    assert m.word(PHI) == 0 and period == m.word(PMAX) and 500 < period < 2000, period

    # mode 0, no interest: the patched path with nothing subscribed is shorter than with one
    m.buffer(0)
    for status in (0, 2):
        assert m.run(0, status) == stock and not m.calls
    assert m.word(HN) == 2 and m.word(HSUM) // 2 < patched_sub, (m.word(HSUM), patched_sub)
    patched_none = m.word(HSUM) // 2

    # mode 1: the stock instruction alone, never a subscriber, timed into the other words
    m.buffer(1)
    for status in (0, 2):
        assert m.run(1, status) == stock and not m.calls
    assert m.word(SN) == 2 and m.word(HN) == 0 and m.word(SSUM) // 2 < patched_none
    stock_ticks = m.word(SSUM) // 2

    # the period sum carries into the high word and the maximum follows the longest block
    m.buffer(0)
    m.w(BUF + PLO, 0xfffffff0)
    m.run(0, 2)
    m.idle(100)
    m.run(0, 2)
    assert m.word(PHI) == 1 and m.word(PN) == 1
    m.idle(7000)
    m.run(0, 2)
    assert m.word(PMAX) > 7000 and m.word(PN) == 2

    print(json.dumps(dict(ok=True, ticks_per_instruction=1, stock=stock_ticks, patched_no_subscriber=patched_none,
                          patched_subscriber_stand_in=patched_sub, device_access=False)))


if __name__ == '__main__':
    main()
