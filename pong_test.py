from app.providers import ClaudeProvider
import subprocess
cmd = ClaudeProvider().build_command("reply with only the word PONG")
cmd = [c if c != "json" else "text" for c in cmd]
print("CMD:", cmd)
r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
print("rc", r.returncode)
print("stdout", (r.stdout or "")[:200])
print("stderr", (r.stderr or "")[:200])
