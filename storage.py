"""Small JSON repository for the local demo. Set PAISALEDGER_DATA for isolated runs."""
import json
import os
import tempfile
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "data" / "demo_data.json"


class StorageError(RuntimeError):
    """A storage file was unreadable, malformed, or could not be safely saved."""


def _to_json_number(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Cannot write {type(value).__name__} to the JSON ledger.")


class JSONStorage:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("PAISALEDGER_DATA", DEFAULT_PATH))

    def load(self):
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            # A new local demo starts empty; an existing but unreadable file is an error.
            return {"transactions": [], "budgets": []}
        except json.JSONDecodeError as exc:
            raise StorageError(
                f"The ledger JSON is malformed at line {exc.lineno}, column {exc.colno}. "
                "The original file was left untouched."
            ) from exc
        except UnicodeDecodeError as exc:
            raise StorageError("The ledger file is not valid UTF-8. The original file was left untouched.") from exc
        except OSError as exc:
            raise StorageError(f"The ledger file could not be read: {exc.strerror or exc}.") from exc

        if (not isinstance(data, dict) or not isinstance(data.get("transactions"), list)
                or not isinstance(data.get("budgets", []), list)):
            raise StorageError(
                "The ledger JSON must contain transaction and budget arrays. The original file was left untouched."
            )
        if any(not isinstance(item, dict) for item in data["transactions"]):
            raise StorageError("A stored transaction is not a JSON object. The original file was left untouched.")
        if any(not isinstance(item, dict) for item in data.get("budgets", [])):
            raise StorageError("A stored budget is not a JSON object. The original file was left untouched.")
        data.setdefault("budgets", [])
        return data

    def save(self, data):
        """Write beside the original and replace it only after JSON is fully written."""
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as file:
                temporary_path = Path(file.name)
                json.dump(data, file, indent=2, ensure_ascii=False, default=_to_json_number, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            reason = str(exc) or "invalid JSON data"
            raise StorageError(f"The ledger could not be saved ({reason}); the previous file was preserved.") from exc

