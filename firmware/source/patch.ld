OUTPUT_ARCH(riscv)
ENTRY(patch_side_entry)
patch_start_tick = 0x00082698;
stock_side_resume = 0x200098f4;
stock_frame_resume = 0x2000c404;
SECTIONS {
    . = 0x2001ed50;
    .text : {
        KEEP(*(.text.patch_side_entry))
        KEEP(*(.text.patch_begin))
        KEEP(*(.text.patch_frame_entry))
        KEEP(*(.text.patch_stock_side))
        KEEP(*(.text.patch_stock_frame))
        *(.text*)
    }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) *(.sdata*) }
    .bss : { *(.bss*) *(.sbss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
ASSERT(SIZEOF(.data) == 0, "Patch must not add initialized RAM data")
ASSERT(SIZEOF(.bss) == 0, "Only the explicitly reserved start tick may use RAM")
ASSERT(. < 0x2001fff0, "Keep the modified image under 128 KiB including CRC")
