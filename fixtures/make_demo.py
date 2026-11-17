"""Create the ZIP input used in the README without mutating the fixture."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent / "customer_catalog"
destination = Path("var/customer_catalog.zip")
destination.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for item in sorted(root.rglob("*")):
        if item.is_file() and "__pycache__" not in item.parts:
            archive.write(item, item.relative_to(root).as_posix())
print(destination)
