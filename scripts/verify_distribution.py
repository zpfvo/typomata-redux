"""Build both projects; run tests and consumer checks outside the source tree.

Run using the project's development environment. uv, mypy and Pyright must be
available; UV_CACHE_DIR can select a writable/cacheable dependency cache.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    tool_bin = Path(sys.executable).parent
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "MYPYPATH"):
        environment.pop(key, None)

    def run(*command: str, cwd: Path) -> None:
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    with tempfile.TemporaryDirectory(prefix="typomata-redux-wheel-") as directory:
        temp = Path(directory)
        wheels = temp / "wheels"
        run("uv", "build", "--out-dir", str(wheels), cwd=project.parent / "typomata")
        run("uv", "build", "--out-dir", str(wheels), cwd=project)
        archive = next(wheels.glob("typomata_redux-*.tar.gz"))
        with tarfile.open(archive) as source:
            names = source.getnames()
            assert any("/tests/test_redux.py" in name for name in names)
            assert any("/examples/counter.py" in name for name in names)
            assert not any("/references/" in name for name in names)
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
        venv = temp / "venv"
        run("uv", "venv", "--python", sys.executable, str(venv), cwd=temp)
        python = venv / "bin" / "python"
        typomata_wheel = next(wheels.glob("typomata-*.whl"))
        run("uv", "pip", "install", "--python", str(python), str(typomata_wheel), str(wheel), cwd=temp)
        run(str(python), "-c", "import typomata_redux; assert 'site-packages' in typomata_redux.__file__", cwd=temp)
        run(str(python), "-m", "unittest", "discover", "-s", "tests", "-v", cwd=temp)
        run(str(python), "examples/counter.py", cwd=temp)
        run(str(tool_bin / "mypy"), "--strict", "--untyped-calls-exclude=typomata",
            "--python-version", "3.10", "--python-executable", str(python),
            "--no-incremental", "tests/typing", cwd=temp)
        config = temp / "pyrightconfig.json"
        config.write_text(json.dumps({
            "include": ["tests/typing"], "pythonVersion": "3.10",
            "venvPath": str(temp), "venv": "venv",
        }))
        run(str(tool_bin / "pyright"), "--pythonpath", str(python), "--project", str(config), cwd=temp)
        print("Built-artifact tests, example, typing marker, license, and consumer checks passed.")


if __name__ == "__main__":
    main()
