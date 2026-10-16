/* MQ80 V0109 only. Build on the audited battery-bar implementation.
 * HID writes affect RAM only; the stock USB/RF reply routines remain in use.
 */
#define patch_frame_entry battery_only_frame_entry
#include "../source/battery_bar.c"
#undef patch_frame_entry

enum {
    RAW_BUFFER = 0x80be8,
    CONNECTION = 0x80994,
    RF_LENGTH = 0x809b0,
    USB_MODE = 2,
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
    uint8_t reserved;
};
_Static_assert(sizeof(struct host_state) == 40, "Reserved BSS size changed");
extern volatile struct host_state side_host;

static uint32_t timeout_ticks(void) {
    uint32_t ticks = side_host.timeout_ticks;
    return ticks ? ticks : DEFAULT_TIMEOUT_MS * TICKS_PER_MS;
}

static uint32_t remaining_ticks(uint32_t now) {
    if (!side_host.active || U8(CONNECTION) != USB_MODE) return 0;
    uint32_t elapsed = now - side_host.last_tick;
    uint32_t limit = timeout_ticks();
    return elapsed < limit ? limit - elapsed : 0;
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
    else if ((p[3] & 8u) || command > OP_TIMEOUT) status = BAD_OPCODE;
    else if (command != OP_INFO && mode != USB_MODE) status = UNSUPPORTED_TRANSPORT;
    else if (command == OP_FRAME) {
        for (uint32_t i = 0; i < 27; ++i) side_host.rgb[i] = p[5 + i];
        side_host.last_tick = now;
        side_host.last_sequence = p[4];
        side_host.dirty = 0;
        side_host.active = 1;
    } else if (command == OP_RELEASE) {
        if (side_host.active) {
            side_host.active = 0;
            side_host.dirty = 1;
        }
    } else if (command == OP_KEEPALIVE) {
        if (!remaining_ticks(now)) status = INACTIVE;
        else side_host.last_tick = now;
    } else if (command == OP_TIMEOUT) {
        uint32_t milliseconds = (uint32_t)p[5] | ((uint32_t)p[6] << 8);
        if (milliseconds < MIN_TIMEOUT_MS || milliseconds > MAX_TIMEOUT_MS)
            status = BAD_ARGUMENT;
        else side_host.timeout_ticks = milliseconds * TICKS_PER_MS;
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
    p[12] = 1; /* Supported control transports: USB. RF info query is allowed. */
    p[13] = (uint8_t)timeout;
    p[14] = (uint8_t)(timeout >> 8);
    /* A still-valid fractional millisecond must not report inactive. */
    remaining = (remaining + TICKS_PER_MS - 1u) / TICKS_PER_MS;
    p[15] = (uint8_t)remaining;
    p[16] = (uint8_t)(remaining >> 8);
    p[17] = 'Q'; p[18] = '1'; p[19] = '0'; p[20] = '1';
    p[21] = GAUGE_BRIGHTNESS;
    p[22] = 15; /* frame, release, keepalive, configurable timeout */
    return 1;
}

__attribute__((used, noinline))
void side_host_before_frame(void) {
    if (side_host.active && !remaining_ticks(U32(SYSTEM_TIMER))) {
        side_host.active = 0;
        side_host.dirty = 1;
    }
    if (side_host.dirty) {
        volatile uint8_t *rgb = (volatile uint8_t *)(uintptr_t)
                                (RGB_FRAME + FIRST_SIDE_LED * 3);
        for (uint32_t i = 0; i < 27; ++i) rgb[i] = 0;
        U8(SIDE_ANIMATION_TICK) = 64;
        side_host.dirty = 0;
    }
}

__attribute__((used, noinline))
void side_host_overlay(void) {
    if (!side_host.active || (U8(STATE_FLAGS) & QUERY_MASK)) return;
    /* The lease is sampled once, before the complete stock frame task. */
    U8(OUTPUT_FLAGS) = (uint8_t)(U8(OUTPUT_FLAGS) & ~2u);
    volatile uint8_t *rgb = (volatile uint8_t *)(uintptr_t)
                            (RGB_FRAME + FIRST_SIDE_LED * 3);
    for (uint32_t i = 0; i < 27; ++i)
        rgb[i] = (uint8_t)((uint32_t)side_host.rgb[i] * GAUGE_BRIGHTNESS / 255u);
}

__attribute__((used, noinline, section(".text.hid_frame_entry")))
void patch_frame_entry(void) {
    side_host_before_frame();
    battery_only_frame_entry();
    side_host_overlay();
}
