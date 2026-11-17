"""SnappTrip domestic flight tools: airports, search, cheapest-days calendar.

The flight API prices in Rial (currency IRR); these tools convert to Toman.
"""

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field

from .http import FLIGHT, FP, ApiError, fetch
from .registry import tool

CHANNEL = {"channel": "website"}  # the web app sends this header; without it the API answers 400
IATA = re.compile(r"^[A-Za-z]{3}$")


def _code(v: str, name: str) -> str:
    if not IATA.match(v.strip()):
        raise ApiError(f"{name} must be a 3-letter IATA code like THR or MHD (see st_flight_airports).")
    return v.strip().upper()


def _toman(v, currency: str) -> int | None:
    if v is None:
        return None
    return round(v / 10) if currency == "IRR" else round(v)


@tool("Flight airports")
async def st_flight_airports(
    query: Annotated[str | None, Field(description="Filter by Persian or English city name or IATA code")] = None,
) -> list:
    """List the cities SnappTrip offers flights from, with IATA codes and airports.

    The list is the site's popular cities; other IATA codes may still work in st_flight_search.
    """
    r = await fetch(f"{FP}/master-data/airport-cities")
    q = (query or "").strip().lower()
    out = []
    for c in r.get("cities") or []:
        text = f"{c['iataCode']} {c['cityName']} {c['cityFaName']} " + " ".join(
            f"{a['iataCode']} {a['name']} {a['faName']}" for a in c.get("airports") or []
        )
        if q in text.lower():
            out.append(
                {
                    "code": c["iataCode"],
                    "city": c["cityFaName"],
                    "en": c["cityName"],
                    "country": c["countryFaName"],
                    "airports": [
                        {"code": a["iataCode"], "name": a["faName"], "type": a["type"]} for a in c["airports"]
                    ],
                }
            )
    return out


@tool("Flight search")
async def st_flight_search(
    origin: Annotated[str, Field(description="Origin IATA code, e.g. THR")],
    destination: Annotated[str, Field(description="Destination IATA code, e.g. MHD")],
    departure: Annotated[str, Field(description="Departure date YYYY-MM-DD (Gregorian)")],
    adults: Annotated[int, Field(ge=1, le=9)] = 1,
    children: Annotated[int, Field(ge=0, le=8)] = 0,
    infants: Annotated[int, Field(ge=0, le=4)] = 0,
    sort: Annotated[Literal["cheapest", "earliest", "latest"], Field(description="Order")] = "cheapest",
    airline: Annotated[str | None, Field(description="Keep one airline by IATA code, e.g. W5")] = None,
    max_results: Annotated[int, Field(ge=1, le=50)] = 12,
) -> dict:
    """Search one-way domestic economy flights for a date, cheapest first.

    Prices are Toman for the whole party (adult_price per adult). Tickets can be bought only on snapptrip.com.
    """
    o, d = _code(origin, "origin"), _code(destination, "destination")
    try:
        day = datetime.strptime(departure, "%Y-%m-%d").date()
    except ValueError as e:
        raise ApiError(f"date must be YYYY-MM-DD, got {departure!r}.") from e
    if day < date.today():
        raise ApiError("date is in the past.")
    body = {
        "dateType": "miladi",
        "origin": o,
        "destination": d,
        "originIsCity": True,
        "destinationIsCity": True,
        "adultCount": adults,
        "childCount": children,
        "infantCount": infants,
        "departureDate": day.isoformat(),
        "cabinType": "ECONOMY",
    }
    r = await fetch(f"{FLIGHT}/dom-flights/v1/one-way/search", {"source": "searchBox"}, body=body, extra=CHANNEL)
    rows = []
    for f in r.get("airfares") or []:
        route = (f.get("routes") or [{}])[0]
        segs = route.get("segments") or []
        if not segs or (airline and f.get("airlineCode") != airline.upper()):
            continue
        p = f.get("pricing") or {}
        cur = p.get("currency", "IRR")
        adult = next((x for x in p.get("pricingDetails") or [] if x.get("passengerType") == "ADULT"), {})
        s0, s1 = segs[0], segs[-1]
        pol = (f.get("policies") or [{}])[0].get("cancellationPolicies") or []
        rows.append(
            {
                "airline": s0.get("carrierAirlineInfo", {}).get("airlineName"),
                "airline_code": f.get("airlineCode"),
                "flight": f"{s0.get('carrierAirlineCode')}{s0.get('flightNumber')}",
                "departs": s0["departureDateTime"],
                "arrives": s1["arrivalDateTime"],
                "minutes": route.get("totalJourneyDuration"),
                "stops": len(segs) - 1 or None,
                "total_price": _toman(p.get("totalPayablePrice"), cur),
                "adult_price": _toman(adult.get("payablePrice"), cur),
                "price_before_discount": _toman(p.get("totalOldPayablePrice"), cur)
                if (p.get("totalOldPayablePrice") or 0) > (p.get("totalPayablePrice") or 0)
                else None,
                "seats_left": s0.get("seatsRemaining"),
                "baggage_kg": (route.get("baggageDetail") or {}).get("weight") or None,
                "charter": s0.get("isCharter") or None,
                "aircraft": s0.get("equipment") or None,
                "refundable": f.get("refundType") == "REFUNDABLE" if f.get("refundType") else None,
                "cancel_rules": [x.get("description") for x in pol][:3] or None,
            }
        )
    rows.sort(
        key=(lambda x: x["total_price"] or 0) if sort == "cheapest" else (lambda x: x["departs"]),
        reverse=sort == "latest",
    )
    return {
        "origin": o,
        "destination": d,
        "date": day.isoformat(),
        "found": len(rows),
        "flights": rows[:max_results],
    }


@tool("Flight price calendar")
async def st_flight_prices(
    origin: Annotated[str, Field(description="Origin IATA code, e.g. THR")],
    destination: Annotated[str, Field(description="Destination IATA code, e.g. MHD")],
    days: Annotated[int, Field(ge=1, le=62, description="How many days from today")] = 30,
) -> dict:
    """Show the lowest one-way price per day for the next weeks and the cheapest days.

    Toman, starting today; days with no data are left out. Check a day with st_flight_search.
    """
    o, d = _code(origin, "origin"), _code(destination, "destination")
    # The API only accepts today's date; it returns about two months ahead.
    r = await fetch(
        f"{FLIGHT}/api/ticket-price-table/lookup",
        {"type": "calendar", "date": date.today().isoformat(), "origin": o, "destination": d},
        extra=CHANNEL,
    )
    end = (date.today() + timedelta(days=days - 1)).isoformat()
    rows = [
        {"date": x["date"], "price": round(x["price"] / 10), "best": x.get("type") == "best_price" or None}
        for x in r.get("price_table") or []
        if x.get("price", -1) > 0 and x["date"] <= end
    ]
    return {"origin": o, "destination": d, "cheapest_days": sorted(rows, key=lambda x: x["price"])[:5], "days": rows}
