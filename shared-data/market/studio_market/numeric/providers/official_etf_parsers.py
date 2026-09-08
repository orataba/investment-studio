from __future__ import annotations

import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Iterable
from xml.etree import ElementTree as ET

from .sec_fund import SecFundFiling


OFFICIAL_ETF_HISTORY_START = date(2004, 11, 18)
STRUCTURED_PERIODIC_START = date(2019, 6, 30)
GLD_ARCHIVE_URL = (
    "https://api.spdrgoldshares.com/api/v1/historical-archive"
)


@dataclass(frozen=True)
class NportFundSpec:
    symbol: str
    cik: str
    series_id: str
    class_id: str
    source_start: date


@dataclass(frozen=True)
class PeriodicFundSpec:
    symbol: str
    cik: str
    source_start: date
    parser: str


NPORT_FUNDS = (
    NportFundSpec(
        "BND", "0000794105", "S000002564", "C000046844", date(2019, 9, 30)
    ),
    NportFundSpec(
        "DTCR", "0001432353", "S000069709", "C000222332", date(2019, 9, 30)
    ),
    NportFundSpec(
        "LITP", "0001728683", "S000078983", "C000239803", date(2019, 9, 30)
    ),
)

PERIODIC_FUNDS = (
    PeriodicFundSpec("DBC", "0001328237", STRUCTURED_PERIODIC_START, "positions"),
    PeriodicFundSpec("USO", "0001327068", STRUCTURED_PERIODIC_START, "positions"),
    PeriodicFundSpec("IBIT", "0001980994", date(2024, 1, 5), "bitcoin"),
)


def parse_nport_feed(
    body: bytes,
    *,
    spec: NportFundSpec,
) -> list[SecFundFiling]:
    root = ET.fromstring(body)
    filings: list[SecFundFiling] = []
    for entry in root.iter():
        if _local_name(entry.tag) != "entry":
            continue
        accession = _descendant_text(entry, "accession-number")
        filing_type = _descendant_text(entry, "filing-type")
        filing_date = _iso_date(_descendant_text(entry, "filing-date"))
        accepted_at = _iso_datetime(_descendant_text(entry, "updated"))
        if not accession or not filing_type or not filing_date or not accepted_at:
            continue
        filings.append(
            SecFundFiling(
                cik=spec.cik,
                filing_date=filing_date,
                report_date=filing_date,
                accepted_at=accepted_at,
                form_type=filing_type,
                accession_number=accession,
                primary_document="primary_doc.xml",
            )
        )
    return sorted(filings, key=lambda item: item.accepted_at)


def parse_nport_filing(
    body: bytes,
    *,
    spec: NportFundSpec,
    filing: SecFundFiling,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    root = ET.fromstring(body)
    series_id = _first_text(root, "seriesId")
    if series_id != spec.series_id:
        raise ValueError(
            f"SEC N-PORT series mismatch for {spec.symbol}: {series_id}"
        )
    class_ids = {
        value
        for element in root.iter()
        if _local_name(element.tag) in {"classId", "classID"}
        for value in [(element.text or "").strip()]
        if value
    }
    if class_ids and spec.class_id not in class_ids:
        raise ValueError(
            f"SEC N-PORT class mismatch for {spec.symbol}: {sorted(class_ids)}"
        )
    report_text = _first_text(root, "repPdDate")
    report_date = _iso_date(report_text)
    if report_date is None:
        raise ValueError(f"SEC N-PORT report date is missing for {spec.symbol}")

    rows: list[dict[str, object]] = []
    occurrences: Counter[str] = Counter()
    for investment in root.iter():
        if _local_name(investment.tag) != "invstOrSec":
            continue
        name = _descendant_text(investment, "name")
        title = _descendant_text(investment, "title")
        cusip = _clean_identifier(_descendant_text(investment, "cusip"))
        isin = _descendant_attribute(investment, "isin", "value")
        lei = _clean_identifier(_descendant_text(investment, "lei"))
        asset_category = _descendant_text(investment, "assetCat")
        base_key = "|".join(
            value.upper()
            for value in (cusip, isin, name, title, asset_category)
            if value
        ) or "UNIDENTIFIED"
        occurrences[base_key] += 1
        issuer_category = _descendant_text(investment, "issuerCat")
        if issuer_category is None:
            issuer_category = _descendant_attribute(
                investment, "issuerConditional", "issuerCat"
            )
        currency = _descendant_text(investment, "curCd")
        if currency is None:
            currency = _descendant_attribute(
                investment, "currencyConditional", "curCd"
            )
        notional_value = _first_number(
            investment,
            ("notionalAmt", "notionalAmount", "notionalValue"),
        )
        rows.append(
            {
                "etf_symbol": spec.symbol,
                "report_date": report_date,
                "accepted_at": filing.accepted_at.replace(tzinfo=None),
                "holding_key": f"{base_key}#{occurrences[base_key]}",
                "holding_symbol": None,
                "holding_name": name,
                "title": title,
                "registrant_cik": spec.cik,
                "lei": lei,
                "cusip": cusip,
                "isin": isin,
                "balance": _number(_descendant_text(investment, "balance")),
                "units": _descendant_text(investment, "units"),
                "currency": currency,
                "value_usd": _number(_descendant_text(investment, "valUSD")),
                "percent_value": _number(_descendant_text(investment, "pctVal")),
                "payoff_profile": _descendant_text(investment, "payoffProfile"),
                "asset_category": asset_category,
                "issuer_category": issuer_category,
                "investment_country": _descendant_text(investment, "invCountry"),
                "is_restricted_security": _descendant_text(
                    investment, "isRestrictedSec"
                ),
                "fair_value_level": _descendant_text(investment, "fairValLevel"),
                "is_cash_collateral": _descendant_text(
                    investment, "isCashCollateral"
                ),
                "is_non_cash_collateral": _descendant_text(
                    investment, "isNonCashCollateral"
                ),
                "is_loan_by_fund": _descendant_text(investment, "isLoanByFund"),
                "source_dataset": "sec_nport_filing",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
                "notional_value_usd": notional_value,
                "position_type": _nport_position_type(
                    investment, asset_category
                ),
            }
        )
    if not rows:
        raise ValueError(f"SEC N-PORT contains no investments for {spec.symbol}")
    return rows


def parse_periodic_positions(
    body: bytes,
    *,
    spec: PeriodicFundSpec,
    filing: SecFundFiling,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    root = _parse_sec_xhtml(body)
    contexts = _instant_contexts(root, filing.report_date)
    facts = _facts_by_context(root, contexts)
    rows: list[dict[str, object]] = []
    occurrences: Counter[str] = Counter()
    for context_id, dimensions in contexts.items():
        identity = _position_identity(dimensions)
        if identity is None:
            continue
        context_facts = facts.get(context_id, {})
        balance_name, balance = _first_fact(
            context_facts,
            (
                "InvestmentOwnedBalanceContracts",
                "InvestmentOwnedBalanceShares",
                "InvestmentOwnedBalancePrincipalAmount",
                "InvestmentOwnedBalanceOtherMeasure",
            ),
        )
        _, notional = _first_fact(
            context_facts,
            (
                "InvestmentOwnedUnderlyingFaceAmountAtMarketValue",
                "DerivativeNotionalAmount",
            ),
        )
        _, value = _first_fact(
            context_facts,
            (
                "InvestmentOwnedAtFairValue",
                "FairValueOrOpenCommoditySwapContracts",
                "InvestmentsFairValueDisclosure",
            ),
        )
        if balance is None and notional is None:
            has_counterparty = _dimension_member(
                dimensions, "CounterpartyNameAxis"
            ) is not None
            if (
                value is None
                or not _is_swap_position(identity, dimensions)
                or not has_counterparty
            ):
                continue
        raw_identity, holding_name = identity
        key = re.sub(r"\s+", " ", raw_identity).strip().upper()
        occurrences[key] += 1
        position_type = _periodic_position_type(
            holding_name, balance_name, dimensions
        )
        if position_type not in {"futures", "swap"}:
            if value is None:
                value = notional
            notional = None
        _, percent_value = _first_fact(
            context_facts,
            ("InvestmentOwnedPercentOfNetAssets", "PercentageOfPartnersCapital"),
            apply_scale=False,
        )
        payoff = _dimension_member(dimensions, "PositionAxis")
        rows.append(
            {
                "etf_symbol": spec.symbol,
                "report_date": filing.report_date,
                "accepted_at": filing.accepted_at.replace(tzinfo=None),
                "holding_key": f"{key}#{occurrences[key]}",
                "holding_symbol": None,
                "holding_name": holding_name,
                "title": raw_identity,
                "registrant_cik": spec.cik,
                "lei": None,
                "cusip": None,
                "isin": None,
                "balance": balance,
                "units": _balance_units(balance_name),
                "currency": "USD",
                "value_usd": value,
                "percent_value": percent_value,
                "payoff_profile": _display_member(payoff) if payoff else None,
                "asset_category": _periodic_asset_category(position_type),
                "issuer_category": None,
                "investment_country": None,
                "is_restricted_security": None,
                "fair_value_level": _fair_value_level(dimensions),
                "is_cash_collateral": None,
                "is_non_cash_collateral": None,
                "is_loan_by_fund": None,
                "source_dataset": "sec_periodic_portfolio_filing",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
                "notional_value_usd": notional,
                "position_type": position_type,
            }
        )
    if not rows:
        raise ValueError(
            f"SEC periodic filing yielded no positions for {spec.symbol} "
            f"at {filing.report_date}"
        )
    return rows


def parse_bitcoin_filing(
    body: bytes,
    *,
    spec: PeriodicFundSpec,
    filing: SecFundFiling,
    raw_sha256: str,
    collected_at: datetime,
) -> list[dict[str, object]]:
    root = _parse_sec_xhtml(body)
    contexts = _instant_contexts(root, filing.report_date)
    facts = _facts_by_context(root, contexts)
    merged: dict[str, list[tuple[str, str | None]]] = {}
    for context_facts in facts.values():
        for name, values in context_facts.items():
            merged.setdefault(name, []).extend(values)
    balance = _unique_fact(merged, "CryptoAssetNumberOfUnits")
    if balance is None:
        balance = _unique_fact(merged, "BitcoinBalance")
    value = _unique_fact(merged, "CryptoAssetFairValue")
    percent_value = _unique_fact(
        merged, "InvestmentOwnedPercentOfNetAssets", apply_scale=False
    )
    if balance is None or value is None:
        raise ValueError(
            f"SEC periodic filing has no Bitcoin position for {filing.report_date}"
        )
    return [
        {
            "etf_symbol": spec.symbol,
            "report_date": filing.report_date,
            "accepted_at": filing.accepted_at.replace(tzinfo=None),
            "holding_key": "BITCOIN#1",
            "holding_symbol": "BTC",
            "holding_name": "Bitcoin",
            "title": "Bitcoin held by the Trust",
            "registrant_cik": spec.cik,
            "lei": None,
            "cusip": None,
            "isin": None,
            "balance": balance,
            "units": "BTC",
            "currency": "USD",
            "value_usd": value,
            "percent_value": percent_value,
            "payoff_profile": "Long",
            "asset_category": "DIGITAL_ASSET",
            "issuer_category": None,
            "investment_country": None,
            "is_restricted_security": None,
            "fair_value_level": None,
            "is_cash_collateral": None,
            "is_non_cash_collateral": None,
            "is_loan_by_fund": None,
            "source_dataset": "sec_periodic_portfolio_filing",
            "raw_sha256": raw_sha256,
            "collected_at": collected_at,
            "notional_value_usd": None,
            "position_type": "digital_asset",
        }
    ]


def parse_gld_archive(
    body: bytes,
    *,
    raw_sha256: str,
    collected_at: datetime,
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    table = _xlsx_sheet_rows(body, "US GLD Historical Archive")
    if not table:
        raise ValueError("SPDR GLD archive has no data rows")
    header = [str(value or "").strip() for value in table[0]]
    try:
        date_index = header.index("Date")
        ounces_index = header.index("Total Ounces of Gold in the Trust")
    except ValueError:
        raise ValueError("SPDR GLD archive headers changed") from None
    rows: list[dict[str, object]] = []
    for values in table[1:]:
        if max(date_index, ounces_index) >= len(values):
            continue
        report_date = _archive_date(values[date_index])
        ounces = _number(values[ounces_index])
        if (
            report_date is None
            or ounces is None
            or ounces <= 0
            or not start_date <= report_date <= end_date
        ):
            continue
        rows.append(
            {
                "etf_symbol": "GLD",
                "report_date": report_date,
                "accepted_at": None,
                "holding_key": "GOLD_BULLION#1",
                "holding_symbol": None,
                "holding_name": "Gold bullion",
                "title": "Total ounces of gold in the Trust",
                "registrant_cik": "0001222333",
                "lei": None,
                "cusip": None,
                "isin": None,
                "balance": ounces,
                "units": "OZ",
                "currency": "USD",
                "value_usd": None,
                "percent_value": None,
                "payoff_profile": "Long",
                "asset_category": "PHYSICAL_COMMODITY",
                "issuer_category": None,
                "investment_country": None,
                "is_restricted_security": None,
                "fair_value_level": None,
                "is_cash_collateral": None,
                "is_non_cash_collateral": None,
                "is_loan_by_fund": None,
                "source_dataset": "spdr_gld_historical_archive",
                "raw_sha256": raw_sha256,
                "collected_at": collected_at,
                "notional_value_usd": None,
                "position_type": "physical_commodity",
            }
        )
    if not rows:
        raise ValueError("SPDR GLD archive has no usable observations")
    return rows


def _instant_contexts(
    root: ET.Element,
    report_date: date,
) -> dict[str, list[tuple[str, str]]]:
    expected = report_date.isoformat()
    contexts: dict[str, list[tuple[str, str]]] = {}
    for element in root.iter():
        if _local_name(element.tag) != "context":
            continue
        instant = _descendant_text(element, "instant")
        context_id = element.attrib.get("id")
        if instant != expected or not context_id:
            continue
        dimensions = []
        for child in element.iter():
            axis = child.attrib.get("dimension")
            if not axis:
                continue
            member = " ".join("".join(child.itertext()).split())
            if member:
                dimensions.append((_local_qname(axis), member))
        contexts[context_id] = dimensions
    return contexts


def _facts_by_context(
    root: ET.Element,
    contexts: dict[str, list[tuple[str, str]]],
) -> dict[str, dict[str, list[tuple[str, str | None]]]]:
    facts: dict[str, dict[str, list[tuple[str, str | None]]]] = {
        context_id: {} for context_id in contexts
    }
    for element in root.iter():
        context_id = element.attrib.get("contextRef")
        if context_id not in facts:
            continue
        name = _local_qname(element.attrib.get("name", ""))
        value = " ".join("".join(element.itertext()).split())
        if not name or not value or value in {"-", "—"}:
            continue
        if element.attrib.get("sign") == "-" and not value.startswith("-"):
            value = f"-{value}"
        facts[context_id].setdefault(name, []).append(
            (value, element.attrib.get("scale"))
        )
    return facts


def _position_identity(
    dimensions: list[tuple[str, str]],
) -> tuple[str, str] | None:
    priority = (
        "OpenFuturesContractIdentifierAxis",
        "InvestmentIdentifierAxis",
        "InvestmentSecondaryCategorizationAxis",
        "EquitySecuritiesByIndustryAxis",
        "InvestmentSecuritiesSeriesAxis",
        "CashAndCashEquivalentsAxis",
        "FinancialInstrumentAxis",
        "ScheduleOfEquityMethodInvestmentEquityMethodInvesteeNameAxis",
        "CounterpartyNameAxis",
        "InvestmentTypeAxis",
    )
    for axis_name in priority:
        for axis, member in dimensions:
            if axis != axis_name or not _specific_member(member):
                continue
            return member, _display_member(member)
    return None


def _specific_member(member: str) -> bool:
    if " " in member:
        return True
    prefix = member.split(":", 1)[0] if ":" in member else ""
    return prefix not in {"", "us-gaap", "srt", "country"}


def _display_member(member: str) -> str:
    value = member.split(":", 1)[-1]
    value = re.sub(r"Member$", "", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    return " ".join(value.replace("_", " ").split())


def _first_fact(
    facts: dict[str, list[tuple[str, str | None]]],
    names: Iterable[str],
    *,
    apply_scale: bool = True,
) -> tuple[str | None, float | None]:
    for name in names:
        value = _unique_fact(facts, name, apply_scale=apply_scale)
        if value is not None:
            return name, value
    return None, None


def _unique_fact(
    facts: dict[str, list[tuple[str, str | None]]],
    name: str,
    *,
    apply_scale: bool = True,
) -> float | None:
    values = {
        parsed
        for raw, scale in facts.get(name, [])
        for parsed in [_xbrl_number(raw, scale if apply_scale else None)]
        if parsed is not None
    }
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"conflicting SEC XBRL values for {name}: {sorted(values)}")
    return values.pop()


def _xbrl_number(value: str, scale: str | None) -> float | None:
    cleaned = value.replace(",", "").replace("$", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("() ")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if scale:
        try:
            number *= 10 ** int(scale)
        except ValueError:
            return None
    if negative:
        number = -number
    return number if math.isfinite(number) else None


def _parse_sec_xhtml(body: bytes) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        sanitized = re.sub(
            rb"<script\b[^>]*>.*?</script\s*>",
            b"",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if sanitized == body:
            raise
        return ET.fromstring(sanitized)


def _periodic_position_type(
    holding_name: str,
    balance_name: str | None,
    dimensions: list[tuple[str, str]],
) -> str:
    text = " ".join([holding_name, *(member for _, member in dimensions)]).lower()
    if balance_name == "InvestmentOwnedBalanceContracts" or "future" in text:
        return "futures"
    if "swap" in text:
        return "swap"
    if any(
        value in text
        for value in (
            "treasury",
            "money market",
            "liquidity fund",
            "government fund",
            "cash equivalent",
        )
    ):
        return "short_term_investment"
    return "security"


def _periodic_asset_category(position_type: str) -> str:
    return {
        "futures": "FUTURES",
        "swap": "SWAP",
        "short_term_investment": "STIV",
    }.get(position_type, "OTHER")


def _balance_units(balance_name: str | None) -> str | None:
    return {
        "InvestmentOwnedBalanceContracts": "CONTRACT",
        "InvestmentOwnedBalanceShares": "NS",
        "InvestmentOwnedBalancePrincipalAmount": "USD",
        "InvestmentOwnedBalanceOtherMeasure": "OTHER",
    }.get(balance_name)


def _is_swap_position(
    identity: tuple[str, str],
    dimensions: list[tuple[str, str]],
) -> bool:
    return "swap" in " ".join(
        [identity[0], *(member for _, member in dimensions)]
    ).lower()


def _fair_value_level(dimensions: list[tuple[str, str]]) -> str | None:
    member = _dimension_member(dimensions, "FairValueByFairValueHierarchyLevelAxis")
    if member is None:
        return None
    match = re.search(r"Level\s*([123])", _display_member(member), re.IGNORECASE)
    return match.group(1) if match else None


def _dimension_member(
    dimensions: list[tuple[str, str]], axis_name: str
) -> str | None:
    return next((member for axis, member in dimensions if axis == axis_name), None)


def _nport_position_type(
    investment: ET.Element,
    asset_category: str | None,
) -> str:
    if any("derivative" in _local_name(item.tag).lower() for item in investment.iter()):
        return "derivative"
    if asset_category in {"EC", "EP"}:
        return "equity"
    if asset_category == "STIV":
        return "short_term_investment"
    if asset_category in {"DBT", "ABS-MBS"}:
        return "debt"
    return "other"


def _xlsx_sheet_rows(body: bytes, sheet_name: str) -> list[list[object]]:
    try:
        archive = zipfile.ZipFile(BytesIO(body))
    except zipfile.BadZipFile:
        raise ValueError("official archive is not a valid XLSX file") from None
    with archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root:
                shared_strings.append(
                    "".join(
                        element.text or ""
                        for element in item.iter()
                        if _local_name(element.tag) == "t"
                    )
                )
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationship_id = None
        for sheet in workbook.iter():
            if _local_name(sheet.tag) == "sheet" and sheet.attrib.get("name") == sheet_name:
                relationship_id = next(
                    (value for key, value in sheet.attrib.items() if key.endswith("}id")),
                    None,
                )
                break
        if relationship_id is None:
            raise ValueError(f"official archive sheet is missing: {sheet_name}")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(
            (
                item.attrib.get("Target")
                for item in relationships
                if item.attrib.get("Id") == relationship_id
            ),
            None,
        )
        if not target:
            raise ValueError(f"official archive sheet target is missing: {sheet_name}")
        sheet_path = target.lstrip("/")
        if not sheet_path.startswith("xl/"):
            sheet_path = f"xl/{sheet_path}"
        sheet_root = ET.fromstring(archive.read(sheet_path))
        rows: list[list[object]] = []
        for row in sheet_root.iter():
            if _local_name(row.tag) != "row":
                continue
            values: dict[int, object] = {}
            for cell in row:
                if _local_name(cell.tag) != "c":
                    continue
                reference = cell.attrib.get("r", "")
                column = _xlsx_column(reference)
                if column is None:
                    continue
                cell_type = cell.attrib.get("t")
                raw = _descendant_text(cell, "v")
                if cell_type == "s" and raw is not None:
                    try:
                        value: object = shared_strings[int(raw)]
                    except (ValueError, IndexError):
                        raise ValueError("official archive has an invalid shared string") from None
                elif cell_type == "inlineStr":
                    value = "".join(
                        element.text or ""
                        for element in cell.iter()
                        if _local_name(element.tag) == "t"
                    )
                elif raw is None:
                    value = None
                else:
                    value = _number(raw)
                    if value is None:
                        value = raw
                values[column] = value
            if values:
                width = max(values) + 1
                rows.append([values.get(index) for index in range(width)])
        return rows


def _xlsx_column(reference: str) -> int | None:
    match = re.match(r"([A-Z]+)", reference.upper())
    if match is None:
        return None
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _archive_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _first_text(root: ET.Element, name: str) -> str | None:
    return next(
        (
            (element.text or "").strip()
            for element in root.iter()
            if _local_name(element.tag) == name and (element.text or "").strip()
        ),
        None,
    )


def _descendant_text(root: ET.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) != name:
            continue
        value = " ".join("".join(element.itertext()).split())
        if value:
            return value
    return None


def _descendant_attribute(
    root: ET.Element, name: str, attribute: str
) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name:
            value = str(element.attrib.get(attribute, "")).strip()
            if value:
                return value
    return None


def _first_number(root: ET.Element, names: Iterable[str]) -> float | None:
    for name in names:
        value = _number(_descendant_text(root, name))
        if value is not None:
            return value
    return None


def _number(value: object) -> float | None:
    if value in (None, "", "-", "—"):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_identifier(value: str | None) -> str | None:
    return None if value in {None, "N/A", "NA"} else value


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _local_qname(value: str) -> str:
    return value.split(":", 1)[-1]
