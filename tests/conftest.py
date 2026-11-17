from pathlib import Path
from zipfile import ZipFile

import pytest

from patchops.config import Settings
from patchops.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_root=tmp_path / "var",
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        allowed_roots=[tmp_path],
        llm_retries=0,
    )


@pytest.fixture
def store(settings):
    return Store(settings)


@pytest.fixture
def archive(tmp_path):
    def create(files: dict[str, str] | Path) -> Path:
        path = tmp_path / "input.zip"
        with ZipFile(path, "w") as target:
            if isinstance(files, Path):
                for file in files.rglob("*"):
                    if file.is_file() and "__pycache__" not in file.parts:
                        target.write(file, file.relative_to(files).as_posix())
            else:
                for name, content in files.items():
                    target.writestr(name, content)
        return path

    return create
