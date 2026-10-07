Machine: macOS-26.6.2-arm64-arm-64bit-Mach-O / Python 3.13.5 / pandas 3.0.6

| Source | Rows | Load into pandas + aggregate (s) | Pushdown (s) | Speedup |
|---|---:|---:|---:|---:|
| DuckDB on parquet | 1,000,000 | 1.02 | 0.199 | 5x |
| SQL on SQLite | 1,000,000 | 4.48 | 0.648 | 7x |
| DuckDB on parquet | 10,000,000 | 11.91 | 0.569 | 21x |
