"""Shopping moments per country: festivals, gifting days and sale events that change what and how people buy.

events_in(start, end, countries) -> the moments whose buying window overlaps a campaign's flight. Used by:
  hooks     copy can ride the moment ("Diwali gifting", "Black Friday deal")
  strategy  the blueprint rationale names them
  budget    BudgetPlan.peaks: the testing reserve is spent as a budget boost on the days people buy most

ponytail: a hand-kept table through 2027 (lunar festivals move every year). Upgrade path: a holidays API
(Calendarific / Nager.Date) when the table goes stale or more countries matter.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel


class Moment(BaseModel):
    name: str
    day: date                   # the day itself (or a sale's last day)
    countries: list[str]        # ISO-2; ["*"] = everywhere
    kind: Literal["festival", "gifting", "sale"]
    lead_days: int = 7          # people shop this many days before

    @property
    def window(self) -> tuple[date, date]:
        return self.day - timedelta(days=self.lead_days), self.day


def _m(name, y, mo, d, countries, kind, lead=7):
    return Moment(name=name, day=date(y, mo, d), countries=countries, kind=kind, lead_days=lead)


IN, GULF, WEST = ["IN"], ["AE", "SA", "QA", "KW", "OM", "BH"], ["US", "GB", "CA", "AU", "NZ", "IE"]
MOMENTS: list[Moment] = [
    # everywhere
    *[_m("Valentine's Day", y, 2, 14, ["*"], "gifting", 10) for y in (2026, 2027)],
    *[_m("New Year", y, 12, 31, ["*"], "festival", 7) for y in (2026, 2027)],
    *[_m("International Women's Day", y, 3, 8, ["*"], "gifting", 5) for y in (2026, 2027)],
    _m("Black Friday", 2026, 11, 27, ["*"], "sale", 5), _m("Cyber Monday", 2026, 11, 30, ["*"], "sale", 2),
    _m("Black Friday", 2027, 11, 26, ["*"], "sale", 5), _m("Cyber Monday", 2027, 11, 29, ["*"], "sale", 2),
    *[_m("Christmas", y, 12, 25, ["*"], "gifting", 21) for y in (2026, 2027)],
    *[_m("Singles' Day 11.11", y, 11, 11, ["SG", "MY", "CN", "PH", "ID", "TH", "VN"], "sale", 3) for y in (2026, 2027)],
    # India
    *[_m("Republic Day sales", y, 1, 26, IN, "sale", 5) for y in (2026, 2027)],
    _m("Holi", 2026, 3, 4, IN, "festival", 7), _m("Holi", 2027, 3, 22, IN, "festival", 7),
    *[_m("Independence Day sales", y, 8, 15, IN, "sale", 5) for y in (2026, 2027)],
    _m("Raksha Bandhan", 2026, 8, 28, IN, "gifting", 12), _m("Raksha Bandhan", 2027, 8, 17, IN, "gifting", 12),
    _m("Ganesh Chaturthi", 2026, 9, 14, IN, "festival", 7), _m("Ganesh Chaturthi", 2027, 9, 4, IN, "festival", 7),
    _m("Navratri", 2026, 10, 19, IN, "festival", 9), _m("Navratri", 2027, 10, 8, IN, "festival", 9),
    _m("Dussehra", 2026, 10, 20, IN, "festival", 5), _m("Dussehra", 2027, 10, 9, IN, "festival", 5),
    _m("Festive sale season (marketplaces)", 2026, 10, 15, IN, "sale", 25),
    _m("Festive sale season (marketplaces)", 2027, 10, 5, IN, "sale", 25),
    _m("Dhanteras", 2026, 11, 6, IN, "sale", 4), _m("Dhanteras", 2027, 10, 27, IN, "sale", 4),
    _m("Diwali", 2026, 11, 8, IN, "gifting", 21), _m("Diwali", 2027, 10, 29, IN, "gifting", 21),
    _m("Wedding season", 2026, 12, 15, IN, "gifting", 40), _m("Wedding season", 2027, 12, 10, IN, "gifting", 40),
    # Gulf + Muslim-majority markets (also India for Eid)
    _m("Ramadan", 2026, 3, 19, GULF + ["ID", "MY", "PK", "EG", "TR"], "festival", 29),
    _m("Ramadan", 2027, 3, 9, GULF + ["ID", "MY", "PK", "EG", "TR"], "festival", 29),
    _m("Eid al-Fitr", 2026, 3, 20, GULF + IN + ["ID", "MY", "PK", "EG", "TR"], "gifting", 10),
    _m("Eid al-Fitr", 2027, 3, 10, GULF + IN + ["ID", "MY", "PK", "EG", "TR"], "gifting", 10),
    _m("Eid al-Adha", 2026, 5, 27, GULF + IN + ["ID", "MY", "PK", "EG", "TR"], "festival", 7),
    _m("Eid al-Adha", 2027, 5, 16, GULF + IN + ["ID", "MY", "PK", "EG", "TR"], "festival", 7),
    _m("White Friday", 2026, 11, 27, GULF, "sale", 5), _m("White Friday", 2027, 11, 26, GULF, "sale", 5),
    # English-speaking West
    _m("Mother's Day", 2026, 5, 10, ["US", "CA", "AU", "NZ", "IN"], "gifting", 14),
    _m("Mother's Day", 2027, 5, 9, ["US", "CA", "AU", "NZ", "IN"], "gifting", 14),
    _m("Mothering Sunday", 2026, 3, 15, ["GB", "IE"], "gifting", 14),
    _m("Mothering Sunday", 2027, 3, 7, ["GB", "IE"], "gifting", 14),
    _m("Father's Day", 2026, 6, 21, ["US", "CA", "GB", "IN"], "gifting", 10),
    _m("Father's Day", 2027, 6, 20, ["US", "CA", "GB", "IN"], "gifting", 10),
    _m("Back to school", 2026, 8, 25, ["US", "CA"], "sale", 30), _m("Back to school", 2027, 8, 24, ["US", "CA"], "sale", 30),
    _m("Halloween", 2026, 10, 31, ["US", "CA", "GB", "IE"], "festival", 14),
    _m("Halloween", 2027, 10, 31, ["US", "CA", "GB", "IE"], "festival", 14),
    _m("Thanksgiving", 2026, 11, 26, ["US"], "festival", 7), _m("Thanksgiving", 2027, 11, 25, ["US"], "festival", 7),
    *[_m("Boxing Day sales", y, 12, 26, ["GB", "CA", "AU", "NZ", "IE"], "sale", 2) for y in (2026, 2027)],
    _m("Amazon Prime Day", 2026, 7, 15, ["US", "GB", "CA", "IN", "DE", "FR", "ES", "IT", "JP", "AU"], "sale", 3),
    _m("Amazon Prime Day", 2027, 7, 14, ["US", "GB", "CA", "IN", "DE", "FR", "ES", "IT", "JP", "AU"], "sale", 3),
]


def events_in(start: date, end: date, countries: list[str]) -> list[Moment]:
    """Moments whose buying window overlaps [start, end] in any of the countries, soonest first."""
    want = {c.upper() for c in countries}
    out = [m for m in MOMENTS
           if ("*" in m.countries or want & set(m.countries)) and m.window[0] <= end and m.day >= start]
    return sorted(out, key=lambda m: m.day)


def describe(moments: list[Moment]) -> str:
    return "; ".join(f"{m.name} ({m.day:%d %b}, people shop from {m.window[0]:%d %b})" for m in moments)
