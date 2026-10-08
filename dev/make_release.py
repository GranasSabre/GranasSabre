"""Build GranasSabre.py from the template: assemble the patch and embed it."""
import base64
import json
import sys
import zlib
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gs_build

orig = open(sys.argv[1], "rb").read()
desc = gs_build.build(orig)
desc.pop("code")
blob = base64.b64encode(zlib.compress(json.dumps(desc, separators=(",", ":")).encode(), 9)).decode()
tpl = open(sys.argv[2], encoding="utf-8").read()
assert "@@PATCH_DATA@@" in tpl
open(sys.argv[3], "w", encoding="utf-8", newline="\n").write(tpl.replace("@@PATCH_DATA@@", blob))
print("embedded", len(blob), "chars")
