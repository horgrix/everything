"""Data query endpoints with filtering, grouping, aggregation, and pagination."""

import json
import re
import logging
from fastapi import APIRouter, Request, Query, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_OPS = {"=", "!=", "<>", ">", "<", ">=", "<=", "IN", "NOT IN", "LIKE", "NOT LIKE",
              "IS NULL", "IS NOT NULL", "BETWEEN"}


def _get_db(request: Request):
    return request.app.state.db


def _validate_table_name(name: str) -> str:
    """Validate table name: only alphanumeric + underscore."""
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        raise HTTPException(status_code=400, detail=f"Invalid table name: {name}")
    return name


def _get_table_columns(request: Request, table_name: str) -> set[str]:
    """Get the set of valid column names for a table."""
    db = _get_db(request)
    rows = db.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _validate_columns(columns: set[str], *names: str) -> None:
    """Raise 400 if any column name is not in the valid set."""
    for name in names:
        if name and name not in columns:
            raise HTTPException(status_code=400, detail=f"Unknown column: {name}")


def _parse_where(where_json: str, valid_columns: set[str]) -> tuple[str, list]:
    """
    Parse a JSON where clause into SQL and params.

    Format: [{"col": "steam_id", "op": ">=", "value": 1000}]

    Returns (where_clause_sql, params_list).
    """
    try:
        conditions = json.loads(where_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'where' parameter")

    if not isinstance(conditions, list):
        raise HTTPException(status_code=400, detail="'where' must be a JSON array")

    clauses = []
    params = []

    for cond in conditions:
        col = cond.get("col", "")
        op = cond.get("op", "=").upper()
        value = cond.get("value")

        if col not in valid_columns:
            raise HTTPException(status_code=400, detail=f"Unknown column in where: {col}")
        if op not in _VALID_OPS:
            raise HTTPException(status_code=400, detail=f"Unsupported operator: {op}")

        if op in ("IS NULL", "IS NOT NULL"):
            clauses.append(f"{col} {op}")
        elif op == "IN":
            if not isinstance(value, list):
                raise HTTPException(status_code=400, detail="IN requires a list value")
            placeholders = ", ".join(["?" for _ in value])
            clauses.append(f"{col} IN ({placeholders})")
            params.extend(value)
        elif op == "NOT IN":
            if not isinstance(value, list):
                raise HTTPException(status_code=400, detail="NOT IN requires a list value")
            placeholders = ", ".join(["?" for _ in value])
            clauses.append(f"{col} NOT IN ({placeholders})")
            params.extend(value)
        elif op == "BETWEEN":
            if not isinstance(value, list) or len(value) != 2:
                raise HTTPException(status_code=400, detail="BETWEEN requires a [low, high] list")
            clauses.append(f"{col} BETWEEN ? AND ?")
            params.extend(value)
        else:
            clauses.append(f"{col} {op} ?")
            params.append(value)

    where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where_clause, params


def _build_order_clause(order_by: str, valid_columns: set[str]) -> str:
    """Parse comma-separated order_by into safe SQL ORDER BY clause."""
    if not order_by:
        return ""
    parts = []
    for item in order_by.split(","):
        item = item.strip()
        if not item:
            continue
        # split by whitespace: "col ASC" or "col DESC" or just "col"
        tokens = item.split()
        col = tokens[0]
        if col not in valid_columns:
            raise HTTPException(status_code=400, detail=f"Unknown column in order_by: {col}")
        direction = "ASC"
        if len(tokens) > 1 and tokens[1].upper() in ("ASC", "DESC"):
            direction = tokens[1].upper()
        parts.append(f"{col} {direction}")
    return " ORDER BY " + ", ".join(parts) if parts else ""


def _build_select_clause(fields: str, valid_columns: set[str]) -> str:
    """Parse comma-separated fields into safe SELECT clause."""
    if not fields:
        return "*"
    selected = []
    for f in fields.split(","):
        f = f.strip()
        if f not in valid_columns:
            raise HTTPException(status_code=400, detail=f"Unknown column in fields: {f}")
        selected.append(f)
    return ", ".join(selected) if selected else "*"


# ================================================================
# Routes
# ================================================================


@router.get("/tables")
async def list_tables(request: Request):
    """List all business tables."""
    db = _get_db(request)
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'crawl_%' "
        "AND name NOT LIKE 'dedup_%' ORDER BY name"
    ).fetchall()
    return {"code": 0, "message": "success", "data": [r["name"] for r in rows]}


@router.get("/{table_name}/columns")
async def get_columns(request: Request, table_name: str):
    """Get column metadata for a table."""
    _validate_table_name(table_name)
    db = _get_db(request)
    rows = db.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
    return {"code": 0, "message": "success", "data": [dict(r) for r in rows]}


@router.get("/{table_name}/query")
async def query_table(
    request: Request,
    table_name: str,
    fields: str = Query(None, description="Comma-separated columns (default: all)"),
    where: str = Query(None, description="JSON filter: [{\"col\":\"x\",\"op\":\">\",\"value\":100}]"),
    group_by: str = Query(None, description="Comma-separated GROUP BY columns"),
    order_by: str = Query("crawled_at DESC", description="ORDER BY: crawled_at DESC, col ASC"),
    limit: int = Query(100, ge=1, le=9999, description="Rows to return"),
    offset: int = Query(0, ge=0, description="Offset"),
    aggregate: str = Query(None, description="Aggregate: SUM(col) as total, COUNT(*) as cnt"),
):
    """
    Query business table data with full filtering, grouping, and aggregation.

    Examples:
      - ?fields=steam_id,peak_players&where=[{"col":"peak_players","op":">","value":5000}]
      - ?fields=steam_id,COUNT(*) as cnt&group_by=steam_id
      - ?aggregate=COUNT(*) as cnt,AVG(peak_players) as avg&group_by=steam_id
    """
    _validate_table_name(table_name)
    db = _get_db(request)
    valid_columns = _get_table_columns(request, table_name)

    # Build SELECT clause
    if aggregate:
        select_clause = _parse_aggregate(aggregate, valid_columns)
    elif fields:
        select_clause = _build_select_clause(fields, valid_columns)
    else:
        select_clause = "*"

    # Build WHERE clause
    where_clause, where_params = "", []
    if where:
        where_clause, where_params = _parse_where(where, valid_columns)

    # Build GROUP BY
    group_clause = ""
    if group_by:
        group_cols = [c.strip() for c in group_by.split(",") if c.strip()]
        for c in group_cols:
            if c not in valid_columns:
                raise HTTPException(status_code=400, detail=f"Unknown column in group_by: {c}")
        group_clause = " GROUP BY " + ", ".join(group_cols)

    # Build ORDER BY
    order_clause = _build_order_clause(order_by, valid_columns)

    # Build and execute query
    sql = f"SELECT {select_clause} FROM {table_name}{where_clause}{group_clause}{order_clause} LIMIT ? OFFSET ?"
    all_params = where_params + [limit, offset]

    logger.debug("Query SQL: %s | params: %s", sql, all_params)

    rows = db.conn.execute(sql, all_params).fetchall()
    result = [dict(r) for r in rows]

    # Count total (without LIMIT/OFFSET, but with WHERE)
    count_sql = f"SELECT COUNT(*) as cnt FROM {table_name}{where_clause}"
    count_row = db.conn.execute(count_sql, where_params).fetchone()
    total = count_row["cnt"] if count_row else 0

    return {"code": 0, "message": "success", "data": result, "total": total}


@router.get("/{table_name}/count")
async def count_table(
    request: Request,
    table_name: str,
    where: str = Query(None, description="JSON filter for conditional count"),
):
    """Get row count for a business table."""
    _validate_table_name(table_name)
    db = _get_db(request)
    valid_columns = _get_table_columns(request, table_name)

    where_clause, where_params = "", []
    if where:
        where_clause, where_params = _parse_where(where, valid_columns)

    sql = f"SELECT COUNT(*) as cnt FROM {table_name}{where_clause}"
    row = db.conn.execute(sql, where_params).fetchone()
    count = row["cnt"] if row else 0

    return {"code": 0, "message": "success", "data": {"table": table_name, "count": count}}


# ================================================================
# Helpers
# ================================================================

def _parse_aggregate(aggregate: str, valid_columns: set[str]) -> str:
    """
    Parse aggregate expressions like: SUM(col) as total, COUNT(*) as cnt
    Validate that column references are valid.
    """
    # Simple validation: extract column names from aggregate functions
    # and verify they exist in valid_columns
    parts = [p.strip() for p in aggregate.split(",")]
    for part in parts:
        match = re.match(r"(SUM|COUNT|AVG|MAX|MIN)\s*\(\s*(.+?)\s*\)", part, re.IGNORECASE)
        if not match:
            raise HTTPException(status_code=400, detail=f"Invalid aggregate expression: {part}")
        col_expr = match.group(2).strip()
        if col_expr != "*":
            # Strip any quoting/whitespace
            col_expr = col_expr.strip("`\"' ")
            if col_expr not in valid_columns:
                raise HTTPException(status_code=400, detail=f"Unknown column in aggregate: {col_expr}")
    return aggregate