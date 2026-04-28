"""One-shot: write a key to the ~/.willow vault. Run once, then delete."""
import sqlite3, sys
from pathlib import Path
from cryptography.fernet import Fernet

key_path   = Path.home() / ".willow" / ".master.key"
vault_path = Path.home() / ".willow" / "vault.db"

name  = sys.argv[1] if len(sys.argv) > 1 else "CEREBRAS_API_KEY"
value = sys.argv[2] if len(sys.argv) > 2 else input(f"Value for {name}: ").strip()

f   = Fernet(key_path.read_bytes().strip())
enc = f.encrypt(value.encode())

conn = sqlite3.connect(str(vault_path))
conn.execute(
    "INSERT OR REPLACE INTO credentials (name, env_key, value_enc) VALUES (?,?,?)",
    (name, name, enc),
)
conn.commit()
conn.close()
print(f"Wrote {name} to vault.")
