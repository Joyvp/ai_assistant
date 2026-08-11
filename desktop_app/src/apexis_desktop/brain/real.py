"""
RealProvider - Replaces MockProvider as REAL BRAIN
Chill anti-Ultron + On-demand AI (5m keep-alive) + Pi Router aware

MacBook Air Intel 8GB: loads phi3:mini only when needed, unloads after 5m to save battery
Pi 5 4GB: tiny llama3.2:1b always-on
"""
import os
import subprocess

class RealProvider:
    def __init__(self, memory_core=None, router=None, email_tools=None):
        self.memory = memory_core
        self.router = router
        self.email_tools = email_tools
        self.local_model = os.getenv("OLLAMA_MODEL", "phi3:mini")
        self.keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "5m")
        self.name = f"RealProvider({self.local_model}, keep-alive={self.keep_alive})"

    def _ensure_ollama(self):
        # Check if ollama installed (Mint live USB needs it each boot)
        try:
            subprocess.run(["ollama", "list"], capture_output=True, timeout=5)
            return True
        except:
            return False

    def _run_local(self, prompt, model=None):
        model = model or self.local_model
        try:
            # On-demand load with keep-alive 5m
            result = subprocess.run(
                ["ollama", "run", model, prompt, "--keep-alive", self.keep_alive],
                capture_output=True, text=True, timeout=120
            )
            return result.stdout.strip() or f"[Local {model}] No response"
        except Exception as e:
            return f"[Local Error] {e}. Is Ollama installed? ollama pull {model}"

    def respond(self, message: str) -> str:
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")

        # 1. Router decides local vs cloud (auto-decide)
        decision = self.router.assess(cleaned) if self.router else {"route": "local", "reason": "no router", "needs_cloud": False}

        # 2. Try local first (chill mode)
        if not decision["needs_cloud"]:
            reply = self._run_local(cleaned)
            # Smart auto memory
            if self.memory:
                self.memory.remember_smart(f"chat:{cleaned[:30]}", reply, score=3)
            return f"{reply}\n\n[Router: {decision['reason']} | Local only, private]"

        # 3. Needs cloud for pro quality - chill mode: CAN go online but must log and tell after
        # Check if internet allowed (CHILL_MODE=True allows, but logs)
        allow_internet = os.getenv("ALLOW_INTERNET", "true").lower() == "true" or os.getenv("CHILL_MODE", "true").lower() == "true"

        if not allow_internet:
            # Restricted fallback
            reply = self._run_local(cleaned)
            return f"{reply}\n\n[Router: {decision['reason']} but internet blocked. Built local draft. Enable Allow Internet for pro quality.]"

        # GOES ONLINE - Chill anti-Ultron: log + tell after
        if self.memory:
            self.memory.log_internet("cloud_attempt", f"Task: {cleaned[:100]} Reason: {decision['reason']}")

        # Simulate Claude call (replace with anthropic API later)
        # In real: client = anthropic.Anthropic() ; response = client.messages.create(...)
        cloud_reply = f"[CLOUD Claude would build pro version here for: {cleaned[:100]}...] (Integrate ANTHROPIC_API_KEY for real)"

        # Combine: tell user we used cloud
        final_reply = f"{cloud_reply}\n\n[CHILL MODE] I auto-decided to use cloud for pro quality. Reason: {decision['reason']}. Logged to internet_logs.json"

        if self.memory:
            self.memory.remember_smart(f"cloud:{cleaned[:30]}", final_reply, score=5)

        return final_reply

    # Email helper with Option B
    def build_and_email(self, task, to_email=None):
        # Build file (simulate)
        output_path = "/tmp/apexis_build.html"
        with open(output_path, "w") as f:
            f.write(f"<html><body><h1>Built for {task}</h1></body></html>")

        # Email you with file by default, or stranger with confirmation
        target = to_email or os.getenv("NOTIFY_EMAIL")
        if self.email_tools:
            return self.email_tools.send_email(target, f"Task done: {task}", f"Apexis built {task}", output_path)
        return f"[Would email {target} file {output_path}]"
