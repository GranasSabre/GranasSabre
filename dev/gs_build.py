"""
Build step for GranasSabre: in-game "System sounds" volume bar.

Adds a 4th row to the in-game config menu ("Quit/Config"). Left/right moves a
bar of 11 levels (0 = mute .. 10 = original volume). The level is stored in
bits 3..6 of the config flag word in the save data (0 = not set -> default).

Assembles the hook code with keystone and emits a JSON patch description
(byte patches with expected originals + new section content) that the
end-user patcher applies without needing keystone.
"""
import json
import struct
import sys

from keystone import Ks, KS_ARCH_X86, KS_MODE_32

IMAGE_BASE = 0x400000
SEC_RVA = 0x70C000                 # = original SizeOfImage
SEC_VA = IMAGE_BASE + SEC_RVA
SEC_SIZE = 0x1000

# section layout
TEXT_TABLE = SEC_VA + 0x000        # 4 rows x (label, sub, options) key pointers
HELP_TABLE = SEC_VA + 0x030        # 4 help-text key pointers
OPT_SLOTS = SEC_VA + 0x040         # 4 rows x 16 bytes option x/width (was 0xAB4F60)
LEVEL_GAIN = SEC_VA + 0x080        # 11 floats, filled by the patcher
DEFAULT_LEVEL = SEC_VA + 0x0B0     # byte, filled by the patcher
ID_MASK = SEC_VA + 0x0C0           # 35 bytes: 1 = sound id follows the bar
TABLE_ENTRIES = 35
KEYBUF = SEC_VA + 0x100            # "gs_bar_?" - last char = 'a' + level
STRINGS = SEC_VA + 0x110
CODE = SEC_VA + 0x200

LEVELS = 11
MAX_LEVEL = LEVELS - 1

# game addresses (known build)
CFG_PTR = 0x6683F0                 # -> save/system struct, word flags at +0x20
CFG_SEL = 0x669224                 # config menu: value of current row
ROW_VALUES = 0xAB4F40              # int[8] option values, row r at +4r
FLOAT_100 = 0x64C020
GET_TEXT = 0x5F7940                # ecx = key -> eax = text
SPLIT_OPTIONS = 0x5F7A90           # ecx = text, edx = option slot array
SET_LINE = 0x590AF0                # ecx = text, push flags, push line (cdecl)
LEVEL_SHIFT = 3                    # bits 3..6
LEVEL_MASK = 0xF << LEVEL_SHIFT
OPTION_LINE_ROW3 = 12              # text line of row 4's options

KEYS = {
    "label": b"gs_system_sounds\0",
    "help": b"gs_system_sounds_help\0",
}


def asm(src, addr):
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    enc, _ = ks.asm(src, addr)
    return bytes(enc)


def jmp(src, dst, total=5):
    b = b"\xE9" + struct.pack("<i", dst - (src + 5))
    return b + b"\x90" * (total - 5)


def build(orig):
    def ptr(va):
        return struct.unpack_from("<I", orig, va - 0x400E00)[0]   # .rdata VA -> file

    sec = bytearray(SEC_SIZE)

    def put(va, data):
        sec[va - SEC_VA: va - SEC_VA + len(data)] = data

    keyaddr = {}
    off = STRINGS
    for k, v in KEYS.items():
        keyaddr[k] = off
        put(off, v)
        off += len(v)
    assert off <= CODE
    put(KEYBUF, b"gs_bar_g\0")

    # text table: original 3 rows copied, row 4 = ours (sub line = the " " string)
    rows = [ptr(0x6429B4 + 4 * i) for i in range(9)]
    rows += [keyaddr["label"], ptr(0x6429B8), KEYBUF]
    put(TEXT_TABLE, struct.pack("<12I", *rows))
    help_rows = [ptr(0x642774 + 4 * i) for i in range(3)] + [keyaddr["help"]]
    put(HELP_TABLE, struct.pack("<4I", *help_rows))
    put(LEVEL_GAIN, struct.pack("<%df" % LEVELS, *([1.0] * LEVELS)))
    put(DEFAULT_LEVEL, bytes([MAX_LEVEL]))

    code = {}
    pc = CODE

    def emit(name, src):
        nonlocal pc
        b = asm(src, pc)
        code[name] = pc
        put(pc, b)
        pc += (len(b) + 15) & ~15

    # eax = current level (0..10). Clobbers eax only.
    emit("getlevel", f"""
        mov eax, dword ptr [{CFG_PTR:#x}]
        test eax, eax
        jz def
        movzx eax, word ptr [eax+0x20]
        shr eax, {LEVEL_SHIFT}
        and eax, 0xf
        jz def
        dec eax
        cmp eax, {MAX_LEVEL}
        jbe done
        mov eax, {MAX_LEVEL}
    done:
        ret
    def:
        movzx eax, byte ptr [{DEFAULT_LEVEL:#x}]
        ret
    """)

    # KEYBUF = "gs_bar_" + ('a' + level). Clobbers eax.
    emit("setkey", f"""
        call {code['getlevel']:#x}
        add al, 0x61
        mov byte ptr [{KEYBUF + 7:#x}], al
        ret
    """)

    # SFX category volume (replaces tail of 0x5EF770 SFX branch).
    # Only eax/edx/xmm0/flags may change: callers keep xmm1/xmm2/ecx alive.
    emit("sfx", f"""
        mov edx, dword ptr [eax+0x2c]
        pop esi
        pop ebx
        movd xmm0, edx
        cvtdq2ps xmm0, xmm0
        divss xmm0, dword ptr [{FLOAT_100:#x}]
        cmp ecx, {TABLE_ENTRIES}
        jae done
        cmp byte ptr [ecx+{ID_MASK:#x}], 0
        je done
        call {code['getlevel']:#x}
        mulss xmm0, dword ptr [eax*4+{LEVEL_GAIN:#x}]
    done:
        ret
    """)

    # Config menu setup, before the row loop (0x582F27): prepare bar key.
    emit("pre", f"""
        call {code['setkey']:#x}
        mov edi, {TEXT_TABLE + 4:#x}
        jmp {0x582F2C:#x}
    """)

    # Config menu setup, after the row loop (0x583012): row 4 has a single
    # option field (the bar), so its "value" is always 0.
    emit("init", f"""
        mov dword ptr [{ROW_VALUES + 12:#x}], 0
        cmp ebx, 2
        jne l2
        mov dword ptr [{CFG_SEL:#x}], eax
        jmp {0x583036:#x}
    l2:
        cmp ebx, 3
        jne l3
        mov dword ptr [{CFG_SEL:#x}], 0
    l3:
        jmp {0x583036:#x}
    """)

    # Config menu left/right (0x57FEE0). esi = 1 (right) / 0 (left).
    emit("toggle", f"""
        cmp edi, 3
        je row3
        mov dword ptr [edi*4+{ROW_VALUES:#x}], esi
        jmp {0x57FEE7:#x}
    row3:
        call {code['getlevel']:#x}
        test esi, esi
        jz down
        cmp eax, {MAX_LEVEL}
        jae store
        inc eax
        jmp store
    down:
        test eax, eax
        jz store
        dec eax
    store:
        mov ecx, dword ptr [{CFG_PTR:#x}]
        test ecx, ecx
        jz redraw
        lea edx, [eax+1]
        shl edx, {LEVEL_SHIFT}
        movzx eax, word ptr [ecx+0x20]
        and eax, {0xFFFF & ~LEVEL_MASK:#x}
        or eax, edx
        mov word ptr [ecx+0x20], ax
    redraw:
        call {code['setkey']:#x}
        mov ecx, {KEYBUF:#x}
        call {GET_TEXT:#x}
        test eax, eax
        jz fin
        push eax
        mov edx, {OPT_SLOTS + 0x30:#x}
        mov ecx, eax
        call {SPLIT_OPTIONS:#x}
        pop ecx
        push 0x100
        push {OPTION_LINE_ROW3}
        call {SET_LINE:#x}
        add esp, 8
    fin:
        xor esi, esi
        mov dword ptr [{ROW_VALUES + 12:#x}], 0
        jmp {0x57FEE7:#x}
    """)
    assert pc <= CODE + 0x400

    P = []

    def patch(va, orig_hex, new):
        o = bytes.fromhex(orig_hex)
        assert len(new) == len(o), hex(va)
        P.append({"va": va, "orig": orig_hex, "new": new.hex()})

    u32 = lambda v: struct.pack("<I", v)
    patch(0x5EF7A9, "8b502c5e5b", jmp(0x5EF7A9, code["sfx"]))
    # draw setup: option slot array, text table walk, end of table, help table
    patch(0x582F20, "c745fc604fab00", bytes.fromhex("c745fc") + u32(OPT_SLOTS))
    patch(0x582F27, "bfb8296400", jmp(0x582F27, code["pre"]))
    patch(0x582F9B, "81ffdc296400", b"\x81\xff" + u32(TEXT_TABLE + 4 + 4 * 12))
    patch(0x582FAD, "8b0c9d74276400", bytes.fromhex("8b0c9d") + u32(HELP_TABLE))
    patch(0x583012, "83fb02751f", jmp(0x583012, code["init"]))
    # input: 4 rows (wrap to row 3), keep cursor sound id 1, help table, left/right
    patch(0x57FE4A, "ba02000000", bytes.fromhex("ba03000000"))
    patch(0x57FE54, "83f903", bytes.fromhex("83f904"))
    patch(0x57FE5E, "8d4aff", bytes.fromhex("8d4afe"))
    patch(0x57FE81, "8b0cbd74276400", bytes.fromhex("8b0cbd") + u32(HELP_TABLE))
    patch(0x57FEE0, "8934bd404fab00", jmp(0x57FEE0, code["toggle"], 7))
    # menu close: keep bits 3..7 (our level) instead of rewriting them
    patch(0x57FF9A, "833d4c4fab0000", jmp(0x57FF9A, 0x58000E, 7))
    # per-frame drawing: option highlight slots, 4 rows
    patch(0x58BC86, "660f6e0c8d604fab00", bytes.fromhex("660f6e0c8d") + u32(OPT_SLOTS))
    patch(0x58BCBA, "660f6e048d684fab00", bytes.fromhex("660f6e048d") + u32(OPT_SLOTS + 8))
    patch(0x58BD0E, "81fed8000000", bytes.fromhex("81fe20010000"))

    return {
        "section": {"name": ".gsabre", "rva": SEC_RVA, "size": SEC_SIZE, "data": bytes(sec).hex()},
        "level_gain_va": LEVEL_GAIN, "levels": LEVELS,
        "default_level_va": DEFAULT_LEVEL,
        "id_mask_va": ID_MASK, "id_entries": TABLE_ENTRIES,
        "patches": P,
        "code": code,
    }


if __name__ == "__main__":
    orig = open(sys.argv[1], "rb").read()
    out = build(orig)
    json.dump(out, open(sys.argv[2], "w"), indent=1)
    print({k: hex(v) for k, v in out["code"].items()}, len(out["patches"]), "patches")
