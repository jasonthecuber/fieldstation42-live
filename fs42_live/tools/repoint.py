"""Points FieldStation42 at media that moved: every catalog link and stored path under OLD now says NEW.

Move the files first, stop fs42-live (it keeps some of these in memory and would write the old paths
back), run this, then start it again:

    docker stop fs42-live
    python3 ~/fieldstation42-live/fs42_live/tools/repoint.py OLD NEW [~/fs42-data] [--dry]
    docker start fs42-live

OLD and NEW are host paths (the container sees /mnt/storage at the same path), a folder or one file.
Without this, a move breaks the catalog's links, and what FS42 measured about each file -- durations,
ad breaks, chapters, loudness, test-bar crops, all keyed by the file's real path -- gets measured
again, which for a whole drive is hours.
"""

import json
import os
import sqlite3
import subprocess
import sys

# columns holding a file's real path
DB_PATHS = [("catalog_entries", "realpath"), ("file_meta", "path"), ("break_points", "path"),
            ("chapter_points", "path")]


def mover(old, new):
    def moved(p):
        if p == old or p.startswith(old + "/"):
            return new + p[len(old):]
        return None
    return moved


def fs42_running():
    try:
        out = subprocess.run(["docker", "ps", "-q", "--filter", "name=^fs42-live$"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.strip())


def links(catalog, moved, dry):
    n = 0
    for root, dirs, files in os.walk(catalog):  # doesn't follow links, so each is seen once
        for name in dirs + files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                continue
            target = moved(os.readlink(path))
            if target:
                n += 1
                if not dry:
                    os.remove(path)
                    os.symlink(target, path)
    return n


def database(db, old, new, dry):
    counts = {}
    con = sqlite3.connect(db)
    try:
        for table, col in DB_PATHS:
            # substr, not LIKE: paths are full of LIKE's wildcards (abc_1983)
            # OR REPLACE: a row already measured at the new path loses to the moved one
            cur = con.execute(f"UPDATE OR REPLACE {table} SET {col} = ? || substr({col}, ?) "
                              f"WHERE {col} = ? OR substr({col}, 1, ?) = ?",
                              (new, len(old) + 1, old, len(old) + 1, old + "/"))
            counts[table] = cur.rowcount
        if dry:
            con.rollback()
        else:
            con.commit()
    finally:
        con.close()
    return counts


def rewrite(value, moved):
    if isinstance(value, str):
        return moved(value) or value
    if isinstance(value, list):
        return [rewrite(v, moved) for v in value]
    if isinstance(value, dict):
        return {(moved(k) or k): rewrite(v, moved) for k, v in value.items()}
    return value


def json_files(folders, moved, dry):
    changed = []
    for folder in folders:
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not name.endswith(".json") or not os.path.isfile(path):
                continue
            with open(path) as f:
                text = f.read()
            try:
                data = json.loads(text)
            except ValueError:
                continue
            new_data = rewrite(data, moved)
            if new_data == data:
                continue
            changed.append(path)
            if not dry:
                # keep the file's own indent (FS42's caches use 0)
                lines = text.split("\n", 2)
                indent = len(lines[1]) - len(lines[1].lstrip(" ")) if len(lines) > 1 else None
                with open(path + ".tmp", "w") as f:
                    json.dump(new_data, f, indent=indent)
                    f.write("\n")
                os.replace(path + ".tmp", path)
    return changed


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    if len(args) not in (2, 3):
        sys.exit(__doc__)
    old, new = (os.path.normpath(a) for a in args[:2])
    data = os.path.expanduser(args[2] if len(args) == 3 else "~/fs42-data")
    if not dry and not os.path.exists(new):
        sys.exit(f"{new} doesn't exist; move the files first")
    if not dry and fs42_running():
        sys.exit("fs42-live is running; docker stop fs42-live first")
    moved = mover(old, new)
    print(f"{'would repoint' if dry else 'repointed'} {old} -> {new}")
    print(f"  catalog links: {links(os.path.join(data, 'catalog'), moved, dry)}")
    for table, n in database(os.path.join(data, "runtime", "fs42_fluid.db"), old, new, dry).items():
        print(f"  {table}: {n}")
    for path in json_files([data, os.path.join(data, "runtime")], moved, dry):
        print(f"  {path}")


if __name__ == "__main__":
    main()
