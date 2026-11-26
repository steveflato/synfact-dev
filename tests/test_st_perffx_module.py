"""perffx-st 0.4.1: the key handling and the two engines in Unicorn.

Compiles mods/perffx-st/main.c with the configured ColdFire compiler, links it
alone and calls it. It checks: the module refuses a host before API 21 and
registers once with no interest; a double-tap of TRK inside 12 ticks opens the
effects (the first tap and a late or interrupted second tap stay native, the
second tap's release is taken once); while open the trig keys are taken,
pressed and released, and other keys pass; each pitch effect (jumps, the five
ramps), their sum and the clamps move the 12 pitches as the table says, the
interest is PITCH while one is down and 0 after; the gate drops note-ons of
the tracks it names (the sequencer's and live ones) and passes note-offs; NO
keys are momentary, and FUNC while some are held latches them (on a latched one: off); PAGE switches the two banks; FUNC + a trig
key includes or excludes a track for the pitch effects and the mutes; NO closes,
frees every key and every latch and takes its own release; nothing is drawn
while closed. It also checks the LED view: opening makes one view (the OS's constructor,
not opaque, a copied vtable with the module's type and destructor, a LedHandler at
+50h) and pushes it on the stack, only over the MainScreen; the trig key LEDs it sets
for each state (blue jumps, orange ramps, red mutes, unlit gaps, white held, yellow
latched, tracks green/dim red with FUNC down); nothing is set when another view is on
top; NO and the destructor end it.

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
APP, N0, N1, NP, MAIN, OTHER = (DATA + o for o in (0x2000, 0x2100, 0x2110, 0x2120, 0x2200, 0x2600))
STORAGE, LEDMGR, ALLOC, ALLOC_AT = DATA + 0x2700, DATA + 0x2800, DATA + 0x3000, DATA + 0x2900
MAIN_VT = 0x401c849c
OS_ARGS = {0x400ffd6a: 2, 0x400ffcb8: 2, 0x4016dfac: 1, 0x400ffe22: 1, 0x400ffec6: 1, 0x400fffc0: 1,
           0x4016efa4: 0, 0x40008220: 4, 0x40100d86: 3}
FILLRECT = 0x400f7fb6
PITCH, NOTE_ACCEPT = 22, 20
KEY_FUNC, KEY_TRK, KEY_NO, KEY_PAGE, KEY_PLAY, TRIG0 = 1, 2, 13, 18, 10, 24
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
                   'const unsigned int o[4]={offsetof(struct st_machine_ui_api,host),'
                   'offsetof(struct st_machine_host_api,native_interest),'
                   'offsetof(struct st_machine_host_api,storage),'
                   'offsetof(struct st_storage_io,alloc)};\n')
    subprocess.run([cc, '-mcpu=5475', '-I', str(ROOT / 'elekloader/sdk/include'), '-c', str(src), '-o', str(w / 'o.o')],
                   check=True)
    subprocess.run([cc[:-3] + 'objcopy', '-O', 'binary', '-j', '.rodata', str(w / 'o.o'), str(w / 'o.bin')], check=True)
    return struct.unpack('>4I', (w / 'o.bin').read_bytes()[:16])


class Unit:
    def __init__(self, image, sym):
        self.sym, self.u = sym, Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        u = self.u
        u.ctl_set_cpu_model(UC_CPU_M68K_CFV4E)
        for at, n in ((0x40000000, 0x300000), (0x46000000, 0x1000000), (DATA, 0x10000)):
            u.mem_map(at, n)
        u.mem_write(CODE, image)
        self.interest, self.fills, self.os, self.leds = [], 0, [], {}
        for at, fn in ((INTEREST, self._interest), (FILLRECT, self._fill), (ALLOC_AT, self._alloc)):
            u.mem_write(at, b'\x4e\x75')
            u.hook_add(UC_HOOK_CODE, fn, begin=at, end=at)
        for at in OS_ARGS:
            u.mem_write(at, b'\x4e\x75')
            u.hook_add(UC_HOOK_CODE, self._os, begin=at, end=at)

    def _interest(self, u, at, size, _):
        self.interest.append(struct.unpack('>2I', u.mem_read(u.reg_read(UC_M68K_REG_A7) + 4, 8)))

    def _fill(self, u, at, size, _):
        self.fills += 1

    def _alloc(self, u, at, size, _):
        u.reg_write(UC_M68K_REG_D0, ALLOC)

    def _os(self, u, at, size, _):                 # the OS routines the view code calls
        sp = u.reg_read(UC_M68K_REG_A7)
        n = OS_ARGS[at]
        args = struct.unpack('>%dI' % n, u.mem_read(sp + 4, 4 * n)) if n else ()
        self.os.append((at, args))
        if at == 0x40100d86:                       # push: the view becomes the stack's top
            view = struct.unpack('>I', u.mem_read(args[1], 4))[0]
            u.mem_write(args[0] + 0x18, struct.pack('>I', NP))
            u.mem_write(NP + 8, struct.pack('>I', view))
        elif at == 0x400ffec6:                     # close: the MainScreen is the top again
            u.mem_write(APP + 0x40 + 0x18, struct.pack('>I', N1))
            u.mem_write(N1 + 8, struct.pack('>I', MAIN))
        elif at == 0x4016efa4:
            u.reg_write(UC_M68K_REG_D0, LEDMGR)
        elif at == 0x40008220:
            self.leds[args[1]] = (args[2], args[3])

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
        return self.call(self.sym['pf_key'], [APP, EVENT]) & 0xff

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
    u.u.mem_write(HOST + offs[2], struct.pack('>I', STORAGE))
    u.u.mem_write(STORAGE + offs[3], struct.pack('>I', ALLOC_AT))
    m = APP + 0x40                                  # the view manager: the highest opaque view and the top are the MainScreen
    u.u.mem_write(m + 0x1c, struct.pack('>I', N0))
    u.u.mem_write(m + 0x18, struct.pack('>I', N1))
    u.u.mem_write(N0 + 8, struct.pack('>I', MAIN))
    u.u.mem_write(N1 + 8, struct.pack('>I', MAIN))
    u.u.mem_write(MAIN, struct.pack('>I', MAIN_VT))
    u.u.mem_write(0x40241ef4, struct.pack('>19I', *(0xc0de0000 + i for i in range(19))))   # the View vtable the module copies
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


def hold(u, k, flags=DOWN):
    r = u.key(trig(k), flags)
    u.tick(10)                          # longer than a tap: the effect is momentary
    return r


def rel(u, k, flags=UP):
    return u.key(trig(k), flags)


def tap(u, k):
    r = u.key(trig(k), DOWN)
    u.tick(2)
    return r, u.key(trig(k), UP)


def on(u):
    return u.interest[-1]


def check_keys(image, sym, offs):
    u = opened(image, sym, offs)
    assert u.key(KEY_PLAY, DOWN) == 0 and u.key(KEY_PLAY, UP) == 0 and u.key(20, DOWN) == 0   # other keys pass
    assert u.key(KEY_PAGE, DOWN) == 1 and u.key(KEY_PAGE, UP) == 1 and u.key(KEY_PAGE, UP) == 0   # PAGE: taken, release once
    assert u.key(trig(15), DOWN) == 1 and u.key(trig(15), UP) == 1                   # an unassigned key: taken, no effect
    assert on(u) == (sym['pf_native'], 0)
    hold(u, 0)
    assert u.key(trig(0), REPEAT) == 1 and rel(u, 0) == 1 and rel(u, 0) == 0
    print('keys: trig keys taken pressed, repeated and released; PAGE taken with its release; PLAY and others pass; unassigned keys do nothing')


def check_pitch(image, sym, offs):
    u = opened(image, sym, offs)
    base = [(60 + t) << 16 for t in range(12)]
    assert u.pitch(base)[1] == base                                                  # nothing down: unchanged
    for k, off in ((0, 12), (1, -12), (2, 7), (3, -7)):
        hold(u, k)
        assert on(u) == (sym['pf_native'], 1 << PITCH)
        r, got = u.pitch(base)
        assert r == 0 and got == [p + off * SEMI for p in base], (k, got)
        rel(u, k)
        assert on(u) == (sym['pf_native'], 0) and u.pitch(base)[1] == base
    hold(u, 0); hold(u, 3)                                                           # +12 and -7
    assert u.pitch(base)[1] == [p + 5 * SEMI for p in base]
    rel(u, 3)
    assert on(u) == (sym['pf_native'], 1 << PITCH)
    rel(u, 0)
    low = [1 << 16, 0, 5 << 16] + [60 << 16] * 9
    hold(u, 1)
    assert u.pitch(low)[1][:3] == [0, 0, 0] and u.pitch(low)[1][3] == 48 << 16     # clamped at 0
    rel(u, 1); hold(u, 0)
    assert u.pitch([126 << 16] + [60 << 16] * 11)[1][0] == 127 << 16                 # clamped at 127
    rel(u, 0)
    print('pitch: jumps, their sum and the clamps; interest is PITCH while one is down, 0 after')
    steps = {4: (-96, 3146), 5: (-96, 8988), 6: (-12, 2621), 7: (-36, 2359), 8: (24, 1573)}
    for k, (semis, step) in steps.items():
        target = semis * SEMI
        hold(u, k)
        cur = 0
        for n in range(1, 2500):
            cur = max(cur - step, target) if target < 0 else min(cur + step, target)
            got = u.pitch([(64 << 16)] * 12)[1][0]
            want = min(max((64 << 16) + cur, 0), 127 << 16)
            assert got == want, (k, n, got, want)
            if cur == target and n > 3:
                break
        assert cur == target, k
        assert u.pitch([64 << 16] * 12)[1][0] == min(max((64 << 16) + target, 0), 127 << 16)   # holds at the target
        rel(u, k)
        hold(u, k)                                                                   # pressed again: from zero
        first = u.pitch([64 << 16] * 12)[1][0]
        assert first == (64 << 16) + (-step if target < 0 else step), (k, first)
        rel(u, k)
    assert u.pitch([64 << 16] * 12, count=5)[1][5] == 64 << 16                       # a count of 5 touches five
    print('ramps: five ramps step as the table says, hold at the target, restart from zero')


def check_gate(image, sym, offs):
    u = opened(image, sym, offs)
    assert u.note(0) == (0, 0)                                                       # nothing down: passes
    hold(u, 10)                                                                      # MUTE ALL
    assert on(u) == (sym['pf_native'], 1 << NOTE_ACCEPT)
    for tr in range(12):
        assert u.note(tr, 1, 1) == (1, 1) and u.note(tr, 1, 2) == (1, 1), tr         # sequencer and live
    assert u.note(0, 2, 1) == (0, 0) and u.note(3, 2, 2) == (0, 0)                   # note-offs pass
    assert u.note(12, 1, 1) == (0, 0)                                                # the FX track is not a track
    rel(u, 10)
    assert on(u) == (sym['pf_native'], 0) and u.note(0) == (0, 0)
    hold(u, 11)                                                                      # MUTE ODD: tracks 1, 3, 5...
    assert [u.note(t)[0] for t in range(12)] == [1, 0] * 6
    rel(u, 11); hold(u, 12)                                                          # MUTE EVEN
    assert [u.note(t)[0] for t in range(12)] == [0, 1] * 6
    hold(u, 11)                                                                      # both: everything
    assert [u.note(t)[0] for t in range(12)] == [1] * 12
    rel(u, 11); rel(u, 12)
    hold(u, 0); hold(u, 10)                                                          # pitch and gate together
    assert on(u) == (sym['pf_native'], 1 << PITCH | 1 << NOTE_ACCEPT)
    print('gate: note-ons of the named tracks are skipped (sequencer and live), note-offs pass, nothing outside 12 tracks, masks combine')


def check_latch(image, sym, offs):
    u = opened(image, sym, offs)
    base = [(60 + t) << 16 for t in range(12)]
    assert u.key(KEY_FUNC, DOWN) == 0 and u.key(KEY_FUNC, UP) == 0                   # FUNC with nothing held stays native
    hold(u, 0)
    assert u.key(KEY_FUNC, DOWN) == 1                                                # FUNC while one is held: it latches
    assert u.key(KEY_FUNC, REPEAT) == 1                                              # taken, with its repeats
    assert rel(u, 0) == 1                                                            # the key can go
    assert u.key(KEY_FUNC, UP) == 1 and u.key(KEY_FUNC, UP) == 0                     # FUNC's release taken once
    assert on(u) == (sym['pf_native'], 1 << PITCH) and u.pitch(base)[1] == [p + 12 * SEMI for p in base]
    assert u.draw() > 0
    hold(u, 1)                                                                       # a held effect adds on top, then leaves
    assert u.pitch(base)[1] == base
    rel(u, 1)
    assert u.pitch(base)[1] == [p + 12 * SEMI for p in base]
    assert u.key(trig(0), DOWN) == 1                                                 # a press of the latched key: off at once
    assert on(u) == (sym['pf_native'], 0) and u.pitch(base)[1] == base
    assert u.key(trig(0), UP) == 1 and u.key(trig(0), UP) == 0                       # its release is ours, once
    hold(u, 0); rel(u, 0)                                                            # and a plain hold is momentary again
    assert on(u) == (sym['pf_native'], 0)
    hold(u, 2); hold(u, 10); hold(u, 4)                                              # three held: +7, mute all, tape stop
    u.key(KEY_FUNC, DOWN)                                                            # one FUNC latches all three
    rel(u, 2); rel(u, 10); rel(u, 4); u.key(KEY_FUNC, UP)
    assert on(u) == (sym['pf_native'], 1 << PITCH | 1 << NOTE_ACCEPT) and u.note(4)[0] == 1
    first = u.pitch(base)[1][0]
    assert first == base[0] + 7 * SEMI - 3146 * 1                                    # the latched ramp runs from its start
    for _ in range(2):
        u.pitch(base)
    assert u.pitch(base)[1][0] == base[0] + 7 * SEMI - 4 * 3146                      # and keeps going with no key down
    hold(u, 1); rel(u, 1)                                                            # a hold elsewhere leaves the latches
    assert on(u) == (sym['pf_native'], 1 << PITCH | 1 << NOTE_ACCEPT)
    u.key(trig(10), DOWN); u.key(trig(10), UP)                                       # press the latched mute: only it turns off
    assert on(u) == (sym['pf_native'], 1 << PITCH) and u.note(4)[0] == 0
    assert u.pitch(base)[1][0] < base[0] + 7 * SEMI - 4 * 3146                       # the pitch latches go on
    assert u.key(KEY_NO, DOWN) == 1                                                  # NO turns every latch off
    assert on(u) == (sym['pf_native'], 0)
    u.key(KEY_NO, UP)
    u.tick(3); u.key(KEY_TRK, DOWN); u.key(KEY_TRK, UP); u.tick(3); u.key(KEY_TRK, DOWN); u.key(KEY_TRK, UP)   # reopen
    hold(u, 0); rel(u, 0)
    assert on(u) == (sym['pf_native'], 0)                                            # reopened: momentary, nothing latched
    print('latch: FUNC while keys are held latches them and the keys can go; pressing a latched key turns it off; NO clears')


def check_shown(image, sym, offs):
    u = opened(image, sym, offs)
    shown = lambda: u.call(sym['pf_shown'], [])
    assert shown() == 0
    hold(u, 0)
    assert shown() == 1                                                              # id 0 + 1
    u.key(KEY_FUNC, DOWN); rel(u, 0); u.key(KEY_FUNC, UP)                            # latched: still named
    assert shown() == 1
    hold(u, 4)                                                                       # something else held: it is named, over the latch
    assert shown() == 5
    hold(u, 2)                                                                       # the last pressed of the held ones
    assert shown() == 3
    rel(u, 2)
    assert shown() == 5                                                              # the other held one
    rel(u, 4)
    assert shown() == 1                                                              # back to the latched one
    assert u.draw() > 0
    print('shown: the bar names a held effect over a latched one, the last pressed first, and the latch again when none is held')


def check_banks(image, sym, offs):
    u = opened(image, sym, offs)
    base = [(60 + t) << 16 for t in range(12)]
    assert u.key(KEY_PAGE, DOWN) == 1 and u.key(KEY_PAGE, UP) == 1                   # bank 2
    hold(u, 0)
    assert u.pitch(base)[1] == [p + 24 * SEMI for p in base]
    rel(u, 0); hold(u, 3)
    assert u.pitch(base)[1] == [p - 1 * SEMI for p in base]
    rel(u, 3); hold(u, 10)                                                           # MUTE 1 4
    assert [u.note(t)[0] for t in range(12)] == [1] * 4 + [0] * 8
    rel(u, 10); hold(u, 12)                                                          # MUTE 9 12
    assert [u.note(t)[0] for t in range(12)] == [0] * 8 + [1] * 4
    rel(u, 12)
    hold(u, 6)                                                                       # SLOW RISE
    assert u.pitch(base)[1][0] == base[0] + 524
    rel(u, 6)
    hold(u, 0)                                                                       # held from bank 2, then back to bank 1
    assert u.key(KEY_PAGE, DOWN) == 1 and u.key(KEY_PAGE, UP) == 1
    assert u.pitch(base)[1] == [p + 24 * SEMI for p in base]                         # still bank 2's effect
    assert rel(u, 0) == 1 and u.pitch(base)[1] == base                               # its release ends it
    hold(u, 0)                                                                       # now bank 1's OCT UP
    assert u.pitch(base)[1] == [p + 12 * SEMI for p in base]
    rel(u, 0)
    print('banks: PAGE switches, bank 2 effects, a key held across a switch keeps its own effect')


def check_include(image, sym, offs):
    u = opened(image, sym, offs)
    base = [(60 + t) << 16 for t in range(12)]
    FUNC = DOWN | 2
    assert u.key(trig(1), FUNC) == 1 and u.key(trig(1), UP | 2) == 1                 # FUNC + trig 2: track 2 out
    assert on(u) == (sym['pf_native'], 0)                                            # not an effect
    hold(u, 0)
    got = u.pitch(base)[1]
    assert got[1] == base[1] and all(got[i] == base[i] + 12 * SEMI for i in range(12) if i != 1)
    rel(u, 0)
    assert u.key(trig(1), FUNC) == 1 and u.key(trig(1), UP) == 1                     # back in
    hold(u, 0)
    assert u.pitch(base)[1] == [p + 12 * SEMI for p in base]
    rel(u, 0)
    for k in (0, 2, 4):
        u.key(trig(k), FUNC); u.key(trig(k), UP)                                     # tracks 1, 3, 5 out
    hold(u, 10)                                                                      # MUTE ALL skips them
    assert [u.note(t)[0] for t in range(12)] == [0, 1, 0, 1, 0] + [1] * 7
    rel(u, 10)
    assert u.key(KEY_PAGE, FUNC) == 1 and u.key(KEY_PAGE, UP | 2) == 1               # FUNC + PAGE: all back in, no bank switch
    hold(u, 10)
    assert [u.note(t)[0] for t in range(12)] == [1] * 12
    rel(u, 10)
    assert u.key(trig(14), FUNC) == 1 and u.key(trig(14), UP) == 1 and on(u) == (sym['pf_native'], 0)   # FUNC + 15: nothing
    assert u.draw() > 0
    print('include: FUNC + trig 1..12 toggles a track for pitch and mutes, FUNC + PAGE brings all back, no effect starts')


def check_close(image, sym, offs):
    u = opened(image, sym, offs)
    hold(u, 0); hold(u, 10)
    assert u.key(KEY_NO, DOWN) == 1                                                  # NO closes and frees every key
    assert on(u) == (sym['pf_native'], 0)
    assert u.pitch([60 << 16] * 12)[1] == [60 << 16] * 12 and u.note(0) == (0, 0)
    assert u.key(KEY_NO, UP) == 1 and u.key(KEY_NO, UP) == 0                         # its release is taken once
    assert rel(u, 0) == 1 and rel(u, 0) == 0                                         # a key held when NO closed: its release is ours, once
    assert u.key(trig(0), DOWN) == 0 and u.key(trig(0), UP) == 0                     # closed: the keys are native again
    assert u.draw() == 0
    u.tick(3)
    assert u.key(KEY_TRK, DOWN) == 0 and u.key(KEY_TRK, UP) == 0
    u.tick(3)
    assert u.key(KEY_TRK, DOWN) == 1                                                 # and it opens again
    print('close: NO frees every key, takes its release once, trig keys are native again, draws nothing, reopens')


def rd(u, at, n=1):
    return struct.unpack('>%dI' % n, u.u.mem_read(at, 4 * n))


def leds(u, handler=None):
    u.leds = {}
    u.call(u.sym['pf_view_leds'], [(ALLOC + 16 + 0x50) if handler is None else handler, 0, 0])
    return u.leds


def check_view(image, sym, offs):
    u = opened(image, sym, offs)
    ops = {a: args for a, args in u.os}
    view, root = ALLOC + 16, ALLOC
    assert [a for a, _ in u.os] == [0x400ffd6a, 0x400ffcb8, 0x40100d86, 0x4016dfac, 0x400ffe22]   # ctor, not opaque, push, release, dirty
    assert ops[0x400ffcb8] == (view, 0) and ops[0x40100d86][0] == APP + 0x40 and ops[0x40100d86][2] == 0 and ops[0x400ffe22] == (view,)
    assert bytes(u.u.mem_read(ops[0x400ffd6a][1], 8)) == b'PERF FX\0'
    assert rd(u, root, 4) == (0x401c9910, 1, 1, view)                                 # the shared control block, owning the view
    vt = sym['pf_vtable'] + 8
    assert rd(u, view)[0] == vt
    words = rd(u, sym['pf_vtable'], 19)
    assert words[0] == 0xc0de0000 and words[3:] == tuple(0xc0de0000 + i for i in range(3, 19))   # copied from the View vtable
    assert words[1] == sym['pf_type'] and words[2] == sym['pf_dispose']              # the module's type and destructor
    assert rd(u, view + 0x50, 3) == (sym['pf_led_vtable'] + 8, 0, 0)                 # the LedHandler: its vptr and a clear claim mask
    ledvt = rd(u, sym['pf_led_vtable'], 5)
    assert ledvt == ((-0x50) & 0xffffffff, sym['pf_type'], sym['pf_led_nothing'], sym['pf_led_nothing'], sym['pf_view_leds'])
    t = rd(u, sym['pf_type'], 8)
    assert t[0] == 0x4024979c and t[2:] == (0, 2, 0x40241ebc, 2, 0x401c73a4, 0x5002)
    assert bytes(u.u.mem_read(t[1], 13)) == b'10PerfFxView\0'
    assert rd(u, APP + 0x40 + 0x18)[0] == NP and rd(u, NP + 8)[0] == view            # on top of the stack
    print('view: one invisible view pushed over the MainScreen (OS constructor, not opaque, copied vtable, LedHandler at +50h)')

    UNLIT, BLUE, ORANGE, RED, WHITE, YELLOW, GREEN, DIMRED, CYAN = (1, 2), (2, 14), (2, 31), (2, 3), (2, 20), (2, 23), (2, 9), (2, 4), (2, 25)
    want = [BLUE] * 4 + [ORANGE] * 5 + [UNLIT] + [RED] * 3 + [UNLIT] * 3
    got = leds(u)
    assert got == {k + 1: want[k] for k in range(16)}, got                           # bank 1, idle: by category
    hold(u, 0); hold(u, 4)
    got = leds(u)
    assert got[1] == WHITE and got[5] == WHITE and got[2] == BLUE and got[10] == UNLIT   # held: white
    u.key(KEY_FUNC, DOWN); rel(u, 0); rel(u, 4); u.key(KEY_FUNC, UP)                 # latched: yellow
    got = leds(u)
    assert got[1] == YELLOW and got[5] == YELLOW and got[3] == BLUE
    u.key(KEY_PAGE, DOWN); u.key(KEY_PAGE, UP)                                       # bank 2: its own effects and latches
    got = leds(u)
    assert got[1] == BLUE and got[5] == ORANGE and got[8] == UNLIT and got[11] == RED and got[14] == UNLIT
    u.key(KEY_PAGE, DOWN); u.key(KEY_PAGE, UP)
    assert u.key(KEY_FUNC, DOWN) == 0                                                # FUNC alone: native, and the keys show the tracks
    got = leds(u)
    assert all(got[k] == GREEN for k in range(1, 13)) and got[13] == UNLIT and got[14] == UNLIT and got[16] == UNLIT
    u.key(trig(1), DOWN | 2); u.key(trig(1), UP | 2)                                 # track 2 out
    got = leds(u)
    assert got[2] == DIMRED and got[1] == GREEN
    u.key(KEY_FUNC, UP)
    assert leds(u)[1] == YELLOW                                                      # FUNC up: effects again
    print('leds: bank 1 by category (blue jumps, orange ramps, red mutes, unlit gaps), white held, yellow latched, bank 2, tracks with FUNC down')

    assert leds(u, handler=ALLOC + 16) == {}                                         # another handler: not ours
    other = bytes(u.u.mem_read(NP + 8, 4))
    u.u.mem_write(NP + 8, struct.pack('>I', OTHER))                                  # a menu is on top
    assert leds(u) == {}
    assert u.key(trig(5), DOWN) == 0 and u.key(KEY_NO, DOWN) == 0 and u.key(KEY_PLAY, DOWN) == 0
    assert u.draw() > 0                                                              # the bar stays; only the keys pass
    u.u.mem_write(NP + 8, other)                                                     # back on top
    assert u.key(trig(5), DOWN) == 1 and rd(u, NP + 8)[0] == ALLOC + 16
    u.key(trig(5), UP)
    print('foreground: with another view on top no LED is set and the keys pass')

    u.key(KEY_NO, DOWN)
    assert u.os[-1] == (0x400ffec6, (ALLOC + 16,)) and leds(u) == {}                 # NO closes the view through the OS
    u.key(KEY_NO, UP)

    r = start(image, sym, offs)                                                      # the stack is not the MainScreen's: no view
    r.u.mem_write(N1 + 8, struct.pack('>I', OTHER))
    assert r.key(KEY_TRK, DOWN) == 0 and r.key(KEY_TRK, UP) == 0
    r.tick(3)
    assert r.key(KEY_TRK, DOWN) == 0 and r.key(KEY_TRK, UP) == 0 and r.draw() == 0
    assert 0x40100d86 not in [a for a, _ in r.os]                                    # nothing pushed, nothing taken
    r.u.mem_write(N1 + 8, struct.pack('>I', MAIN))
    r.tick(1)
    assert r.key(KEY_TRK, DOWN) == 0 and r.key(KEY_TRK, UP) == 0
    r.tick(3)
    assert r.key(KEY_TRK, DOWN) == 1                                                 # over the MainScreen: opens
    print('open: only over the MainScreen; with another view on top the taps stay native and nothing is pushed')

    d = opened(image, sym, offs)
    hold(d, 0); hold(d, 10)
    d.call(sym['pf_dispose'], [ALLOC + 16 + 4])                                      # another view's destructor: not ours
    assert on(d)[1] == 1 << PITCH | 1 << NOTE_ACCEPT and d.key(trig(2), DOWN) == 1
    d.call(sym['pf_dispose'], [ALLOC + 16])                                          # ours: the OS let go of the view
    assert d.os[-1] == (0x400fffc0, (ALLOC + 16,))                                   # the base destructor runs
    assert on(d) == (sym['pf_native'], 0) and d.pitch([60 << 16] * 12)[1] == [60 << 16] * 12
    assert d.key(trig(3), DOWN) == 0 and d.draw() == 0 and leds(d) == {}             # closed: everything native again
    assert d.key(trig(0), UP) == 1 and d.key(trig(0), UP) == 0                       # keys held then: their releases are ours, once
    print('dispose: the OS letting go of the view ends the effects and the mode; the base destructor still runs')


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--cc', default=shutil.which(os.environ.get('ELEKLOADER_CROSS', 'm68k-elf-') + 'gcc'))
    args = a.parse_args()
    if not args.cc:
        print('SKIP: no ColdFire compiler'); return
    image, sym = build(args.cc)
    offs = offsets(args.cc)
    for check in (check_init, check_open, check_keys, check_pitch, check_gate, check_latch, check_shown, check_banks,
                  check_include, check_close, check_view):
        check(image, sym, offs)


if __name__ == '__main__':
    main()
