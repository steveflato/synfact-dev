"""microtune-st: the PITCH handler, SETTINGS rows and preferences in Unicorn.

Compiles mods/microtune-st/main.c with the configured ColdFire compiler (the
device's cflags; ELEKLOADER_CROSS or --cc), links it alone and calls it. It
checks: the module refuses a host before API 21 and registers interest in
PITCH (22) only; SETTINGS adds two rows (TUNING, TUNE ROOT) through additem;
TUNING starts OFF and then every pitch passes unchanged; each factory tuning
and root moves the 12 pitches exactly as a Python port of tune.h's mt_map,
in place, never consuming, with count respected and other operations
ignored; YES and LEFT/RIGHT step and wrap, and redraw; with host API 21 the
choice is read from the core preferences (key 4) at start and written on
each change, and stored values out of range are ignored.

It does not run the real OS or the core's dispatcher
(tests/test_st_native_pitch_emu.py covers the core's side).

    python tests/test_st_microtune_module.py [--cc m68k-elf-gcc]
"""
import argparse, os, shutil, struct, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'mods/microtune-st'
sys.path.insert(0, str(ROOT))

from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE   # noqa: E402
from unicorn.m68k_const import UC_CPU_M68K_CFV4E, UC_M68K_REG_A7, UC_M68K_REG_D0, UC_M68K_REG_SR  # noqa: E402

CODE, DATA, STOP = 0x46100000, 0x48000000, 0x46fffff0
EVENT, ARGS, RESULT, HOST, UI, API, INTEREST = (DATA + o for o in (0, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600))
PITCH, PITCHES = 22, DATA + 0xc00
ADDITEM, VIEW, PREFS, PERSIST = DATA + 0x700, DATA + 0x800, DATA + 0xa00, DATA + 0xb00


def build(cc):
    from elekloader.devices import syntakt
    flags = syntakt.DEVICE.toolchain['cflags']
    w = Path(tempfile.mkdtemp())
    subprocess.run([cc, *flags, '-fno-jump-tables', '-I', str(ROOT / 'elekloader/sdk/include'), '-I', str(MOD),
                    '-c', str(MOD / 'main.c'), '-o', str(w / 'm.o')], check=True)
    pre = cc[:-3]
    subprocess.run([pre + 'ld', '-o', str(w / 'm.elf'), '-Ttext=%#x' % CODE, '-e', 'microtune_module', str(w / 'm.o')],
                   check=True)
    nm = subprocess.run([pre + 'nm', str(w / 'm.elf')], check=True, capture_output=True, text=True).stdout
    sym = {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
    subprocess.run([pre + 'objcopy', '-O', 'binary', str(w / 'm.elf'), str(w / 'm.bin')], check=True)
    return (w / 'm.bin').read_bytes(), sym


class Unit:
    def __init__(self, image, sym):
        self.sym, self.u = sym, Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        u = self.u
        u.ctl_set_cpu_model(UC_CPU_M68K_CFV4E)
        for at, n in ((0x40000000, 0x200000), (0x43b00000, 0x100000), (0x46000000, 0x1000000), (DATA, 0x10000), (0x80000000, 0x10000)):
            u.mem_map(at, n)
        u.mem_write(CODE, image)
        self.interest, self.invalidated, self.rows = [], [], []
        self.prefs, self.pref_calls = {}, []
        for at, fn in ((INTEREST, self._interest), (0x400ffe22, self._invalidate), (ADDITEM, self._additem),
                       (PREFS, self._prefs)):
            u.mem_write(at, b'\x4e\x75')
            u.hook_add(UC_HOOK_CODE, fn, begin=at, end=at)

    def _invalidate(self, u, at, size, _):
        self.invalidated.append(struct.unpack('>I', u.mem_read(u.reg_read(UC_M68K_REG_A7) + 4, 4))[0])

    def _prefs(self, u, at, size, _):           # preferences(key, words, count, write): 1 found/ok, 0 absent
        key, v, n, write = struct.unpack('>4I', u.mem_read(u.reg_read(UC_M68K_REG_A7) + 4, 16))
        self.pref_calls.append((key, n, write))
        if write:
            self.prefs[key] = list(struct.unpack('>%dI' % n, u.mem_read(v, 4 * n)))
            r = 1
        elif key in self.prefs and len(self.prefs[key]) == n:      # the core: another length reads as absent
            u.mem_write(v, struct.pack('>%dI' % n, *self.prefs[key]))
            r = 1
        else:
            r = 0
        u.reg_write(UC_M68K_REG_D0, r)

    def _additem(self, u, at, size, _):
        menu, row = struct.unpack('>2I', u.mem_read(u.reg_read(UC_M68K_REG_A7) + 4, 8))
        self.rows.append((menu, struct.unpack('>4I', u.mem_read(row, 16))))

    def text(self, fn):
        return bytes(self.u.mem_read(self.call(self.sym[fn], []), 24)).split(b'\0')[0].decode()

    def _interest(self, u, at, size, _):
        sp = u.reg_read(UC_M68K_REG_A7)
        self.interest.append(struct.unpack('>2I', u.mem_read(sp + 4, 8)))

    def call(self, fn, args):
        u = self.u
        sp = 0x46f00000 - 4 * len(args) - 4
        u.mem_write(sp, struct.pack('>%dI' % (len(args) + 1), STOP, *args))
        u.reg_write(UC_M68K_REG_SR, 0x2000)          # supervisor, as the OS (the module masks IRQs around native_interest);
        u.reg_write(UC_M68K_REG_A7, sp)              # set first: it swaps the stack pointers
        u.emu_start(fn, STOP, count=200000)
        return u.reg_read(UC_M68K_REG_D0)

    def pitch(self, values, op=PITCH, count=12):
        self.u.mem_write(PITCHES, struct.pack('>%di' % len(values), *values))
        self.u.mem_write(ARGS, struct.pack('>2I', PITCHES, count))
        r = self.call(self.sym['mt_native'], [op, ARGS, RESULT])
        return r, list(struct.unpack('>%di' % len(values), self.u.mem_read(PITCHES, 4 * len(values))))


def offsets(cc):
    """The host pointer in st_machine_ui_api and native_interest in st_machine_host_api, as compiled."""
    w = Path(tempfile.mkdtemp())
    src = w / 'o.c'
    src.write_text('#include <stddef.h>\n#include <syntakt/modules.h>\n#include <syntakt/host.h>\n'
                   'const unsigned int o[4]={offsetof(struct st_machine_ui_api,host),'
                   'offsetof(struct st_machine_host_api,native_interest),'
                   'offsetof(struct st_machine_host_api,additem),'
                   'offsetof(struct st_machine_host_api,persistence)};\n')
    subprocess.run([cc, '-mcpu=5475', '-I', str(ROOT / 'elekloader/sdk/include'), '-c', str(src), '-o', str(w / 'o.o')],
                   check=True)
    subprocess.run([cc[:-3] + 'objcopy', '-O', 'binary', '-j', '.rodata', str(w / 'o.o'), str(w / 'o.bin')], check=True)
    return struct.unpack('>4I', (w / 'o.bin').read_bytes()[:16])



NAMES = ['OFF', '12-TET', 'JUST MAJOR', 'JUST MINOR', 'PYTHAGOREAN', 'MEANTONE 1/4', 'WERCKM. III', 'KIRNB. III',
         'VALLOTTI', 'ARABIC RAST', 'POOLE ROD12']
NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def factory():
    import re
    t = (MOD / 'tunings.h').read_text()
    rows = re.findall(r'\{([-0-9, ]+)\}, /\*', t)
    return [[int(x) for x in r.split(',')] for r in rows]


def q16(t):
    a = abs(t)
    q = (a * 8389 + 64) >> 7
    return -q if t < 0 else q


def ref(off, root, p):
    """tune.h's mt_map, in Python integers (C's truncating % fixed up as there)."""
    if not any(off):
        return p
    q = [q16(max(-1000, min(1000, o))) for o in off]
    q.append(q[0])
    semi, frac = p >> 16, p & 0xffff
    k = (semi - root) % 12
    a, d = q[k], q[k + 1] - q[k]
    prod = d * (frac >> 4)
    return p + a + (prod >> 12)


SWEEP = [(48 + t) << 16 for t in range(12)]
SWEEP2 = [(60 << 16) + 0x8000, (61 << 16) + 0x1234, (35 << 16) + 0xffff, -(3 << 16) + 7, 0, 127 << 16,
          (72 << 16) + 0x4000, (59 << 16) + 0xc000, (64 << 16) + 1, (71 << 16) + 0xfff0, (55 << 16), (66 << 16) + 0x8001]


def start(image, sym, offs, version, stored=None):
    u = Unit(image, sym)
    if stored is not None:
        u.prefs[4] = list(stored)
    desc = struct.unpack('>4I', image[sym['microtune_module'] - CODE:sym['microtune_module'] - CODE + 16])
    u.u.mem_write(HOST, struct.pack('>I', version) + b'\0' * 0x100)
    u.u.mem_write(HOST + offs[1], struct.pack('>I', INTEREST))
    u.u.mem_write(HOST + offs[2], struct.pack('>I', ADDITEM))
    if version >= 20:
        u.u.mem_write(HOST + offs[3], struct.pack('>I', PERSIST))
        u.u.mem_write(PERSIST, struct.pack('>6I', 1, 0, 0, 0, 0, PREFS))
    u.u.mem_write(UI, b'\0' * 0x40)
    u.u.mem_write(UI + offs[0], struct.pack('>I', HOST))
    u.u.mem_write(API, struct.pack('>2I', 3, UI))
    u.rc = u.call(desc[1], [API])
    u.desc = desc
    return u


def check_init(image, sym, offs):
    for version in (10, 20):
        u = start(image, sym, offs, version)
        assert u.rc == 1 and not u.interest, version
    u = start(image, sym, offs, 21)
    # nothing is tuned yet: registered once with an empty mask (an unregistered subscriber would hear every op)
    assert u.rc == 0 and u.interest == [(sym['mt_native'], 0)], u.interest
    assert u.desc[3] == 2
    u.call(sym['mt_settings'], [0x48009000])
    assert u.rows == [(0x48009000, (sym['mt_tuning_label'], sym['mt_tuning_select'], sym['mt_draw'],
                                    sym['mt_tuning_change'])),
                      (0x48009000, (sym['mt_root_label'], sym['mt_root_select'], sym['mt_draw'],
                                    sym['mt_root_change'])),
                      (0x48009000, (sym['mt_tracks_label'], sym['mt_tracks_select'], sym['mt_draw'],
                                    sym['mt_tracks_change']))], u.rows
    print('init: refuses host API < 21; registers once with no interest; three SETTINGS rows through additem')


def check_map(image, sym, offs):
    tunings = factory()
    assert len(tunings) == 10
    u = start(image, sym, offs, 21)
    fn = DATA + 0x900
    u.u.mem_write(fn, struct.pack('>I', VIEW))
    assert u.text('mt_tuning_text') == 'TUNING: OFF' and u.text('mt_root_text') == 'TUNE ROOT: C'
    assert u.pitch(SWEEP) == (0, SWEEP) and u.pitch(SWEEP2) == (0, SWEEP2)          # OFF: untouched
    cases = 0
    for n in range(1, 11):
        u.call(sym['mt_tuning_select'], [fn])
        assert u.text('mt_tuning_text') == 'TUNING: ' + NAMES[n]
        for root in range(12):
            for vals in (SWEEP, SWEEP2):
                r, got = u.pitch(vals)
                assert r == 0 and got == [ref(tunings[n - 1], root, p) for p in vals], (NAMES[n], root)
                cases += len(vals)
            u.call(sym['mt_root_select'], [fn])
        assert u.text('mt_root_text') == 'TUNE ROOT: C'                               # 12 steps wrap
    assert len(u.invalidated) == 10 * 13 and set(u.invalidated) == {VIEW + 0x38}
    # POOLE ROD12, root C: E (step 4) is 17.5 cents sharp, an exact semitone gets exactly its offset
    r, got = u.pitch([64 << 16])
    assert got == [(64 << 16) + q16(175)]
    # count, other operations, NULL args
    r, got = u.pitch(SWEEP, count=3)
    assert got[3:] == SWEEP[3:] and got[:3] != SWEEP[:3]
    assert u.pitch(SWEEP, op=21) == (0, SWEEP)
    assert u.call(sym['mt_native'], [PITCH, 0, RESULT]) == 0
    u.call(sym['mt_tuning_select'], [fn])                                              # wraps to OFF
    assert u.text('mt_tuning_text') == 'TUNING: OFF' and u.pitch(SWEEP) == (0, SWEEP)
    u.call(sym['mt_tuning_change'], [fn, 0xffffffff])                                  # LEFT from OFF: last
    assert u.text('mt_tuning_text') == 'TUNING: POOLE ROD12'
    u.call(sym['mt_tuning_change'], [fn, 1])
    assert u.text('mt_tuning_text') == 'TUNING: OFF'
    u.call(sym['mt_root_change'], [fn, 0xffffffff])
    assert u.text('mt_root_text') == 'TUNE ROOT: B'
    u.call(sym['mt_root_change'], [fn, 1])
    assert u.text('mt_root_text') == 'TUNE ROOT: C'
    u.call(sym['mt_tuning_select'], [0])                                               # no view: no redraw
    assert len(u.invalidated) == 10 * 13 + 5
    print('map: %d pitches over 10 tunings x 12 roots match mt_map; OFF and 12-TET pass; count, '
          'other ops and NULL args respected; YES/LEFT/RIGHT step and wrap, redraw' % cases)


def check_mask(image, sym, offs):
    tunings = factory()
    fn = DATA + 0x900
    u = start(image, sym, offs, 21)
    u.u.mem_write(fn, struct.pack('>I', VIEW))
    live = lambda: u.interest[-1][1]
    assert u.text('mt_tracks_text') == 'ALL ON'
    for _ in range(9):
        u.call(sym['mt_tuning_select'], [fn])                                          # ... ARABIC RAST
    assert u.text('mt_tuning_text') == 'TUNING: ARABIC RAST' and live() == 1 << PITCH
    ref_all = [ref(tunings[8], 0, p) for p in SWEEP]
    assert u.pitch(SWEEP)[1] == ref_all
    u.call(sym['mt_tracks_change'], [fn, 1])                                           # ALL -> T1
    assert u.text('mt_tracks_text') == 'T1 ON'
    inv = len(u.invalidated)
    n_prefs = len(u.pref_calls)
    u.call(sym['mt_tracks_select'], [fn])
    assert u.text('mt_tracks_text') == 'T1 OFF'
    got = u.pitch(SWEEP)[1]
    assert got[0] == SWEEP[0] and got[1:] == ref_all[1:] and live() == 1 << PITCH
    u.call(sym['mt_tracks_change'], [fn, 1])
    for _ in range(10):
        u.call(sym['mt_tracks_change'], [fn, 1])                                       # T2 .. T11
    assert u.text('mt_tracks_text') == 'T12 ON'
    u.call(sym['mt_tracks_select'], [fn])
    got = u.pitch(SWEEP)[1]
    assert got[0] == SWEEP[0] and got[11] == SWEEP[11] and got[1:11] == ref_all[1:11]
    u.call(sym['mt_tracks_change'], [fn, 1])                                           # -> ALL
    assert u.text('mt_tracks_text') == 'ALL SOME'
    u.call(sym['mt_tracks_select'], [fn])                                              # some -> all on
    assert u.text('mt_tracks_text') == 'ALL ON' and u.pitch(SWEEP)[1] == ref_all
    u.call(sym['mt_tracks_select'], [fn])                                              # all on -> all off
    assert u.text('mt_tracks_text') == 'ALL OFF' and live() == 0 and u.pitch(SWEEP) == (0, SWEEP)
    u.call(sym['mt_tracks_select'], [fn])
    assert live() == 1 << PITCH and u.pitch(SWEEP)[1] == ref_all
    # a flat tuning (12-TET) and OFF clear the interest too
    u.call(sym['mt_tuning_change'], [fn, 0xffffffff])                                  # ... VALLOTTI .. back down
    u.call(sym['mt_tuning_select'], [fn])
    while u.text('mt_tuning_text') != 'TUNING: 12-TET':
        u.call(sym['mt_tuning_select'], [fn])
    assert live() == 0 and u.pitch(SWEEP) == (0, SWEEP)
    u.call(sym['mt_tuning_select'], [fn])
    assert live() == 1 << PITCH
    while u.text('mt_tuning_text') != 'TUNING: OFF':
        u.call(sym['mt_tuning_select'], [fn])
    assert live() == 0 and u.pitch(SWEEP) == (0, SWEEP)
    # moving the cursor redraws but writes no preference; a switch writes one
    n = len(u.pref_calls)
    u.call(sym['mt_tracks_change'], [fn, 1])
    assert len(u.pref_calls) == n and u.invalidated[-1] == VIEW + 0x38
    u.call(sym['mt_tracks_select'], [fn])
    assert len(u.pref_calls) == n + 1
    # the interest only ever moves between 0 and PITCH, and only on a change
    seq = [m for _, m in u.interest]
    assert set(seq) == {0, 1 << PITCH} and all(a != b for a, b in zip(seq, seq[1:]))
    print('mask: T1..T12 and ALL switch per track on a tuned pitch array; interest is PITCH only while a '
          'non-flat tuning is on for at least one track (12-TET, OFF and no track clear it); cursor moves write nothing')


def check_prefs(image, sym, offs):
    fn = DATA + 0x900
    u = start(image, sym, offs, 21)
    assert u.pref_calls == [(4, 3, 0)] and u.text('mt_tuning_text') == 'TUNING: OFF'
    u.call(sym['mt_tuning_change'], [fn, 0xffffffff])
    u.call(sym['mt_root_change'], [fn, 1])
    assert u.prefs[4] == [10, 1, 0xfff] and u.pref_calls[-1] == (4, 3, 1)
    v = start(image, sym, offs, 21, stored=u.prefs[4])                               # power-on: kept
    assert v.text('mt_tuning_text') == 'TUNING: POOLE ROD12' and v.text('mt_root_text') == 'TUNE ROOT: C#'
    tunings = factory()
    assert v.pitch(SWEEP)[1] == [ref(tunings[9], 1, p) for p in SWEEP]                # applied at start
    u.call(sym['mt_tracks_change'], [fn, 1])
    u.call(sym['mt_tracks_select'], [fn])                                              # T1 off
    assert u.prefs[4] == [10, 1, 0xffe]
    v = start(image, sym, offs, 21, stored=u.prefs[4])
    got = v.pitch(SWEEP)[1]
    assert got[0] == SWEEP[0] and got[1:] == [ref(tunings[9], 1, p) for p in SWEEP[1:]]
    assert v.interest[-1] == (sym['mt_native'], 1 << PITCH)                            # tuned at start
    for bad in ([11, 0, 0xfff], [3, 12, 0xfff], [3, 0, 0x1000], [0xffffffff, 0, 0xfff]):
        w = start(image, sym, offs, 21, stored=bad)
        assert w.text('mt_tuning_text') == 'TUNING: OFF' and w.text('mt_root_text') == 'TUNE ROOT: C', bad
    old = start(image, sym, offs, 21, stored=[5, 3])                                   # 2-word prototype record
    assert old.text('mt_tuning_text') == 'TUNING: OFF' and old.interest == [(sym['mt_native'], 0)]
    print('prefs: reads key 4 {tuning, root, track mask} at start and applies it, writes each change, '
          'out-of-range values ignored')


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--cc', default=shutil.which(os.environ.get('ELEKLOADER_CROSS', 'm68k-elf-') + 'gcc'))
    args = a.parse_args()
    if not args.cc:
        print('SKIP: no ColdFire compiler'); return
    image, sym = build(args.cc)
    offs = offsets(args.cc)
    check_init(image, sym, offs)
    check_map(image, sym, offs)
    check_mask(image, sym, offs)
    check_prefs(image, sym, offs)


if __name__ == '__main__':
    main()
