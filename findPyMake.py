#!/usr/bin/env python3
#
"""
Used in launch.json to back out of a source subfolder until pyMake.xml is found.

    {
        "type": "shell",
        "label": "Compile active file",
        "command": "cd $(./findPyMake.py ${fileDirname}) ; pyMake.py -c -o ${fileBasename}",
        "options": {
            "cwd": "${workspaceFolder}"
        },
        "problemMatcher": [],
        "group": {
            "kind": "build",
            "isDefault": false
        }
    },

"""
import os
import sys

def find_directory_with_file(start_dir, filename):
    current_dir = start_dir

    while True:
        if os.path.isfile(os.path.join(current_dir, filename)):
            return current_dir
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            return None
        current_dir = parent_dir

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python findPyMake.py <start_directory>")
        sys.exit(1)

    start_directory = sys.argv[1]
    result_dir = find_directory_with_file(start_directory, 'pyMake.xml')
    
    if result_dir:
        print(result_dir)
    else:
        print("pyMake.xml not found in any parent directories.")
        sys.exit(1)