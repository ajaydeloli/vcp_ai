"""Ad-hoc verification of review fixes. Run: .venv/bin/python scratch/verify_fixes.py"""
from datetime import date, datetime, timedelta, timezone, UTC
from unittest.mock import patch

from vcp_scanner.data.storage.duckdb_store import DuckDBStore

IST = timezone(timedelta(hours=5, minutes=30))
ok = lambda name, cond: print(("PASS " if cond else "FAIL ") + name)

# 1. Kite date
from vcp_scanner.data.providers import kite as kite_mod
from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.domain.market import Instrument
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
with patch.object(kite_mod, "KiteConnect") as KC:
    i = KC.return_value
    i.instruments.return_value = [{"tradingsymbol": "ABC", "instrument_token": 1, "exchange": "NSE", "segment": "NSE", "instrument_type": "EQ"}]
    i.historical_data.return_value = [{"date": datetime(2024, 1, 1, tzinfo=IST), "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100}]
    p = KiteProvider("k", "t")
    inst = p.get_instruments()[0]
    c = p.get_historical_daily(inst, date(2024, 1, 1), date(2024, 1, 1))[0]
    s = DuckDBStore(":memory:"); s.migrate()
    DuckDBMarketDataRepository(s).save_daily([c])
    ok("1 kite Monday bar stored as Monday", s.conn.execute("select trade_date from daily_prices").fetchone()[0] == date(2024, 1, 1))
    ok("3 kite id minted", inst.instrument_id == "NSE_EQ|ABC")

# 5-8 corporate actions
from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.reconciliation.engine import ReconciliationEngine
from vcp_scanner.domain.corporate_actions import CorporateAction, CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T
now = datetime(2024, 1, 5, tzinfo=UTC)
r = CorporateActionResolution("r1", "X", T.SPLIT, CorporateActionStatus.PROVIDER_CONFLICT, ex_date=date(2024, 1, 10), ratio_numerator=5, ratio_denominator=1)
ok("6 conflict resolution yields no factors", AdjustmentEngine().compute_factors([r]) == [])
nse = CorporateAction("n", "X", T.SPLIT, "NSE", now, ex_date=date(2024, 1, 10), ratio_numerator=10.0, ratio_denominator=2.0)
ups = CorporateAction("u", "X", T.SPLIT, "UPSTOX", now, ex_date=date(2024, 1, 10), ratio_numerator=5.0, ratio_denominator=1.0)
res = ReconciliationEngine().reconcile("X", [nse, ups], date(2024, 1, 6))
ok("7 10:2 == 5:1 -> CONFIRMED", res[0].resolution.status == CorporateActionStatus.CONFIRMED)

# 9 sm_worker
from vcp_scanner.data.ingestion.sm_worker import SecurityMasterIngestionWorker
ok("9 PROVIDER_NAME defined", hasattr(SecurityMasterIngestionWorker, "PROVIDER_NAME"))

# 11 weekly rerun
from vcp_scanner.features.weekly_aggregation import WeeklyAggregationEngine
def seed(s, iid, start, n, ver="v1"):
    s.conn.execute(f"""INSERT INTO daily_prices_adjusted SELECT '{iid}', DATE '{start}' + CAST(i AS INTEGER),
      100+i,101+i,99+i,100+i,1000,'{ver}',1.0,1.0,NULL,current_timestamp FROM range(0,{n}) t(i)""")
s = DuckDBStore(":memory:"); s.migrate()
seed(s, "A", "2024-01-01", 3); WeeklyAggregationEngine(s).compute_for_instrument("A")
s.conn.execute("DELETE FROM daily_prices_adjusted"); seed(s, "A", "2024-01-01", 5)
WeeklyAggregationEngine(s).compute_for_instrument("A")
ok("11 one weekly row after Wed-then-Fri rerun", s.conn.execute("select count(*) from weekly_prices").fetchone()[0] == 1)

# 13 RS
from vcp_scanner.features.relative_strength import RelativeStrengthEngine
s = DuckDBStore(":memory:"); s.migrate()
seed(s, "LONG", "2023-01-01", 300); seed(s, "SHORT", "2023-09-01", 60); seed(s, "STALE", "2022-01-01", 300)
s.conn.execute("INSERT INTO universe_snapshots VALUES ('U1','X',DATE '2023-10-27',now(),'h','1','POINT_IN_TIME_COMPLETE')")
s.conn.execute("INSERT INTO universe_memberships VALUES ('U1','LONG',TRUE,NULL,1,1,'EQUITY','EQ',NULL,NULL,NULL),('U1','SHORT',TRUE,NULL,1,1,'EQUITY','EQ',NULL,NULL,NULL),('U1','STALE',TRUE,NULL,1,1,'EQUITY','EQ',NULL,NULL,NULL)")
RelativeStrengthEngine(s).compute_for_date(date(2023, 10, 27), "U1")
rows = {r[0]: r for r in s.conn.execute("select instrument_id, rs_status, rs_rank, rs_percentile from relative_strength_snapshots").fetchall()}
print("   RS rows:", rows)
ok("13 SHORT has NULL rank/percentile", rows["SHORT"][2] is None and rows["SHORT"][3] is None)
ok("13 STALE not ranked", rows["STALE"][1] != "PASS" and rows["STALE"][2] is None)
