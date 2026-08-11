"""
MemoryCore - Replaces MockBrain as long-term memory (smart auto + trust+log)
Lives on hard drive: /media/mint/APEXIS-DATA/projects/APEXIS/ memory
"""
import json, os
from datetime import datetime

class MemoryCore:
    def __init__(self, storage_path="/media/mint/APEXIS-DATA/memory"):
        # Fallback for dev / Pi
        if not os.path.exists("/media/mint"):
            storage_path = "./memory"
            if not os.path.exists("/mnt/harddrive"):
                storage_path = "./memory"
            else:
                storage_path = "/mnt/harddrive/apexis/memory"

        self.storage_path = storage_path
        os.makedirs(storage_path, exist_ok=True)
        self.memory_file = os.path.join(storage_path, "memory.json")
        self.logs_file = os.path.join(storage_path, "internet_logs.json")
        self.permissions_file = os.path.join(storage_path, "permissions.json")

        self.memory = self._load(self.memory_file)
        self.logs = self._load(self.logs_file)
        self.permissions = self._load(self.permissions_file)

    def _load(self, p):
        if os.path.exists(p):
            try:
                with open(p, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def _save(self, data, path):
        with open(path, 'w') as f: json.dump(data, f, indent=2)

    # Smart auto: score >=3 = important, but you can delete anytime
    def remember_smart(self, key, value, score=5):
        if score >= 3:
            self.memory[key] = {"value": value, "score": score, "ts": datetime.now().isoformat()}
            self._save(self.memory, self.memory_file)

    def forget(self, key):
        if key in self.memory:
            del self.memory[key]
            self._save(self.memory, self.memory_file)

    # Trust but log - anti-Ultron: always log internet
    def log_internet(self, action, details=None):
        if "internet" not in self.logs: self.logs["internet"] = []
        self.logs["internet"].append({
            "action": action,
            "details": details,
            "ts": datetime.now().isoformat()
        })
        self._save(self.logs, self.logs_file)

    # File permission: ask once, remember always
    def check_file_allowed(self, path):
        allowed = self.permissions.get("allowed_folders", [])
        return any(path.startswith(a) for a in allowed)

    def grant_file_allowed(self, path):
        if "allowed_folders" not in self.permissions: self.permissions["allowed_folders"] = []
        if path not in self.permissions["allowed_folders"]:
            self.permissions["allowed_folders"].append(path)
            self._save(self.permissions, self.permissions_file)
