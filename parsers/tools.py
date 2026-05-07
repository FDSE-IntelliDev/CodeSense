import re
from typing import Optional, Tuple

def get_function_position(filepath: str, function_name: str) -> Optional[Tuple[int, int]]:
    """
    Finds the 0-based line and character (column) of a function definition in a file.
    This uses basic regex and is meant as a lightweight tool for LSP clients
    across different programming languages.

    Returns:
        (line, character) as integers if found, else None
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

    # Simplistic regex to find a method definition-like signature:
    # matches exact function_name followed by optional space and '('
    pattern = re.compile(r'\b' + re.escape(function_name) + r'\s*\(')

    for line_idx, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            # We want the character index of the start of the function_name
            char_idx = match.start()
            return line_idx, char_idx

    # Fallback: just find the exact word if the above fails
    # Useful for properties or variables as well
    word_pattern = re.compile(r'\b' + re.escape(function_name) + r'\b')
    for line_idx, line in enumerate(lines):
        match = word_pattern.search(line)
        if match:
            return line_idx, match.start()

    return None
