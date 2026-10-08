# Building GranasSabre

Needs Python 3.8+ with `keystone-engine` (only for building) and `pyinstaller`.

    pip install keystone-engine pyinstaller
    python make_release.py <original grandia2.exe> GranasSabre.template.py GranasSabre.py
    pyinstaller --onefile --console --name GranasSabre GranasSabre.py

`gs_build.py` contains the hook code (x86 assembly) and the list of byte
patches. `make_release.py` assembles it and embeds the result in the template,
so the end-user tool needs neither keystone nor any game file.
