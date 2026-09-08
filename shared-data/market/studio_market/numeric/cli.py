from __future__ import annotations
import argparse
import json
from datetime import date

from studio_market.config import MarketSettings
from .store import NumericStore,serializable


def csv_values(value):return [v.strip() for v in value.split(",") if v.strip()] if value else None


def main(argv=None):
    parser=argparse.ArgumentParser(prog="studio-market numeric")
    parser.add_argument("--database-url")
    parser.add_argument("--data-root")
    commands=parser.add_subparsers(dest="command",required=True)
    commands.add_parser("status")
    q=commands.add_parser("query");q.add_argument("dataset");q.add_argument("--symbols");q.add_argument("--start");q.add_argument("--end");q.add_argument("--as-of");q.add_argument("--observed-at");q.add_argument("--batch-id");q.add_argument("--versions",action="store_true");q.add_argument("--limit",type=int,default=1000);q.add_argument("--offset",type=int,default=0)
    c=commands.add_parser("collect");c.add_argument("--groups");c.add_argument("--symbols");c.add_argument("--start",type=date.fromisoformat);c.add_argument("--end",type=date.fromisoformat)
    m=commands.add_parser("migrate-source");m.add_argument("source_root");m.add_argument("--datasets");m.add_argument("--batch-rows",type=int,default=65536);m.add_argument("--reimport",action="store_true");m.add_argument("--apply",action="store_true")
    e=commands.add_parser("export-bundle");e.add_argument("output");e.add_argument("--batch-ids");e.add_argument("--datasets");e.add_argument("--since")
    i=commands.add_parser("import-bundle");i.add_argument("source")
    r=commands.add_parser("read-source");r.add_argument("source_id")
    recovery=commands.add_parser("recover-price-revisions");recovery.add_argument("--since",required=True);recovery.add_argument("--end",required=True,type=date.fromisoformat);recovery.add_argument("--symbols");recovery.add_argument("--apply",action="store_true")
    args=parser.parse_args(argv)
    if args.command=="migrate-source" and not args.apply:
        from .migrate import migration_plan
        result=migration_plan(args.source_root,csv_values(args.datasets))
    else:
        settings=MarketSettings.from_environment(args.database_url,args.data_root)
        if args.command=="status":result=NumericStore(settings).status()
        elif args.command=="query":result=NumericStore(settings).query(args.dataset,symbols=csv_values(args.symbols),start=args.start,end=args.end,as_of=args.as_of,observed_at=args.observed_at,batch_id=args.batch_id,versions=args.versions,limit=args.limit,offset=args.offset)
        elif args.command=="read-source":result=NumericStore(settings).read_source(args.source_id)
        elif args.command=="recover-price-revisions":
            from .price_revisions import recover_legacy_revisions
            from .collect import Collector
            store=NumericStore(settings)
            try:
                symbols=csv_values(args.symbols) or sorted(Collector(settings,store=store).universe())
                result=recover_legacy_revisions(store,since=args.since,end=args.end,symbols=symbols,apply=args.apply)
            finally:store.close()
        elif args.command=="collect":
            from .collect import collect
            result=collect(settings,groups=csv_values(args.groups),symbols=csv_values(args.symbols),start=args.start,end=args.end,progress=lambda r:print(json.dumps(r,ensure_ascii=False),flush=True))
        elif args.command=="migrate-source":
            from .migrate import migrate_source
            result=migrate_source(settings,args.source_root,names=csv_values(args.datasets),batch_rows=args.batch_rows,reimport=args.reimport,progress=lambda r:print(json.dumps(r,ensure_ascii=False),flush=True))
        elif args.command=="export-bundle":
            from .replication import export_bundle
            result=export_bundle(settings,args.output,batch_ids=csv_values(args.batch_ids),since=args.since,names=csv_values(args.datasets))
        elif args.command=="import-bundle":
            from .replication import import_bundle
            result=import_bundle(settings,args.source)
        else:raise AssertionError(args.command)
    print(json.dumps(serializable(result),ensure_ascii=False,indent=2))
    return 1 if isinstance(result,dict) and result.get("status")=="failed" else 0


if __name__=="__main__":raise SystemExit(main())
