from datetime import date, timedelta

import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]

CHECK_IN = (date.today() + timedelta(days=14)).isoformat()
CHECK_OUT = (date.today() + timedelta(days=16)).isoformat()


async def test_live_hotel_flow(client):
    r = (await client.call_tool("st_hotel_places", {"query": "شیراز"})).structured_content
    assert r["cities"][0]["city_id"] == 6640 and r["hotels"]
    r = (
        await client.call_tool(
            "st_hotel_search", {"city": "شیراز", "check_in": CHECK_IN, "check_out": CHECK_OUT, "sort": "cheapest"}
        )
    ).structured_content
    assert r["total"] > 0 and r["nights"] == 2
    prices = [h["total_price"] for h in r["hotels"] if h["total_price"]]
    assert prices == sorted(prices)
    h = r["hotels"][0]
    d = (await client.call_tool("st_hotel_detail", {"hotel_url": h["hotel_url"]})).structured_content
    assert d["id"] == h["id"] and d["address"] and d["location"]
    rooms = (
        await client.call_tool("st_hotel_rooms", {"hotel_id": h["id"], "check_in": CHECK_IN, "check_out": CHECK_OUT})
    ).structured_content["rooms"]
    assert rooms and rooms[0]["total_price"] and len(rooms[0]["nightly"]) == 2
    cal = (
        await client.call_tool("st_hotel_calendar", {"hotel_id": h["id"], "room_id": rooms[0]["room_id"]})
    ).structured_content
    assert cal["days"]


async def test_live_reviews_and_filters(client):
    r = (await client.call_tool("st_hotel_reviews", {"hotel_id": 94})).structured_content
    assert r["total"] > 0 and r["reviews"][0]["date"]
    f = (await client.call_tool("st_hotel_filters", {"city": "شیراز", "check_in": CHECK_IN})).structured_content
    assert f["facilities"] and f["types"]


async def test_live_flights(client):
    a = (await client.call_tool("st_flight_airports", {"query": "مشهد"})).structured_content
    assert "MHD" in str(a)
    when = (date.today() + timedelta(days=10)).isoformat()
    r = (
        await client.call_tool("st_flight_search", {"origin": "THR", "destination": "MHD", "departure": when})
    ).structured_content
    assert r["found"] > 0 and r["flights"][0]["total_price"] > 100_000
    p = (await client.call_tool("st_flight_prices", {"origin": "THR", "destination": "MHD"})).structured_content
    assert p["cheapest_days"]
