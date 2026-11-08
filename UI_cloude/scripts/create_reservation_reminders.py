from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from notifications import create_reservation_reminders  # noqa: E402


if __name__ == "__main__":
    count = create_reservation_reminders(PROJECT_ROOT / "database" / "database.db")
    print(f"前日リマインドを{count}件作成しました。")
