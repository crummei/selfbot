import os
import sys

sys.dont_write_bytecode = True

# 1. SRC_DIR: The folder where this file (paths.py) is located.
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. ROOT_DIR: One level up from 'src'.
ROOT_DIR = os.path.dirname(SRC_DIR)

# 3. DATA_DIR: The path to your new 'data' folder.
DATA_DIR = os.path.join(SRC_DIR, "data")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    print(f"Created new data directory at: {DATA_DIR}")
