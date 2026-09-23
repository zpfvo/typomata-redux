"""Verify positive consumers and explicit negative diagnostics with both checkers.

Run with the development Python. --python-executable selects the environment whose
installed packages are checked (also used by verify_distribution.py). Fixtures are
never executed. Known gaps are reported separately and are not typing guarantees.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# Native suppressions keep ordinary mypy/pyright runs useful. Removing both in a
# temporary copy turns them into explicit, independently checked expectations.
IGNORE = re.compile(r'# (type|pyright): ignore\[([^\]]+)\]')
MYPY_ERROR = re.compile(r'^(.*?):(\d+): error: .*?  \[([^\]]+)\]$')
Diagnostic = tuple[str, int, str]


def check(command: list[str], cwd: Path, environment: dict[str, str], checker: str) -> set[Diagnostic]:
    result = subprocess.run(command, cwd=cwd, env=environment, text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(f'{checker} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}')
    diagnostics = set()
    if checker == 'mypy':
        for line in result.stdout.splitlines():
            match = MYPY_ERROR.match(line)
            if match:
                path, number, code = match.groups()
                diagnostics.add((Path(path).name, int(number), code))
            elif ': note:' not in line:
                raise RuntimeError(f'Unrecognized mypy output: {line}')
    else:
        for entry in json.loads(result.stdout)['generalDiagnostics']:
            if entry['severity'] != 'information':
                diagnostics.add((Path(entry['file']).name, entry['range']['start']['line'] + 1,
                                 entry.get('rule', '<no-rule>')))
    if result.returncode == 1 and not diagnostics:
        raise RuntimeError(f'{checker} failed without diagnostics:\n{result.stdout}\n{result.stderr}')
    return diagnostics


def verify(fixtures: Path, python: Path) -> None:
    tools = Path(sys.executable).parent
    environment = dict(os.environ)
    for key in ('PYTHONPATH', 'MYPYPATH'):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='typomata-redux-typing-') as directory:
        temp = Path(directory)
        files = sorted(fixtures.glob('*.py'))
        gaps = sorted((fixtures / 'known_gaps').glob('*.py'))
        if not files:
            raise RuntimeError(f'No typing fixtures in {fixtures}')
        names = [path.name for path in files + gaps]
        if len(names) != len(set(names)):
            raise RuntimeError('Typing fixture filenames must be unique')
        config = temp / 'pyrightconfig.json'
        settings = {'pythonVersion': '3.10', 'typeCheckingMode': 'standard', 'include': names}
        venv = python.parent.parent
        if (venv / 'pyvenv.cfg').exists():
            settings.update(venvPath=str(venv.parent), venv=venv.name)
        config.write_text(json.dumps(settings))
        commands = {
            'mypy': [str(tools / 'mypy'), '--strict', '--untyped-calls-exclude=typomata',
                     '--python-version', '3.10', '--python-executable', str(python),
                     '--no-incremental', '--no-pretty', '--no-color-output',
                     '--no-error-summary', '--show-error-codes', *names],
            'pyright': [str(tools / 'pyright'), '--pythonpath', str(python),
                        '--project', str(config), '--outputjson'],
        }
        for file in files + gaps:
            (temp / file.name).write_text(file.read_text())
        # Original fixtures must pass too: valid declarations and calls must remain
        # accepted, and mypy checks for stale suppressions before they are removed.
        for checker, command in commands.items():
            actual = check(command, temp, environment, checker)
            if actual:
                raise RuntimeError(f'{checker}: positive fixtures or known gaps changed: {sorted(actual)}')
        expected: dict[str, set[Diagnostic]] = {'mypy': set(), 'pyright': set()}
        for file in files:
            source = file.read_text()
            for number, line in enumerate(source.splitlines(), 1):
                markers = IGNORE.findall(line)
                if 'ignore' in line and ('# type:' in line or '# pyright:' in line):
                    if sorted(kind for kind, _ in markers) != ['pyright', 'type']:
                        raise RuntimeError(f'{file.name}:{number}: specify both checkers and their diagnostic codes')
                for kind, codes in markers:
                    checker = 'mypy' if kind == 'type' else 'pyright'
                    for code in codes.split(','):
                        expected[checker].add((file.name, number, code.strip()))
            (temp / file.name).write_text(IGNORE.sub('', source))
        if not all(expected.values()):
            raise RuntimeError('Negative diagnostic expectations are missing')
        for checker, command in commands.items():
            actual = check(command, temp, environment, checker)
            missing, unexpected = expected[checker] - actual, actual - expected[checker]
            if missing or unexpected:
                raise RuntimeError(f'{checker}: diagnostic mismatch\n'
                                   f'  Missing: {sorted(missing)}\n  Unexpected: {sorted(unexpected)}')
            lines = {(name, line) for name, line, _ in expected[checker]}
            print(f'{checker}: positive fixtures and {len(lines)} negative lines verified.')
        for gap in gaps:
            print(f'KNOWN GAP (not rejected by either checker): {gap.stem}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'tests' / 'typing')
    parser.add_argument('--python-executable', type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    try:
        verify(args.fixtures.resolve(), args.python_executable.absolute())
    except RuntimeError as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
