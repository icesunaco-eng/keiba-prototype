"""通信・子プロセスを、システムコールの前に監査フックで拒否して記録する。対象コードではない（試験の道具）。"""
import sys, os, runpy, atexit, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
attempts = []
def hook(event, args):
    if event.startswith("socket.") and event != "socket.__new__" or event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "pty.spawn", "urllib.Request"):
        attempts.append({"event": event, "target": repr(args)[:200]})
        raise PermissionError("network/child execution denied: " + event)
sys.addaudithook(hook)
@atexit.register
def record():
    (ROOT / "logs" / os.environ.get("AUDIT_NAME", "audit.json")).write_text(json.dumps({"argv": sys.argv, "attempts": attempts}, ensure_ascii=False))
script = Path(sys.argv[1]).resolve(); sys.argv = [str(script)] + sys.argv[2:]
runpy.run_path(str(script), run_name="__main__")
