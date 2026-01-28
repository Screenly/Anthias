import os
import subprocess
from pathlib import Path


# Getting the local machine IP address
def get_ip():
    try:
        result = subprocess.run(
            ["ip", "-4", "route", "get", "8.8.8.8"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.split()[6].strip()
    except Exception as e:
        print(f"Error getting IP address: {e}")
        return "0.0.0.0"

MY_IP = get_ip()
os.environ["MY_IP"] = MY_IP


# Getting total system memory in KB
def get_total_memory_kb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return int(line.split()[1])
    except Exception as e:
        print(f"Error reading memory info: {e}")
        return 0

TOTAL_MEMORY_KB = get_total_memory_kb()


# Calculating memory limits
VIEWER_MEMORY_LIMIT_KB = int(TOTAL_MEMORY_KB * 0.8)
SHM_SIZE_KB = int(TOTAL_MEMORY_KB * 0.3)

# Export to environment variables
os.environ["VIEWER_MEMORY_LIMIT_KB"] = str(VIEWER_MEMORY_LIMIT_KB)
os.environ["SHM_SIZE_KB"] = str(SHM_SIZE_KB)
