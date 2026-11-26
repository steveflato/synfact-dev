/* SPDX-License-Identifier: GPL-2.0-or-later
 * microtune-st: microtuning on the 12 audio tracks. See README.md.
 *
 * Needs ST_NATIVE_PITCH (host API 21, core 2.9.0-mt1 prototype): MAIN's IRQ
 * pass hands the bus the 12 tracks' Q16 pitches at 80004688 before the audio
 * frame reads them, and this module moves each by the tuning (tune.h). The
 * IRQ side reads one pointer and a table; it allocates nothing and never
 * consumes the call.
 *
 * SETTINGS > TUNING picks OFF or a factory tuning; SETTINGS > TUNE ROOT the
 * note the tuning starts from (YES steps forward, LEFT/RIGHT step either
 * way). With host API 20+ the choice is kept in the core's preferences (key
 * ST_PREF_MICROTUNE) across power-off.
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
unsigned int mt_calls __attribute__((section(".data"))) = 0;
static const struct st_machine_host_api *mt_host __attribute__((section(".data"))) = 0;

/* Two tables: the settings task fills the one MAIN is not reading, then
 * switches the pointer with one aligned word store. NULL is off. */
static struct mt_table mt_tables[2] __attribute__((section(".data")));
static struct mt_table *volatile mt_live __attribute__((section(".data"))) = 0;

static void mt_apply(void)
{
    struct mt_table *t;
    if (!mt_tuning || mt_tuning > MT_FACTORY) { mt_live = 0; return; }
    t = mt_live == &mt_tables[0] ? &mt_tables[1] : &mt_tables[0];
    mt_prepare(t, mt_factory[mt_tuning - 1], (int)mt_root);
    mt_live = t->flat ? 0 : t;
}

/* The core's preferences (host API 20, task only): {tuning, root}. */
static void mt_prefs(unsigned int write)
{
    unsigned int v[2] = {mt_tuning, mt_root};
    if (!mt_host || mt_host->version < 20 || !mt_host->persistence) return;
    if (mt_host->persistence->preferences(ST_PREF_MICROTUNE, v, 2, write) != 1 || write) return;
    if (v[0] <= MT_FACTORY && v[1] < 12u) { mt_tuning = v[0]; mt_root = v[1]; }
}

/* args = {pitches, count}: Q16 semitones, edited in place. MAIN IRQ. */
int mt_native(unsigned int op, const unsigned int *a, unsigned int *result)
{
    const struct mt_table *t = mt_live;
    mt_s32 *p;
    unsigned int i, n;
    (void)result;
    if (op != ST_NATIVE_PITCH || !t || !a || !a[0]) return 0;
    p = (mt_s32 *)a[0];
    n = a[1] < MT_TRACKS ? a[1] : MT_TRACKS;
    for (i = 0; i < n; i++) p[i] = mt_map(t, p[i]);
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
void mt_draw(const unsigned int *fn, void *item, void *bmp, int x, int y)
{
    (void)fn; (void)item; (void)bmp; (void)x; (void)y;
}
void mt_settings(void *menu)
{
    extern void mt_tuning_label(void), mt_root_label(void);
    static const void *tuning[4] = {(const void *)mt_tuning_label, (const void *)mt_tuning_select,
                                    (const void *)mt_draw, (const void *)mt_tuning_change};
    static const void *root[4] = {(const void *)mt_root_label, (const void *)mt_root_select,
                                  (const void *)mt_draw, (const void *)mt_root_change};
    if (!mt_host) return;
    mt_host->additem(menu, tuning);
    mt_host->additem(menu, root);
}

static int initialize(const struct st_smz_api *api)
{
    const struct st_machine_host_api *h;
    if (!api || !api->ui || !(h = api->ui->host) || h->version < 21 || !h->native_interest) return 1;
    mt_host = h;
    mt_prefs(0);
    mt_apply();
    h->native_interest((void (*)(void))mt_native, 1u << ST_NATIVE_PITCH);
    return 0;
}

static const struct st_smz_subscription subscriptions[] = {
    {ST_EV_SETTINGS, (void (*)(void))mt_settings},
    {ST_EV_NATIVE, (void (*)(void))mt_native}};
const struct st_smz_module microtune_module = {"MICROTUNE", initialize, subscriptions, 2};
