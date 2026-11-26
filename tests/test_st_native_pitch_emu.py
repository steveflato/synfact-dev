#!/usr/bin/env python3
"""ST_NATIVE_PITCH (host API 21) on the built core, in Unicorn.

Runs MAIN from the patched site 400a4bb8 (the jump to core_native_pitch)
through the core's real resident dispatcher until MAIN resumes at 400a4bbe,
with a subscriber stand-in in the native list. It checks, with and without
interest in PITCH:
  - the subscriber gets op 22 and args {80004688, 12}, and its in-place edits
    of the 12 pitches are what MAIN reads on;
  - without interest it is not called and the pitches are untouched;
  - d0 holds the stock load of 43bdd4c8, and MAIN's beq.s at 400a4bbe, run
    on the flags the adapter leaves, branches as stock would, for a zero and
    a nonzero word;
  - d1/a0/a1 and the callee-saved registers d2-d7/a2-a6 come back unchanged,
    and the stack pointer is balanced.

    python tests/test_st_native_pitch_emu.py --core <core build folder>
"""
import argparse, json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_st_safe_boot_emu import Core, SP   # noqa: E402
from unicorn import UcError, UC_HOOK_CODE    # noqa: E402
from unicorn.m68k_const import *              # noqa: E402,F403

SITE, RESUME, PITCHES, STATUS = 0x400a4bb8, 0x400a4bbe, 0x80004688, 0x43bdd4c8
LIST, CALLBACK = 0x44000000, 0x44010000
SAVED = [UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3, UC_M68K_REG_D4, UC_M68K_REG_D5, UC_M68K_REG_D6,
         UC_M68K_REG_D7, UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A2, UC_M68K_REG_A3, UC_M68K_REG_A4,
         UC_M68K_REG_A5, UC_M68K_REG_A6]
pack = lambda *v: struct.pack('>%dI' % len(v), *v)


class Pitch(Core):
    def __init__(self, folder):
        super().__init__(folder)
        self.u.mem_map(0x80000000, 0x10000)
        self.u.mem_write(CALLBACK, bytes.fromhex('70004e75'))     # moveq #0,d0; rts: an observer
        self.u.hook_add(UC_HOOK_CODE, self.callback, begin=CALLBACK, end=CALLBACK)
        self.calls = []
        self.stop_at = None
        # Unicorn can miss emu_start's `until` on a block chained from an earlier run, so stop by hook
        self.u.hook_add(UC_HOOK_CODE, self.stop, begin=RESUME, end=RESUME)
        self.w(self.sym['core:core_native_list'], LIST)
        self.u.mem_write(LIST, pack(CALLBACK, 0))

    def callback(self, u, pc, n, _):          # int f(op, args, result): rewrite in place (it returns 0)
        op, args, result = self.args(3)
        ptr, count = struct.unpack('>2I', bytes(u.mem_read(args, 8)))
        self.calls.append((op, ptr, count))
        p = list(struct.unpack('>12i', bytes(u.mem_read(ptr, 48))))
        u.mem_write(ptr, struct.pack('>12i', *[v + 1000 * (t + 1) for t, v in enumerate(p)]))

    def stop(self, u, pc, n, _):
        if self.stop_at == pc:
            u.emu_stop()

    def run(self, interest, status):
        u = self.u
        self.calls = []
        u.mem_write(self.sym['core:core_native_interest'] + 22, bytes([interest]))
        u.mem_write(PITCHES, struct.pack('>12i', *[(48 + t) << 16 for t in range(12)]))
        self.w(STATUS, status)
        regs = {r: 0x11110000 + i for i, r in enumerate(SAVED)}
        for r, v in regs.items():
            u.reg_write(r, v)
        u.reg_write(UC_M68K_REG_D0, 0xdeadbeef)
        u.reg_write(UC_M68K_REG_SR, 0x2700)
        u.reg_write(UC_M68K_REG_A7, SP)
        self.stop_at = RESUME
        try:
            u.emu_start(SITE, RESUME, count=200000)
        except UcError as e:
            raise AssertionError('faulted at %#010x: %s' % (u.reg_read(UC_M68K_REG_PC), e)) from None
        assert u.reg_read(UC_M68K_REG_PC) == RESUME, hex(u.reg_read(UC_M68K_REG_PC))
        assert u.reg_read(UC_M68K_REG_A7) == SP, 'stack not balanced'
        for r, v in regs.items():
            assert u.reg_read(r) == v, ('register', r, hex(u.reg_read(r)))
        assert u.reg_read(UC_M68K_REG_D0) == status
        pitches = list(struct.unpack('>12i', bytes(u.mem_read(PITCHES, 48))))
        self.stop_at = None
        u.emu_start(RESUME, 0, count=1)                                     # MAIN's own beq.s on those flags
        assert u.reg_read(UC_M68K_REG_PC) == (0x400a4c3e if status == 0 else 0x400a4bc0), ('Z flag', status)
        return pitches


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('--core', type=Path, required=True)
    args = a.parse_args()
    m = Pitch(args.core)
    stock = [(48 + t) << 16 for t in range(12)]
    site = bytes(m.u.mem_read(SITE, 6))
    assert site[:2] == b'\x4e\xf9' and struct.unpack('>I', site[2:])[0] == m.sym['core:core_native_pitch'], site.hex()
    for status in (0, 2):
        assert m.run(0, status) == stock and not m.calls                            # no interest: stock
        got = m.run(1, status)
        assert m.calls == [(22, PITCHES, 12)]                                       # op 22, {80004688, 12}
        assert got == [v + 1000 * (t + 1) for t, v in enumerate(stock)]             # edits stay in place
    print(json.dumps(dict(ok=True, site='400a4bb8 -> core_native_pitch', resumes='400a4bbe',
                          checks=['op 22 args {80004688,12}', 'in-place edits kept', 'no interest: not called',
                                  'd0/Z replay for 0 and nonzero', 'd1/a0/a1 and d2-d7/a2-a6 kept',
                                  'stack balanced'], device_access=False)))


if __name__ == '__main__':
    main()
