import logging
import re
import random
from pathlib import Path
from typing import Dict, List

class PlaceholderResolver:
    pattern = re.compile(r"\{([A-Za-z0-9_\-:]+)\}")

    def __init__(self, folder: Path, rotation: str = "sequential") -> None:
        self.folder = folder
        self.rotation = rotation.lower()
        self.values: Dict[str, List[str]] = {}
        self.indexes: Dict[str, int] = {}
        folder.mkdir(parents=True, exist_ok=True)
        if self.rotation not in {"sequential", "random"}:
            logging.warning(
                "Unknown placeholder rotation '%s', falling back to 'sequential'",
                rotation,
            )
            self.rotation = "sequential"

    def _path_for(self, name: str) -> Path:
        direct = self.folder / name
        with_txt = self.folder / f"{name}.txt"
        if direct.exists():
            return direct
        if with_txt.exists():
            return with_txt
        return direct

    def _ensure_loaded(self, name: str) -> None:
        if name in self.values:
            return
        
        # Skip loading for known dynamic patterns
        if name == "uuid" or name == "timestamp" or name.startswith("random_int:"):
            return

        path = self._path_for(name)
        if not path.exists():
            raise ValueError(f"Placeholder '{name}' not found (expected {path} or {path}.txt)")
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not lines:
            raise ValueError(f"Placeholder '{name}' has no values in {path}")
        self.values[name] = lines
        self.indexes.setdefault(name, 0)

    def _next_value(self, name: str) -> str:
        # Built-in dynamic placeholders
        if name == "uuid":
            import uuid
            return str(uuid.uuid4())
        if name == "timestamp":
            import time
            return str(int(time.time()))
        if name.startswith("random_int"):
            parts = name.split(":")
            if len(parts) == 3:
                try:
                    low, high = int(parts[1]), int(parts[2])
                    return str(random.randint(low, high))
                except ValueError:
                    pass # Fallback to file lookup if parsing fails

        self._ensure_loaded(name)
        vals = self.values[name]
        if self.rotation == "random":
            return random.choice(vals)
        idx = self.indexes.get(name, 0) % len(vals)
        self.indexes[name] = (idx + 1) % len(vals)
        return vals[idx]

    def replace(self, text: str) -> str:
        names = set(self.pattern.findall(text))
        if not names:
            return text
        replacements = {name: self._next_value(name) for name in names}
        return self.pattern.sub(lambda m: replacements[m.group(1)], text)
