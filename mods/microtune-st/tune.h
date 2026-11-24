/* SPDX-License-Identifier: GPL-2.0-or-later
 * microtune-st: the tuning map. Pure arithmetic, no OS calls, so the same
 * code runs on the instrument and in the host tests.
 *
 * A tuning is 12 offsets from 12-TET in tenths of a cent, one per semitone
 * above ROOT (0 = C ... 11 = B), repeating every octave; |offset| <= 1000
 * (100 cents). Pitches are Q16 semitones, as the OS's per-track pitch
 * (60 << 16 is C4).
 *
 * mt_prepare() turns a tuning into Q16 once, when it changes. mt_map() is
 * then a few integer operations per pitch (one remainder by 12, no 64-bit
 * arithmetic): a pitch between two semitones moves by the straight line
 * between their offsets, so slides, glides and pitch modulation stay smooth
 * and an exact semitone gets exactly its step's offset. 12-TET (all zero)
 * returns every pitch unchanged.
 */
#ifndef MICROTUNE_TUNE_H
#define MICROTUNE_TUNE_H
typedef int mt_s32;

struct mt_table {
    mt_s32 q16[13];                     /* the 12 offsets in Q16, and [12] = [0] */
    mt_s32 root;                        /* 0..11 */
    mt_s32 flat;                        /* every offset zero: mt_map returns the pitch */
};

/* tenths of a cent -> Q16 semitones (65536 / 1000 per unit), rounded to nearest */
static inline mt_s32 mt_tenths_q16(mt_s32 t)
{
    mt_s32 a = t < 0 ? -t : t;
    mt_s32 q = (a * 8389 + 64) >> 7;    /* 65536/1000 = 65.536 ~ 8389/128: 0.005 % high, < 0.01 cent */
    return t < 0 ? -q : q;
}

static inline void mt_prepare(struct mt_table *t, const short off[12], int root)
{
    int i;
    t->flat = 1;
    for (i = 0; i < 12; i++) {
        short o = off[i];
        if (o > 1000) o = 1000;
        if (o < -1000) o = -1000;
        t->q16[i] = mt_tenths_q16(o);
        if (o) t->flat = 0;
    }
    t->q16[12] = t->q16[0];
    t->root = root < 0 ? 0 : root > 11 ? 11 : root;
}

/* Q16 pitch -> tuned Q16 pitch. |pitch| < 2^30. */
static inline mt_s32 mt_map(const struct mt_table *t, mt_s32 pitch)
{
    mt_s32 semi, frac, k, a, d;
    if (t->flat) return pitch;
    semi = pitch >> 16;                 /* floor, negative pitches included */
    frac = pitch & 0xffff;
    k = (semi - t->root) % 12;
    if (k < 0) k += 12;
    a = t->q16[k];
    d = t->q16[k + 1] - a;              /* |d| <= 2^17 (200 cents): d * (frac >> 4) < 2^29 */
    return pitch + a + ((d * (frac >> 4)) >> 12);
}
#endif
