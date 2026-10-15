/* Version-specific binary patch for MQ80-CS836B-V0109-20241220.
 * No USB, radio, ADC, flash, GPIO, allocator, or interrupt-control calls.
 * Only reads the stock battery byte and system timer; writes stock RGB RAM.
 */
#include <stdint.h>

#define U8(address) (*(volatile uint8_t *)(uintptr_t)(address))
#define U32(address) (*(volatile uint32_t *)(uintptr_t)(address))

enum {
    BATTERY_PERCENT = 0x00080948,
    STATE_FLAGS = 0x00080b58,
    OUTPUT_FLAGS = 0x00080b57,
    OLD_BLINK_COUNT = 0x00080aa2,
    OLD_BLINK_TIMER = 0x00080b45,
    SIDE_ANIMATION_TICK = 0x00080b8e,
    RGB_FRAME = 0x00081cc4,
    SYSTEM_TIMER = 0x80140200,
    QUERY_MASK = 2,
    FIRST_SIDE_LED = 84,
    SIDE_LED_COUNT = 9,
    GAUGE_BRIGHTNESS = 162,
    VIEW_TICKS = 48000000u /* Stock delay routine uses 16 ticks per us. */
};

/* Absolute linker symbol at the old BSS end. Startup clears four extra bytes.
 * No initialized data is relocated and the original stack stays at 0xa0000.
 */
extern volatile uint32_t patch_start_tick;
extern void patch_stock_side(void);
extern void patch_stock_frame(void);

__attribute__((used, noinline, section(".text.patch_begin")))
void patch_begin(void) {
    patch_start_tick = U32(SYSTEM_TIMER);
    U8(OLD_BLINK_COUNT) = 0;
    U8(OLD_BLINK_TIMER) = 0;
    /* Reproduce the stock Fn+B press flags; set active only after initialization. */
    U8(STATE_FLAGS) = (uint8_t)((U8(STATE_FLAGS) & 0xfeu) | QUERY_MASK);
}

__attribute__((used, noinline, section(".text.patch_side_entry")))
void patch_side_entry(void) {
    if ((U8(STATE_FLAGS) & QUERY_MASK) == 0) {
        patch_stock_side();
        return;
    }

    /* Expiry is sampled once by patch_frame_entry, before stock rendering.
     * Both stock calls to this renderer are inside that frame task. Rechecking
     * the timer here could expire after the stock disabled-light decision and
     * then call the stock side renderer despite that decision (v1 boundary bug).
     * Keep one coherent query state throughout a frame; expire next frame.
     */

    /* Stock battery display clears this bit to enable the frame output. */
    U8(OUTPUT_FLAGS) = (uint8_t)(U8(OUTPUT_FLAGS) & ~2u);
    uint32_t p = U8(BATTERY_PERCENT);
    if (p > 100) p = 100;
    uint32_t red = p <= 50 ? GAUGE_BRIGHTNESS
                          : GAUGE_BRIGHTNESS * (100 - p) / 50;
    uint32_t green = p >= 50 ? GAUGE_BRIGHTNESS
                            : GAUGE_BRIGHTNESS * p / 50;
    volatile uint8_t *rgb = (volatile uint8_t *)(uintptr_t)
                            (RGB_FRAME + FIRST_SIDE_LED * 3);
    for (uint32_t i = 0; i < SIDE_LED_COUNT; ++i) {
        int32_t coverage = (int32_t)(p * SIDE_LED_COUNT) - (int32_t)(i * 100);
        if (coverage < 0) coverage = 0;
        if (coverage > 100) coverage = 100;
        rgb[i * 3] = (uint8_t)(red * (uint32_t)coverage / 100);
        rgb[i * 3 + 1] = (uint8_t)(green * (uint32_t)coverage / 100);
        rgb[i * 3 + 2] = 0;
    }
}

/* The stock total-frame task can bypass the side renderer when side lighting
 * is disabled, or draw a connection indication after it. Overlay only the
 * side pixels after that task, and expire before it so its normal priority
 * and disabled-light behavior are restored.
 */
__attribute__((used, noinline, section(".text.patch_frame_entry")))
void patch_frame_entry(void) {
    if ((U8(STATE_FLAGS) & QUERY_MASK) != 0 &&
        (uint32_t)(U32(SYSTEM_TIMER) - patch_start_tick) >= VIEW_TICKS) {
        U8(STATE_FLAGS) = (uint8_t)(U8(STATE_FLAGS) & ~QUERY_MASK);
        U8(OLD_BLINK_COUNT) = 0;
        U8(OLD_BLINK_TIMER) = 0;
        U8(SIDE_ANIMATION_TICK) = 64;
        /* Remove the temporary bar before the stock task. It may leave side
         * RAM untouched when disabled; zero remains the correct off frame.
         */
        volatile uint8_t *side = (volatile uint8_t *)(uintptr_t)
                                (RGB_FRAME + FIRST_SIDE_LED * 3);
        for (uint32_t i = 0; i < SIDE_LED_COUNT * 3; ++i) side[i] = 0;
    }
    patch_stock_frame();
    if ((U8(STATE_FLAGS) & QUERY_MASK) != 0) patch_side_entry();
}
