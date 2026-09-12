"""Compact MCP views of complete, retained analyst-estimate evidence."""
from collections import Counter


TABLES = ("changes", "observations", "unmatched")


def estimate_overview(evidence, *, detail_tool):
    symbols = set(evidence.get("company_symbols", []))
    for table in TABLES:
        symbols.update(row["symbol"] for row in evidence.get(table, []) if row.get("symbol"))
    changes = Counter((row["frequency"], row["metric"], "increase" if row["delta"] > 0 else "decrease")
                      for row in evidence.get("changes", []))
    return {**{key: value for key, value in evidence.items() if key not in {*TABLES, "source_ids"}},
        "company_symbols": sorted(symbols),
        "change_counts": [{"frequency": frequency, "metric": metric, "direction": direction, "count": count}
                          for (frequency, metric, direction), count in sorted(changes.items())],
        "row_counts": {table: len(evidence.get(table, [])) for table in TABLES},
        "observation_reason_counts": dict(Counter(row["reason"] for row in evidence.get("observations", []))),
        "unmatched_reason_counts": dict(Counter(row["reason"] for row in evidence.get("unmatched", []))),
        "detail_read": detail_tool,
        "overview_note": "这是全部对照行的概览，不是逐行证据；计数是公司/财期/指标行数，不是公司数或重要性判断。按公司读取完整对照后再引用数值变化。"}


def estimate_company(evidence, symbol):
    """Keep every matching row; columns and common fields remove repetition."""
    symbol = symbol.strip().upper()
    overview = estimate_overview(evidence, detail_tool=None)
    if symbol not in overview["company_symbols"]:
        raise ValueError("预期对照中没有该公司，请使用返回的company_symbols。")
    receipts = []
    tables = {}
    for table in TABLES:
        rows = []
        for original in evidence.get(table, []):
            if original.get("symbol") != symbol:
                continue
            row = dict(original)
            for field in ("current_currency_source", "previous_currency_source"):
                source = row.pop(field, None)
                if source is not None:
                    if source not in receipts:
                        receipts.append(source)
                    row[field + "_index"] = receipts.index(source)
            rows.append(row)
        common = {key: value for key, value in rows[0].items()
                  if all(key in row and row[key] == value for row in rows)} if rows else {}
        columns = sorted({key for row in rows for key in row} - common.keys())
        tables[table] = {"common": common, "columns": columns,
                         "rows": [[row.get(key) for key in columns] for row in rows]}
    return {**{key: value for key, value in evidence.items() if key not in {*TABLES, "company_symbols", "source_ids"}},
        "symbol": symbol, "tables": tables, "currency_source_receipts": receipts,
        "row_format": "每行按columns顺序读取，并合并同表common字段；*_currency_source_index指向currency_source_receipts。保留全部该公司对照行；无行表示本对照未记录该公司变化或覆盖差异，不是零预期。"}
