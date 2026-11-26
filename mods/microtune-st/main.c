/* SPDX-License-Identifier: GPL-2.0-or-later
 * microtune-st: microtuning on the 12 audio tracks. See README.md.
 *
 * Needs ST_NATIVE_PITCH (host API 21, core 2.9.0-mt1 prototype): MAIN's IRQ
 * pass hands the bus the 12 tracks' Q16 pitches at 80004688 before the audio
 * frame reads them, and this module moves each by the tuning (tune.h). The
 * IRQ side reads one pointer and a table; it allocates nothing and never
 * consumes the call.
 *
 * SETTINGS > TUNING picks OFF or a factory tuning; TUNE ROOT the note the
 * tuning starts from; TUNE TRACKS which of the 12 tracks it applies to
 * (LEFT/RIGHT pick a track or ALL, YES switches it). YES steps forward on
 * the other two, LEFT/RIGHT step either way. With host API 20+ the choice
 * is kept in the core's preferences (key ST_PREF_MICROTUNE: tuning, root,
 * track mask) across power-off.
 *
 * Interest in PITCH is declared only while something would change: with
 * TUNING OFF, a flat tuning (12-TET) or no track enabled, MAIN takes the
 * stock path without entering the bus.
 */
#include <syntakt/modules.h>
#include <syntakt/host.h>
#include <syntakt/native.h>
#include <syntakt/persist.h>
#include "tune.h"
#include "tunings.h"

#define MT_TRACKS 12u
#define INVALIDATE ((void (*)(void *))0x400ffe22u)   /* (view + 0x38): redraw */

unsigned int mt_tuning __attribute__((section(".data"))) = 0;   /* 0 off, 1..MT_FACTORY */
unsigned int mt_root __attribute__((section(".data"))) = 0;     /* 0 = C .. 11 = B */
unsigned int mt_mask __attribute__((section(".data"))) = 0xfffu; /* bit t: track t+1 is tuned */
unsigned int mt_cursor __attribute__((section(".data"))) = 12;  /* TUNE TRACKS row: 0..11 a track, 12 ALL */
unsigned int mt_calls __attribute__((section(".data"))) = 0;
static const struct st_machine_host_api *mt_host __attribute__((section(".data"))) = 0;

/* Two tables: the settings task fills the one MAIN is not reading, then
 * switches the pointer with one aligned word store. NULL is off. */
struct mt_live_table { struct mt_table t; unsigned int mask; };
static struct mt_live_table mt_tables[2] __attribute__((section(".data")));
static struct mt_live_table *volatile mt_live __attribute__((section(".data"))) = 0;
/* Interest last given to the core. ~0 = never: the first call always registers, because an
 * unregistered subscriber would be called for every operation. */
static unsigned int mt_declared __attribute__((section(".data"))) = ~0u;
int mt_native(unsigned int op, const unsigned int *a, unsigned int *result);

/* The core wants interest changes from init, the audio IRQ or an
 * IRQ-masked section; the settings task masks IRQs around its one call. */
static void mt_interest(unsigned int mask)
{
    unsigned short sr;
    if (mask == mt_declared || !mt_host) return;
    __asm__ volatile("move.w %%sr,%0\n\tmove.w #0x2700,%%sr" : "=d"(sr) : : "memory");
    mt_host->native_interest((void (*)(void))mt_native, mask);
    __asm__ volatile("move.w %0,%%sr" : : "d"(sr) : "memory");
    mt_declared = mask;
}

static void mt_apply(void)
{
    struct mt_live_table *t;
    if (mt_tuning && mt_tuning <= MT_FACTORY && (mt_mask & 0xfffu)) {
        t = mt_live == &mt_tables[0] ? &mt_tables[1] : &mt_tables[0];
        mt_prepare(&t->t, mt_factory[mt_tuning - 1], (int)mt_root);
        t->mask = mt_mask & 0xfffu;
        if (!t->t.flat) {
            mt_live = t;                          /* table first, then interest */
            mt_interest(1u << ST_NATIVE_PITCH);
            return;
        }
    }
    mt_interest(0);                               /* interest first, then the table */
    mt_live = 0;
}

/* The core's preferences (host API 20, task only): {tuning, root, track
 * mask}. A record of another length (the 2-word prototype) reads as absent. */
static void mt_prefs(unsigned int write)
{
    unsigned int v[3] = {mt_tuning, mt_root, mt_mask};
    if (!mt_host || mt_host->version < 20 || !mt_host->persistence) return;
    if (mt_host->persistence->preferences(ST_PREF_MICROTUNE, v, 3, write) != 1 || write) return;
    if (v[0] <= MT_FACTORY && v[1] < 12u && v[2] <= 0xfffu) { mt_tuning = v[0]; mt_root = v[1]; mt_mask = v[2]; }
}

/* args = {pitches, count}: Q16 semitones, edited in place. MAIN IRQ. */
int mt_native(unsigned int op, const unsigned int *a, unsigned int *result)
{
    const struct mt_live_table *t = mt_live;
    mt_s32 *p;
    unsigned int i, n;
    (void)result;
    if (op != ST_NATIVE_PITCH || !t || !a || !a[0]) return 0;
    p = (mt_s32 *)a[0];
    n = a[1] < MT_TRACKS ? a[1] : MT_TRACKS;
    for (i = 0; i < n; i++) if (t->mask >> i & 1u) p[i] = mt_map(&t->t, p[i]);
    mt_calls++;
    return 0;
}

/* SETTINGS rows: the four MenuItem callbacks (label, select, draw, change),
 * added with the host's additem, as LIVE MUTE's and KIT BANK's. */
static char mt_line[24] __attribute__((section(".data")));
static const char mt_notes[12][3] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};

static const char *mt_compose(const char *head, const char *tail)
{
    unsigned int i = 0;
    while (*head && i < sizeof mt_line - 1) mt_line[i++] = *head++;
    while (*tail && i < sizeof mt_line - 1) mt_line[i++] = *tail++;
    mt_line[i] = 0;
    return mt_line;
}
const char *mt_tuning_text(void)
{
    return mt_compose("TUNING: ", mt_tuning ? mt_factory_name[mt_tuning - 1] : "OFF");
}
const char *mt_root_text(void) { return mt_compose("TUNE ROOT: ", mt_notes[mt_root % 12u]); }
static const char mt_trk[13][8] = {"T1 ", "T2 ", "T3 ", "T4 ", "T5 ", "T6 ", "T7 ", "T8 ", "T9 ", "T10 ", "T11 ", "T12 ", "ALL "};
const char *mt_tracks_text(void)
{
    unsigned int c = mt_cursor > 12u ? 12u : mt_cursor;
    unsigned int on = c == 12u ? (mt_mask == 0xfffu ? 2u : mt_mask ? 1u : 0u) : (mt_mask >> c & 1u);
    return mt_compose(mt_trk[c], c == 12u ? (on == 2u ? "ON" : on ? "SOME" : "OFF") : (on ? "ON" : "OFF"));
}

/* label(item) -> std::string, built in place at a0 from the C string */
#define MT_LABEL(name, text)                                   \
    __asm__(".text\n.globl " #name "\n" #name ":\n"             \
            "  move.l %a0,-(%sp)\n"                             \
            "  jsr " #text "\n"                                 \
            "  move.l (%sp),%a0\n"                              \
            "  pea 3(%sp)\n"                                    \
            "  move.l %d0,-(%sp)\n"                             \
            "  move.l %a0,-(%sp)\n"                             \
            "  jsr 0x401bf31e\n"                                \
            "  lea 12(%sp),%sp\n"                               \
            "  move.l (%sp)+,%d0\n"                             \
            "  rts\n")
MT_LABEL(mt_tuning_label, mt_tuning_text);
MT_LABEL(mt_root_label, mt_root_text);
MT_LABEL(mt_tracks_label, mt_tracks_text);

static void mt_changed(const unsigned int *fn)
{
    mt_apply();
    mt_prefs(1);
    if (fn && fn[0]) INVALIDATE((char *)fn[0] + 0x38);
}
void mt_tuning_select(const unsigned int *fn) { mt_tuning = (mt_tuning + 1u) % (MT_FACTORY + 1u); mt_changed(fn); }
void mt_tuning_change(const unsigned int *fn, int amount)
{
    mt_tuning = (mt_tuning + (amount > 0 ? 1u : MT_FACTORY)) % (MT_FACTORY + 1u);
    mt_changed(fn);
}
void mt_root_select(const unsigned int *fn) { mt_root = (mt_root + 1u) % 12u; mt_changed(fn); }
void mt_root_change(const unsigned int *fn, int amount)
{
    mt_root = (mt_root + (amount > 0 ? 1u : 11u)) % 12u;
    mt_changed(fn);
}
void mt_tracks_select(const unsigned int *fn)
{
    if (mt_cursor >= 12u) mt_mask = mt_mask == 0xfffu ? 0u : 0xfffu;
    else mt_mask ^= 1u << mt_cursor;
    mt_changed(fn);
}
void mt_tracks_change(const unsigned int *fn, int amount)
{
    mt_cursor = (mt_cursor + (amount > 0 ? 1u : 12u)) % 13u;
    if (fn && fn[0]) INVALIDATE((char *)fn[0] + 0x38);      /* the cursor is not stored: no write */
}
void mt_draw(const unsigned int *fn, void *item, void *bmp, int x, int y)
{
    (void)fn; (void)item; (void)bmp; (void)x; (void)y;
}
void mt_settings(void *menu)
{
    extern void mt_tuning_label(void), mt_root_label(void), mt_tracks_label(void);
    static const void *tuning[4] = {(const void *)mt_tuning_label, (const void *)mt_tuning_select,
                                    (const void *)mt_draw, (const void *)mt_tuning_change};
    static const void *root[4] = {(const void *)mt_root_label, (const void *)mt_root_select,
                                  (const void *)mt_draw, (const void *)mt_root_change};
    static const void *tracks[4] = {(const void *)mt_tracks_label, (const void *)mt_tracks_select,
                                    (const void *)mt_draw, (const void *)mt_tracks_change};
    if (!mt_host) return;
    mt_host->additem(menu, tuning);
    mt_host->additem(menu, root);
    mt_host->additem(menu, tracks);
}

static int initialize(const struct st_smz_api *api)
{
    const struct st_machine_host_api *h;
    if (!api || !api->ui || !(h = api->ui->host) || h->version < 21 || !h->native_interest) return 1;
    mt_host = h;
    mt_prefs(0);
    mt_apply();                      /* declares PITCH interest only if a tuning is on */
    return 0;
}

static const struct st_smz_subscription subscriptions[] = {
    {ST_EV_SETTINGS, (void (*)(void))mt_settings},
    {ST_EV_NATIVE, (void (*)(void))mt_native}};
const struct st_smz_module microtune_module = {"MICROTUNE", initialize, subscriptions, 2};
