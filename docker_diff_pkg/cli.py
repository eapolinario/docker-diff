#!/usr/bin/env python3
"""
Docker Image Comparison Database Manager
Provides functions to store and query Docker image file comparison results in SQLite
Supports local SQLite via `sqlite3` and remote Turso via `libsql`.
"""

import sqlite3
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import os
import threading
import time

try:
    # Optional: Turso/libsql client for remote DB (sync wrapper)
    from libsql_client.sync import create_client_sync  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    create_client_sync = None  # type: ignore

class DockerImageDB:
    def __init__(self, db_path: str = "docker_images.db"):
        """
        Initialize DB access. If environment variables for Turso/libsql are set,
        connect to the remote database. Otherwise, use local SQLite file.

        Env vars recognized (first found wins):
        - TURSO_DATABASE_URL / LIBSQL_URL
        - TURSO_AUTH_TOKEN   / LIBSQL_AUTH_TOKEN
        """
        self.db_path = db_path

        # Detect remote libsql configuration via env vars
        self.libsql_url = os.getenv("TURSO_DATABASE_URL") or os.getenv("LIBSQL_URL")
        self.libsql_token = os.getenv("TURSO_AUTH_TOKEN") or os.getenv("LIBSQL_AUTH_TOKEN")
        self.use_libsql = bool(self.libsql_url)

        self.client = None
        if self.use_libsql:
            if create_client_sync is None:
                raise RuntimeError(
                    "libsql-client is not installed but LIBSQL/TURSO env vars are set. "
                    "Install dependency or unset the env vars."
                )
            # Create libsql client
            self.client = create_client_sync(url=self.libsql_url, auth_token=self.libsql_token)

        self.init_database()
    
    def close(self) -> None:
        """Close any remote DB client resources."""
        try:
            if self.use_libsql and self.client is not None and hasattr(self.client, "close"):
                # ClientSync.close() is synchronous
                self.client.close()  # type: ignore[call-arg]
        except Exception:
            # Best-effort shutdown; avoid masking exit with close errors
            pass
    
    def init_database(self):
        """Initialize the database with schema for either backend"""
        # Read schema using importlib.resources for proper packaging
        try:
            import importlib.resources as pkg_resources
            with pkg_resources.files('docker_diff_pkg').joinpath('schema.sql').open('r') as f:
                schema_content = f.read()
        except (ImportError, AttributeError):
            # Fallback for Python < 3.9
            import importlib_resources as pkg_resources
            with pkg_resources.files('docker_diff_pkg').joinpath('schema.sql').open('r') as f:
                schema_content = f.read()

        self._executescript(schema_content)

    # ---- Internal helpers to abstract DB backend ----
    class _Result:
        def __init__(self, rows: List[Tuple[Any, ...]] | None = None, columns: List[str] | None = None, last_row_id: Optional[int] = None):
            self.rows = rows or []
            self.columns = columns or []
            self.last_row_id = last_row_id

    def _to_tuples(self, rows: Any) -> List[Tuple[Any, ...]]:
        """Normalize libsql rows (list[list|tuple|dict]) to list of tuples."""
        if rows is None:
            return []
        if not rows:
            return []
        first = rows[0]
        if isinstance(first, dict):
            # Keep insertion order of dict; assume all rows share keys order
            return [tuple(r.values()) for r in rows]
        if isinstance(first, (list, tuple)):
            return [tuple(r) for r in rows]
        # Fallback single column scalar rows
        return [(r,) for r in rows]

    def _exec(self, sql: str, params: Tuple[Any, ...] | List[Any] | None = None) -> "DockerImageDB._Result":
        if self.use_libsql:
            res = self.client.execute(sql, params or [])  # type: ignore[attr-defined]
            # libsql ResultSet rows are Row objects; convert to tuples
            rs_rows = getattr(res, "rows", [])
            rows: List[Tuple[Any, ...]] = []
            for r in rs_rows:
                if hasattr(r, "astuple"):
                    rows.append(tuple(r.astuple()))  # type: ignore[attr-defined]
                elif isinstance(r, (list, tuple)):
                    rows.append(tuple(r))
                else:
                    # Sequence fallback
                    try:
                        rows.append(tuple(r))  # type: ignore[arg-type]
                    except Exception:
                        rows.append((r,))
            columns = list(getattr(res, "columns", []) or [])
            last_row_id = getattr(res, "last_insert_rowid", None)
            return DockerImageDB._Result(rows, columns, last_row_id)
        else:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
                columns = [d[0] for d in cur.description] if cur.description else []
                return DockerImageDB._Result(rows, columns, cur.lastrowid)

    def _executemany(self, sql: str, seq_params: List[Tuple[Any, ...]]):
        if not seq_params:
            return
        if self.use_libsql:
            # Execute sequentially for compatibility
            for p in seq_params:
                self.client.execute(sql, list(p))  # type: ignore[attr-defined]
        else:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.executemany(sql, seq_params)

    def _executescript(self, script: str):
        if self.use_libsql:
            # Split naive on ';' and run statements; ignore empty
            statements = [s.strip() for s in script.split(';') if s.strip()]
            for stmt in statements:
                self.client.execute(stmt)  # type: ignore[attr-defined]
        else:
            with sqlite3.connect(self.db_path) as conn:
                conn.executescript(script)
    
    def add_image(self, name: str, digest: str = None, size_bytes: int = None) -> int:
        """Add an image to the database, return image_id"""
        self._exec(
            """
            INSERT OR IGNORE INTO images (name, digest, size_bytes) 
            VALUES (?, ?, ?)
            """,
            (name, digest, size_bytes),
        )
        row = self._exec("SELECT id FROM images WHERE name = ?", (name,)).rows
        return int(row[0][0]) if row else -1
    
    def add_files_for_image(self, image_id: int, files: List[Dict]):
        """Add file listings for an image"""
        # Clear existing files for this image
        self._exec("DELETE FROM files WHERE image_id = ?", (image_id,))

        # Insert new files
        file_data: List[Tuple[Any, ...]] = []
        for file_info in files:
            file_data.append((
                image_id,
                file_info['path'],
                file_info.get('size', 0),
                file_info.get('mode'),
                file_info.get('mtime'),
                file_info.get('type', 'file'),
                file_info.get('checksum')
            ))

        self._executemany(
            """
            INSERT INTO files 
            (image_id, file_path, file_size, file_mode, modified_time, file_type, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            file_data,
        )
    
    def scan_image(self, image_name: str, force: bool = False) -> int:
        """Scan a Docker image and store its files"""
        print(f"Scanning {image_name}...")

        # Helper to parse tag safely: consider last ':' after last '/'
        def _get_tag(name: str) -> str:
            slash_idx = name.rfind('/')
            colon_idx = name.rfind(':')
            if colon_idx > slash_idx:
                return name[colon_idx + 1 :]
            return 'latest'

        # If image already exists with files and tag is not 'latest', skip re-scan
        tag = _get_tag(image_name).lower() if image_name else 'latest'
        if not force and tag != 'latest':
            rows = self._exec(
                """
                SELECT i.id, COUNT(f.id) as file_count
                FROM images i
                LEFT JOIN files f ON i.id = f.image_id
                WHERE i.name = ?
                GROUP BY i.id
                """,
                (image_name,),
            ).rows
            if rows and rows[0][1] and int(rows[0][1]) > 0:
                print(f"  Skipping re-scan for {image_name} (already in DB with files)")
                return int(rows[0][0])
        
        # Add image to database
        image_id = self.add_image(image_name)
        
        # Get file listing using docker run
        try:
            # Stream file listing using Docker SDK if available; fallback to streaming subprocess
            # Clear existing files first (we'll insert incrementally)
            self._exec("DELETE FROM files WHERE image_id = ?", (image_id,))

            insert_sql = """
                INSERT INTO files 
                (image_id, file_path, file_size, file_mode, modified_time, file_type, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """

            batch: List[Tuple[Any, ...]] = []
            batch_size = 500
            inserted = 0
            last_activity = time.time()

            def flush_batch():
                nonlocal batch, inserted
                if batch:
                    self._executemany(insert_sql, batch)
                    inserted += len(batch)
                    batch = []
                    if inserted % 100 == 0:
                        print(f"  Discovered {inserted} files...", flush=True)

            def parse_line(line: str):
                nonlocal last_activity
                parts = line.strip().split("|")
                if len(parts) >= 3:
                    path = parts[0]
                    size = int(parts[1]) if parts[1].isdigit() else 0
                    mtime = int(parts[2]) if parts[2].isdigit() else None
                    batch.append((image_id, path, size, None, mtime, "file", None))
                    last_activity = time.time()
                    if len(batch) >= batch_size:
                        flush_batch()

            cmd = ['find', '/', '-type', 'f', '-exec', 'stat', '-c', '%n|%s|%Y', '{}', ';']

            try:
                try:
                    import docker  # type: ignore
                    client = docker.from_env()
                    # Ensure image is available; pull only if not present
                    try:
                        client.images.get(image_name)
                    except Exception:
                        client.images.pull(image_name)

                    container = client.containers.create(image=image_name, command=cmd, detach=True, oom_kill_disable=True)
                    try:
                        container.start()
                        # Watchdog to detect inactivity/hangs and stop stuck containers
                        inactivity_timeout = 60  # seconds with no output considered stuck
                        watchdog_stop = False

                        def _watchdog():
                            nonlocal watchdog_stop
                            while not watchdog_stop:
                                time.sleep(5)
                                try:
                                    container.reload()
                                    status = getattr(container, "status", "")
                                    if status not in ("created", "running"):
                                        break
                                    if time.time() - last_activity > inactivity_timeout:
                                        try:
                                            container.kill()
                                        except Exception:
                                            pass
                                        break
                                except Exception:
                                    break

                        wd_thread = threading.Thread(target=_watchdog, daemon=True)
                        wd_thread.start()
                        remainder = ""
                        for chunk in container.logs(stream=True, stdout=True, stderr=False, follow=True):
                            if not chunk:
                                continue
                            text = chunk.decode("utf-8", errors="ignore")
                            # Any incoming chunk counts as activity
                            last_activity = time.time()
                            remainder += text
                            while True:
                                nl = remainder.find("\n")
                                if nl == -1:
                                    break
                                line = remainder[:nl]
                                remainder = remainder[nl + 1 :]
                                if line:
                                    parse_line(line)
                        if remainder.strip():
                            parse_line(remainder.strip())
                        flush_batch()
                    finally:
                        try:
                            watchdog_stop = True
                            try:
                                wd_thread.join(timeout=1)
                            except Exception:
                                pass
                            container.remove(force=True)
                        except Exception:
                            pass

                    print(f"  Stored {inserted} files for {image_name}")
                    return image_id

                except Exception as docker_err:
                    # Fallback to streaming via subprocess
                    proc = subprocess.Popen(
                        ['docker', 'run', '--rm', image_name, *cmd],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )
                    assert proc.stdout is not None
                    # Drain stderr concurrently to avoid deadlocks when it fills
                    stderr_lines: List[str] = []

                    def _drain_stderr(stream):
                        nonlocal last_activity
                        try:
                            for eline in stream:  # type: ignore[assignment]
                                stderr_lines.append(eline)
                                last_activity = time.time()
                        except Exception:
                            pass

                    t_err = None
                    if proc.stderr is not None:
                        t_err = threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True)
                        t_err.start()
                    for line in proc.stdout:
                        if line:
                            parse_line(line)
                    # After stdout is consumed, wait with a timeout to avoid hangs
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        raise subprocess.SubprocessError("docker run did not exit in time; killed to prevent hang")
                    finally:
                        try:
                            if t_err is not None:
                                t_err.join(timeout=2)
                        except Exception:
                            pass
                    if proc.returncode != 0:
                        err = "".join(stderr_lines[-50:]).strip()
                        raise subprocess.SubprocessError(err or f"docker run exited with {proc.returncode}")
                    flush_batch()
                    print(f"  Stored {inserted} files for {image_name}")
                    return image_id

            except Exception as e:
                raise subprocess.SubprocessError(str(e))
            
        except subprocess.SubprocessError as e:
            print(f"Error scanning {image_name}: {e}")
            return image_id
    
    def create_comparison(self, name: str, description: str = None) -> int:
        """Create a new comparison session"""
        res = self._exec(
            """
            INSERT INTO comparisons (name, description) 
            VALUES (?, ?)
            """,
            (name, description),
        )
        if self.use_libsql:
            # Fetch id via last row if needed
            row = self._exec("SELECT id FROM comparisons WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).rows
            return int(row[0][0]) if row else -1
        return int(res.last_row_id or -1)
    
    def add_images_to_comparison(self, comparison_id: int, image_ids: List[int]):
        """Add images to a comparison"""
        for image_id in image_ids:
            self._exec(
                """
                INSERT OR IGNORE INTO comparison_images (comparison_id, image_id)
                VALUES (?, ?)
                """,
                (comparison_id, image_id),
            )
    
    def compare_images(self, image_names: List[str], comparison_name: str = None, *, force: bool = False) -> int:
        """Compare multiple images and store results"""
        if not comparison_name:
            comparison_name = f"Comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Scan all images
        image_ids = []
        for image_name in image_names:
            image_id = self.scan_image(image_name, force=force)
            image_ids.append(image_id)
        
        # Create comparison
        comparison_id = self.create_comparison(
            comparison_name, 
            f"Comparing: {', '.join(image_names)}"
        )
        
        # Add images to comparison
        self.add_images_to_comparison(comparison_id, image_ids)
        
        # Generate file differences
        self._generate_file_differences(comparison_id)
        
        return comparison_id
    
    def _generate_file_differences(self, comparison_id: int):
        """Generate file difference records for a comparison"""
        self._exec(
            """
            INSERT INTO file_differences 
            (comparison_id, file_path, difference_type, source_image_id, target_image_id, old_size, new_size, size_change)
            SELECT 
                ? as comparison_id,
                f1.file_path,
                CASE 
                    WHEN f2.file_path IS NULL THEN 'only_in_first'
                    WHEN f1.file_size != f2.file_size THEN 'changed'
                    ELSE 'common'
                END as difference_type,
                f1.image_id as source_image_id,
                f2.image_id as target_image_id,
                f1.file_size as old_size,
                f2.file_size as new_size,
                COALESCE(f2.file_size - f1.file_size, -f1.file_size) as size_change
            FROM files f1
            JOIN comparison_images ci1 ON f1.image_id = ci1.image_id
            LEFT JOIN files f2 ON f1.file_path = f2.file_path 
                AND f2.image_id IN (
                    SELECT ci2.image_id FROM comparison_images ci2 
                    WHERE ci2.comparison_id = ? AND ci2.image_id != f1.image_id
                )
            WHERE ci1.comparison_id = ?
            """,
            (comparison_id, comparison_id, comparison_id),
        )
    
    def get_comparison_summary(self, comparison_id: int) -> Dict:
        """Get summary statistics for a comparison"""
        # Get basic info
        basic_rows = self._exec(
            """
            SELECT c.name, c.description, c.created_at,
                   GROUP_CONCAT(i.name, ', ') as images
            FROM comparisons c
            JOIN comparison_images ci ON c.id = ci.comparison_id
            JOIN images i ON ci.image_id = i.id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (comparison_id,),
        ).rows

        if not basic_rows:
            return {}

        basic_info = basic_rows[0]

        # Get difference counts
        diff_rows = self._exec(
            """
            SELECT difference_type, COUNT(*) as count
            FROM file_differences
            WHERE comparison_id = ?
            GROUP BY difference_type
            """,
            (comparison_id,),
        ).rows

        diff_counts = {k: v for (k, v) in diff_rows}

        return {
            'name': basic_info[0],
            'description': basic_info[1],
            'created_at': basic_info[2],
            'images': basic_info[3].split(', '),
            'differences': diff_counts,
            'total_differences': sum(diff_counts.values()) if diff_counts else 0,
        }
    
    def query_unique_files(self, comparison_id: int) -> List[Tuple]:
        """Get files unique to each image in comparison"""
        rows = self._exec(
            """
            SELECT i.name as image_name, f.file_path, f.file_size
            FROM unique_files uf
            JOIN comparisons c ON uf.comparison_name = c.name
            JOIN images i ON uf.image_name = i.name
            JOIN files f ON i.id = f.image_id AND uf.file_path = f.file_path
            WHERE c.id = ?
            ORDER BY i.name, f.file_size DESC
            """,
            (comparison_id,),
        ).rows
        return rows


def print_comparison_summary(db: DockerImageDB, comparison_id: int):
    """Print detailed comparison summary"""
    summary = db.get_comparison_summary(comparison_id)
    if not summary:
        print(f"No comparison found with ID {comparison_id}")
        return
    
    print(f"\n{'='*60}")
    print(f"COMPARISON SUMMARY")
    print(f"{'='*60}")
    print(f"Name: {summary['name']}")
    print(f"Description: {summary.get('description', 'N/A')}")
    print(f"Created: {summary['created_at']}")
    print(f"Images: {', '.join(summary['images'])}")
    print(f"\nDifference Summary:")
    
    diff_counts = summary.get('differences', {})
    total = summary.get('total_differences', 0)
    
    if total == 0:
        print("  No differences found")
    else:
        for diff_type, count in diff_counts.items():
            percentage = (count / total) * 100
            print(f"  {diff_type.capitalize()}: {count} ({percentage:.1f}%)")
        print(f"  Total: {total}")


def list_comparisons(db: DockerImageDB):
    """List all comparisons in the database"""
    rows = db._exec(
        """
        SELECT 
            c.id,
            c.name,
            c.created_at,
            COUNT(DISTINCT ci.image_id) as image_count,
            GROUP_CONCAT(i.name, ', ') as images
        FROM comparisons c
        JOIN comparison_images ci ON c.id = ci.comparison_id
        JOIN images i ON ci.image_id = i.id
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """,
    ).rows

    if not rows:
        print("No comparisons found in database.")
        return

    print(f"\n{'ID':<4} {'Name':<25} {'Images':<8} {'Date':<20} {'Image Names'}")
    print("-" * 80)

    for comp_id, name, created, img_count, img_names in rows:
        # Truncate long image names
        if img_names and len(img_names) > 35:
            img_names = img_names[:32] + "..."
        print(f"{comp_id:<4} {name[:24]:<25} {img_count:<8} {created[:19]:<20} {img_names}")


def list_images(db: DockerImageDB):
    """List all images in the database"""
    rows = db._exec(
        """
        SELECT 
            i.id,
            i.name,
            i.scanned_at,
            COUNT(f.id) as file_count,
            COALESCE(SUM(f.file_size), 0) as total_size
        FROM images i
        LEFT JOIN files f ON i.id = f.image_id
        GROUP BY i.id
        ORDER BY i.scanned_at DESC
        """,
    ).rows

    if not rows:
        print("No images found in database.")
        return

    print(f"\n{'ID':<4} {'Image Name':<30} {'Files':<8} {'Size (MB)':<12} {'Scanned'}")
    print("-" * 80)

    for img_id, name, scanned, file_count, total_size in rows:
        size_mb = total_size / (1024 * 1024) if total_size else 0
        print(f"{img_id:<4} {name[:29]:<30} {file_count:<8} {size_mb:<12.2f} {scanned[:19] if scanned else 'Never'}")


def show_unique_files(db: DockerImageDB, comparison_id: int, limit: int = 20):
    """Show files unique to each image in a comparison"""
    unique_files = db.query_unique_files(comparison_id)
    
    if not unique_files:
        print(f"No unique files found for comparison {comparison_id}")
        return
    
    print(f"\nUnique Files (showing first {limit}):")
    print(f"{'Image':<25} {'Size (KB)':<12} {'File Path'}")
    print("-" * 80)
    
    for i, (image_name, file_path, file_size) in enumerate(unique_files[:limit]):
        size_kb = file_size / 1024 if file_size else 0
        print(f"{image_name[:24]:<25} {size_kb:<12.2f} {file_path}")


# ---- CLI entry point ----

def _cmd_scan(db: DockerImageDB, args):
    for image in args.images:
        db.scan_image(image, force=args.force)

def _cmd_compare(db: DockerImageDB, args):
    comparison_id = db.compare_images(args.images, args.name, force=args.force)
    print_comparison_summary(db, comparison_id)

def _cmd_list_images(db: DockerImageDB, args):
    list_images(db)

def _cmd_list_comparisons(db: DockerImageDB, args):
    list_comparisons(db)

def _cmd_summary(db: DockerImageDB, args):
    print_comparison_summary(db, args.id)

def _cmd_unique(db: DockerImageDB, args):
    show_unique_files(db, args.id, args.limit)


def main():
    """docker-diff command line interface"""
    import argparse

    parser = argparse.ArgumentParser(prog="docker-diff", description="Docker image file comparison and database manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan one or more images and store file listings")
    p_scan.add_argument("images", nargs="+", help="Docker image names (e.g., ubuntu:22.04)")
    p_scan.add_argument("--force", action="store_true", help="Re-scan even if image exists in DB (overrides skip)")
    p_scan.set_defaults(func=_cmd_scan)

    p_compare = sub.add_parser("compare", help="Compare images and store results")
    p_compare.add_argument("images", nargs="+", help="Docker image names to compare")
    p_compare.add_argument("--name", help="Optional comparison name")
    p_compare.add_argument("--force", action="store_true", help="Re-scan images even if they exist in DB")
    p_compare.set_defaults(func=_cmd_compare)

    p_list = sub.add_parser("list", help="List images or comparisons")
    sub_list = p_list.add_subparsers(dest="what", required=True)

    p_list_images = sub_list.add_parser("images", help="List scanned images")
    p_list_images.set_defaults(func=_cmd_list_images)

    p_list_comparisons = sub_list.add_parser("comparisons", help="List comparisons")
    p_list_comparisons.set_defaults(func=_cmd_list_comparisons)

    p_summary = sub.add_parser("summary", help="Show summary for a comparison")
    p_summary.add_argument("id", type=int, help="Comparison ID")
    p_summary.set_defaults(func=_cmd_summary)

    p_unique = sub.add_parser("unique", help="Show files unique to each image in a comparison")
    p_unique.add_argument("id", type=int, help="Comparison ID")
    p_unique.add_argument("--limit", type=int, default=20, help="Max rows to display")
    p_unique.set_defaults(func=_cmd_unique)

    args = parser.parse_args()
    db = DockerImageDB()
    try:
        args.func(db, args)
    finally:
        db.close()


if __name__ == "__main__":
    main()
