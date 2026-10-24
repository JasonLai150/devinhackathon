"""Skill-note memory store.

Kickoff decision: memory is a single markdown doc injected into the
agent's context — simplest rejection-sampling format, and demo-friendly
(you can read the diff between rounds).
"""
import hashlib
import os


class MemoryStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        if self.path and os.path.exists(self.path):
            with open(self.path) as f:
                return f.read()
        return ""

    def save(self, text):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            f.write(text)

    def hash(self):
        return hashlib.sha1(self.load().encode()).hexdigest()[:8]
