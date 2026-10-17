/* MQ80 V0109 only. Build on the audited battery-bar implementation.
 * HID writes affect RAM only; the stock USB/RF reply routines remain in use.
 */
#define patch_frame_entry battery_only_frame_entry
#include "../source/battery_bar.c"
#undef patch_frame_entry
#include "firmware_version.h"

/* Optional v1 INFO metadata, occupying previously zero reply bytes 23..31.
 * Generated from version.json. Protocol and official image versions stay
 * independent. These constants require no additional RAM or Flash writes.
 */
__attribute__((used))
const uint8_t side_firmware_metadata[9] = {
    'F', 'V', 1, SIDE_FW_MAJOR, SIDE_FW_MINOR, SIDE_FW_PATCH,
    SIDE_FW_FEATURE_FLAGS, SIDE_FW_BUILD & 255u, SIDE_FW_BUILD >> 8
};

enum {
    RAW_BUFFER = 0x80be8,
    CONNECTION = 0x80994,
    RF_LENGTH = 0x809b0,
    RF_MODE = 1,
    USB_MODE = 2,
    FAST_IDLE_COUNT = 0x80b30,
    IDLE_SECONDS = 0x80a64,
    DEFAULT_TIMEOUT_MS = 2000,
    MIN_TIMEOUT_MS = 500,
    MAX_TIMEOUT_MS = 10000,
    TICKS_PER_MS = 16000,
    HID_MAGIC = 0xfe,
    PROTOCOL_VERSION = 1,
    OP_INFO = 0,
    OP_FRAME = 1,
    OP_RELEASE = 2,
    OP_KEEPALIVE = 3,
    OP_TIMEOUT = 4,
    OP_PLAY = 5,
    EFFECT_SOLID = 1,
    EFFECT_BLINK = 2,
    EFFECT_BREATHE = 3,
    EFFECT_RAINBOW = 4,
    OK = 0,
    BAD_VERSION = 1,
    BAD_OPCODE = 2,
    BAD_ARGUMENT = 3,
    INACTIVE = 4,
    UNSUPPORTED_TRANSPORT = 5,
    BAD_LENGTH = 6
};

struct host_state {
    uint32_t last_tick;
    uint32_t timeout_ticks;
    uint8_t rgb[27];
    uint8_t active;
    uint8_t last_sequence;
    uint8_t dirty;
    uint8_t usb_length;
    uint8_t owner_mode;
    uint32_t play_id;
    uint16_t play_period;
    uint16_t play_duration;
    uint8_t effect;
    uint8_t brightness;
};
_Static_assert(sizeof(struct host_state) == 52, "Reserved BSS size changed");
extern volatile struct host_state side_host;
static void expire_host(uint32_t now);

static uint32_t timeout_ticks(void) {
    if (side_host.effect) return (uint32_t)side_host.play_duration * TICKS_PER_MS;
    uint32_t ticks = side_host.timeout_ticks;
    return ticks ? ticks : DEFAULT_TIMEOUT_MS * TICKS_PER_MS;
}

/* PLAY payload: effect, brightness, RGB, period:u16, duration:u16,
 * request_id:u32, fourteen zero reserved bytes. IDs are nonzero and a retry
 * of the last ID must have identical parameters. It never renews/restarts,
 * including after expiry, cancellation, sleep or a connection-mode change.
 */
static uint32_t play(volatile uint8_t *p, uint32_t now, uint32_t mode) {
    uint32_t effect = p[5], brightness = p[6];
    uint32_t period = (uint32_t)p[10] | ((uint32_t)p[11] << 8);
    uint32_t duration = (uint32_t)p[12] | ((uint32_t)p[13] << 8);
    uint32_t id = (uint32_t)p[14] | ((uint32_t)p[15] << 8) |
                  ((uint32_t)p[16] << 16) | ((uint32_t)p[17] << 24);
    if (effect < EFFECT_SOLID || effect > EFFECT_RAINBOW || brightness > 100 ||
        duration < 100 || duration > 60000 || !id ||
        (effect == EFFECT_SOLID ? period != 0 : (period < 500 || period > 20000)) ||
        (effect == EFFECT_RAINBOW && (p[7] || p[8] || p[9]))) return BAD_ARGUMENT;
    for (uint32_t i = 18; i < 32; ++i) if (p[i]) return BAD_ARGUMENT;
    if (id == side_host.play_id) {
        if (effect != side_host.effect || brightness != side_host.brightness ||
            period != side_host.play_period || duration != side_host.play_duration)
            return BAD_ARGUMENT;
        for (uint32_t i = 0; i < 3; ++i)
            if (p[7 + i] != side_host.rgb[i]) return BAD_ARGUMENT;
        expire_host(now);
        return OK;
    }
    side_host.play_id = id;
    side_host.play_period = (uint16_t)period;
    side_host.play_duration = (uint16_t)duration;
    side_host.effect = (uint8_t)effect;
    side_host.brightness = (uint8_t)brightness;
    for (uint32_t i = 0; i < 3; ++i) side_host.rgb[i] = p[7 + i];
    side_host.last_tick = now;
    side_host.last_sequence = p[4];
    side_host.owner_mode = (uint8_t)mode;
    side_host.dirty = 0;
    side_host.active = 1;
    return OK;
}

static uint32_t remaining_ticks(uint32_t now) {
    uint32_t mode = U8(CONNECTION);
    if (!side_host.active || mode != side_host.owner_mode ||
        (mode != USB_MODE && mode != RF_MODE)) return 0;
    uint32_t elapsed = now - side_host.last_tick;
    uint32_t limit = timeout_ticks();
    return elapsed < limit ? limit - elapsed : 0;
}

static void expire_host(uint32_t now) {
    if (side_host.active && !remaining_ticks(now)) {
        side_host.active = 0;
        side_host.dirty = 1;
    }
}

/* Only a live RF lighting lease delays the original idle sleep gates.
 * No radio/PM API or persistent setting is changed. Expiry/release stops these
 * writes so the stock idle counters can run again; INFO cannot keep awake.
 */
static void keep_rf_awake(void) {
    if (U8(CONNECTION) == RF_MODE) {
        U32(FAST_IDLE_COUNT) = 0;
        U32(IDLE_SECONDS) = 0;
    }
}

/* Called only after the stock transport gates have produced a request.
 * Return zero for non-extension traffic so all original VIA dispatch executes.
 * This function never invokes USB, RF, flash, or an interrupt-control API.
 */
__attribute__((used, noinline))
uint32_t side_hid_process(void) {
    volatile uint8_t *p = (volatile uint8_t *)(uintptr_t)RAW_BUFFER;
    if (p[0] != HID_MAGIC || p[1] != 'S' || p[2] != 'L') return 0;
    uint32_t command = p[3] & 7u;
    uint32_t now = U32(SYSTEM_TIMER);
    uint32_t status = OK;
    uint32_t mode = U8(CONNECTION);
    uint32_t length = mode == USB_MODE ? side_host.usb_length : U8(RF_LENGTH);
    if (length != 32) status = BAD_LENGTH;
    else if ((p[3] >> 4) != PROTOCOL_VERSION) status = BAD_VERSION;
    else if ((p[3] & 8u) || command > OP_PLAY) status = BAD_OPCODE;
    else if (command != OP_INFO && mode != USB_MODE && mode != RF_MODE)
        status = UNSUPPORTED_TRANSPORT;
    else if (command == OP_FRAME) {
        side_host.effect = 0;
        side_host.play_id = 0;
        for (uint32_t i = 0; i < 27; ++i) side_host.rgb[i] = p[5 + i];
        side_host.last_tick = now;
        side_host.last_sequence = p[4];
        side_host.dirty = 0;
        side_host.owner_mode = (uint8_t)mode;
        side_host.active = 1;
        keep_rf_awake();
    } else if (command == OP_RELEASE) {
        if (side_host.active) {
            side_host.active = 0;
            side_host.dirty = 1;
        }
    } else if (command == OP_KEEPALIVE) {
        if (side_host.effect || !remaining_ticks(now)) status = INACTIVE;
        else {
            side_host.last_tick = now;
            keep_rf_awake();
        }
    } else if (command == OP_TIMEOUT) {
        uint32_t milliseconds = (uint32_t)p[5] | ((uint32_t)p[6] << 8);
        if (milliseconds < MIN_TIMEOUT_MS || milliseconds > MAX_TIMEOUT_MS)
            status = BAD_ARGUMENT;
        else {
            /* Latch expiry before changing its deadline, even if the frame
             * task has not run yet. A shorter deadline can also expire now.
             * Only a new FRAME may reactivate an expired host picture.
             */
            if (!side_host.effect) expire_host(now);
            side_host.timeout_ticks = milliseconds * TICKS_PER_MS;
            if (!side_host.effect) expire_host(now);
        }
    } else if (command == OP_PLAY) {
        status = play(p, now, mode);
    }

    /* ACK: same magic/sequence, response bit in byte 3, compact status payload.
     * No part of the RGB input remains in the response or points outside RAM.
     */
    p[3] = (PROTOCOL_VERSION << 4) | 8u | command;
    for (uint32_t i = 5; i < 32; ++i) p[i] = 0;
    uint32_t remaining = remaining_ticks(now);
    uint32_t timeout = timeout_ticks() / TICKS_PER_MS;
    uint32_t percent = U8(BATTERY_PERCENT);
    if (percent > 100) percent = 100;
    p[5] = (uint8_t)status;
    p[6] = PROTOCOL_VERSION;
    p[7] = SIDE_LED_COUNT;
    p[8] = (remaining != 0 ? 1u : 0u) | ((U8(STATE_FLAGS) & QUERY_MASK) ? 2u : 0u);
    p[9] = (uint8_t)percent;
    p[10] = (uint8_t)mode;
    p[11] = side_host.last_sequence;
    p[12] = 3; /* Supported control transports: bit0 USB, bit1 2.4G. */
    p[13] = (uint8_t)timeout;
    p[14] = (uint8_t)(timeout >> 8);
    /* A still-valid fractional millisecond must not report inactive. */
    remaining = (remaining + TICKS_PER_MS - 1u) / TICKS_PER_MS;
    p[15] = (uint8_t)remaining;
    p[16] = (uint8_t)(remaining >> 8);
    p[17] = 'Q'; p[18] = '1'; p[19] = '0'; p[20] = '1';
    p[21] = GAUGE_BRIGHTNESS;
    p[22] = 31; /* frame, release, keepalive, timeout, local effects */
    for (uint32_t i = 0; i < sizeof(side_firmware_metadata); ++i)
        p[23 + i] = side_firmware_metadata[i];
    return 1;
}

__attribute__((used, noinline))
void side_host_before_frame(void) {
    if (side_host.active) expire_host(U32(SYSTEM_TIMER));
    if (side_host.active && !side_host.effect) keep_rf_awake();
    if (side_host.dirty) {
        volatile uint8_t *rgb = (volatile uint8_t *)(uintptr_t)
                                (RGB_FRAME + FIRST_SIDE_LED * 3);
        for (uint32_t i = 0; i < 27; ++i) rgb[i] = 0;
        U8(SIDE_ANIMATION_TICK) = 64;
        side_host.dirty = 0;
    }
}

/* Called from both original RF sleep paths. Local notifications yield to the
 * original PM decision; they never reset idle counters. Wake must not replay
 * an interrupted notification. Original legacy FRAME behavior is retained.
 */
__attribute__((used, noinline))
void side_host_before_sleep(void) {
    if (side_host.active && side_host.effect) {
        side_host.active = 0;
        side_host.dirty = 1;
    }
}

static uint32_t rainbow_channel(uint32_t hue, uint32_t channel) {
    /* Full-saturation RGB wheel, 6 * 256 phase units. */
    hue = (hue + (channel == 0 ? 0u : channel == 1 ? 1024u : 512u)) % 1536u;
    if (hue < 256u || hue >= 1280u) return 255u;
    if (hue < 512u) return 511u - hue;
    if (hue < 1024u) return 0;
    return hue - 1024u;
}

__attribute__((used, noinline))
void side_host_overlay(void) {
    if (!side_host.active || (U8(STATE_FLAGS) & QUERY_MASK)) return;
    /* The lease is sampled once, before the complete stock frame task. */
    U8(OUTPUT_FLAGS) = (uint8_t)(U8(OUTPUT_FLAGS) & ~2u);
    volatile uint8_t *rgb = (volatile uint8_t *)(uintptr_t)
                            (RGB_FRAME + FIRST_SIDE_LED * 3);
    uint32_t effect = side_host.effect;
    uint32_t level = 255, phase = 0;
    if (effect && effect != EFFECT_SOLID) {
        uint32_t elapsed = (U32(SYSTEM_TIMER) - side_host.last_tick) / TICKS_PER_MS;
        phase = elapsed % side_host.play_period;
        if (effect == EFFECT_BLINK) level = phase * 2 < side_host.play_period ? 255u : 0u;
        if (effect == EFFECT_BREATHE) {
            uint32_t triangle = phase * 510u / side_host.play_period;
            if (triangle > 255u) triangle = 510u - triangle;
            level = triangle * triangle / 255u;
        }
        if (effect == EFFECT_RAINBOW) phase = phase * 1536u / side_host.play_period;
    }
    for (uint32_t i = 0; i < 27; ++i) {
        uint32_t value = effect == EFFECT_RAINBOW ? rainbow_channel(phase + i / 3 * 1536u / 9u, i % 3) :
                         side_host.rgb[effect ? i % 3 : i];
        if (effect) value = value * side_host.brightness / 100u * level / 255u;
        rgb[i] = (uint8_t)(value * GAUGE_BRIGHTNESS / 255u);
    }
}

__attribute__((used, noinline, section(".text.hid_frame_entry")))
void patch_frame_entry(void) {
    side_host_before_frame();
    battery_only_frame_entry();
    side_host_overlay();
}
