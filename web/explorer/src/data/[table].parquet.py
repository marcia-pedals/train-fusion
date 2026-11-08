# Data loader for every data/<table>.parquet: train_fusion.explorer_data.TABLES[<table>]()
# as Parquet on stdout. Observable Framework passes the file name as --table=<table>.
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from train_fusion import explorer_data  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--table", required=True, choices=sorted(explorer_data.TABLES))
explorer_data.TABLES[ap.parse_args().table]().write_parquet(sys.stdout.buffer)
