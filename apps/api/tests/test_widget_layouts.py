"""Widget layouts (docs/27 Phase 1): layout / corner radius / density are additive.

An agent that never touches them must read back `null` for all three, a legacy payload must
stay valid, and bad values must be refused with the existing typed error.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.public import schemas, service

LAYOUTS = (
    "classic",
    "glass",
    "hairline",
    "paper",
    "pebble",
    "console",
    "concierge",
    "command",
    "drawer",
    "block",
)


async def _headers(client: AsyncClient, email: str) -> dict[str, str]:
    signup = await client.post("/v1/auth/signup", json={"email": email, "password": "password123"})
    token = signup.json()["access_token"]
    org = await client.post("/v1/orgs", json={"name": "LayoutCo"}, headers={"Authorization": f"Bearer {token}"})
    return {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}


async def _published_agent(client: AsyncClient, headers: dict[str, str]) -> dict:
    agent = (await client.post("/v1/agents", json={"name": "Layout Bot"}, headers=headers)).json()
    await client.post(f"/v1/agents/{agent['id']}/versions/{agent['draft_version']}/publish", headers=headers)
    return agent


def test_layout_list_matches_the_designs_in_the_spec() -> None:
    assert set(schemas.WIDGET_LAYOUTS) == set(LAYOUTS)


async def test_untouched_agent_reports_null_defaults(client: AsyncClient) -> None:
    headers = await _headers(client, "wl1@example.com")
    agent = await _published_agent(client, headers)
    theme = (await client.get(f"/v1/public/agents/{agent['public_key']}/config")).json()["theme"]
    assert theme["widget_layout"] is None
    assert theme["corner_radius"] is None
    assert theme["density"] is None
    # Everything that was there before is still there, unchanged.
    assert theme["widget_style"] == "solid"
    assert theme["font_family"] == "system"
    assert theme["input_bar_buttons"] == ["attachment"]
    assert theme["floating_button_style"] is None


async def test_legacy_payload_without_the_new_keys_is_still_valid(client: AsyncClient) -> None:
    headers = await _headers(client, "wl2@example.com")
    agent = await _published_agent(client, headers)
    r = await client.patch(
        f"/v1/agents/{agent['id']}/widget-config",
        json={
            "primaryColor": "#123456",
            "widgetStyle": "transparent",
            "fontFamily": "inter",
            "floatingButtonStyle": "pulse-ring",
            "inputBarButtons": ["attachment", "emoji"],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    theme = (await client.get(f"/v1/public/agents/{agent['public_key']}/config")).json()["theme"]
    assert theme["primary_color"] == "#123456"
    assert (theme["widget_layout"], theme["corner_radius"], theme["density"]) == (None, None, None)


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_every_layout_round_trips_to_the_public_config(client: AsyncClient, layout: str) -> None:
    headers = await _headers(client, f"wl-{layout}@example.com")
    agent = await _published_agent(client, headers)
    r = await client.patch(
        f"/v1/agents/{agent['id']}/widget-config",
        json={"widgetLayout": layout, "cornerRadius": 28, "density": "compact"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    theme = (await client.get(f"/v1/public/agents/{agent['public_key']}/config")).json()["theme"]
    assert theme["widget_layout"] == layout
    assert theme["corner_radius"] == 28
    assert theme["density"] == "compact"


@pytest.mark.parametrize(
    "payload",
    [
        {"widgetLayout": "neon"},
        {"widgetLayout": "Classic"},  # case-sensitive: the enum is the contract
        {"cornerRadius": 29},
        {"cornerRadius": -1},
        {"cornerRadius": "round"},
        {"density": "huge"},
    ],
)
async def test_invalid_layout_values_are_rejected_with_the_typed_error(client: AsyncClient, payload: dict) -> None:
    headers = await _headers(client, f"wl-bad-{abs(hash(str(payload)))}@example.com")
    agent = await _published_agent(client, headers)
    r = await client.patch(f"/v1/agents/{agent['id']}/widget-config", json=payload, headers=headers)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "widget.invalid_config"


async def test_null_clears_a_layout_without_touching_siblings(client: AsyncClient) -> None:
    """The designer sends explicit nulls for 'Original'; merge-on-write must store them."""
    headers = await _headers(client, "wl3@example.com")
    agent = await _published_agent(client, headers)
    url = f"/v1/agents/{agent['id']}/widget-config"
    seed = {"widgetLayout": "glass", "cornerRadius": 12, "density": "roomy", "primaryColor": "#ABCDEF"}
    await client.patch(url, json=seed, headers=headers)
    r = await client.patch(url, json={"widgetLayout": None, "cornerRadius": None, "density": None}, headers=headers)
    assert r.status_code == 200, r.text
    stored = (await client.get(url, headers=headers)).json()
    assert stored["primaryColor"] == "#ABCDEF"
    theme = (await client.get(f"/v1/public/agents/{agent['public_key']}/config")).json()["theme"]
    assert (theme["widget_layout"], theme["corner_radius"], theme["density"]) == (None, None, None)


def test_a_stored_value_the_server_no_longer_knows_degrades_to_the_original_look() -> None:
    """A row is JSONB and unversioned. Junk must never reach the widget as a layout name."""
    theme = service._theme_from({"widgetLayout": "retired-layout", "cornerRadius": True, "density": "huge"})
    assert theme.widget_layout is None
    assert theme.corner_radius is None  # bool is an int subclass; `true` must not become radius 1
    assert theme.density is None
    ok = service._theme_from({"widgetLayout": "paper", "cornerRadius": 0, "density": "roomy"})
    assert (ok.widget_layout, ok.corner_radius, ok.density) == ("paper", 0, "roomy")
