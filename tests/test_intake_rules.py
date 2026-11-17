import io
import shutil
import stat
import subprocess
from zipfile import ZipFile, ZipInfo

import pytest

from patchops.analysis import inspect_repo
from patchops.intake import Extract, Snapshot, extract_zip, snapshot, validate_source
from patchops.migrations.pydantic_v2 import PydanticV2, dependency_edit, transform
from patchops.models import Request
from patchops.patch import unified
from patchops.process import run
from patchops.security import Blocked, redact, relative
from patchops.validation import pytest_results

SOURCE = """from pydantic import BaseModel as PModel

class Book(PModel):
    price: int

# preserve this
new = Book(price=2).dict( by_alias = True )  # and this
read = Book.parse_obj({'price': 3})
unchanged = Unknown().dict()
text = Book(price=2).json(indent=2)
"""


def request(**source):
    return Request.model_validate({"source": source})


@pytest.mark.parametrize(
    "path", ["../x", "/root/file", "C:/host", "path\\name", "foo//bar", "CON", "."]
)
def test_unsafe_paths(path):
    with pytest.raises(Blocked):
        relative(path)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://github.com.evil/x/y",
        "https://user:pass@github.com/x/y",
        "https://github.com/x/..",
        "https://github.com/x/y?token=abc",
        "https://127.0.0.1/x/y",
    ],
)
def test_remote_url_scope(settings, url):
    with pytest.raises(Blocked):
        validate_source(request(type="git_url", url=url), settings)


def test_git_rejects_option_like_ref(settings):
    with pytest.raises(Blocked):
        validate_source(
            request(
                type="git_url", url="https://github.com/x/y", ref="--upload-pack=bad"
            ),
            settings,
        )


def test_local_path_boundary(settings, tmp_path):
    settings.allowed_roots = [tmp_path / "one"]
    (tmp_path / "one-two").mkdir()
    with pytest.raises(Blocked, match="allowed roots"):
        validate_source(request(type="local", path=str(tmp_path / "one-two")), settings)


@pytest.mark.parametrize("entry", ["../escape.txt", "/absolute", ".\\escape"])
def test_zip_rejects_traversal(settings, archive, tmp_path, entry):
    zip_path = archive({entry: "data"})
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(Blocked, match="Unsafe"):
        extract_zip(zip_path, Extract(target, settings, lambda: None))
    assert not list(target.iterdir())


def test_zip_symlink(settings, tmp_path):
    zip_path = tmp_path / "link.zip"
    with ZipFile(zip_path, "w") as archive:
        link = ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(Blocked, match="symlink"):
        extract_zip(zip_path, Extract(tmp_path, settings, lambda: None))


def test_zip_file_count(settings, archive, tmp_path):
    settings.max_files = 1
    zip_path = archive({"one.py": "one", "two.py": "two"})
    with pytest.raises(Blocked, match="file count"):
        extract_zip(zip_path, Extract(tmp_path, settings, lambda: None))


def test_zip_uncompressed_limit(settings, tmp_path):
    from zipfile import ZIP_DEFLATED

    path = tmp_path / "bomb.zip"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("x", b"a" * (1024 * 1024 + 1))
    settings.max_repo_mb = 1
    with pytest.raises(Blocked, match="uncompressed"):
        extract_zip(path, Extract(tmp_path, settings, lambda: None))


def test_git_snapshot_uses_committed_revision(settings, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.py").write_text("value = 1\n")
    (repo / ".gitattributes").write_text("main.py export-ignore\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "main.py", ".gitattributes"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (repo / "main.py").write_text("value = 2\n")
    destination = tmp_path / "job"
    destination.mkdir()
    result = snapshot(
        request(type="local", path=str(repo)), destination, settings, lambda: None
    )
    assert result.commit
    assert (result.root / "main.py").read_text() == "value = 1\n"
    assert (repo / "main.py").read_text() == "value = 2\n"


def test_inventory_excludes_secrets_and_resolves_imports(settings, archive, tmp_path):
    folder = tmp_path / "job"
    folder.mkdir()
    shutil.copyfile(
        archive(
            {
                "app.py": "import utils\nclass App: ...\n",
                "utils.py": SOURCE,
                "tests/test_app.py": "",
                ".env.local": "token=x",
                "vendor/no.py": "raise RuntimeError()",
                "uv.lock": "",
            }
        ),
        folder / "input.zip",
    )
    req = request(type="zip_upload")
    fixed = snapshot(req, folder, settings, lambda: None)
    result = inspect_repo(fixed, req, PydanticV2(), lambda: None)
    assert result.local_import_graph["app.py"] == ["utils.py"]
    assert result.symbols["app.py"] == ["ClassDef App:2"]
    assert result.test_inventory == ["tests/test_app.py"]
    assert result.lockfiles == ["uv.lock"]
    assert result.files_skipped == 2
    assert not (fixed.root / ".env.local").exists()


def test_rewrite_preserves_spacing_and_resolves_receiver():
    output, signals = transform("models.py", SOURCE)
    assert "Book(price=2).model_dump( by_alias = True )  # and this" in output
    assert "Book.model_validate({'price': 3})" in output
    assert "Unknown().dict()" in output
    assert ".json(indent=2)" in output
    assert any(
        item.strategy == "semantic" and item.rule == "api-json" for item in signals
    )
    assert transform("models.py", output)[0] == output


def test_dependency_preserves_unrelated_packages_and_markers():
    source = 'pydantic==1.10.22; python_version >= "3.10" # stay\npytest==8.4.2\n'
    assert dependency_edit("requirements.txt", source, "2.11") == (
        'pydantic==2.11.*; python_version >= "3.10" # stay\npytest==8.4.2\n'
    )
    source = '[project]\ndependencies = ["pydantic>=1,<2", "other==1"]\n'
    assert dependency_edit("pyproject.toml", source, "2.x") == source.replace(
        ">=1,<2", ">=2,<3"
    )
    assert dependency_edit("uv.lock", source, "2.x") == source


def test_dependency_rewrites_only_v1_exclusive_constraints():
    path = "requirements.txt"
    original = (
        'pydantic[email, timezone] ~=1.10; python_version >= "3.10"\n'
        'pydantic>=1,<2.1,!=2.0; python_version < "3.10"\n'
    )
    assert dependency_edit(path, original, "2.11") == original.replace(
        "~=1.10", "==2.11.*"
    )
    signals = PydanticV2().inspect(path, original, request(type="zip_upload"))
    assert {item.strategy for item in signals} == {"deterministic", "manual"}


def test_plan_protects_tests_and_locked_dependencies(tmp_path):
    plugin = PydanticV2()
    req = request(type="zip_upload")
    req.constraints.allow_dependency_update = False
    (tmp_path / "test_models.py").write_text(SOURCE)
    (tmp_path / "requirements.txt").write_text("pydantic==1.10.22\n")
    analysis = inspect_repo(
        Snapshot(tmp_path, None, "fixture", None, 0, []), req, plugin, lambda: None
    )
    plan = plugin.plan(analysis, req, ["pytest"])
    assert all(task.strategy == "manual" for task in plan.tasks)
    assert not plan.files
    assert not plugin.permits(tmp_path, "test_models.py", plan)


def test_patch_applies_as_unified_diff(tmp_path):
    before, after = "x = 1", "x = 2"
    (tmp_path / "a.py").write_text(before)
    with io.BytesIO(unified("a.py", before, after).encode()) as stream:
        assert b"No newline at end of file" in stream.getvalue()
    patch = tmp_path / "change.diff"
    patch.write_bytes(unified("a.py", before, after).encode())
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=tmp_path, check=True)
    patch.write_text("not a patch")
    result = subprocess.run(
        ["git", "apply", "--check", str(patch)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0


def test_patch_preserves_crlf_and_embedded_form_feed(tmp_path):
    path = tmp_path / "my models.py"
    before, after = "text = '''x\fy'''\r\n", "text = '''x\fz'''\r\n"
    path.write_bytes(before.encode())
    patch = tmp_path / "change.diff"
    patch.write_bytes(unified(path.name, before, after).encode())
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True)
    assert path.read_bytes() == after.encode()


def test_redaction_and_xml():
    assert "ghp_mytoken123" not in redact("Bearer ghp_mytoken123")
    outcomes, failures = pytest_results(
        b'<testsuite><testcase classname="tests.test_a" name="test_case">'
        b'<failure message="a.py:42: assert 2 == 1"/></testcase></testsuite>'
    )
    assert outcomes["tests.test_a::test_case"].startswith("FAILED")
    assert "a.py:#" in failures[0]
    assert pytest_results(b"<!DOCTYPE root><root/>") == ({}, [])


def test_process_timeout_nonzero_and_output_cap():
    import sys

    assert run(
        [sys.executable, "-c", "import time;time.sleep(5)"], seconds=0.1
    ).timed_out
    result = run(
        [sys.executable, "-c", "import sys;print('a'*1000);sys.exit(7)"],
        seconds=3,
        limit=20,
    )
    assert result.code == 7 and result.truncated and len(result.stdout) == 20


def test_shadowed_or_overridden_model_methods_stay_unchanged():
    source = """from pydantic import BaseModel

class A(BaseModel):
    def dict(self):
        return {"custom": 1}

class B(BaseModel):
    price: int

A().dict()
B = other
B.parse_obj({})
"""
    changed, signals = transform("models.py", source)
    assert changed == source
    assert {"api-dict", "unresolved-receiver"} <= {
        item.rule for item in signals if item.strategy == "manual"
    }


def test_v2_method_names_defined_by_project_require_manual_review():
    source = """from pydantic import BaseModel

class Parent(BaseModel):
    def model_dump(self):
        return {"custom": True}

class Child(Parent):
    @classmethod
    def model_validate(cls, value):
        return value

Child().dict()
Child.parse_obj({})
"""
    changed, signals = transform("models.py", source)
    assert changed == source
    assert {item.rule for item in signals if item.strategy == "manual"} == {
        "api-dict",
        "api-parse_obj",
    }


def test_version_selector_uses_requested_minor_and_exact_patch():
    from patchops.migrations.pydantic_v2 import version_matches

    assert version_matches("1.10.22", "1.x")
    assert version_matches("2.11.10", "2.11")
    assert not version_matches("2.11.10", "2.11.9")
    assert not version_matches("2.11.10", "1.x")
    assert not version_matches("unknown", "2.x")
