# Data loader: built dates, the reference's fitted parameters and headline numbers.
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from train_fusion import explorer_data  # noqa: E402

json.dump(explorer_data.meta(), sys.stdout)
