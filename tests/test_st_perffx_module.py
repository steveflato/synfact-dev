"""perffx-st 0.1.0: the key handling and the two engines in Unicorn.

Compiles mods/perffx-st/main.c with the configured ColdFire compiler, links it
alone and calls it. It checks: the module refuses a host before API 21 and
registers once with no interest; a double-tap of TRK inside 12 ticks opens the
effects (the first tap and a late or interrupted second tap stay native, the
second tap's release is taken once); while open the trig keys are taken,
pressed and released, and other keys pass; each pitch effect (jumps, the five
ramps), their sum and the clamps move the 12 pitches as the table says, the
interest is PITCH while one is down and 0 after; the gate drops note-ons of
the tracks it names (the sequencer's and live ones) and passes note-offs; NO
closes, frees every key and takes its own release; nothing is drawn while
closed.

It does not run the real OS or the core's dispatcher.

    python tests/test_st_perffx_module.py [--cc m68k-elf-gcc]
"""
import argparse, os, shutil, struct, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'mods/perffx-st'
sys.path.insert(0, str(ROOT))

from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE   # noqa: E402
from unicorn.m68k_const import UC_CPU_M68K_CFV4E, UC_M68K_REG_A7, UC_M68K_REG_D0, UC_M68K_REG_SR  # noqa: E402

CODE, DATA, STOP = 0x46100000, 0x48000000, 0x46fffff0
EVENT, ARGS, RESULT, HOST, UI, API, INTEREST = (DATA + o for o in (0, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600))
PITCHES, CTRL, BMP = DATA + 0xc00, DATA + 0xd00, DATA + 0xe00
FILLRECT = 0x400f7fb6
PITCH, NOTE_ACCEPT = 22, 20
KEY_TRK, KEY_NO, KEY_PAGE, KEY_PLAY, TRIG0 = 2, 13, 18, 10, 24
DOWN, REPEAT, UP = 1, 9, 0x10
SEMI = 1 << 16


def build(cc):
    from elekloader.devices import syntakt
    flags = syntakt.DEVICE.toolchain['cflags']
    w = Path(tempfile.mkdtemp())
    subprocess.run([cc, *flags, '-fno-jump-tables', '-I', str(ROOT / 'elekloader/sdk/include'),
                    '-c', str(MOD / 'main.c'), '-o', str(w / 'm.o')], check=True)
    pre = cc[:-3]
    subprocess.run([pre + 'ld', '-o', str(w / 'm.elf'), '-Ttext=%#x' % CODE, '-e', 'perffx_module', str(w / 'm.o')],
                   check=True)
    nm = subprocess.run([pre + 'nm', str(w / 'm.elf')], check=True, capture_output=True, text=True).stdout
    sym = {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
    subprocess.run([pre + 'objcopy', '-O', 'binary', str(w / 'm.elf'), str(w / 'm.bin')], check=True)
    return (w / 'm.bin').read_bytes(), sym


def offsets(cc):
    w = Path(tempfile.mkdtemp())
    src = w / 'o.c'
    src.write_text('#include <stddef.h>\n#include <syntakt/modules.h>\n#include <syntakt/host.h>\n'
                   'const unsigned int o[2]={offsetof(struct st_machine_ui_api,host),'
                   'offsetof(struct st_machine_host_api,native_interest)};\n')
    subprocess.run([cc, '-mcpu=5475', '-I', str(ROOT / 'elekloader/sdk/include'), '-c', str(src), '-o', str(w / 'o.o')],
                   check=True)
    subprocess.run([cc[:-3] + 'objcopy', '-O', 'binary', '-j', '.rodata', str(w / 'o.o'), str(w / 'o.bin')], check=True)
    return struct.unpack('>2I', (w / 'o.bin').read_bytes()[:8])


class Unit:
    def __init__(self, image, sym):
        self.sym, self.u = sym, Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        u = self.u
        u.ctl_set_cpu_model(UC_CPU_M68K_CFV4E)
        for at, n in ((0x40000000, 0x200000), (0x46000000, 0x1000000), (DATA, 0x10000)):
            u.mem_map(at, n)
        u.mem_write(CODE, image)
        self.interest, self.fills = [], 0
        for at, fn in ((INTEREST, self._interest), (FILLRECT, self._fill)):
            u.mem_write(at, b'\x4e\x75')
            u.hook_add(UC_HOOK_CODE, fn, begin=at, end=at)

    def _interest(self, u, at, size, _):
        self.interest.append(struct.unpack('>2I', u.mem_read(u.reg_read(UC_M68K_REG_A7) + 4, 8)))

    def _fill(self, u, at, size, _):
        self.fills += 1

    def call(self, fn, args):
        u = self.u
        sp = 0x46f00000 - 4 * len(args) - 4
        u.mem_write(sp, struct.pack('>%dI' % (len(args) + 1), STOP, *args))
        u.reg_write(UC_M68K_REG_SR, 0x2000)
        u.reg_write(UC_M68K_REG_A7, sp)
        u.emu_start(fn, STOP, count=400000)
        return u.reg_read(UC_M68K_REG_D0)

    def key(self, key, flags):
        self.u.mem_write(EVENT, b'\0' * 12 + struct.pack('>2I', key, flags))
        return self.call(self.sym['pf_key'], [0, EVENT]) & 0xff

    def tick(self, n=1):
        for _ in range(n):
            self.call(self.sym['pf_tick'], [CTRL])

    def pitch(self, values, count=12):
        self.u.mem_write(PITCHES, struct.pack('>%di' % len(values), *values))
        self.u.mem_write(ARGS, struct.pack('>2I', PITCHES, count))
        r = self.call(self.sym['pf_native'], [PITCH, ARGS, RESULT])
        return r, list(struct.unpack('>%di' % len(values), self.u.mem_read(PITCHES, 4 * len(values))))

    def note(self, track, action=1, kind=1):
        ev = DATA + 0x1000
        self.u.mem_write(ev, struct.pack('>4I', 0, action, track, kind))
        self.u.mem_write(ARGS, struct.pack('>4I', ev, 0, 0, 0))
        self.u.mem_write(RESULT, struct.pack('>I', 0))
        r = self.call(self.sym['pf_native'], [NOTE_ACCEPT, ARGS, RESULT]) & 0xff
        return r, struct.unpack('>I', self.u.mem_read(RESULT, 4))[0]

    def draw(self):
        before = self.fills
        self.call(self.sym['pf_draw'], [BMP, CTRL])
        return self.fills - before


def start(image, sym, offs, version=21):
    u = Unit(image, sym)
    u.u.mem_write(HOST, struct.pack('>I', version) + b'\0' * 0x100)
    u.u.mem_write(HOST + offs[1], struct.pack('>I', INTEREST))
    u.u.mem_write(UI, b'\0' * 0x40)
    u.u.mem_write(UI + offs[0], struct.pack('>I', HOST))
    u.u.mem_write(API, struct.pack('>2I', 3, UI))
    desc = struct.unpack('>4I', image[sym['perffx_module'] - CODE:sym['perffx_module'] - CODE + 16])
    u.rc = u.call(desc[1], [API])
    return u


def opened(image, sym, offs):
    u = start(image, sym, offs)
    assert u.key(KEY_TRK, DOWN) == 0
    u.key(KEY_TRK, UP)
    u.tick(3)
    assert u.key(KEY_TRK, DOWN) == 1
    assert u.key(KEY_TRK, UP) == 1
    return u


def trig(k):
    return TRIG0 + k


def check_init(image, sym, offs):
    u = start(image, sym, offs)
    assert u.rc == 0 and u.interest == [(sym['pf_native'], 0)], (u.rc, u.interest)
    assert start(image, sym, offs, 20).rc != 0
    print('init: refuses a host before API 21, registers once with no interest')


def check_open(image, sym, offs):
    u = start(image, sym, offs)
    assert u.key(KEY_TRK, DOWN) == 0 and u.key(KEY_TRK, UP) == 0                    # one tap: native
    u.tick(12)
    assert u.key(KEY_TRK, DOWN) == 1 and u.key(KEY_TRK, UP) == 1 and u.key(KEY_TRK, UP) == 0   # taken once
    assert u.draw() > 0
    c = start(image, sym, offs)
    assert c.key(KEY_TRK, DOWN) == 0 and c.key(KEY_TRK, UP) == 0
    c.tick(13)
    assert c.key(KEY_TRK, DOWN) == 0 and c.key(KEY_TRK, UP) == 0 and c.draw() == 0   # too late: a new first tap
    assert c.key(trig(0), DOWN) == 0
    d = start(image, sym, offs)
    assert d.key(KEY_TRK, DOWN) == 0 and d.key(KEY_TRK, UP) == 0
    assert d.key(KEY_PLAY, DOWN) == 0 and d.key(KEY_PLAY, UP) == 0                  # another key in between
    assert d.key(KEY_TRK, DOWN) == 0 and d.draw() == 0
    e = start(image, sym, offs)
    e.key(KEY_TRK, DOWN); e.key(KEY_TRK, UP)
    assert e.key(KEY_TRK, REPEAT) == 0 and e.key(KEY_TRK, DOWN) == 1                # a repeat is not a tap
    print('open: a second TRK tap within 12 ticks opens and is taken once; late, interrupted and repeat taps stay native')


def check_keys(image, sym, offs):
    u = opened(image, sym, offs)
    assert u.key(KEY_PLAY, DOWN) == 0 and u.key(KEY_PLAY, UP) == 0 and u.key(20, DOWN) == 0   # other keys pass
    assert u.key(KEY_PAGE, DOWN) == 1
    assert u.key(trig(15), DOWN) == 1 and u.key(trig(15), UP) == 1                   # an unassigned key: taken, no effect
    assert u.interest[-1] == (sym['pf_native'], 0)
    u.key(trig(0), DOWN)
    assert u.key(trig(0), REPEAT) == 1 and u.key(trig(0), UP) == 1 and u.key(trig(0), UP) == 0
    print('keys: trig keys taken pressed, repeated and released; PLAY and others pass; unassigned keys do nothing')


def check_pitch(image, sym, offs):
    u = opened(image, sym, offs)
    base = [(60 + t) << 16 for t in range(12)]
    assert u.pitch(base)[1] == base                                                  # nothing down: unchanged
    for k, off in ((0, 12), (1, -12), (2, 7), (3, -7)):
        u.key(trig(k), DOWN)
        assert u.interest[-1] == (sym['pf_native'], 1 << PITCH)
        r, got = u.pitch(base)
        assert r == 0 and got == [p + off * SEMI for p in base], (k, got)
        u.key(trig(k), UP)
        assert u.interest[-1] == (sym['pf_native'], 0) and u.pitch(base)[1] == base
    u.key(trig(0), DOWN); u.key(trig(3), DOWN)                                       # +12 and -7
    assert u.pitch(base)[1] == [p + 5 * SEMI for p in base]
    u.key(trig(3), UP)
    assert u.interest[-1] == (sym['pf_native'], 1 << PITCH)
    u.key(trig(0), UP)
    low = [1 << 16, 0, 5 << 16] + [60 << 16] * 9
    u.key(trig(1), DOWN)
    assert u.pitch(low)[1][:3] == [0, 0, 0] and u.pitch(low)[1][3] == 48 << 16     # clamped at 0
    u.key(trig(1), UP); u.key(trig(0), DOWN)
    assert u.pitch([126 << 16] + [60 << 16] * 11)[1][0] == 127 << 16                 # clamped at 127
    u.key(trig(0), UP)
    print('pitch: jumps, their sum and the clamps; interest is PITCH while one is down, 0 after')
    steps = {4: (-96, 4194), 5: (-96, 11984), 6: (-12, 3495), 7: (-36, 3145), 8: (24, 2097)}
    for k, (semis, step) in steps.items():
        target = semis * SEMI
        u.key(trig(k), DOWN)
        cur = 0
        for n in range(1, 2000):
            cur = max(cur - step, target) if target < 0 else min(cur + step, target)
            got = u.pitch([(64 << 16)] * 12)[1][0]
            want = min(max((64 << 16) + cur, 0), 127 << 16)
            assert got == want, (k, n, got, want)
            if cur == target and n > 3:
                break
        assert cur == target, k
        assert u.pitch([64 << 16] * 12)[1][0] == min(max((64 << 16) + target, 0), 127 << 16)   # holds at the target
        u.key(trig(k), UP)
        u.key(trig(k), DOWN)                                                         # pressed again: from zero
        first = u.pitch([64 << 16] * 12)[1][0]
        assert first == (64 << 16) + (-step if target < 0 else step), (k, first)
        u.key(trig(k), UP)
    assert u.pitch([64 << 16] * 12, count=5)[1][5] == 64 << 16                       # a count of 5 touches five
    print('ramps: tape stop, stop fast, half speed, dive and rise step as the table says, hold at the target, restart from zero')


def check_gate(image, sym, offs):
    u = opened(image, sym, offs)
    assert u.note(0) == (0, 0)                                                       # nothing down: passes
    u.key(trig(10), DOWN)                                                            # MUTE ALL
    assert u.interest[-1] == (sym['pf_native'], 1 << NOTE_ACCEPT)
    for tr in range(12):
        assert u.note(tr, 1, 1) == (1, 1) and u.note(tr, 1, 2) == (1, 1), tr         # sequencer and live
    assert u.note(0, 2, 1) == (0, 0) and u.note(3, 2, 2) == (0, 0)                   # note-offs pass
    assert u.note(12, 1, 1) == (0, 0)                                                # the FX track is not a track
    u.key(trig(10), UP)
    assert u.interest[-1] == (sym['pf_native'], 0) and u.note(0) == (0, 0)
    u.key(trig(11), DOWN)                                                            # MUTE ODD: tracks 1, 3, 5...
    assert [u.note(t)[0] for t in range(12)] == [1, 0] * 6
    u.key(trig(11), UP); u.key(trig(12), DOWN)                                       # MUTE EVEN
    assert [u.note(t)[0] for t in range(12)] == [0, 1] * 6
    u.key(trig(11), DOWN)                                                            # both: everything
    assert [u.note(t)[0] for t in range(12)] == [1] * 12
    u.key(trig(11), UP); u.key(trig(12), UP)
    u.key(trig(0), DOWN); u.key(trig(10), DOWN)                                      # pitch and gate together
    assert u.interest[-1] == (sym['pf_native'], 1 << PITCH | 1 << NOTE_ACCEPT)
    print('gate: note-ons of the named tracks are skipped (sequencer and live), note-offs pass, nothing outside 12 tracks, masks combine')


def check_close(image, sym, offs):
    u = opened(image, sym, offs)
    u.key(trig(0), DOWN); u.key(trig(10), DOWN)
    assert u.key(KEY_NO, DOWN) == 1                                                  # NO closes and frees every key
    assert u.interest[-1] == (sym['pf_native'], 0)
    assert u.pitch([60 << 16] * 12)[1] == [60 << 16] * 12 and u.note(0) == (0, 0)
    assert u.key(KEY_NO, UP) == 1 and u.key(KEY_NO, UP) == 0                         # its release is taken once
    assert u.key(trig(0), UP) == 1 and u.key(trig(0), UP) == 0                       # a key held when NO closed: its release is ours, once
    assert u.key(trig(0), DOWN) == 0 and u.key(trig(0), UP) == 0                     # closed: the keys are native again
    assert u.draw() == 0
    u.tick(3)
    assert u.key(KEY_TRK, DOWN) == 0 and u.key(KEY_TRK, UP) == 0
    u.tick(3)
    assert u.key(KEY_TRK, DOWN) == 1                                                 # and it opens again
    print('close: NO frees every key, takes its release once, trig keys are native again, draws nothing, reopens')


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--cc', default=shutil.which(os.environ.get('ELEKLOADER_CROSS', 'm68k-elf-') + 'gcc'))
    args = a.parse_args()
    if not args.cc:
        print('SKIP: no ColdFire compiler'); return
    image, sym = build(args.cc)
    offs = offsets(args.cc)
    for check in (check_init, check_open, check_keys, check_pitch, check_gate, check_close):
        check(image, sym, offs)


if __name__ == '__main__':
    main()
