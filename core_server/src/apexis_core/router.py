"""
Apexis Router - Auto-decide local vs cloud (Chill but anti-Ultron)
For Pi 5 4GB Router @ 260 Mbps + MacBook Intel 8GB
"""

class ModelRouter:
    def __init__(self, local_model="phi3:mini"):
        self.local_model = local_model
        self.heavy_keywords = [
            "company website", "polished", "investors", "production",
            "e-commerce", "react", "next.js", "high quality"
        ]
        self.light_keywords = [
            "simple", "draft", "quick", "list", "summarize", "organize"
        ]

    def assess(self, task: str) -> dict:
        t = task.lower()
        heavy = sum(1 for kw in self.heavy_keywords if kw in t)
        light = sum(1 for kw in self.light_keywords if kw in t)

        # Very long website = heavy
        if len(t) > 250 and "website" in t:
            heavy += 2

        if heavy > light:
            return {
                "route": "cloud",
                "reason": f"Task heavy ({heavy} heavy signals) - local {self.local_model} draft ok but pro quality needs Claude",
                "needs_cloud": True
            }
        else:
            return {
                "route": "local",
                "reason": f"Task light ({light} signals) - doable 100% locally, private",
                "needs_cloud": False
            }
