import json

import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

TOOLS = {
    "st_hotel_places",
    "st_hotel_search",
    "st_hotel_filters",
    "st_hotel_detail",
    "st_hotel_rooms",
    "st_hotel_calendar",
    "st_hotel_reviews",
    "st_flight_airports",
    "st_flight_search",
    "st_flight_prices",
}


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name
        assert "title" not in str(t.input_schema.get("properties", {}).values()), t.name


async def test_places_splits_cities_and_hotels(client, api):
    api["/hotel/api/v2/search-text"] = fixture("places.json")
    r = (await client.call_tool("st_hotel_places", {"query": "شیراز"})).structured_content
    assert r["cities"][0] == {"city_id": 6640, "name": "شیراز", "state": "فارس", "hotels": 128}
    assert r["hotels"][1]["hotel_url"] == "شیراز/هتل-پرسپولیس" and r["hotels"][1]["hotel_id"] == 267


async def test_search_resolves_city_and_filters(client, api):
    api["/hotel/api/v2/search-text"] = fixture("places.json")
    api["/hotel/api/v2/search-city"] = fixture("search.json")
    r = (
        await client.call_tool(
            "st_hotel_search",
            {
                "city": "شیراز",
                "check_in": "2026-11-25",
                "check_out": "2026-11-27",
                "stars": [4, 5],
                "max_price": 9000000,
            },
        )
    ).structured_content
    q = dict(api.calls[-1].url.params)
    assert q["city_id"] == "6640" and q["stars"] == "4,5" and q["price_to"] == "9000000" and q["token"] == "Jek"
    assert q["date_from"] == "2026-11-25" and q["order_by"] == "selling"
    assert r["nights"] == 2 and r["total"] == 112 and r["pages"] >= 1
    h = r["hotels"][0]
    assert h["total_price"] and h["per_night"] == round(h["total_price"] / 2) and h["hotel_url"].count("/") == 1
    assert isinstance(h["rating"], float)


async def test_search_rejects_bad_dates(client, api):
    r = await client.call_tool("st_hotel_search", {"city": "6640", "check_in": "2026-11-27", "check_out": "2026-11-25"})
    assert r.is_error
    r = await client.call_tool("st_hotel_search", {"city": "6640", "check_in": "25/11/2026"})
    assert r.is_error


async def test_numeric_city_skips_autocomplete(client, api):
    api["/hotel/api/v2/search-city"] = fixture("search.json")
    await client.call_tool("st_hotel_search", {"city": "6640", "sort": "cheapest", "facility_ids": [393]})
    assert [c.url.path for c in api.calls] == ["/hotel/api/v2/search-city"]
    q = dict(api.calls[0].url.params)
    assert q["order_by"] == "min_sum_local_price_off" and q["facilities"] == "393"


async def test_filters(client, api):
    api["/hotel/api/v2/cities/6640/filters"] = fixture("filters.json")
    r = (await client.call_tool("st_hotel_filters", {"city": "6640"})).structured_content
    assert r["facilities"]["393"] == "اینترنت رایگان و نامحدود" and r["types"]["Hotel"] == "هتل"


async def test_detail_with_rating(client, api):
    api["/hotel/api/v2/hotels/slug/شیراز/هتل-پرسپولیس"] = fixture("info.json")
    api["/hotel/review/v2/ratings/267"] = fixture("ratings.json")
    r = (await client.call_tool("st_hotel_detail", {"hotel_url": "شیراز/هتل-پرسپولیس"})).structured_content
    assert r["id"] == 267 and r["stars"] == 5 and r["location"]["lat"] and r["check_in"] == "14:00"
    assert r["rating"]["overall"] == 4.3 and r["rating"]["count"] == 363
    assert api.calls[-1].headers["product"] == "hotel-domestic"
    assert (await client.call_tool("st_hotel_detail", {"hotel_url": "نادرست"})).is_error


async def test_rooms_sorted_by_price(client, api):
    api["/hotel/api/v2/hotels/94/rooms"] = fixture("rooms.json")
    r = (
        await client.call_tool("st_hotel_rooms", {"hotel_id": 94, "check_in": "2026-11-25", "check_out": "2026-11-27"})
    ).structured_content
    prices = [x["total_price"] for x in r["rooms"]]
    assert prices == sorted(prices) and r["rooms"][0]["room_id"] == 27769

    sold = {**api["/hotel/api/v2/hotels/94/rooms"][0], "room_id": 1, "prices": {"local_price": 0, "local_price_off": 0}}
    api["/hotel/api/v2/hotels/94/rooms"] = [sold, *api["/hotel/api/v2/hotels/94/rooms"]]
    r = (
        await client.call_tool("st_hotel_rooms", {"hotel_id": 94, "check_in": "2026-11-25", "check_out": "2026-11-28"})
    ).structured_content
    assert r["rooms"][0]["room_id"] == 27769 and not r["rooms"][-1].get("total_price")
    assert r["rooms"][0]["nightly"] == [2489000, 2489000]


async def test_calendar_cheapest_open_nights(client, api):
    api["/hotel/api/v2/hotels/94/calendar/27769"] = fixture("calendar.json")
    r = (
        await client.call_tool(
            "st_hotel_calendar", {"hotel_id": 94, "room_id": 27769, "from_date": "2026-11-16", "days": 30}
        )
    ).structured_content
    assert len(r["days"]) == 6 and r["days"][0]["rooms_left"] == 0
    assert all(x["rooms_left"] for x in r["cheapest_nights"])


async def test_reviews(client, api):
    api["/hotel/review/v2/reviews/94"] = fixture("reviews.json")
    r = (await client.call_tool("st_hotel_reviews", {"hotel_id": 94})).structured_content
    assert r["total"] == 363 and len(r["reviews"]) == 3 and r["reviews"][0]["rating"] == 4.3
    assert dict(api.calls[-1].url.params) == {"page": "1"}


async def test_airports_filter(client, api):
    api["/master-data/airport-cities"] = fixture("airports.json")
    r = (await client.call_tool("st_flight_airports", {"query": "thr"})).structured_content
    assert [c["code"] for c in r] == ["THR"]


async def test_flight_search_posts_and_converts_rial(client, api):
    api["/dom-flights/v1/one-way/search"] = fixture("flights.json")
    r = (
        await client.call_tool(
            "st_flight_search", {"origin": "thr", "destination": "MHD", "departure": "2099-01-01", "adults": 2}
        )
    ).structured_content
    req = api.calls[-1]
    assert req.method == "POST" and req.headers["channel"] == "website"
    body = json.loads(req.content)
    assert body["origin"] == "THR" and body["adultCount"] == 2 and body["departureDate"] == "2099-01-01"
    f = r["flights"][0]
    raw = fixture("flights.json")["airfares"]
    assert f["total_price"] == round(min(a["pricing"]["totalPayablePrice"] for a in raw) / 10)
    assert f["flight"] and f["departs"] and f["airline"]


async def test_flight_search_validates(client, api):
    assert (
        await client.call_tool(
            "st_flight_search", {"origin": "Tehran", "destination": "MHD", "departure": "2099-01-01"}
        )
    ).is_error
    assert (
        await client.call_tool("st_flight_search", {"origin": "THR", "destination": "MHD", "departure": "2001-01-01"})
    ).is_error


async def test_flight_prices(client, api):
    api["/api/ticket-price-table/lookup"] = fixture("pricetable.json")
    r = (
        await client.call_tool("st_flight_prices", {"origin": "THR", "destination": "MHD", "days": 62})
    ).structured_content
    raw = fixture("pricetable.json")["price_table"]
    assert r["cheapest_days"][0]["price"] == round(min(x["price"] for x in raw if x["price"] > 0) / 10)
    assert api.calls[-1].headers["channel"] == "website" and api.calls[-1].url.params["type"] == "calendar"
