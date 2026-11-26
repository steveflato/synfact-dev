/* SPDX-License-Identifier: GPL-2.0-or-later
 * perffx-st 0.1.0 (prototype): performance effects on the 16 trig keys. See README.md.
 *
 * Double-tap TRK to open (the first tap keeps its native action; the second
 * is taken, as the recorder does with SOUND). While it is open the 16 trig
 * keys are effects: hold to apply, release to resume. NO closes it. Other
 * keys (PLAY, STOP, FUNC, the encoders) stay native.
 *
 * Two engines, both on the core's native bus, both declared only while a key
 * of theirs is down (zero cost otherwise):
 *   pitch  ST_NATIVE_PITCH (core 2.9.0-mt1, host API 21): the 12 tracks' Q16
 *          pitches (1.0 = a semitone), edited in place per audio block.
 *   gate   ST_NATIVE_NOTE_ACCEPT: a note-on of a gated track is skipped.
 * Not here yet: LED colours, banks, per-track include/exclude, stutter,
 * bit crush, step effects, a screen of its own. */
#include <syntakt/modules.h>
#include <syntakt/host.h>
#include <syntakt/native.h>

#define PF_TRACKS 12u
#define PF_KEY_TRK 2u
#define PF_KEY_NO 13u
#define PF_KEY_PAGE 18u
#define PF_KEY_TRIG0 24u            /* trig keys 24..39 */
#define PF_DOUBLE_TICKS 12u         /* 30 ticks a second: 400 ms */
#define PF_SEMI (1 << 16)
#define PF_MAXP (127 << 16)
#define PF_KIND_SEQ 1u
#define PF_NOTE_ON 1u

#define fw_fillrect ((void (*)(void *,int,int,int,int,unsigned int))0x400f7fb6u)

/* kind: 0 none, 1 jump (instant offset), 2 ramp to a target at a step per
 * audio block (1500 a second), 3 gate */
struct pf_fx { const char *name; unsigned int kind; int target; int step; unsigned int mask; };
static const struct pf_fx pf_table[16] = {
    {"OCT UP",     1,  12 * PF_SEMI, 0, 0},
    {"OCT DOWN",   1, -12 * PF_SEMI, 0, 0},
    {"FIFTH UP",   1,   7 * PF_SEMI, 0, 0},
    {"FIFTH DOWN", 1,  -7 * PF_SEMI, 0, 0},
    {"TAPE STOP",  2, -96 * PF_SEMI, 4194, 0},      /* about a second */
    {"STOP FAST",  2, -96 * PF_SEMI, 11984, 0},     /* about a third of a second */
    {"HALF SPEED", 2, -12 * PF_SEMI, 3495, 0},      /* glides an octave down in 0.15 s */
    {"DIVE",       2, -36 * PF_SEMI, 3145, 0},      /* half a second */
    {"RISE",       2,  24 * PF_SEMI, 2097, 0},
    {"",           0,  0, 0, 0},
    {"MUTE ALL",   3,  0, 0, 0xfffu},
    {"MUTE ODD",   3,  0, 0, 0x555u},               /* tracks 1,3,5.. (bit 0 is track 1) */
    {"MUTE EVEN",  3,  0, 0, 0xaaau},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0},
    {"",           0,  0, 0, 0}};

static const struct st_machine_host_api *pf_host __attribute__((section(".data"))) = 0;
static unsigned int pf_declared __attribute__((section(".data"))) = ~0u;
static volatile unsigned int pf_open __attribute__((section(".data"))) = 0;
static volatile unsigned int pf_held __attribute__((section(".data"))) = 0;      /* bit k: trig key k+1 is down and has an effect */
static unsigned int pf_down __attribute__((section(".data"))) = 0;              /* bit k: trig key k+1 went down while open: its release is ours */
static volatile int pf_cur[16] __attribute__((section(".data")));                /* a ramp's current offset */
static unsigned int pf_ticks __attribute__((section(".data"))) = 0;
static unsigned int pf_trk_at __attribute__((section(".data"))) = 0;
static unsigned int pf_trk_pending __attribute__((section(".data"))) = 0;
static unsigned int pf_swallow __attribute__((section(".data"))) = 0;            /* a key whose release is ours */
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
    for (k = 0; k < 16; k++) if ((h >> k & 1u) && pf_table[k].kind == 3) m |= pf_table[k].mask;
    return m;
}

static void pf_refresh(void)
{
    unsigned int k, h = pf_held, mask = 0;
    for (k = 0; k < 16; k++) {
        if (!(h >> k & 1u)) { pf_cur[k] = 0; continue; }
        if (pf_table[k].kind == 1 || pf_table[k].kind == 2) mask |= 1u << ST_NATIVE_PITCH;
        else if (pf_table[k].kind == 3) mask |= 1u << ST_NATIVE_NOTE_ACCEPT;
    }
    pf_interest(mask);
    pf_dirty = 1;
}

/* MAIN IRQ, once per audio block while a pitch key is down. */
int pf_native(unsigned int op, const unsigned int *a, unsigned int *result)
{
    if (op == ST_NATIVE_PITCH) {
        int *p, total = 0, v;
        unsigned int k, i, n, h = pf_held;
        if (!a || !a[0]) return 0;
        for (k = 0; k < 16; k++) {
            const struct pf_fx *f = &pf_table[k];
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

static void pf_close(void)
{
    pf_open = 0;
    pf_held = 0;
    pf_refresh();
}

/* ev_key, UI task. flags: bit 0 down, bit 3 repeat; a release has bit 0 clear. */
int pf_key(void *brain, const void *ev)
{
    const unsigned char *e = ev;
    unsigned int key = be32(e + 12), flags = be32(e + 16);
    int down = flags & 1u, repeat = (flags & 8u) != 0;
    (void)brain;

    if (!down) {                                   /* releases */
        if (pf_swallow && key == pf_swallow) { pf_swallow = 0; return 1; }
        if (key >= PF_KEY_TRIG0 && key < PF_KEY_TRIG0 + 16 && (pf_down >> (key - PF_KEY_TRIG0) & 1u)) {
            pf_down &= ~(1u << (key - PF_KEY_TRIG0));
            pf_held &= ~(1u << (key - PF_KEY_TRIG0));
            pf_refresh();
            return 1;
        }
        return 0;
    }
    if (!pf_open) {                                /* double-tap TRK */
        if (key != PF_KEY_TRK) { pf_trk_pending = 0; return 0; }
        if (repeat) return 0;
        if (pf_trk_pending && pf_ticks - pf_trk_at <= PF_DOUBLE_TICKS) {
            pf_trk_pending = 0;
            pf_open = 1;
            pf_swallow = PF_KEY_TRK;
            pf_dirty = 1;
            return 1;
        }
        pf_trk_at = pf_ticks;
        pf_trk_pending = 1;
        return 0;
    }
    if (key == PF_KEY_NO) {                        /* closes, and keeps NO's release */
        if (!repeat) { pf_swallow = PF_KEY_NO; pf_close(); }
        return 1;
    }
    if (key == PF_KEY_PAGE) return 1;              /* banks come later */
    if (key >= PF_KEY_TRIG0 && key < PF_KEY_TRIG0 + 16) {
        unsigned int k = key - PF_KEY_TRIG0;
        if (!repeat) {
            pf_down |= 1u << k;
            if (pf_table[k].kind) {
                pf_held |= 1u << k;
                pf_refresh();
            }
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

void pf_draw(void *bmp, void *ctrl)
{
    unsigned int k, h = pf_held;
    int x = 20;
    (void)ctrl;
    if (!pf_open) return;
    fw_fillrect(bmp, 18, 54, 127, 63, 0);
    pf_text(bmp, x, 60, "PERF FX");
    x += 36;
    for (k = 0; k < 16; k++)
        if ((h >> k & 1u) && pf_table[k].name[0]) { pf_text(bmp, x, 60, pf_table[k].name); break; }
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
