import logging
import sqlite3
import datetime
import os
import json
from fs42.media_processor import MediaProcessor
from fs42.fluid_objects import FileRepoEntry


class FluidStatements:
    """Basic static SQL functions for interacting with the Fluid catalog DB"""

    @staticmethod
    def check_file_cache(connection: sqlite3.Connection, full_path) -> FileRepoEntry:
        """Find full_path and return fullpath if its in the file cache"""

        cursor = connection.cursor()
        cursor.execute("SELECT * FROM file_meta WHERE path = ?;", (full_path,))
        row = cursor.fetchone()
        result = None
        if row:
            repo_entry = FileRepoEntry(row)
            result = repo_entry
        cursor.close()
        return result

    @staticmethod
    def iterate_file_entries(connection: sqlite3.Connection, entries: list[FileRepoEntry]) -> None:
        """Takes a list of file entries, determines if they are cached and adds them if not."""

        cursor = connection.cursor()
        for entry in entries:
            # see if there is an entry already
            cursor.execute("SELECT * FROM file_meta WHERE path = ?;", (entry.path,))
            row = cursor.fetchone()
            if row:
                repo_entry = FileRepoEntry()
                repo_entry.from_db_row(row)

                # Check if we need to update this entry
                needs_update = False

                # Update if file stats changed
                if entry != repo_entry:
                    needs_update = True

                # Also update if this is an audio file with empty metadata
                # (happens when catalog was built before mutagen was installed)
                if repo_entry.media_type == 'audio' and not repo_entry.meta:
                    logging.getLogger("FLUID").info(f"Audio file missing metadata, will refresh: {entry.path}")
                    needs_update = True

                if needs_update:
                    FluidStatements.update_file_entry(connection, entry)
                elif repo_entry.media_type != 'audio':
                    FluidStatements.refresh_video_meta(connection, repo_entry)

            else:

                FluidStatements.add_file_entry(connection, entry)
        cursor.close()

    @staticmethod
    def refresh_video_meta(connection: sqlite3.Connection, repo_entry: FileRepoEntry):

        metadata = MediaProcessor.extract_metadata(repo_entry.path, 'video')
        new_meta = json.dumps(metadata) if metadata else ""

        if new_meta != (repo_entry.meta or ""):
            logging.getLogger("FLUID").info(f"NFO metadata changed, refreshing: {repo_entry.path}")
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE file_meta SET meta=?, last_checked=? WHERE path=?",
                (new_meta, datetime.datetime.now(), repo_entry.path),
            )
            cursor.close()
            connection.commit()

    @staticmethod
    def trim_file_entries(connection: sqlite3.Connection, older_than: datetime):
        """Checks all files in the cache to ensure still on disk and removes them if not."""

        cursor = connection.cursor()
        cursor.execute("SELECT * FROM file_meta WHERE last_updated < ?;", (older_than,))
        to_remove = []
        rows = cursor.fetchall()
        logging.getLogger("FLUID").info(f"Checking {len(rows)} files on the filesystem")
        for row in rows:
            repo_entry = FileRepoEntry()
            repo_entry.from_db_row(row)
            if not os.path.exists(repo_entry.path):
                logging.getLogger("FLUID").info(f"File not found on filesystem - will remove: {repo_entry}")
                to_remove.append(repo_entry.path)

        connection.execute("BEGIN TRANSACTION;")

        for p in to_remove:
            cursor.execute("DELETE from file_meta WHERE path=?", (p,))

        cursor.close()
        connection.commit()

    @staticmethod
    def update_file_entry(connection: sqlite3.Connection, entry: FileRepoEntry):
        """An old entry has changed, get the new stats and update it."""
        cursor = connection.cursor()
        now = datetime.datetime.now()

        processed = MediaProcessor.process_one(entry.path, "processing", [])
        if not processed:
            return False
        entry.duration = processed.duration

        # Extract metadata: ID3 tags for audio, NFO sidecar for video
        media_type = MediaProcessor.get_media_type(entry.path)
        metadata = MediaProcessor.extract_metadata(entry.path, media_type)
        entry.meta = json.dumps(metadata) if metadata else ""

        logging.getLogger("FLUID").info(f"Updating existing file entry: {entry.path}")

        update = """UPDATE file_meta SET duration=?, size=?, last_mod=?, last_updated=?, last_checked=?, meta=?, media_type=?
        WHERE path=?;
        """
        values = (entry.duration, entry.size, entry.last_mod, now, now, entry.meta, media_type, entry.path)
        cursor.execute(update, values)
        cursor.close()
        connection.commit()

    @staticmethod
    def add_file_entry(connection: sqlite3.Connection, entry: FileRepoEntry):
        """This file isn't in the cache - add it."""
        cursor = connection.cursor()
        now = datetime.datetime.now()

        entry.first_added = now
        entry.last_checked = now
        entry.last_updates = now

        processed = MediaProcessor.process_one(entry.path, "processing", [])
        if not processed:
            return False

        entry.duration = processed.duration

        # Extract metadata: ID3 tags for audio, NFO sidecar for video
        media_type = MediaProcessor.get_media_type(entry.path)
        metadata = MediaProcessor.extract_metadata(entry.path, media_type)
        entry.meta = json.dumps(metadata) if metadata else ""

        logging.getLogger("FLUID").info(f"Caching new file entry: {entry}")

        # Note: to_db_row() should now include media_type column
        cursor.execute("INSERT INTO file_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", entry.to_db_row() + (media_type,))
        cursor.close()
        connection.commit()

    @staticmethod
    def add_break_points(connection: sqlite3.Connection, path: str, points: dict):
        """Add or update the break points for this file"""
        cursor = connection.cursor()
        now = datetime.datetime.now()
        json_points = json.dumps(points)
        cursor.execute("REPLACE INTO break_points VALUES(?, ?, ?)", (path, json_points, now))
        cursor.close()
        connection.commit()

    @staticmethod
    def get_break_points(connection: sqlite3.Connection, path: str) -> dict:
        """Get the break points for this file"""
        cursor = connection.cursor()
        cursor.execute("SELECT points FROM break_points WHERE path=?", (path,))
        row = cursor.fetchone()
        result = {}
        if row:
            result = json.loads(row[0])
        cursor.close()
        return result
    
    def delete_break_points(connection: sqlite3.Connection, path: str):
        """Delete any break points for this file"""
        cursor = connection.cursor()
        cursor.execute("DELETE FROM break_points WHERE path=?", (path,))
        cursor.close()
        connection.commit()

    @staticmethod
    def add_chapter_points(connection: sqlite3.Connection, path: str, points: dict):
        """Add or update the chapter points for this file"""
        cursor = connection.cursor()
        now = datetime.datetime.now()
        json_points = json.dumps(points)
        cursor.execute("REPLACE INTO chapter_points VALUES(?, ?, ?)", (path, json_points, now))
        cursor.close()
        connection.commit()

    @staticmethod
    def get_chapter_points(connection: sqlite3.Connection, path: str) -> dict:
        """Get the chapter points for this file. Returns {} if no chapters or never scanned."""
        cursor = connection.cursor()
        cursor.execute("SELECT points FROM chapter_points WHERE path=?", (path,))
        row = cursor.fetchone()
        result = {}
        if row:
            loaded = json.loads(row[0])
            # Return {} if empty list (file was scanned but has no chapters)
            # This way schedule build sees it as "no usable chapters"
            result = loaded if loaded else {}
        cursor.close()
        return result

    @staticmethod
    def delete_chapter_points(connection: sqlite3.Connection, path: str):
        """Delete any chapter points for this file"""
        cursor = connection.cursor()
        cursor.execute("DELETE FROM chapter_points WHERE path=?", (path,))
        cursor.close()
        connection.commit()

    @staticmethod
    def add_play_end(connection: sqlite3.Connection, path: str, end: float, duration: float):
        """fs42_live (end_finder): where this file's picture and sound really end, for a file
        `duration` long (a file replaced under the same name is looked at again)"""
        connection.execute("REPLACE INTO play_ends VALUES(?, ?, ?, ?)",
                           (path, end, duration, datetime.datetime.now()))
        connection.commit()

    @staticmethod
    def get_play_end(connection: sqlite3.Connection, path: str, duration: float):
        """Where a file `duration` long stops playing, if it ends before that; else None."""
        row = connection.execute("SELECT end, duration FROM play_ends WHERE path=?", (path,)).fetchone()
        if row and abs(row[1] - duration) < 1 and row[0] < duration:
            return row[0]
        return None

    @staticmethod
    def add_play_cuts(connection: sqlite3.Connection, path: str, cuts, duration: float):
        """fs42_live (ad_finder): stretches [start, end] of a file `duration` long that don't play,
        such as ads recorded with an old broadcast; [] when it has none"""
        connection.execute("REPLACE INTO play_cuts VALUES(?, ?, ?, ?)",
                           (path, json.dumps([[round(a, 3), round(b, 3)] for a, b in cuts]),
                            duration, datetime.datetime.now()))
        connection.commit()

    @staticmethod
    def get_play_cuts(connection: sqlite3.Connection, path: str, duration: float):
        """The stretches a file `duration` long skips, oldest first; [] if none (or it was replaced).
        An uploader's card at its start (uploader_cards) is one: from 0 to where the show starts."""
        out = []
        start = FluidStatements.get_play_start(connection, path, duration)
        if start:
            out.append((0.0, start))
        row = connection.execute("SELECT cuts, duration FROM play_cuts WHERE path=?", (path,)).fetchone()
        if row and abs(row[1] - duration) < 1:
            out += [tuple(c) for c in json.loads(row[0])]
        return out

    @staticmethod
    def add_play_start(connection: sqlite3.Connection, path: str, start: float, duration: float):
        """fs42_live (uploader_cards): where the show starts in a file `duration` long, after an
        uploader's card; 0 when it has none (it was looked at)"""
        connection.execute("REPLACE INTO play_starts VALUES(?, ?, ?, ?)",
                           (path, start, duration, datetime.datetime.now()))
        connection.commit()

    @staticmethod
    def get_play_start(connection: sqlite3.Connection, path: str, duration: float):
        """Where a file `duration` long starts playing, if not at 0; else None."""
        try:
            row = connection.execute("SELECT start, duration FROM play_starts WHERE path=?", (path,)).fetchone()
        except sqlite3.OperationalError:  # a DB from before play_starts
            return None
        if row and abs(row[1] - duration) < 1 and row[0] > 0:
            return row[0]
        return None

    @staticmethod
    def init_db(connection: sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS file_meta (
                            path TEXT PRIMARY KEY,
                            duration REAL,
                            size INTEGER,
                            first_added TIMESTAMP,
                            last_mod TIMESTAMP,
                            last_checked TIMESTAMP,
                            last_updated TIMESTAMP,
                            meta TEXT
                            )
                            """)

        # Check if media_type column exists, add it if it doesn't
        cursor.execute("PRAGMA table_info(file_meta)")
        columns = [column[1] for column in cursor.fetchall()]

        if "media_type" not in columns:
            logging.getLogger("FLUID").info("Adding media_type column to file_meta table")
            cursor.execute("ALTER TABLE file_meta ADD COLUMN media_type TEXT DEFAULT 'video'")
            connection.commit()
            logging.getLogger("FLUID").info("Added media_type column to file_meta table")

        cursor.execute("""CREATE TABLE IF NOT EXISTS break_points (
                            path TEXT REFERENCES file_meta(path) PRIMARY KEY,
                            points TEXT,
                            last_updated TIMESTAMP
                            )
                       """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS chapter_points (
                            path TEXT REFERENCES file_meta(path) PRIMARY KEY,
                            points TEXT,
                            last_updated TIMESTAMP
                            )
                       """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS play_ends (
                            path TEXT PRIMARY KEY,
                            end REAL,
                            duration REAL,
                            last_updated TIMESTAMP
                            )
                       """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS play_starts (
                            path TEXT PRIMARY KEY,
                            start REAL,
                            duration REAL,
                            last_updated TIMESTAMP
                            )
                       """)

        cursor.execute("""CREATE TABLE IF NOT EXISTS play_cuts (
                            path TEXT PRIMARY KEY,
                            cuts TEXT,
                            duration REAL,
                            last_updated TIMESTAMP
                            )
                       """)

        cursor.close()
