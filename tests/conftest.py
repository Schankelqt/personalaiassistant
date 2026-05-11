import sys
from pathlib import Path

# Пакет `personal_ai_os` лежит в родительском каталоге этого файла.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
