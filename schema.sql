-- SQLite schema for Docker image file comparison results
-- This database stores file listings and comparison results across multiple images

-- Table to store unique images being analyzed
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,           -- e.g., 'ubuntu:20.04'
    digest TEXT,                         -- SHA256 digest if available
    size_bytes INTEGER,                  -- Total image size
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    scanned_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Table to store files found in each image
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,             -- Full path: /usr/bin/curl
    file_size INTEGER,                   -- Size in bytes
    file_mode TEXT,                      -- File permissions (optional)
    modified_time INTEGER,              -- Unix timestamp
    file_type TEXT DEFAULT 'file',      -- 'file', 'directory', 'symlink'
    checksum TEXT,                       -- MD5/SHA256 if calculated
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    UNIQUE(image_id, file_path)
);

-- Table to store comparison sessions
CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,                           -- Optional comparison name
    description TEXT,                    -- Optional description
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Junction table linking images to comparisons
CREATE TABLE IF NOT EXISTS comparison_images (
    comparison_id INTEGER NOT NULL,
    image_id INTEGER NOT NULL,
    FOREIGN KEY (comparison_id) REFERENCES comparisons(id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    PRIMARY KEY (comparison_id, image_id)
);

-- Table to store file differences between images
CREATE TABLE IF NOT EXISTS file_differences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    difference_type TEXT NOT NULL,       -- 'added', 'removed', 'modified', 'common'
    source_image_id INTEGER,             -- Image where file exists/existed
    target_image_id INTEGER,             -- Image being compared to
    old_size INTEGER,                    -- Previous size (for modified files)
    new_size INTEGER,                    -- New size (for modified files)
    size_change INTEGER,                 -- Calculated: new_size - old_size
    FOREIGN KEY (comparison_id) REFERENCES comparisons(id) ON DELETE CASCADE,
    FOREIGN KEY (source_image_id) REFERENCES images(id),
    FOREIGN KEY (target_image_id) REFERENCES images(id)
);

-- Table for summary statistics per comparison
CREATE TABLE IF NOT EXISTS comparison_stats (
    comparison_id INTEGER PRIMARY KEY,
    total_files_compared INTEGER,
    files_added INTEGER,
    files_removed INTEGER,
    files_modified INTEGER,
    files_common INTEGER,
    total_size_change INTEGER,           -- Net size change in bytes
    largest_file_added TEXT,             -- Path to largest added file
    largest_file_removed TEXT,           -- Path to largest removed file
    FOREIGN KEY (comparison_id) REFERENCES comparisons(id) ON DELETE CASCADE
);

-- Indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_files_image_id ON files(image_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(file_size);
CREATE INDEX IF NOT EXISTS idx_differences_comparison ON file_differences(comparison_id);
CREATE INDEX IF NOT EXISTS idx_differences_type ON file_differences(difference_type);
CREATE INDEX IF NOT EXISTS idx_differences_path ON file_differences(file_path);

-- Views for common queries

-- View: Files unique to each image in a comparison
CREATE VIEW IF NOT EXISTS unique_files AS
SELECT 
    c.name as comparison_name,
    i.name as image_name,
    f.file_path,
    f.file_size,
    'unique' as status
FROM files f
JOIN images i ON f.image_id = i.id
JOIN comparison_images ci ON i.id = ci.image_id
JOIN comparisons c ON ci.comparison_id = c.id
WHERE f.file_path NOT IN (
    SELECT DISTINCT f2.file_path 
    FROM files f2 
    JOIN comparison_images ci2 ON f2.image_id = ci2.image_id 
    WHERE ci2.comparison_id = ci.comparison_id 
    AND f2.image_id != f.image_id
);

-- View: Files common across all images in a comparison
CREATE VIEW IF NOT EXISTS common_files AS
SELECT 
    c.name as comparison_name,
    f.file_path,
    COUNT(DISTINCT f.image_id) as image_count,
    MIN(f.file_size) as min_size,
    MAX(f.file_size) as max_size,
    AVG(f.file_size) as avg_size
FROM files f
JOIN images i ON f.image_id = i.id
JOIN comparison_images ci ON i.id = ci.image_id
JOIN comparisons c ON ci.comparison_id = c.id
GROUP BY c.id, f.file_path
HAVING COUNT(DISTINCT f.image_id) = (
    SELECT COUNT(*) 
    FROM comparison_images ci2 
    WHERE ci2.comparison_id = c.id
);

-- View: Size comparison between images
CREATE VIEW IF NOT EXISTS image_sizes AS
SELECT 
    c.name as comparison_name,
    i.name as image_name,
    COUNT(f.id) as file_count,
    SUM(f.file_size) as total_file_size,
    AVG(f.file_size) as avg_file_size,
    MAX(f.file_size) as largest_file_size
FROM images i
JOIN files f ON i.id = f.image_id
JOIN comparison_images ci ON i.id = ci.image_id
JOIN comparisons c ON ci.comparison_id = c.id
GROUP BY c.id, i.id;