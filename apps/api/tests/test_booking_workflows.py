"""The three booking workflows in infra/n8n/workflows/ (ADR-120): shape, hygiene, n8n-side validation, installer.

What cannot be checked here (a live Google account) is isolated behind a node that ships disabled; everything else
was also run end to end against a real n8n 2.43 and this API (scripts/test_booking_tools.py).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.integrations.n8n_signature import unverified_reason
from app.modules.bookings.schemas import TOOL_SPECS
from scripts import install_booking_tools as installer

N8N_DIR = Path(__file__).resolve().parents[3] / "infra" / "n8n"
WF_DIR = N8N_DIR / "workflows"
FILES = sorted(WF_DIR.glob("*.json"))
ALLOWED_ENV = {"AUTOMATION_REPORT_SECRET", "N8N_WEBHOOK_SIGNING_SECRET", "VICERO_API_INTERNAL_URL"}
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_there_is_one_workflow_per_tool() -> None:
    assert [p.stem for p in FILES] == sorted(s["slug"] for s in TOOL_SPECS)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_signature_lint_passes_so_the_workflow_can_be_bound(path: Path) -> None:
    assert unverified_reason(_load(path)) is None


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_no_secret_and_no_credential_is_stored_in_the_json(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    wf = json.loads(raw)
    assert all("credentials" not in n for n in wf["nodes"]), "a credential reference is bound per organisation in n8n"
    used = set(re.findall(r"\$env\.([A-Z_]+)|\$env\['([A-Z_]+)'\]", raw))
    assert {a or b for a, b in used} <= ALLOWED_ENV
    assert not re.search(r"(sk-|gsk_|EAA[A-Za-z0-9]{20,}|ya29\.)", raw)
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names))


def test_the_google_calendar_mirror_ships_disabled_and_cannot_fail_the_booking() -> None:
    wf = _load(WF_DIR / "book-appointment.json")
    node = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.googleCalendar")
    assert node["disabled"] is True
    assert node["onError"] == "continueRegularOutput"
    # Both outcomes of the booking step reach the answer, so the model always gets Vicero's reply.
    conns = wf["connections"]
    assert "Respond" in {c["node"] for b in conns["New booking?"]["main"] for c in b}
    assert "Respond" in {c["node"] for b in conns["Event created?"]["main"] for c in b}


def test_the_committed_json_is_what_the_builder_produces() -> None:
    spec = importlib.util.spec_from_file_location("build_booking_workflows", N8N_DIR / "build_booking_workflows.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.build()
    for slug, wf in built.items():
        assert json.loads((WF_DIR / f"{slug}.json").read_text(encoding="utf-8")) == json.loads(json.dumps(wf)), slug


# ── the validation that runs inside n8n, executed for real under Node ─────────────────

HARNESS = r"""
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
let input = '';
process.stdin.on('data', (d) => (input += d));
process.stdin.on('end', async () => {
  const { code, bodies } = JSON.parse(input);
  const fn = new AsyncFunction('$input', code);
  const out = [];
  for (const body of bodies) {
    const res = await fn({ first: () => ({ json: { body } }) });
    out.push(res[0].json);
  }
  console.log(JSON.stringify(out));
});
"""

RUN = {"run_id": "0e1d0c9e-6f0e-4b43-9a3c-3b6a4d2a1f10", "callback_token": "a" * 64}


def _prepare(slug: str, args_list: list[Any]) -> list[dict[str, Any]]:
    code = next(n for n in _load(WF_DIR / f"{slug}.json")["nodes"] if n["name"] == "Prepare request")["parameters"][
        "jsCode"
    ]
    bodies = [{**RUN, "args": a} for a in args_list]
    run = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps({"code": code, "bodies": bodies}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    return list(json.loads(run.stdout))


GOOD_BOOK = {"name": "Asha K", "contact": "asha@example.com", "start_iso": "2026-10-20T15:00:00+05:30", "notes": "demo"}


@needs_node
def test_book_validation_in_n8n_accepts_good_input_and_forwards_only_known_fields() -> None:
    out = _prepare("book-appointment", [{**GOOD_BOOK, "evil": "x", "run_id": "other"}])[0]
    assert out["valid"] is True and out["errors"] == {}
    sent = json.loads(out["body"])
    assert sent["run_id"] == RUN["run_id"] and sent["callback_token"] == RUN["callback_token"]
    assert sent["args"] == {
        "name": "Asha K",
        "contact": "asha@example.com",
        "start_iso": "2026-10-20T15:00:00+05:30",
        "notes": "demo",
    }
    assert out["ts"].isdigit()


@needs_node
@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "x" * 121}, "name"),
        ({"contact": "not-a-contact"}, "contact"),
        ({"contact": "12"}, "contact"),
        ({"start_iso": "garbage"}, "start_iso"),
        ({"start_iso": ""}, "start_iso"),
        ({"notes": "n" * 501}, "notes"),
    ],
)
def test_book_validation_in_n8n_rejects_bad_input(override: dict[str, str], field: str) -> None:
    out = _prepare("book-appointment", [{**GOOD_BOOK, **override}])[0]
    assert out["valid"] is False and field in out["errors"]


@needs_node
def test_book_validation_in_n8n_accepts_phone_numbers_and_strips_control_characters() -> None:
    out = _prepare("book-appointment", [{**GOOD_BOOK, "contact": "+91 98765-43210", "name": "A\u0000sha"}])[0]
    assert out["valid"] is True
    assert json.loads(out["body"])["args"]["name"] == "Asha"


@needs_node
def test_a_call_without_run_context_is_invalid() -> None:
    code = next(n for n in _load(WF_DIR / "get-customer.json")["nodes"] if n["name"] == "Prepare request")[
        "parameters"
    ]["jsCode"]
    run = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps({"code": code, "bodies": [{"args": {"contact": "a@b.co"}}, {"run_id": 5}, {}]}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    results = json.loads(run.stdout)
    assert all(r["valid"] is False and "run" in r["errors"] for r in results)


@needs_node
def test_availability_and_customer_validation_in_n8n() -> None:
    ok, bad = _prepare("check-availability", [{"date": "2026-10-20"}, {"date": "tomorrow"}])
    assert ok["valid"] and json.loads(ok["body"])["args"] == {"date": "2026-10-20"}
    assert not bad["valid"] and "date" in bad["errors"]
    fine, empty = _prepare("get-customer", [{"contact": "a@b.co"}, {"contact": "  "}])
    assert fine["valid"] and json.loads(fine["body"])["args"] == {"contact": "a@b.co"}
    assert not empty["valid"] and "contact" in empty["errors"]


# ── the installer's pure part ─────────────────────────────────────────────────────────


def test_localize_names_the_workflow_for_the_org_and_gives_it_its_own_webhook_path() -> None:
    template = _load(WF_DIR / "book-appointment.json")
    snapshot = json.dumps(template, sort_keys=True)
    org = uuid.uuid4()
    wf = installer.localize(
        template, org_id=org, org_slug="acme", tool="book_appointment", slug="book-appointment", error_wf_id="ERR1"
    )
    assert wf["name"] == f"ORG-{org} | Booking | book_appointment"  # the prefix routes failures to the org
    assert installer.webhook_path(wf) == "acme-book-appointment"
    hook = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["webhookId"] == "acme-book-appointment"
    assert wf["settings"]["errorWorkflow"] == "ERR1" and wf["settings"]["executionOrder"] == "v1"
    assert json.dumps(template, sort_keys=True) == snapshot  # the template is untouched
    assert unverified_reason(wf) is None  # still passes the bind-time signature lint


def test_two_orgs_never_share_a_webhook_path() -> None:
    template = _load(WF_DIR / "get-customer.json")
    paths = {
        installer.webhook_path(
            installer.localize(
                template, org_id=uuid.uuid4(), org_slug=slug, tool="get_customer", slug="get-customer", error_wf_id=None
            )
        )
        for slug in ("acme", "globex")
    }
    assert len(paths) == 2


def test_localize_without_an_error_workflow_leaves_settings_alone() -> None:
    wf = installer.localize(
        _load(WF_DIR / "check-availability.json"),
        org_id=uuid.uuid4(),
        org_slug="acme",
        tool="check_availability",
        slug="check-availability",
        error_wf_id=None,
    )
    assert "errorWorkflow" not in wf["settings"]
