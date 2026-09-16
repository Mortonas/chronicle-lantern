import subprocess
from typing import List

def search_files(pattern: str, path: str) -> List[str]:
    """Run ripgrep (rg.exe) to search for a pattern in the given path."""
    try:
        result = subprocess.run([
            "rg.exe", pattern, path, "--files-with-matches"
        ], capture_output=True, text=True, check=True)
        return result.stdout.strip().splitlines()
    except subprocess.CalledProcessError as e:
        return []
