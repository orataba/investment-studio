"""Issuer/SEC holdings for funds whose FMP disclosure coverage is incomplete."""
from datetime import timedelta
import json
from .providers.http_client import get_public_bytes
from .providers.official_etf_parsers import GLD_ARCHIVE_URL,NPORT_FUNDS,PERIODIC_FUNDS,parse_gld_archive,parse_nport_feed,parse_nport_filing,parse_bitcoin_filing,parse_periodic_positions
from .providers.sec_fund import SecFundFiling,_date_at,_datetime_at,_text_at


def collect_official_etf(collector,start,end,symbols=None):
    """Keep one issuer's unavailable source from blocking independent funds."""
    from .collect import failure_summary

    selected = ["GLD", *(spec.symbol for spec in NPORT_FUNDS), *(spec.symbol for spec in PERIODIC_FUNDS)]
    results = []
    for symbol in dict.fromkeys(selected):
        if symbols is not None and symbol not in symbols:
            continue
        try:
            _collect_official_etf(collector,start,end,[symbol])
            results.append({"symbol":symbol,"status":"ready"})
        except Exception as error:
            results.append({"symbol":symbol,"status":"failed",**failure_summary(error)})
    return {"status":"failed" if any(item["status"]=="failed" for item in results) else "ready","symbols":results}


def _collect_official_etf(collector,start,end,symbols):
    if symbols is None or "GLD" in symbols:
        response=get_public_bytes(GLD_ARCHIVE_URL,{"product":"gld","exchange":"NYSE","lang":"en"},timeout_seconds=90)
        clock,ref=collector.archive(response,"spdr_gold_shares")
        rows=parse_gld_archive(response.body,start_date=start,end_date=end,**clock)
        collector.publish("etf_disclosures",rows,response,ref,provider="spdr_gold_shares",release_clock="unknown")
    for spec in NPORT_FUNDS:
        if symbols and spec.symbol not in symbols:continue
        response=get_public_bytes("https://www.sec.gov/cgi-bin/browse-edgar",{"action":"getcompany","CIK":spec.series_id,"type":"NPORT-P","output":"atom","count":100})
        collector.archive(response,"sec")
        for filing in parse_nport_feed(response.body,spec=spec):
            if not start-timedelta(days=100)<=filing.filing_date<=end+timedelta(days=100):continue
            response=get_public_bytes(filing.document_url,timeout_seconds=90);clock,ref=collector.archive(response,"sec")
            rows=parse_nport_filing(response.body,spec=spec,filing=filing,**clock)
            rows=[r for r in rows if start<=r["report_date"]<=end]
            collector.publish("etf_disclosures",rows,response,ref,provider="sec",accession=filing.accession_number)
    for spec in PERIODIC_FUNDS:
        if symbols and spec.symbol not in symbols:continue
        response=get_public_bytes(f"https://data.sec.gov/submissions/CIK{spec.cik}.json")
        collector.archive(response,"sec")
        root=json.loads(response.body)
        batches=[root["filings"]["recent"]]
        for item in root["filings"].get("files",[]):
            # Download only filing-index history whose filing dates overlap the requested range.
            if item.get("filingTo") and item["filingTo"]<start.isoformat():continue
            response=get_public_bytes(f"https://data.sec.gov/submissions/{item['name']}")
            collector.archive(response,"sec");batches.append(json.loads(response.body))
        for batch in batches:
            for index,form in enumerate(batch.get("form",[])):
                if form not in {"10-K","10-K/A","10-Q","10-Q/A"}:continue
                report=_date_at(batch,"reportDate",index);filing_date=_date_at(batch,"filingDate",index)
                accepted=_datetime_at(batch,"acceptanceDateTime",index);accession=_text_at(batch,"accessionNumber",index);document=_text_at(batch,"primaryDocument",index)
                if not report or not filing_date or not accepted or not accession or not document:continue
                if not max(start,spec.source_start)<=report<=end:continue
                filing=SecFundFiling(spec.cik,filing_date,report,accepted,form,accession,document)
                response=get_public_bytes(filing.document_url,timeout_seconds=90);clock,ref=collector.archive(response,"sec")
                parser=parse_bitcoin_filing if spec.parser=="bitcoin" else parse_periodic_positions
                collector.publish("etf_disclosures",parser(response.body,spec=spec,filing=filing,**clock),response,ref,provider="sec",accession=accession)
