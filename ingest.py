"""Fetch new data from all enabled sources.  Run from this folder:  python ingest.py
Options:  --source NAME  (one source)   --reextract  (redo facts for stored items)"""
from portal.ingest import main

if __name__ == "__main__":
    main()
