"""microtune-st: the tuning map, the factory tables and the .scl converter.

Compiles mods/microtune-st/tune.h with the host C compiler (it is plain C with
no OS calls) and compares mt_map with an exact Python reference over every
factory tuning, every root and a sweep of pitches, including negative ones.
With a ColdFire compiler (ELEKLOADER_CROSS or --cross) it also compiles the
header for the instrument with -Wall -Werror.

    python tests/test_st_microtune.py [--cc cc] [--cross m68k-elf-gcc]
"""
import argparse, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'mods/microtune-st'
sys.path.insert(0, str(ROOT / 'tools'))
import microtune  # noqa: E402

TOL = 40          # Q16 units, about 0.06 cent: the interpolation drops 4 bits of the fraction

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include "tune.h"
#include "tunings.h"
int main(void)
{
    short off[12]; int root, n, i; long p;
    struct mt_table t;
    while (scanf("%d", &root) == 1) {
        for (i = 0; i < 12; i++) { int v; if (scanf("%d", &v) != 1) return 1; off[i] = (short)v; }
        if (scanf("%d", &n) != 1) return 1;
        mt_prepare(&t, off, root);
        for (i = 0; i < n; i++) { if (scanf("%ld", &p) != 1) return 1; printf("%d\n", (int)mt_map(&t, (mt_s32)p)); }
    }
    printf("%d\n", MT_FACTORY);
    return 0;
}
'''


def reference(offs, root, p):
    q = [o * 65536 / 1000 for o in offs] + [offs[0] * 65536 / 1000]
    semi, frac = p >> 16, (p & 0xffff) / 65536
    k = (semi - root) % 12
    return p + q[k] + (q[k + 1] - q[k]) * frac


def run(exe, cases):
    text = []
    for root, offs, pitches in cases:
        text.append('%d %s %d %s' % (root, ' '.join(map(str, offs)), len(pitches), ' '.join(map(str, pitches))))
    out = subprocess.run([exe], input='\n'.join(text) + '\n', capture_output=True, text=True, check=True).stdout
    return [int(x) for x in out.split()]


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--cc', default=shutil.which('cc') or shutil.which('gcc'))
    a.add_argument('--cross', default=shutil.which(os.environ.get('ELEKLOADER_CROSS', 'm68k-elf-') + 'gcc'))
    args = a.parse_args()

    # tables: generated file current, Poole Rod 12 equals the user's .scl
    assert (MOD / 'tunings.h').read_text() == microtune.header(), 'run tools/microtune.py --write'
    scl = '''! poole-rod12tone.scl
!
poole-rod12tone
 12
!
 33/32
 9/8
 7/6
 14/11
 4/3
 11/8
 3/2
 14/9
 27/16
 7/4
 11/6
 2/1
'''
    _, poole = microtune.parse_scl(scl)
    factory = dict(microtune.factory())
    assert factory['POOLE ROD12'] == poole == [0, -467, 39, -331, 175, -20, -487, 20, -351, 59, -312, -506]
    assert factory['12-TET'] == [0] * 12
    assert factory['PYTHAGOREAN'][7] == 20 and factory['MEANTONE 1/4'][4] == -137     # 3/2, a pure 5/4
    for bad in ('x\n 7\n 1\n 2\n 3\n 4\n 5\n 6\n 2/1\n', 'x\n 12\n' + ' 101.\n' * 11 + ' 2/1\n',
                'x\n 12\n' + ' 100.\n' * 11 + ' 3/1\n'):
        try:
            microtune.parse_scl(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('accepted ' + bad[:20])
    print('tables: tunings.h current, POOLE ROD12 = the .scl, bad scales refused')

    with tempfile.TemporaryDirectory() as w:
        w = Path(w)
        (w / 'd.c').write_text(DRIVER)
        exe = w / 'd'
        subprocess.run([args.cc, '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(MOD),
                        str(w / 'd.c'), '-o', str(exe)], check=True)
        pitches = list(range(-3 * 65536, 130 * 65536, 4099)) + [k << 16 for k in range(-12, 128)] + \
            [(60 << 16) + f for f in (1, 0x7fff, 0x8000, 0xffff)]
        cases = [(root, offs, pitches) for _, offs in microtune.factory() for root in range(12)]
        got = run(exe, cases)
        assert got[-1] == len(microtune.FACTORY)
        i = worst = 0
        for (name, offs), root in [(f, r) for f in microtune.factory() for r in range(12)]:
            for p in pitches:
                ref = reference(offs, root, p)
                err = abs(got[i] - ref)
                worst = max(worst, err)
                assert err <= TOL, (name, root, p, got[i], ref)
                if p & 0xffff == 0:                                        # a semitone: exactly its step
                    k = ((p >> 16) - root) % 12
                    assert got[i] - p == microtune_q16(offs[k]), (name, root, p)
                if name == '12-TET':
                    assert got[i] == p
                i += 1
        # factory tunings keep pitch rising: no step's offset drops 100 cents below the next
        for name, offs in microtune.factory():
            seq = run(exe, [(0, offs, list(range(0, 24 * 65536, 997)))])[:-1]
            assert all(b >= a for a, b in zip(seq, seq[1:])), name
        # clamping: offsets past 100 cents act as 100
        clamp = run(exe, [(0, [0, 5000, -5000] + [0] * 9, [1 << 16, 2 << 16])])
        assert clamp[:2] == [(1 << 16) + 65539, (2 << 16) - 65539]
        print('map: %d tunings x 12 roots x %d pitches within %d Q16 (worst %.1f), semitones exact, '
              '12-TET identity, rising, clamped' % (len(microtune.FACTORY), len(pitches), TOL, worst))

    if args.cross:
        with tempfile.TemporaryDirectory() as w:
            src = Path(w) / 'c.c'
            src.write_text('#include "tune.h"\n#include "tunings.h"\nint f(struct mt_table *t,int p,int r)'
                           '{mt_prepare(t,mt_factory[9],r);return mt_map(t,p)+mt_factory_name[0][0];}\n')
            subprocess.run([args.cross, '-mcpu=5475', '-Os', '-Wall', '-Werror', '-I', str(MOD), '-c', str(src),
                            '-o', str(Path(w) / 'c.o')], check=True)
        print('ColdFire: tune.h and tunings.h compile with -Wall -Werror')
    else:
        print('ColdFire: skipped (no cross compiler)')


def microtune_q16(t):
    a = abs(t)
    q = (a * 8389 + 64) >> 7
    return -q if t < 0 else q


if __name__ == '__main__':
    main()
