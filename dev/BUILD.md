# Building GranasSabre

Needs Python 3.8+ with `keystone-engine` and `pefile` (only for building) and `pyinstaller`.

    pip install keystone-engine pefile pyinstaller
    python dev/make_release.py dev/GranasSabre.template.py GranasSabre.py <2020 grandia2.exe> <2022 grandia2.exe>
    pyinstaller --onefile --console --name GranasSabre GranasSabre.py

`gs_build.py` contains the hook code (x86 assembly) and an address table per
supported `grandia2.exe` build. Every patch site is checked against the real
exe while building. `make_release.py` assembles one patch per build and embeds
them in the template, so the end-user tool needs neither keystone nor any game
file.

Supported builds:

| Build | Size | SHA-256 |
|---|---|---|
| 2020-07-31 | 2 841 088 | `06ad6c88778409f65dd111493faf57c3dd92f3292edd6a8ce6ea0109ee5d630e` |
| 2022-04-02 (Steam) | 3 198 464 | `adb2bc70dd4707fdb6a6163a867bfdecceb55fc3391486e2902c16d1ce77d3e3` |

## Command line

    GranasSabre patch   [--game-dir DIR] [--default-level 6] [--step-db 3] [--ids 1,2,3]
    GranasSabre restore [--game-dir DIR]
    GranasSabre status  [--game-dir DIR]

`--default-level` is the bar step (0–10) for saves without a setting,
`--step-db` the volume change per step, `--ids` the sound IDs controlled by the
bar (`all` = all 21 system sounds).

## How it works

Addresses are for the 2020 build. The Steam build contains the same code at
other addresses (see the tables in `gs_build.py`).

- The game computes the volume of every sound in one function (VA `0x5EF770`).
  GranasSabre hooks its SFX branch and multiplies the volume of the selected
  sound IDs by the gain of the current bar step.
- The config menu (setup at `0x582F20`, input at `0x57FE40`, per-frame drawing
  at `0x58BC86`) is extended from 3 to 4 rows. Text and help tables move into
  a new PE section `.gsabre`.
- The bar step is stored in bits 3–6 of the config flag word in the save data.
  These bits are unused by the game. The menu's close handler is patched so it
  no longer clears them.
- The bar is a normal option text from `strings.txt` (`gs_bar_a` … `gs_bar_k`).
  The game requires a `/` in option texts. The part before it is highlighted.
- Because the new code uses absolute addresses, the EXE's ASLR flag
  (DYNAMIC_BASE) is cleared.
