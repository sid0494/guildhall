"""Gank Report: post-match reports on the Dota 2 skills a player is practising.

Data comes from the OpenDota API. A match is fetched once, waiting for
OpenDota to parse it, and its raw response archived; each linked account in it
is then analysed from that archive (docs/adr/0043).
"""

from typing import Any

from guildhall.contract import JobType, RateLimitPolicy, SourcePlugin
from guildhall.plugins.gank_report.tables import metadata


class GankReportPlugin(SourcePlugin):
    name = "gank_report"
    # OpenDota allows 60 calls a minute and 3,000 a day per IP address without
    # a key; the headroom absorbs window mismatch and calls made by hand
    # (docs/adr/0048).
    rate_limit = RateLimitPolicy(per_minute=50, per_day=2_000)
    tables = metadata

    def job_types(self) -> list[JobType[Any]]:
        return []
