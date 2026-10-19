"""
Guarda la clave privada de la wallet del bot en el .env del VPS SIN mostrarla. La corre el usuario en el VPS:

  cd /opt/apps/surebet3 && .venv/bin/python tools/set_azuro_key.py

  - la pide oculta (no se ve al pegar);
  - limpia lo que algunas terminales agregan al pegar (marcas de "pegado", saltos de línea, espacios);
  - comprueba que sea una clave de 64 caracteres y muestra SOLO la dirección de la wallet para confirmarla;
  - reemplaza la línea AZURO_PRIVATE_KEY del .env (nunca la duplica) y deja el archivo solo para root.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"


def clean(raw: str) -> str:
    k = re.sub(r"\x1b\[20[01]~", "", raw)          # marcas de pegado de la terminal (bracketed paste)
    k = re.sub(r"[\s\x00-\x1f\x7f]", "", k)
    return k[2:] if k.lower().startswith("0x") else k


def main() -> None:
    k = clean(getpass.getpass("Pega la clave privada (no se ve) y Enter: "))
    if not re.fullmatch(r"[0-9a-fA-F]{64}", k):
        print(f"Eso no es una clave privada: tiene {len(k)} caracteres y una clave tiene 64. No guardé nada.")
        print("Pégala UNA sola vez (clic derecho una vez) y Enter.")
        sys.exit(1)
    from eth_account import Account
    address = Account.from_key("0x" + k).address
    print(f"La clave es de la wallet: {address}")
    if input("¿Es la wallet del bot? (s/n): ").strip().lower() not in ("s", "si", "sí"):
        print("No guardé nada.")
        sys.exit(1)
    lines = [l for l in ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("AZURO_PRIVATE_KEY=")]
    lines.append("AZURO_PRIVATE_KEY=0x" + k)
    tmp = ENV.with_name(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV)
    print("GUARDADA. Avísale a Claude: listo.")


if __name__ == "__main__":
    main()
