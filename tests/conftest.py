import sys
from pathlib import Path

# o servidor e os processos de comparação (spawn) precisam enxergar pdf_diff e webapp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
