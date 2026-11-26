/* SPDX-License-Identifier: GPL-2.0-or-later
 * Perf FX hand-off: what the key side tells an audio engine.
 *
 * `pf_state` is written by the UI task only (the keys, the latches, the track
 * picks, the mode closing) and read by anything else: a MAIN IRQ engine, or
 * the UI. Every field is one aligned 32-bit word. Read a consistent set with
 * pf_state_read(): the generation is odd while a write is under way, so a
 * reader keeps its last good copy when it sees an odd or changed generation
 * (never wait in the IRQ).
 *
 * Effects are numbered id = bank * 16 + key (0..31); bit id of a mask is that
 * effect. `kind` in the effect table: PF_KIND_AUDIO is for the audio effects:
 * the key side only lights the key (magenta) and reports it in `audio`; the
 * engine does the rest and decides what the id means.
 */
#ifndef PERFFX_H
#define PERFFX_H

#define PF_STATE_VERSION 1u
#define PF_KIND_NONE 0u
#define PF_KIND_JUMP 1u     /* instant pitch offset (the key side does it) */
#define PF_KIND_RAMP 2u     /* pitch ramp to a target (the key side does it) */
#define PF_KIND_GATE 3u     /* mutes tracks at the note gate (the key side does it) */
#define PF_KIND_AUDIO 4u    /* the audio engine's */

struct pf_state {
    unsigned int version;      /* PF_STATE_VERSION */
    unsigned int generation;   /* odd while the key side is writing */
    unsigned int open;         /* the mode is open */
    unsigned int active;       /* effects on now: held or latched (bit = effect id) */
    unsigned int held;         /* ... of those, the keys that are down */
    unsigned int latched;      /* ... and the latched ones */
    unsigned int audio;        /* active, of the PF_KIND_AUDIO effects */
    unsigned int include;      /* tracks the effects touch: bit 0 is track 1 (12 bits) */
    unsigned int shown;        /* the effect the bar names: id + 1, or 0 */
};
extern volatile struct pf_state pf_state;

/* A consistent copy of pf_state, or 0 when a write was under way (keep the last copy). */
static __inline__ int pf_state_read(struct pf_state *out)
{
    unsigned int g = pf_state.generation;
    __asm__ volatile("" ::: "memory");
    if (g & 1u) return 0;
    out->version = pf_state.version; out->open = pf_state.open; out->active = pf_state.active;
    out->held = pf_state.held; out->latched = pf_state.latched; out->audio = pf_state.audio;
    out->include = pf_state.include; out->shown = pf_state.shown;
    __asm__ volatile("" ::: "memory");
    if (pf_state.generation != g) return 0;
    out->generation = g;
    return 1;
}
#endif
