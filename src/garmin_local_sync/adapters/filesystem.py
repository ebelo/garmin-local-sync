"""Private, atomic local artifact storage."""

from pathlib import Path

from ..util import atomic_write


class FileArchiveStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve() / "archives"

    def read(self, activity_id: int) -> bytes | None:
        source = self.root / f"{int(activity_id)}.zip"
        try:
            return source.read_bytes()
        except FileNotFoundError:
            return None

    def write(self, activity_id: int, content: bytes) -> None:
        atomic_write(self.root / f"{int(activity_id)}.zip", content)
