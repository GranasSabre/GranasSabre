"""
Build step for GranasSabre: in-game "System sounds" volume bar.

Adds a 4th row to the in-game config menu ("Quit/Config"). Left/right moves a
bar of 11 levels (0 = mute .. 10 = original volume). The level is stored in
bits 3..6 of the config flag word in the save data (0 = not set -> default).

Assembles the hook code with keystone and emits a JSON patch description per
supported grandia2.exe build (byte patches with expected originals + new
section content). The end-user patcher applies it without keystone.
"""
import hashlib
import struct
import sys

import pefile
from keystone import Ks, KS_ARCH_X86, KS_MODE_32

IMAGE_BASE = 0x400000
SEC_SIZE = 0x1000

# section layout (offsets inside the new section)
OFF_TEXT_TABLE = 0x000             # 4 rows x (label, sub, options) key pointers
OFF_HELP_TABLE = 0x030             # 4 help-text key pointers
OFF_OPT_SLOTS = 0x040              # 4 rows x 16 bytes option x/width
OFF_LEVEL_GAIN = 0x080             # 11 floats, filled by the patcher
OFF_DEFAULT_LEVEL = 0x0B0          # byte, filled by the patcher
OFF_ID_MASK = 0x0C0                # 35 bytes: 1 = sound id follows the bar
OFF_KEYBUF = 0x100                 # "gs_bar_?" - last char = 'a' + level
OFF_STRINGS = 0x110
OFF_CODE = 0x200
TABLE_ENTRIES = 35

LEVELS = 11
MAX_LEVEL = LEVELS - 1
LEVEL_SHIFT = 3                    # bits 3..6 of the config flag word
LEVEL_MASK = 0xF << LEVEL_SHIFT
OPTION_LINE_ROW3 = 12              # text line of row 4's options

KEYS = {
    "label": b"gs_system_sounds\0",
    "help": b"gs_system_sounds_help\0",
}

# Addresses per supported build. All patch sites are checked against the exe.
BUILDS = {
    # 2020-07-31, 2 841 088 bytes
    "06ad6c88778409f65dd111493faf57c3dd92f3292edd6a8ce6ea0109ee5d630e": dict(
        label="build 2020-07-31 (PE timestamp 0x5F2448F7)",
        cfg_ptr=0x6683F0,          # -> save/system struct, word flags at +0x20
        cfg_sel=0x669224,          # config menu: value of current row
        row_values=0xAB4F40,       # int[8] option values, row r at +4r
        opt_slots=0xAB4F60,        # original option slot array (4 x 16 bytes)
        float_100=0x64C020,
        get_text=0x5F7940,         # ecx = key -> eax = text
        split_options=0x5F7A90,    # ecx = text, edx = option slot array
        set_line=0x590AF0,         # ecx = text, push flags, push line (cdecl)
        text_src=0x6429B4,         # 3 rows x (label, sub, options)
        help_src=0x642774,         # 3 help keys
        sfx=0x5EF7A9,              # mov edx,[eax+2Ch]; pop esi; pop ebx
        setup_slots=0x582F20,
        setup_table=0x582F27,
        setup_end=0x582F9B,
        setup_help=0x582FAD,
        setup_init=0x583012, setup_epilogue=0x583036, row2_reg="eax",
        in_wrap=0x57FE4A, in_cmp=0x57FE54, in_sound=0x57FE5E, in_help=0x57FE81,
        in_store=0x57FEE0,
        close_skip=0x57FF9A, close_target=0x58000E,
        draw_x=0x58BC86, draw_w=0x58BCBA, draw_end=0x58BD0E,
    ),
    # 2022-04-02 (Steam, build id 8485014), 3 198 464 bytes
    "adb2bc70dd4707fdb6a6163a867bfdecceb55fc3391486e2902c16d1ce77d3e3": dict(
        label="build 2022-04-02 (PE timestamp 0x6247AE91)",
        cfg_ptr=0x6C48E4,
        cfg_sel=0x6C55CC,
        row_values=0xB11338,
        opt_slots=0xB11358,
        float_100=0x6A83A0,
        get_text=0x651DA0,
        split_options=0x651EF0,
        set_line=0x5EC840,
        text_src=0x69FC78,
        help_src=0x69FAAC,
        sfx=0x649869,
        setup_slots=0x5DF750,
        setup_table=0x5DF757,
        setup_end=0x5DF7CB,
        setup_help=0x5DF7DD,
        setup_init=0x5DF84C, setup_epilogue=0x5DF871, row2_reg="esi",
        in_wrap=0x5DC68A, in_cmp=0x5DC694, in_sound=0x5DC69E, in_help=0x5DC6C1,
        in_store=0x5DC720,
        close_skip=0x5DC7DA, close_target=0x5DC84E,
        draw_x=0x5E7AD1, draw_w=0x5E7B11, draw_end=0x5E7B81,
    ),
}


def asm(src, addr):
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    enc, _ = ks.asm(src, addr)
    return bytes(enc)


def jmp(src, dst, total=5):
    b = b"\xE9" + struct.pack("<i", dst - (src + 5))
    return b + b"\x90" * (total - 5)


def u32(v):
    return struct.pack("<I", v)


def build(orig):
    sha = hashlib.sha256(orig).hexdigest()
    if sha not in BUILDS:
        raise SystemExit("unsupported exe %s" % sha)
    B = BUILDS[sha]
    pe = pefile.PE(data=orig)
    if pe.OPTIONAL_HEADER.ImageBase != IMAGE_BASE:
        raise SystemExit("unexpected image base")
    sec_rva = pe.OPTIONAL_HEADER.SizeOfImage
    sec_va = IMAGE_BASE + sec_rva
    A = {k: sec_va + v for k, v in dict(
        text_table=OFF_TEXT_TABLE, help_table=OFF_HELP_TABLE, opt_slots=OFF_OPT_SLOTS,
        level_gain=OFF_LEVEL_GAIN, default_level=OFF_DEFAULT_LEVEL, id_mask=OFF_ID_MASK,
        keybuf=OFF_KEYBUF, strings=OFF_STRINGS, code=OFF_CODE).items()}

    def ptr(va):
        return pe.get_dword_at_rva(va - IMAGE_BASE)

    sec = bytearray(SEC_SIZE)

    def put(va, data):
        sec[va - sec_va: va - sec_va + len(data)] = data

    keyaddr = {}
    off = A["strings"]
    for k, v in KEYS.items():
        keyaddr[k] = off
        put(off, v)
        off += len(v)
    assert off <= A["code"]
    put(A["keybuf"], b"gs_bar_g\0")

    # text table: original 3 rows copied, row 4 = ours (sub line = the " " string)
    rows = [ptr(B["text_src"] + 4 * i) for i in range(9)]
    rows += [keyaddr["label"], ptr(B["text_src"] + 4), A["keybuf"]]
    put(A["text_table"], struct.pack("<12I", *rows))
    help_rows = [ptr(B["help_src"] + 4 * i) for i in range(3)] + [keyaddr["help"]]
    put(A["help_table"], struct.pack("<4I", *help_rows))
    put(A["level_gain"], struct.pack("<%df" % LEVELS, *([1.0] * LEVELS)))
    put(A["default_level"], bytes([MAX_LEVEL]))

    code = {}
    pc = A["code"]

    def emit(name, src):
        nonlocal pc
        b = asm(src, pc)
        code[name] = pc
        put(pc, b)
        pc += (len(b) + 15) & ~15

    # eax = current level (0..10). Clobbers eax only.
    emit("getlevel", f"""
        mov eax, dword ptr [{B['cfg_ptr']:#x}]
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
        movzx eax, byte ptr [{A['default_level']:#x}]
        ret
    """)

    # KEYBUF = "gs_bar_" + ('a' + level). Clobbers eax.
    emit("setkey", f"""
        call {code['getlevel']:#x}
        add al, 0x61
        mov byte ptr [{A['keybuf'] + 7:#x}], al
        ret
    """)

    # SFX category volume (replaces the tail of the SFX branch).
    # Only eax/edx/xmm0/flags may change: callers keep xmm1/xmm2/ecx alive.
    emit("sfx", f"""
        mov edx, dword ptr [eax+0x2c]
        pop esi
        pop ebx
        movd xmm0, edx
        cvtdq2ps xmm0, xmm0
        divss xmm0, dword ptr [{B['float_100']:#x}]
        cmp ecx, {TABLE_ENTRIES}
        jae done
        cmp byte ptr [ecx+{A['id_mask']:#x}], 0
        je done
        call {code['getlevel']:#x}
        mulss xmm0, dword ptr [eax*4+{A['level_gain']:#x}]
    done:
        ret
    """)

    # Config menu setup, before the row loop: prepare the bar key.
    emit("pre", f"""
        call {code['setkey']:#x}
        mov edi, {A['text_table'] + 4:#x}
        jmp {B['setup_table'] + 5:#x}
    """)

    # Config menu setup, after the row loop: row 4 has a single option field
    # (the bar), so its "value" is always 0.
    emit("init", f"""
        mov dword ptr [{B['row_values'] + 12:#x}], 0
        cmp ebx, 2
        jne l2
        mov dword ptr [{B['cfg_sel']:#x}], {B['row2_reg']}
        jmp {B['setup_epilogue']:#x}
    l2:
        cmp ebx, 3
        jne l3
        mov dword ptr [{B['cfg_sel']:#x}], 0
    l3:
        jmp {B['setup_epilogue']:#x}
    """)

    # Config menu left/right. esi = 1 (right) / 0 (left).
    emit("toggle", f"""
        cmp edi, 3
        je row3
        mov dword ptr [edi*4+{B['row_values']:#x}], esi
        jmp {B['in_store'] + 7:#x}
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
        mov ecx, dword ptr [{B['cfg_ptr']:#x}]
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
        mov ecx, {A['keybuf']:#x}
        call {B['get_text']:#x}
        test eax, eax
        jz fin
        push eax
        mov edx, {A['opt_slots'] + 0x30:#x}
        mov ecx, eax
        call {B['split_options']:#x}
        pop ecx
        push 0x100
        push {OPTION_LINE_ROW3}
        call {B['set_line']:#x}
        add esp, 8
    fin:
        xor esi, esi
        mov dword ptr [{B['row_values'] + 12:#x}], 0
        jmp {B['in_store'] + 7:#x}
    """)
    assert pc <= A["code"] + 0x400

    P = []

    def patch(va, expected, new):
        assert len(new) == len(expected), hex(va)
        got = pe.get_data(va - IMAGE_BASE, len(expected))
        if got != expected:
            raise SystemExit("site %#x: expected %s, found %s" % (va, expected.hex(), got.hex()))
        P.append({"va": va, "orig": expected.hex(), "new": new.hex()})

    rel8 = B["setup_epilogue"] - (B["setup_init"] + 5)
    patch(B["sfx"], bytes.fromhex("8b502c5e5b"), jmp(B["sfx"], code["sfx"]))
    # draw setup: option slot array, text table walk, end of table, help table
    patch(B["setup_slots"], bytes.fromhex("c745fc") + u32(B["opt_slots"]),
          bytes.fromhex("c745fc") + u32(A["opt_slots"]))
    patch(B["setup_table"], b"\xbf" + u32(B["text_src"] + 4), jmp(B["setup_table"], code["pre"]))
    patch(B["setup_end"], b"\x81\xff" + u32(B["text_src"] + 4 + 4 * 9),
          b"\x81\xff" + u32(A["text_table"] + 4 + 4 * 12))
    patch(B["setup_help"], bytes.fromhex("8b0c9d") + u32(B["help_src"]),
          bytes.fromhex("8b0c9d") + u32(A["help_table"]))
    patch(B["setup_init"], bytes.fromhex("83fb0275") + bytes([rel8]), jmp(B["setup_init"], code["init"]))
    # input: 4 rows (wrap to row 3), keep cursor sound id 1, help table, left/right
    patch(B["in_wrap"], bytes.fromhex("ba02000000"), bytes.fromhex("ba03000000"))
    patch(B["in_cmp"], bytes.fromhex("83f903"), bytes.fromhex("83f904"))
    patch(B["in_sound"], bytes.fromhex("8d4aff"), bytes.fromhex("8d4afe"))
    patch(B["in_help"], bytes.fromhex("8b0cbd") + u32(B["help_src"]),
          bytes.fromhex("8b0cbd") + u32(A["help_table"]))
    patch(B["in_store"], bytes.fromhex("8934bd") + u32(B["row_values"]),
          jmp(B["in_store"], code["toggle"], 7))
    # menu close: keep bits 3..7 (our level) instead of rewriting them
    patch(B["close_skip"], bytes.fromhex("833d") + u32(B["row_values"] + 12) + b"\x00",
          jmp(B["close_skip"], B["close_target"], 7))
    # per-frame drawing: option highlight slots, 4 rows
    patch(B["draw_x"], bytes.fromhex("660f6e0c8d") + u32(B["opt_slots"]),
          bytes.fromhex("660f6e0c8d") + u32(A["opt_slots"]))
    patch(B["draw_w"], bytes.fromhex("660f6e048d") + u32(B["opt_slots"] + 8),
          bytes.fromhex("660f6e048d") + u32(A["opt_slots"] + 8))
    patch(B["draw_end"], bytes.fromhex("81fed8000000"), bytes.fromhex("81fe20010000"))
    # the close handler must store ax at close_target
    if pe.get_data(B["close_target"] - IMAGE_BASE, 3) != bytes.fromhex("668901"):
        raise SystemExit("close target %#x is not 'mov [ecx],ax'" % B["close_target"])

    return {
        "sha256": sha, "label": B["label"],
        "section": {"name": ".gsabre", "rva": sec_rva, "size": SEC_SIZE, "data": bytes(sec).hex()},
        "level_gain_va": A["level_gain"], "levels": LEVELS,
        "default_level_va": A["default_level"],
        "id_mask_va": A["id_mask"], "id_entries": TABLE_ENTRIES,
        "patches": P,
        "code": code,
    }


if __name__ == "__main__":
    for path in sys.argv[1:]:
        out = build(open(path, "rb").read())
        print(out["label"], {k: hex(v) for k, v in out["code"].items()}, len(out["patches"]), "patches")
