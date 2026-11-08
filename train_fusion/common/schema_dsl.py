"""A tiny schema language for the pipeline's tables.

Each table is declared once (in its stage's schemas.py) as a `Table` of `col`s. From that
one declaration we derive:

- the polars schema used when a stage writes the table (`Table.conform`, `Table.write`),
- TypeScript interfaces for the explorer (`typescript`).

Column types: "str", "i64", "f64", "bool", "enum" (with `values`). A trailing "?"
marks the column nullable ("f64?"). Units go in the column name suffix (_s, _m, _epoch)
and are repeated in `unit` for the docs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

_POLARS = {"str": pl.String, "i64": pl.Int64, "f64": pl.Float64, "bool": pl.Boolean,
           "enum": pl.String}
_TS = {"str": "string", "i64": "number", "f64": "number", "bool": "boolean"}


@dataclass(frozen=True)
class Col:
    name: str
    type: str
    desc: str
    unit: str | None = None
    values: tuple[str, ...] | None = None

    @property
    def base(self) -> str:
        return self.type.rstrip("?")

    @property
    def nullable(self) -> bool:
        return self.type.endswith("?")


def col(name: str, type: str, desc: str, unit: str | None = None,
        values: list[str] | None = None) -> Col:
    if type.rstrip("?") not in _POLARS:
        raise ValueError(f"{name}: unknown type {type!r}")
    if (type.rstrip("?") == "enum") != (values is not None):
        raise ValueError(f"{name}: enum columns need values (and only they)")
    return Col(name, type, desc, unit, tuple(values) if values else None)


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Table:
    name: str
    desc: str
    cols: list[Col] = field(default_factory=list)

    def polars_schema(self) -> dict[str, pl.DataType]:
        return {c.name: _POLARS[c.base] for c in self.cols}

    def conform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Select this table's columns in order, cast strictly, and validate.
        Raises SchemaError on missing columns, failed casts, nulls in non-nullable
        columns, or enum values outside the declared set."""
        missing = [c.name for c in self.cols if c.name not in df.columns]
        extra = [n for n in df.columns if n not in {c.name for c in self.cols}]
        if missing or extra:
            raise SchemaError(f"{self.name}: missing {missing}, unexpected {extra}")
        try:
            out = df.select([pl.col(c.name).cast(_POLARS[c.base], strict=True) for c in self.cols])
        except Exception as e:  # polars raises several types for failed casts
            raise SchemaError(f"{self.name}: {e}") from e
        for c in self.cols:
            s = out[c.name]
            if not c.nullable and s.null_count():
                raise SchemaError(f"{self.name}.{c.name}: {s.null_count()} nulls in non-nullable column")
            if c.values is not None:
                bad = set(s.drop_nulls().unique().to_list()) - set(c.values)
                if bad:
                    raise SchemaError(f"{self.name}.{c.name}: values {sorted(bad)} not in {c.values}")
        return out

    def empty(self) -> pl.DataFrame:
        return pl.DataFrame(schema=self.polars_schema())

    def write(self, df: pl.DataFrame, path: Path) -> None:
        """Conform, then write Parquet atomically (tmp file + rename)."""
        out = self.conform(df)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        out.write_parquet(tmp)
        tmp.rename(path)

    def read(self, path: Path) -> pl.DataFrame:
        return self.conform(pl.read_parquet(path))


def _ts_name(table: str) -> str:
    return "".join(p.capitalize() for p in table.split("_"))


def typescript(tables: list[Table]) -> str:
    out = ["// Generated from train_fusion/stages/*/schemas.py by `just types`. Do not edit.", ""]
    for t in tables:
        out.append(f"/** {t.desc} */")
        out.append(f"export interface {_ts_name(t.name)} {{")
        for c in t.cols:
            ts = " | ".join(f'"{v}"' for v in c.values) if c.values else _TS[c.base]
            if c.nullable:
                ts += " | null"
            unit = f" [{c.unit}]" if c.unit else ""
            out.append(f"  /** {c.desc}{unit} */")
            out.append(f"  {c.name}: {ts};")
        out.append("}")
        out.append("")
    return "\n".join(out)
