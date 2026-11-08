import polars as pl
import pytest

from train_fusion.common.schema_dsl import SchemaError, Table, col, typescript
from train_fusion.gen_types import all_tables

T = Table("t", "test", [col("a", "str", "a"), col("b", "f64?", "b", "m"), col("k", "enum", "k", values=["x", "y"])])


def test_conform_orders_and_casts():
    df = pl.DataFrame({"k": ["x"], "b": [1], "a": ["q"]})
    out = T.conform(df)
    assert out.columns == ["a", "b", "k"] and out["b"].dtype == pl.Float64


def test_conform_rejects_bad_enum_and_nulls():
    with pytest.raises(SchemaError):
        T.conform(pl.DataFrame({"a": ["q"], "b": [1.0], "k": ["z"]}))
    with pytest.raises(SchemaError):
        T.conform(pl.DataFrame({"a": [None], "b": [1.0], "k": ["x"]}, schema_overrides={"a": pl.String}))


def test_typescript_covers_every_stage():
    tables = all_tables()
    assert {"trips", "vp", "vp_lin", "timetable", "ref_pairs"} <= {t.name for t in tables}
    ts = typescript(tables)
    assert "export interface VpLin {" in ts and '"run" | "dwell" | "end"' in ts
