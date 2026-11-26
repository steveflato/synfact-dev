/* SPDX-License-Identifier: GPL-2.0-or-later
 * perffx-st 0.5.1 (prototype): performance effects on the 16 trig keys. See README.md.
 *
 * Double-tap TRK to open (the first tap keeps its native action; the second
 * is taken, as the recorder does with SOUND). While it is open the 16 trig
 * keys are effects: hold to apply and release to resume. Press FUNC while
 * one or more are held and they latch on, so the keys can be let go; press
 * a latched effect's key and it turns off. PAGE switches the two banks, TRK
 * held plus a trig key 1..12 includes or excludes that track (all are back
 * in when the mode is closed; FUNC + a key is the OS's quick mute and is not ours). NO closes it and turns everything off. Other keys (PLAY, STOP, FUNC, the
 * encoders) stay native.
 *
 * Two engines, both on the core's native bus, both declared only while a key
 * of theirs is down (zero cost otherwise):
 *   pitch  ST_NATIVE_PITCH (core 2.9.0-mt1, host API 21): the 12 tracks' Q16
 *          pitches (1.0 = a semitone), edited in place per audio block.
 *   gate   ST_NATIVE_NOTE_ACCEPT: a note-on of a gated track is skipped.
 * LEDs: while it is open the module owns an invisible view on the UI stack
 * (made as arp-st makes its own) that is a LedHandler, so the 16 trig keys
 * are lit by category: blue pitch jumps, orange ramps, red mutes, dark where
 * a key has no effect; white while held, yellow when latched. With TRK held
 * they show the tracks instead: green in, dim red out, cyan = all.
 * Not here yet: stutter, bit crush, step effects, a kept track selection. */
#include <syntakt/modules.h>
#include <syntakt/host.h>
#include <syntakt/native.h>

#define PF_TRACKS 12u
#define PF_KEY_FUNC 1u
#define PF_KEY_TRK 2u
#define PF_KEY_NO 13u
#define PF_KEY_PAGE 18u
#define PF_KEY_TRIG0 24u            /* trig keys 24..39 */
#define PF_DOUBLE_TICKS 12u         /* 30 ticks a second: 400 ms */
#define PF_SEMI (1 << 16)
#define PF_MAXP (127 << 16)
#include "perffx.h"
#define PF_KIND_SEQ 1u
#define PF_NOTE_ON 1u

#define fw_fillrect ((void (*)(void *,int,int,int,int,unsigned int))0x400f7fb6u)
#define UI_CALL(a,t) ((t)(a))
#define PF_VIEW_BYTES 0x50u         /* the view, then its LedHandler (vptr and a 64-bit claim mask) */
#define PF_LED_BYTES 12u

/* kind: PF_KIND_* (perffx.h): 0 none, 1 jump (instant offset), 2 ramp to a
 * target at a step per audio block (1500 a second), 3 gate, 4 audio (the
 * engine's; only lit and reported here) */
struct pf_fx { const char *name; unsigned int kind; int target; int step; unsigned int mask; };
static const struct pf_fx pf_table[2][16] = {{
    {"OCT UP",     1,  12 * PF_SEMI, 0, 0},
    {"OCT DOWN",   1, -12 * PF_SEMI, 0, 0},
    {"FIFTH UP",   1,   7 * PF_SEMI, 0, 0},
    {"FIFTH DOWN", 1,  -7 * PF_SEMI, 0, 0},
    {"TAPE STOP",  2, -96 * PF_SEMI, 1398, 0},      /* about 3 s */
    {"STOP FAST",  2, -96 * PF_SEMI, 8988, 0},      /* about 0.5 s */
    {"HALF SPEED", 2, -12 * PF_SEMI, 2621, 0},      /* an octave down in 0.3 s */
    {"DIVE",       2, -36 * PF_SEMI, 393, 0},       /* about 4 s */
    {"RISE",       2,  24 * PF_SEMI, 262, 0},       /* about 4 s */
    {"",           0,  0, 0, 0},
    {"MUTE ALL",   3,  0, 0, 0xfffu},
    {"MUTE ODD",   3,  0, 0, 0x555u},               /* tracks 1,3,5.. (bit 0 is track 1) */
    {"MUTE EVEN",  3,  0, 0, 0xaaau},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0}},{
    {"2 OCT UP",   1,  24 * PF_SEMI, 0, 0},
    {"2 OCT DOWN", 1, -24 * PF_SEMI, 0, 0},
    {"SEMI UP",    1,   1 * PF_SEMI, 0, 0},
    {"SEMI DOWN",  1,  -1 * PF_SEMI, 0, 0},
    {"LONG STOP",  2, -96 * PF_SEMI, 350, 0},       /* about 12 s */
    {"TRITONE",    1,   6 * PF_SEMI, 0, 0},
    {"SLOW RISE",  2,  24 * PF_SEMI, 105, 0},       /* about 10 s */
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0}}};
#define PF_FX(id) (&pf_table[(id) >> 4][(id) & 15u])    /* an effect id is bank * 16 + key */

static const struct st_machine_host_api *pf_host __attribute__((section(".data"))) = 0;
static unsigned int pf_declared __attribute__((section(".data"))) = ~0u;
static volatile unsigned int pf_open __attribute__((section(".data"))) = 0;
static volatile unsigned int pf_held __attribute__((section(".data"))) = 0;      /* bit id: the effect is on (held or latched) */
static unsigned int pf_phys __attribute__((section(".data"))) = 0;               /* bit id: its key is down */
static unsigned int pf_latch __attribute__((section(".data"))) = 0;              /* bit id: latched on */
static unsigned int pf_down __attribute__((section(".data"))) = 0;              /* bit k: trig key k+1 went down while open: its release is ours */
static unsigned int pf_bank __attribute__((section(".data"))) = 0;
static unsigned int pf_last __attribute__((section(".data"))) = 0;               /* the effect id last pressed or latched: the one the bar names */
static unsigned int pf_trk_down __attribute__((section(".data"))) = 0;           /* TRK is held in the mode: the trig keys pick tracks */
static unsigned char *pf_view __attribute__((section(".data"))) = 0;             /* the LED view, while open */
static unsigned char *pf_manager __attribute__((section(".data"))) = 0;
static unsigned int pf_vtable[19] __attribute__((section(".data")));
static unsigned int pf_func __attribute__((section(".data"))) = 0;               /* bit k: that press was a track toggle */
static unsigned char pf_key_fx[16] __attribute__((section(".data")));            /* the effect id key k started */
static volatile unsigned int pf_include __attribute__((section(".data"))) = 0xfffu;  /* bit t: track t+1 is affected */
static volatile int pf_cur[32] __attribute__((section(".data")));                /* a ramp's current offset */
static unsigned int pf_ticks __attribute__((section(".data"))) = 0;
static unsigned int pf_trk_at __attribute__((section(".data"))) = 0;
static unsigned int pf_trk_pending __attribute__((section(".data"))) = 0;
static unsigned int pf_swallow __attribute__((section(".data"))) = 0;            /* bit key: a non-trig key whose release is ours */
static unsigned int pf_dirty __attribute__((section(".data"))) = 0;
unsigned int pf_calls __attribute__((section(".data"))) = 0;                     /* pitch hook calls: the rate check */
unsigned int pf_dropped __attribute__((section(".data"))) = 0;

int pf_native(unsigned int op, const unsigned int *a, unsigned int *result);

/* The core wants interest changes from init, the audio IRQ or an
 * IRQ-masked section (see microtune-st). */
static void pf_interest(unsigned int mask)
{
    unsigned short sr;
    if (mask == pf_declared || !pf_host) return;
    __asm__ volatile("move.w %%sr,%0\n\tmove.w #0x2700,%%sr" : "=d"(sr) : : "memory");
    pf_host->native_interest((void (*)(void))pf_native, mask);
    __asm__ volatile("move.w %0,%%sr" : : "d"(sr) : "memory");
    pf_declared = mask;
}

static unsigned int pf_gate_mask(void)
{
    unsigned int k, m = 0, h = pf_held;
    for (k = 0; k < 32; k++) if ((h >> k & 1u) && PF_FX(k)->kind == 3) m |= PF_FX(k)->mask;
    return m & pf_include;
}

volatile struct pf_state pf_state __attribute__((section(".data"))) =
    {PF_STATE_VERSION, 0, 0, 0, 0, 0, 0, 0xfffu, 0};
unsigned int pf_shown(void);
/* The hand-off (perffx.h): after every change of the effects, the mode or the tracks. */
static void pf_publish(void)
{
    unsigned int k, audio = 0;
    for (k = 0; k < 32; k++) if ((pf_held >> k & 1u) && PF_FX(k)->kind == PF_KIND_AUDIO) audio |= 1u << k;
    pf_state.generation++;
    __asm__ volatile("" ::: "memory");
    pf_state.open = pf_open;
    pf_state.active = pf_held;
    pf_state.held = pf_phys;
    pf_state.latched = pf_latch;
    pf_state.audio = audio;
    pf_state.include = pf_include;
    pf_state.shown = pf_shown();
    __asm__ volatile("" ::: "memory");
    pf_state.generation++;
}

static void pf_refresh(void)
{
    unsigned int k, mask = 0;
    pf_held = pf_phys | pf_latch;
    for (k = 0; k < 32; k++) {
        if (!(pf_held >> k & 1u)) { pf_cur[k] = 0; continue; }
        if (PF_FX(k)->kind == 1 || PF_FX(k)->kind == 2) mask |= 1u << ST_NATIVE_PITCH;
        else if (PF_FX(k)->kind == 3) mask |= 1u << ST_NATIVE_NOTE_ACCEPT;
    }
    pf_interest(mask);
    pf_publish();
    pf_dirty = 1;
}

/* MAIN IRQ, once per audio block while a pitch key is down. */
int pf_native(unsigned int op, const unsigned int *a, unsigned int *result)
{
    if (op == ST_NATIVE_PITCH) {
        int *p, total = 0, v;
        unsigned int k, i, n, h = pf_held, inc = pf_include;
        if (!a || !a[0]) return 0;
        for (k = 0; k < 32; k++) {
            const struct pf_fx *f = PF_FX(k);
            if (!(h >> k & 1u)) continue;
            if (f->kind == 1) total += f->target;
            else if (f->kind == 2) {
                int c = pf_cur[k];
                if (f->target < 0) { c -= f->step; if (c < f->target) c = f->target; }
                else { c += f->step; if (c > f->target) c = f->target; }
                pf_cur[k] = c;
                total += c;
            }
        }
        if (!total) return 0;
        p = (int *)a[0];
        n = a[1] < PF_TRACKS ? a[1] : PF_TRACKS;
        for (i = 0; i < n; i++) {
            if (!(inc >> i & 1u)) continue;
            v = p[i] + total;
            if (v < 0) v = 0;
            if (v > PF_MAXP) v = PF_MAXP;
            p[i] = v;
        }
        pf_calls++;
        return 0;
    }
    if (op == ST_NATIVE_NOTE_ACCEPT) {
        const unsigned int *e;
        unsigned int mask;
        if (!a || !a[0]) return 0;
        e = (const unsigned int *)a[0];
        if (e[1] != PF_NOTE_ON || e[2] >= PF_TRACKS) return 0;
        mask = pf_gate_mask();
        if (!(mask >> e[2] & 1u)) return 0;
        pf_dropped++;
        *result = 1;
        return 1;
    }
    return 0;
}

static unsigned int be32(const unsigned char *p)
{
    return (unsigned int)p[0] << 24 | (unsigned int)p[1] << 16 | (unsigned int)p[2] << 8 | p[3];
}

/* The LED view, made as arp-st's (mods/arp-st/view.c): a View followed by a
 * LedHandler, on the UI stack, not opaque and drawing nothing, so the
 * MainScreen stays visible and keys it does not take pass through. Each UI
 * frame (400094f4) clears the key LEDs and the stack offers them from the top
 * down; the first setter of an LED wins (40008220). */
static const char pf_type_name[] = "10PerfFxView";
static const unsigned int pf_type[8] = {0x4024979cu, (unsigned int)pf_type_name, 0, 2,
    0x40241ebcu, 2u, 0x401c73a4u, (PF_VIEW_BYTES << 8) | 2u};

static unsigned int pf_word(const void *v, unsigned int off)
{
    return *(const unsigned int *)((const unsigned char *)v + off);
}
static unsigned char *pf_stack_top(unsigned char *manager)
{
    unsigned char *n = *(unsigned char **)(manager + 0x18);
    return n && n != manager + 0x14 ? *(unsigned char **)(n + 8) : 0;
}
static int pf_foreground(void)
{
    return pf_view && pf_manager && !pf_view[0x30] && pf_stack_top(pf_manager) == pf_view;
}

/* A palette of the stock table (see arp-st): 2 unlit, 3 red, 4 dim red, 9 green,
 * 14 blue, 20 white, 23 yellow, 31 orange. Mode 1 forces unlit, 2 takes the palette. */
static unsigned int pf_palette(unsigned int k, unsigned int *mode)
{
    unsigned int id = pf_bank * 16u + k;
    *mode = 2;
    if (pf_trk_down) {                             /* TRK held: the tracks the effects touch */
        if (k < PF_TRACKS) return (pf_include >> k & 1u) ? 9u : 4u;
        *mode = 1;
        return 2u;
    }
    if (!PF_FX(id)->kind) { *mode = 1; return 2u; }
    if (pf_latch >> id & 1u) return 23u;           /* latched */
    if (pf_phys >> id & 1u) return 20u;            /* held */
    return PF_FX(id)->kind == PF_KIND_JUMP ? 14u : PF_FX(id)->kind == PF_KIND_RAMP ? 31u :
           PF_FX(id)->kind == PF_KIND_AUDIO ? 26u : 3u;      /* 26 magenta: the audio effects */
}
static void pf_led_nothing(void *handler) { (void)handler; }
static void pf_view_leds(void *handler, void *claimed, void *offered)
{
    unsigned int k, mode, palette;
    void *mgr;
    (void)claimed; (void)offered;
    if (!pf_view || handler != pf_view + PF_VIEW_BYTES || !pf_open || !pf_foreground()) return;
    mgr = UI_CALL(0x4016efa4u, void *(*)(void))();
    for (k = 0; k < 16; k++) {
        palette = pf_palette(k, &mode);
        UI_CALL(0x40008220u, void (*)(void *, unsigned int, unsigned int, unsigned int))(mgr, k + 1, mode, palette);
    }
}
static const unsigned int pf_led_vtable[5] = {(unsigned int)-(int)PF_VIEW_BYTES, (unsigned int)pf_type,
    (unsigned int)pf_led_nothing, (unsigned int)pf_led_nothing, (unsigned int)pf_view_leds};

static void pf_forget(void *v)
{
    if (v == pf_view) { pf_view = 0; pf_manager = 0; }
}
/* The view's destructor slot: the stack let go of it (closed here, or by the
 * OS). The effects end with it. */
static void pf_dispose(void *v)
{
    if (v == pf_view) {
        pf_open = 0;
        pf_phys = 0;
        pf_latch = 0;
        pf_trk_down = 0;
        pf_forget(v);
        pf_refresh();
    }
    UI_CALL(0x400fffc0u, void (*)(void *))(v);
}

/* MainScreen::show publishes these persistent children; the stack top is one
 * of them (or the MainScreen) when nothing else is open (arp-st's arp_can_open). */
static int pf_can_open(void *app, unsigned char *main)
{
    static const unsigned short children[] = {0x220, 0xc0, 0xf0, 0x218, 0xe8, 0x228};
    unsigned char *top = pf_stack_top((unsigned char *)app + 0x40);
    unsigned int i;
    if (!top) return 0;
    if (top == main) return 1;
    for (i = 0; i < sizeof(children) / sizeof(children[0]); i++)
        if (top == *(unsigned char **)(main + children[i])) return 1;
    return 0;
}

static int pf_open_view(void *app)
{
    unsigned char *m = (unsigned char *)app + 0x40, *n = *(unsigned char **)(m + 0x1c), *main, *root, *v;
    void *pair[2];
    unsigned int i;
    if (pf_view || !pf_host || !pf_host->storage) return 0;
    main = n && n != m + 0x14 ? *(unsigned char **)(n + 8) : 0;                /* the highest opaque view */
    if (!main || pf_word(main, 0) != 0x401c849cu || !pf_can_open(app, main)) return 0;
    root = pf_host->storage->alloc(16 + PF_VIEW_BYTES + PF_LED_BYTES);
    if ((unsigned int)root < 0x40000000u) return 0;
    *(unsigned int *)root = 0x401c9910u; *(unsigned int *)(root + 4) = 1; *(unsigned int *)(root + 8) = 1;
    v = root + 16;
    *(void **)(root + 12) = v;
    UI_CALL(0x400ffd6au, void (*)(void *, const char *))(v, "PERF FX");
    UI_CALL(0x400ffcb8u, void (*)(void *, unsigned int))(v, 0);                  /* not opaque */
    for (i = 0; i < 19; i++) pf_vtable[i] = ((const unsigned int *)0x40241ef4u)[i];
    pf_vtable[1] = (unsigned int)pf_type;
    pf_vtable[2] = (unsigned int)pf_dispose;
    *(unsigned int **)v = pf_vtable + 2;
    *(const unsigned int **)(v + PF_VIEW_BYTES) = pf_led_vtable + 2;
    *(unsigned int *)(v + PF_VIEW_BYTES + 4) = 0;
    *(unsigned int *)(v + PF_VIEW_BYTES + 8) = 0;
    pf_view = v;
    pf_manager = m;
    pair[0] = v; pair[1] = root;
    UI_CALL(0x40100d86u, unsigned int (*)(void *, void *, unsigned int))(m, pair, 0);   /* push */
    UI_CALL(0x4016dfacu, void (*)(void *))(pair + 1);
    UI_CALL(0x400ffe22u, void (*)(void *))(v);
    return 1;
}

static void pf_close(void)
{
    unsigned char *v = pf_view;
    pf_open = 0;
    pf_phys = 0;
    pf_latch = 0;
    pf_trk_down = 0;
    pf_include = 0xfffu;                           /* every track is back in the next time */
    pf_refresh();
    if (v) {
        UI_CALL(0x400ffec6u, void (*)(void *))(v);
        pf_forget(v);
    }
}

/* ev_key, UI task. flags: bit 0 down, bit 1 FUNC held (the OS then puts its mute view on top), bit 3 repeat; a release has bit 0 clear. */
int pf_key(void *brain, const void *ev)
{
    const unsigned char *e = ev;
    unsigned int key = be32(e + 12), flags = be32(e + 16);
    int down = flags & 1u, repeat = (flags & 8u) != 0;

    if (!down) {                                   /* releases */
        if (key == PF_KEY_TRK) { pf_trk_down = 0; pf_dirty = 1; }
        if (key < 32 && (pf_swallow >> key & 1u)) { pf_swallow &= ~(1u << key); return 1; }
        if (key >= PF_KEY_TRIG0 && key < PF_KEY_TRIG0 + 16 && (pf_down >> (key - PF_KEY_TRIG0) & 1u)) {
            unsigned int k = key - PF_KEY_TRIG0, id = pf_key_fx[k];
            pf_down &= ~(1u << k);
            if (pf_func >> k & 1u) { pf_func &= ~(1u << k); return 1; }
            if (pf_phys >> id & 1u) {                  /* momentary: off with the key */
                pf_phys &= ~(1u << id);
                pf_refresh();
            }
            return 1;
        }
        return 0;
    }
    if (!pf_open) {                                /* double-tap TRK */
        if (key != PF_KEY_TRK) { pf_trk_pending = 0; return 0; }
        if (repeat) return 0;
        if (pf_trk_pending && pf_ticks - pf_trk_at <= PF_DOUBLE_TICKS) {
            pf_trk_pending = 0;
            if (!pf_open_view(brain)) return 0;    /* something else is open: stays native */
            pf_open = 1;
            pf_publish();
            pf_swallow |= 1u << PF_KEY_TRK;
            pf_dirty = 1;
            return 1;
        }
        pf_trk_at = pf_ticks;
        pf_trk_pending = 1;
        return 0;
    }
    if (!pf_foreground()) return 0;                /* a menu or the track selection is on top: its keys */
    if (key == PF_KEY_NO) {                        /* closes, turns everything off, keeps NO's release */
        if (!repeat) { pf_swallow |= 1u << PF_KEY_NO; pf_close(); }
        return 1;
    }
    if (key == PF_KEY_TRK) {                       /* TRK held + a trig key 1..12: which tracks the effects touch */
        if (repeat) return 1;
        pf_trk_down = 1;
        pf_swallow |= 1u << PF_KEY_TRK;
        pf_dirty = 1;
        return 1;
    }
    if (key == PF_KEY_FUNC) {                      /* FUNC while effects are held: they latch, and the keys can go */
        if (repeat) return (pf_swallow >> PF_KEY_FUNC) & 1u;
        if (!pf_phys) return 0;                    /* otherwise native (the OS's own FUNC) */
        pf_latch |= pf_phys;
        pf_phys = 0;
        pf_swallow |= 1u << PF_KEY_FUNC;
        pf_refresh();
        return 1;
    }
    if (key == PF_KEY_PAGE) {
        if (flags & 2u) return 0;                  /* FUNC + PAGE is the OS's (Length, Scale ...) */
        if (!repeat) { pf_swallow |= 1u << PF_KEY_PAGE; pf_bank ^= 1u; pf_dirty = 1; }
        return 1;
    }
    if (key >= PF_KEY_TRIG0 && key < PF_KEY_TRIG0 + 16) {
        unsigned int k = key - PF_KEY_TRIG0, id;
        if (repeat) return 1;
        pf_down |= 1u << k;
        if (pf_trk_down) {                         /* TRK + trig: which tracks the effects touch */
            pf_func |= 1u << k;
            if (k < PF_TRACKS) { pf_include ^= 1u << k; pf_publish(); }
            pf_dirty = 1;
            return 1;
        }
        id = pf_bank * 16u + k;
        pf_key_fx[k] = (unsigned char)id;
        if (PF_FX(id)->kind) {
            pf_last = id;
            if (pf_latch >> id & 1u) pf_latch &= ~(1u << id);       /* a latched effect: this press turns it off */
            else pf_phys |= 1u << id;                                /* otherwise on while the key is down */
            pf_refresh();
        }
        return 1;
    }
    return 0;
}

void pf_tick(void *ctrl)
{
    pf_ticks++;
    if (pf_dirty) { pf_dirty = 0; ((unsigned char *)ctrl)[0x20] = 1; }
}

/* A 3x5 font, as system-info-st: digits, A-Z, space. */
static const unsigned char pf_font[36][5] = {
 {7,5,5,5,7},{2,6,2,2,7},{7,1,7,4,7},{7,1,7,1,7},{5,5,7,1,1},
 {7,4,7,1,7},{7,4,7,5,7},{7,1,1,1,1},{7,5,7,5,7},{7,5,7,1,7},
 {2,5,7,5,5},{6,5,6,5,6},{7,4,4,4,7},{6,5,5,5,6},{7,4,6,4,7},
 {7,4,6,4,4},{7,4,5,5,7},{5,5,7,5,5},{7,2,2,2,7},{1,1,1,5,7},
 {5,5,6,5,5},{4,4,4,4,7},{5,7,7,5,5},{5,7,7,7,5},{7,5,5,5,7},
 {6,5,6,4,4},{7,5,5,7,1},{6,5,6,5,5},{7,4,7,1,7},{7,2,2,2,2},
 {5,5,5,5,7},{5,5,5,5,2},{5,5,7,7,5},{5,5,2,5,5},{5,5,2,2,2},
 {7,1,2,4,7}};

static void pf_text(void *bmp, int x, int base, const char *s)
{
    int r, c;
    const unsigned char *g;
    for (; *s && x <= 124; s++, x += 4) {
        if (*s >= '0' && *s <= '9') g = pf_font[*s - '0'];
        else if (*s >= 'A' && *s <= 'Z') g = pf_font[10 + *s - 'A'];
        else continue;
        for (r = 0; r < 5; r++)
            for (c = 0; c < 3; c++)
                if (g[r] & (4u >> c)) fw_fillrect(bmp, x + c, base - r, x + c, base - r, 1);
    }
}

/* The effect the bar names: a held one before a latched one, the last pressed
 * of a set first, else the lowest. 0 when none; else the effect id + 1. */
static unsigned int pf_pick(unsigned int mask)
{
    unsigned int k;
    if (!mask) return 0;
    if (mask >> pf_last & 1u) return pf_last + 1;
    for (k = 0; k < 32; k++) if (mask >> k & 1u) return k + 1;
    return 0;
}
unsigned int pf_shown(void)
{
    unsigned int id = pf_pick(pf_phys);
    return id ? id : pf_pick(pf_latch);
}

void pf_draw(void *bmp, void *ctrl)
{
    unsigned int k, t, n = 0, id, inc = pf_include;
    char head[10] = "PERF FX 1", latched[3] = "L1";
    (void)ctrl;
    if (!pf_open) return;
    head[8] = (char)('1' + pf_bank);
    fw_fillrect(bmp, 18, 54, 127, 63, 0);
    pf_text(bmp, 20, 60, head);
    id = pf_shown();
    if (id) pf_text(bmp, 60, 60, PF_FX(id - 1)->name);
    for (k = 0; k < 32; k++) n += pf_latch >> k & 1u;
    if (n) {                                       /* how many are latched */
        latched[1] = (char)('0' + (n > 9 ? 9 : n));
        pf_text(bmp, 108, 60, latched);
    }
    for (t = 0; t < PF_TRACKS; t++) {              /* the tracks the effects touch: a block, or a dot when left out */
        int x = 20 + 6 * (int)t;
        if (inc >> t & 1u) fw_fillrect(bmp, x, 62, x + 3, 63, 1);
        else fw_fillrect(bmp, x, 63, x, 63, 1);
    }
}

static int initialize(const struct st_smz_api *api)
{
    const struct st_machine_host_api *h;
    if (!api || !api->ui || !(h = api->ui->host) || h->version < 21 || !h->native_interest) return 1;
    pf_host = h;
    pf_interest(0);                 /* registers once with nothing: an unregistered subscriber hears everything */
    return 0;
}

static const struct st_smz_subscription subscriptions[] = {
    {ST_EV_TICK, (void (*)(void))pf_tick},
    {ST_EV_DRAW, (void (*)(void))pf_draw},
    {ST_EV_KEY, (void (*)(void))pf_key},
    {ST_EV_NATIVE, (void (*)(void))pf_native}};
const struct st_smz_module perffx_module = {"PERF FX", initialize, subscriptions, 4};
