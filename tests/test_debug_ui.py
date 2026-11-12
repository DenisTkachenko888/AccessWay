"""Exercise the shipped HTML renderer with Node's standard library (no npm deps)."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.engine import extract_evidence, score


PAGE = Path(__file__).resolve().parents[1] / "debug.html"


class RenderedFacts(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.facts = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if tag == "div" and "fact" in classes:
            self.current = {"classes": classes, "text": ""}
            self.facts.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "div":
            self.current = None


def render(report):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to exercise the browser renderer")
    script = r"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const page = fs.readFileSync(process.argv[1], 'utf8');
        const elements = new Map();
        const document = {getElementById(id) {
            if (!elements.has(id)) elements.set(id, {addEventListener() {}});
            return elements.get(id);
        }};
        const context = vm.createContext({window: {}, document});
        vm.runInContext(page.match(/<script>([\s\S]*?)<\/script>/)[1], context);
        context.reports = [JSON.parse(fs.readFileSync(0, 'utf8'))];
        vm.runInContext('renderReports(reports)', context);
        process.stdout.write(elements.get('results').innerHTML);
    """
    return subprocess.run(
        [node, "-e", script, str(PAGE)], input=json.dumps(report),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=10,
    ).stdout


@pytest.fixture
def mixed_report():
    evidence = extract_evidence({
        "wheelchair": "yes", "barrier": "yes", "toilets:wheelchair": "no",
        "ramp:wheelchair": "yes",
    })
    return {
        "name": "Mixed claims", "category": "museum",
        "evidence": [e.model_dump(mode="json") for e in evidence],
        "scoring": score(evidence).model_dump(mode="json"),
    }


def test_all_source_claims_are_visible_and_neutral(mixed_report):
    html = render(mixed_report)
    facts = RenderedFacts(html).facts
    reasons = mixed_report["scoring"]["reasons"]
    assert len(reasons) > 2  # Regression: the old summary truncated the claims.
    for reason in reasons:
        matches = [f for f in facts if reason["text"] in f["text"]]
        assert len(matches) == 1
        assert matches[0]["classes"] == ["fact"]
        assert "✓" not in matches[0]["text"]
    assert "barrier_present=yes" in html
    assert "accessible_toilet=no" in html
    assert ".fact.good" not in PAGE.read_text(encoding="utf-8")
    present = [item for item in mixed_report["evidence"] if item["present"]]
    assert html.count("<li>") == len(present)
    for value in {item["raw_value"] for item in present}:
        assert html.count(f"</strong>: {value} ") == sum(
            item["raw_value"] == value for item in present
        )


def test_risks_and_unknowns_are_not_truncated(mixed_report):
    mixed_report["scoring"]["risks"] = [f"Risk {i}" for i in range(4)]
    mixed_report["scoring"]["unknowns"] = [f"missing{i}" for i in range(5)]
    facts = RenderedFacts(render(mixed_report)).facts
    for risk in mixed_report["scoring"]["risks"]:
        assert any(risk in f["text"] and "warn" in f["classes"] for f in facts)
    for unknown in mixed_report["scoring"]["unknowns"]:
        assert any(unknown in f["text"] and "unknown-fact" in f["classes"] for f in facts)


def test_source_claim_text_is_still_escaped(mixed_report):
    hostile = '<img src=x onerror="alert(1)"> & barrier=yes'
    mixed_report["scoring"]["reasons"][0]["text"] = hostile
    html = render(mixed_report)
    assert "<img" not in html
    assert "&lt;img" in html
    assert any(hostile in f["text"] for f in RenderedFacts(html).facts)
