/* Replace only the shared dispatch's displaced ADDIGP s1,gp+0x3e8.
 * Preserve every caller-saved register across C on both paths. The stock
 * enclosing prologue/epilogue keeps the original callee-saved registers.
 */
.section .text.hid_dispatch_entry,"ax",@progbits
.globl patch_hid_dispatch_entry
.type patch_hid_dispatch_entry,@function
.balign 4
patch_hid_dispatch_entry:
    addi sp,sp,-80
    sw ra,0(sp)
    sw t0,4(sp)
    sw t1,8(sp)
    sw t2,12(sp)
    sw a0,16(sp)
    sw a1,20(sp)
    sw a2,24(sp)
    sw a3,28(sp)
    sw a4,32(sp)
    sw a5,36(sp)
    sw a6,40(sp)
    sw a7,44(sp)
    sw t3,48(sp)
    sw t4,52(sp)
    sw t5,56(sp)
    sw t6,60(sp)
    call side_hid_process
    sw a0,64(sp)
    lw ra,0(sp)
    lw t1,8(sp)
    lw t2,12(sp)
    lw a0,16(sp)
    lw a1,20(sp)
    lw a2,24(sp)
    lw a3,28(sp)
    lw a4,32(sp)
    lw a5,36(sp)
    lw a6,40(sp)
    lw a7,44(sp)
    lw t3,48(sp)
    lw t4,52(sp)
    lw t5,56(sp)
    lw t6,60(sp)
    lw t0,64(sp)
    beqz t0,1f
    lw t0,4(sp)
    addi sp,sp,80
    li s1,0x80be8
    jal zero,stock_hid_reply
1:
    lw t0,4(sp)
    addi sp,sp,80
    li s1,0x80be8
    jal zero,stock_hid_dispatch_resume
.size patch_hid_dispatch_entry,.-patch_hid_dispatch_entry

/* Both stock receive paths read FIFO length into a4, then immediately set
 * a3=31. Use that dead a3 to record length, preserving all live registers.
 */
.macro CAPTURE_LENGTH name,resume
.section .text.\name,"ax",@progbits
.globl \name
.type \name,@function
.balign 4
\name:
    lb a4,-2026(a5)
    lui a3,%hi(side_usb_length)
    addi a3,a3,%lo(side_usb_length)
    sb a4,0(a3)
    jal zero,\resume
.size \name,.-\name
.endm
CAPTURE_LENGTH patch_usb_length_general,stock_usb_general_resume
CAPTURE_LENGTH patch_usb_length_direct,stock_usb_direct_resume
