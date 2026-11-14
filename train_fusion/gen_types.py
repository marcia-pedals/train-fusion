"""`just types`: write the explorer's TypeScript types (web/packages/types/tables.ts, not
checked in) from every stage's schemas.py."""
from train_fusion.common import paths
from train_fusion.common.schema_dsl import Table, typescript
from train_fusion.stages.decode import schemas as decode
from train_fusion.stages.linearize import schemas as linearize
from train_fusion.stages.reference import schemas as reference
from train_fusion.stages.static import schemas as static
from train_fusion.stages.timetable import schemas as timetable
from train_fusion.stages.tu_checks import schemas as tu_checks


def all_tables() -> list[Table]:
    """Every table the pipeline writes, in stage order."""
    return [t for m in (static, decode, tu_checks, linearize, timetable, reference)
            for t in vars(m).values() if isinstance(t, Table)]


if __name__ == "__main__":
    ts = paths.ROOT / "web" / "packages" / "types" / "tables.ts"
    ts.parent.mkdir(parents=True, exist_ok=True)
    ts.write_text(typescript(all_tables()))
    print(f"wrote {ts.relative_to(paths.ROOT)}")
