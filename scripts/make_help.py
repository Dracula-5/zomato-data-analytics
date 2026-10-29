"""Print Makefile targets that carry a `## description` comment (used by `make help`)."""

import re
import sys
from pathlib import Path

TARGET = re.compile(r"^([a-zA-Z0-9_-]+):.*?## (.+)$")

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if match := TARGET.match(line):
        print(f"  {match.group(1):<14} {match.group(2)}")
