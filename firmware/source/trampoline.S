/* Reproduce exactly the two compressed instructions displaced at 0x200098f0.
 * The remaining stock prologue, renderer and epilogue execute unmodified.
 */
.section .text.patch_stock_side,"ax",@progbits
.globl patch_stock_side
.type patch_stock_side,@function
.balign 4
patch_stock_side:
    .2byte 0x1141 /* c.addi sp,-16 */
    .2byte 0xc622 /* c.swsp s0,12(sp) */
    jal zero, stock_side_resume
.size patch_stock_side, .-patch_stock_side

.section .text.patch_stock_frame,"ax",@progbits
.globl patch_stock_frame
.type patch_stock_frame,@function
.balign 4
patch_stock_frame:
    .2byte 0x1101 /* c.addi sp,-32 */
    .2byte 0xcc22 /* c.swsp s0,24(sp) */
    jal zero, stock_frame_resume
.size patch_stock_frame, .-patch_stock_frame
