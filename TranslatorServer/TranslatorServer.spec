# PyInstaller spec for TranslatorServer.exe (run build_server_exe.cmd in the repository root, not this file directly).
#
# One-folder build: dist\TranslatorServer\TranslatorServer.exe plus _internal\ (Python, PyTorch,
# FastAPI, ...). build_server_exe.cmd copies models\ next to the exe, so the models can be replaced
# without rebuilding. One-folder (not one-file) because the cluster starts several processes of
# the same exe: a one-file exe would unpack about 580 MB to %TEMP% for every process.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    ["app", "cluster", "loadbalancer", "logsetup", "translator"]
    + collect_submodules("translator")
    + collect_submodules("uvicorn")  # protocols / loops / lifespan are imported by name at runtime
)

a = Analysis(
    ["server_main.py"],
    pathex=["."],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "_pytest", "sacrebleu", "torch.utils.tensorboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TranslatorServer",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TranslatorServer", upx=False)
