"""
Panel web local sin el escáner (por ejemplo para revisar resultados):

    python dashboard.py            # http://localhost:8765
"""

import sys
import webbrowser

from surebet.config import Settings
from surebet.dashboard_server import serve

if __name__ == "__main__":
    s = Settings()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else s.dashboard_port
    print(f"Panel en http://localhost:{port}  (Ctrl+C para cerrar)")
    webbrowser.open(f"http://localhost:{port}")
    try:
        serve(s.db_path, s.db_path.parent / "status.json", port, background=False)
    except KeyboardInterrupt:
        pass
