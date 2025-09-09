#!/usr/bin/env python3
"""
Docker Image Comparison Database Manager
Provides functions to store and query Docker image file comparison results in SQLite
"""

import sqlite3
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

class DockerImageDB:
    def __init__(self, db_path: str = "docker_images.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with schema"""
        with sqlite3.connect(self.db_path) as conn:
            # Read and execute schema
            schema_path = Path(__file__).parent / "schema.sql"
            with open(schema_path) as f:
                conn.executescript(f.read())
    
    def add_image(self, name: str, digest: str = None, size_bytes: int = None) -> int:
        """Add an image to the database, return image_id"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO images (name, digest, size_bytes) 
                VALUES (?, ?, ?)
            """, (name, digest, size_bytes))
            
            # Get the image ID
            cursor.execute("SELECT id FROM images WHERE name = ?", (name,))
            return cursor.fetchone()[0]
    
    def add_files_for_image(self, image_id: int, files: List[Dict]):
        """Add file listings for an image"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Clear existing files for this image
            cursor.execute("DELETE FROM files WHERE image_id = ?", (image_id,))
            
            # Insert new files
            file_data = []
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
            
            cursor.executemany("""
                INSERT INTO files 
                (image_id, file_path, file_size, file_mode, modified_time, file_type, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, file_data)
    
    def scan_image(self, image_name: str) -> int:
        """Scan a Docker image and store its files"""
        print(f"Scanning {image_name}...")
        
        # Add image to database
        image_id = self.add_image(image_name)
        
        # Get file listing using docker run
        try:
            result = subprocess.run([
                'docker', 'run', '--rm', image_name, 
                'find', '/', '-type', 'f', '-exec', 'stat', '-c', '%n|%s|%Y', '{}', ';'
            ], capture_output=True, text=True)
            
            files = []
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    parts = line.split('|')
                    if len(parts) >= 3:
                        files.append({
                            'path': parts[0],
                            'size': int(parts[1]) if parts[1].isdigit() else 0,
                            'mtime': int(parts[2]) if parts[2].isdigit() else None
                        })
            
            self.add_files_for_image(image_id, files)
            print(f"  Stored {len(files)} files for {image_name}")
            return image_id
            
        except subprocess.SubprocessError as e:
            print(f"Error scanning {image_name}: {e}")
            return image_id
    
    def create_comparison(self, name: str, description: str = None) -> int:
        """Create a new comparison session"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO comparisons (name, description) 
                VALUES (?, ?)
            """, (name, description))
            return cursor.lastrowid
    
    def add_images_to_comparison(self, comparison_id: int, image_ids: List[int]):
        """Add images to a comparison"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for image_id in image_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO comparison_images (comparison_id, image_id)
                    VALUES (?, ?)
                """, (comparison_id, image_id))
    
    def compare_images(self, image_names: List[str], comparison_name: str = None) -> int:
        """Compare multiple images and store results"""
        if not comparison_name:
            comparison_name = f"Comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Scan all images
        image_ids = []
        for image_name in image_names:
            image_id = self.scan_image(image_name)
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
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
            """, (comparison_id, comparison_id, comparison_id))
    
    def get_comparison_summary(self, comparison_id: int) -> Dict:
        """Get summary statistics for a comparison"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get basic info
            cursor.execute("""
                SELECT c.name, c.description, c.created_at,
                       GROUP_CONCAT(i.name, ', ') as images
                FROM comparisons c
                JOIN comparison_images ci ON c.id = ci.comparison_id
                JOIN images i ON ci.image_id = i.id
                WHERE c.id = ?
                GROUP BY c.id
            """, (comparison_id,))
            
            basic_info = cursor.fetchone()
            if not basic_info:
                return {}
            
            # Get difference counts
            cursor.execute("""
                SELECT difference_type, COUNT(*) as count
                FROM file_differences
                WHERE comparison_id = ?
                GROUP BY difference_type
            """, (comparison_id,))
            
            diff_counts = dict(cursor.fetchall())
            
            return {
                'name': basic_info[0],
                'description': basic_info[1],
                'created_at': basic_info[2],
                'images': basic_info[3].split(', '),
                'differences': diff_counts,
                'total_differences': sum(diff_counts.values())
            }
    
    def query_unique_files(self, comparison_id: int) -> List[Tuple]:
        """Get files unique to each image in comparison"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT i.name as image_name, f.file_path, f.file_size
                FROM unique_files uf
                JOIN comparisons c ON uf.comparison_name = c.name
                JOIN images i ON uf.image_name = i.name
                JOIN files f ON i.id = f.image_id AND uf.file_path = f.file_path
                WHERE c.id = ?
                ORDER BY i.name, f.file_size DESC
            """, (comparison_id,))
            return cursor.fetchall()


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
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
        """)
        
        comparisons = cursor.fetchall()
        
        if not comparisons:
            print("No comparisons found in database.")
            return
        
        print(f"\n{'ID':<4} {'Name':<25} {'Images':<8} {'Date':<20} {'Image Names'}")
        print("-" * 80)
        
        for comp_id, name, created, img_count, img_names in comparisons:
            # Truncate long image names
            if len(img_names) > 35:
                img_names = img_names[:32] + "..."
            print(f"{comp_id:<4} {name[:24]:<25} {img_count:<8} {created[:19]:<20} {img_names}")


def list_images(db: DockerImageDB):
    """List all images in the database"""
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
        """)
        
        images = cursor.fetchall()
        
        if not images:
            print("No images found in database.")
            return
        
        print(f"\n{'ID':<4} {'Image Name':<30} {'Files':<8} {'Size (MB)':<12} {'Scanned'}")
        print("-" * 80)
        
        for img_id, name, scanned, file_count, total_size in images:
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