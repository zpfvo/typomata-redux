"""Verify the installed wheel with the complete runtime and typing suites.

Run using the project's development environment. uv, mypy and Pyright must be
available; UV_CACHE_DIR can select a writable/cacheable dependency cache.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "MYPYPATH"):
        environment.pop(key, None)

    def run(*command: str, cwd: Path) -> None:
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    with tempfile.TemporaryDirectory(prefix="typomata-redux-wheel-") as directory:
        temp = Path(directory)
        wheels = temp / "wheels"
        run("uv", "build", "--out-dir", str(wheels), cwd=project)
        archive = next(wheels.glob("typomata_redux-*.tar.gz"))
        with tarfile.open(archive) as source:
            names = source.getnames()
            assert any("/tests/test_redux.py" in name for name in names)
            assert any("/examples/counter.py" in name for name in names)
            assert not any("/references/" in name for name in names)
            assert not any("/typing_optional/" in name or name.endswith("/typomata_counter.py") for name in names)
            # Copy only the test/example files, without trusting archive paths.
            for member in source.getmembers():
                relative = Path(*Path(member.name).parts[1:])
                if not member.isfile() or not relative.parts or relative.parts[0] not in ("tests", "examples"):
                    continue
                assert ".." not in relative.parts
                target = temp / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                data = source.extractfile(member)
                assert data is not None
                target.write_bytes(data.read())
        wheel = next(wheels.glob("typomata_redux-*.whl"))
        with zipfile.ZipFile(wheel) as built:
            assert "typomata_redux/py.typed" in built.namelist()
            assert any(name.endswith("/licenses/LICENSE") for name in built.namelist())
            metadata = next(name for name in built.namelist() if name.endswith("/METADATA"))
            assert b"Requires-Python: >=3.10" in built.read(metadata)
            assert b"Requires-Dist: typomata" not in built.read(metadata)
        venv = temp / "venv"
        run("uv", "venv", "--python", sys.executable, str(venv), cwd=temp)
        python = venv / "bin" / "python"
        run("uv", "pip", "install", "--python", str(python), str(wheel), cwd=temp)
        run(str(python), "-c", "import importlib.util, typomata_redux; "
            "assert 'site-packages' in typomata_redux.__file__; "
            "assert not hasattr(typomata_redux, 'MachineReducer'); "
            "assert importlib.util.find_spec('typomata') is None", cwd=temp)
        run(str(python), "-m", "unittest", "discover", "-s", "tests", "-v", cwd=temp)
        run(str(python), "examples/counter.py", cwd=temp)
        run(sys.executable, str(project / "scripts" / "verify_typing.py"),
            "--fixtures", str(temp / "tests" / "typing"),
            "--python-executable", str(python), cwd=temp)
        print("Installed-wheel runtime, typing, and example checks passed without Typomata.")


if __name__ == "__main__":
    main()
