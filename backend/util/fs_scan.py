"""Directory listings that take each file's type from the listing itself.

Path.iterdir()/rglob() followed by is_file() costs one stat per entry, which on a network
share is one round trip per file. scandir entries already carry the type, so a regular file
costs nothing beyond the listing.
"""
import os
from typing import Collection, Iterator, Optional


def iter_file_entries(
    root,
    suffixes: Optional[Collection[str]] = None,
    *,
    recursive: bool = True,
    skip_dirs: Collection[str] = (),
    strict: bool = False,
) -> Iterator[os.DirEntry]:
    """scandir entries for the files under root, optionally limited to lower-cased `suffixes`.

    Symlinked folders are not followed (same as rglob). `skip_dirs` are lower-cased folder
    names pruned at any depth. Non-strict skips unreadable folders the way rglob and os.walk
    do; strict raises the OSError so a caller can refuse to act on a partial listing.
    """
    pending = [os.fspath(root)]
    while pending:
        try:
            it = os.scandir(pending.pop())
        except OSError:
            if strict:
                raise
            continue
        with it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    if recursive and entry.name.lower() not in skip_dirs:
                        pending.append(entry.path)
                elif entry.is_file() and (suffixes is None or os.path.splitext(entry.name)[1].lower() in suffixes):
                    yield entry
