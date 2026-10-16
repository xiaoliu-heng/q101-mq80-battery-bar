OUTPUT_ARCH(riscv)
ENTRY(patch_side_entry)
patch_start_tick = 0x00082698;
side_host = 0x0008269c;
side_usb_length = 0x000826c2;
stock_side_resume = 0x200098f4;
stock_frame_resume = 0x2000c404;
stock_hid_dispatch_resume = 0x2000e230;
stock_hid_reply = 0x2000e2f4;
stock_usb_general_resume = 0x2000dfc8;
stock_usb_direct_resume = 0x2000e1dc;
SECTIONS {
    . = 0x2001ed50;
    .text : {
        KEEP(*(.text.patch_side_entry))
        KEEP(*(.text.patch_begin))
        KEEP(*(.text.patch_frame_entry))
        KEEP(*(.text.hid_frame_entry))
        KEEP(*(.text.patch_stock_side))
        KEEP(*(.text.patch_stock_frame))
        KEEP(*(.text.hid_dispatch_entry))
        *(.text*)
    }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) *(.sdata*) }
    .bss : { *(.bss*) *(.sbss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
ASSERT(SIZEOF(.data) == 0, "No initialized RAM data may be appended")
ASSERT(SIZEOF(.bss) == 0, "Only fixed reserved BSS is allowed")
ASSERT(. < 0x2001fff0, "Keep image under 128 KiB including CRC and padding")
