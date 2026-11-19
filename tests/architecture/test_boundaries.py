import subprocess

from pytest_check import check


def test_layered_architecture_contracts() -> None:
    import shutil

    executable = shutil.which("lint-imports")
    assert executable is not None
    result = subprocess.run([executable], capture_output=True, text=True)
    output = result.stdout + result.stderr
    with check:
        assert result.returncode == 0, output
    with check:
        assert "0 broken" in output
    with check:
        assert "Contracts: 2 kept" in output


def test_services_do_not_import_fastapi() -> None:
    from pathlib import Path

    forbidden = ("fastapi", "starlette")
    offenders = []
    for path in Path("app/services").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if f"import {name}" in source or f"from {name}" in source:
                offenders.append(f"{path}: {name}")
    assert offenders == []


def test_models_do_not_import_outer_layers() -> None:
    from pathlib import Path

    forbidden = ("app.services", "app.repositories", "app.api")
    offenders = []
    for path in Path("app/models").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if f"from {name}" in source or f"import {name}" in source:
                offenders.append(f"{path}: {name}")
    assert offenders == []


def test_no_try_except_in_services_and_api() -> None:
    from pathlib import Path

    offenders = []
    for base in (Path("app/services"), Path("app/api")):
        for path in base.rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped == "try:" or stripped.startswith("except"):
                    offenders.append(f"{path}:{number}: {stripped}")
    assert offenders == []


def test_no_comments_in_application_code() -> None:
    from pathlib import Path

    offenders = []
    for path in Path("app").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                offenders.append(f"{path}:{number}")
    assert offenders == []
