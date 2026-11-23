"""`sn ci-link`: is a service offering linked to a CI?  (against the fake CMDB)"""

import json

import pytest
from click.testing import CliRunner

from fake_servicenow import CI
from sncli.cli import main


def run(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


@pytest.fixture
def signed_in(fake, browser_session):
    assert run("configure", "--instance", fake.url).exit_code == 0
    cookie = "; ".join(f"{c.name}={c.value}" for c in browser_session.cookies.jar)
    assert run("login", "--method", "cookie", "--cookie", cookie).exit_code == 0
    return fake


def link(*args):
    res = CliRunner().invoke(main, ["ci-link", *args, "--format", "json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def test_indirect_link_through_relationships(signed_in):
    r = link("Email - Standard", "SRV-MAIL-01", "--ci-table", "cmdb_ci_win_server")
    assert r["linked"] and r["link"] == "indirect" and r["depth"] == 3
    assert r["chain"] == ["Email - Standard", "Exchange Prod", "Exchange 2019", "SRV-MAIL-01"]
    assert [s["type"] for s in r["steps"]] == ["Depends on::Used by", "Depends on::Used by", "Runs on::Runs"]
    assert r["steps"][-1]["child"]["sys_class_name"] == "cmdb_ci_win_server"
    assert r["offering"]["parent"] == "Email" and r["offering"]["_url"].startswith(signed_in.url)


def test_link_through_service_map(signed_in):
    r = link("Email - Standard", "MAILDB")
    assert r["linked"] and r["chain"] == ["Email - Standard", "Exchange Prod", "MAILDB"]
    assert r["steps"][-1]["source"] == "svc_ci_assoc"
    assert link("Email - Standard", "MAILDB", "--no-service-map")["linked"] is False


def test_offering_parent_and_direct_link(signed_in):
    r = link("Email - Standard", "Email")
    assert r["linked"] and r["link"] == "offering-parent"
    r = link("Email - Standard", "Exchange Prod")
    assert r["linked"] and r["link"] == "direct" and r["depth"] == 1


def test_not_linked_and_direction(signed_in):
    r = link("Email - Premium", CI["mail01"])
    assert r["linked"] is False and r["search"]["exhausted"] is True
    # Payroll -> SRV-PAY-01 <- SRV-MAIL-01 ("Connects to"): only an undirected search finds it.
    assert link("Payroll", CI["mail01"])["linked"] is False
    r = link("Payroll", CI["mail01"], "--direction", "any")
    assert r["linked"] and r["chain"] == ["Payroll", "SRV-PAY-01", "SRV-MAIL-01"]
    assert link("Payroll", CI["mail01"], "--direction", "any", "--exclude-type", "Connects to")["linked"] is False


def test_depth_limit_and_excluded_types(signed_in):
    r = link("Email - Standard", CI["mail01"], "--depth", "2")
    assert r["linked"] is False and r["search"]["exhausted"] is False
    assert link("Email - Standard", CI["mail01"], "--exclude-type", "Runs on")["linked"] is False


def test_ambiguous_or_unknown_names_are_errors(signed_in):
    res = CliRunner().invoke(main, ["ci-link", "Email", "MAILDB"])  # Email - Standard / Email - Premium
    assert res.exit_code == 1 and "Several offerings" in res.output and "Email - Premium" in res.output
    res = CliRunner().invoke(main, ["ci-link", "Email - Standard", "SRV-MAIL-01"])  # server + application
    assert res.exit_code == 1 and "Several CIs" in res.output and "--ci-table" in res.output
    res = CliRunner().invoke(main, ["ci-link", "Nope", "MAILDB"])
    assert res.exit_code == 1 and "No offering matching 'Nope'" in res.output


def test_human_output(signed_in):
    res = run("ci-link", "Email - Standard", "MAILDB")
    assert res.exit_code == 0
    assert "LINKED (indirect, 2 relationship(s))" in res.output
    assert "Exchange Prod [cmdb_ci_service_auto]  --service map (contains CI)-->  MAILDB" in res.output


def test_batched_queries_are_chunked(signed_in, monkeypatch):
    import sncli.cmdb
    monkeypatch.setattr(sncli.cmdb, "CHUNK", 1)  # one sys_id per IN clause
    r = link("Payroll", CI["mail01"], "--direction", "any")
    assert r["linked"] and r["depth"] == 2
