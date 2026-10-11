"""Build GranasSabre.py from the template: assemble the patch for every
supported grandia2.exe build and embed the results.

Usage: make_release.py GranasSabre.template.py GranasSabre.py original1.exe [original2.exe ...]
"""
import base64
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gs_build  # noqa: E402

template, output, exes = sys.argv[1], sys.argv[2], sys.argv[3:]
descs = {}
for path in exes:
    desc = gs_build.build(open(path, "rb").read())
    desc.pop("code")
    descs[desc["sha256"]] = desc
    print("built", desc["label"])
missing = set(gs_build.BUILDS) - set(descs)
if missing:
    raise SystemExit("missing original exe for: %s" % ", ".join(gs_build.BUILDS[h]["label"] for h in missing))
blob = base64.b64encode(zlib.compress(json.dumps(descs, separators=(",", ":")).encode(), 9)).decode()
tpl = open(template, encoding="utf-8").read()
assert "@@PATCH_DATA@@" in tpl
open(output, "w", encoding="utf-8", newline="\n").write(tpl.replace("@@PATCH_DATA@@", blob))
print("embedded", len(blob), "chars")
