"""Decode the bounded JUnit report dialect used for flake evidence."""

import io
import stat
import zipfile
import zlib
from collections import Counter
from xml.etree import ElementTree as ET

from plotroom.adapters.github.read import Row

MAX_XML = 4 * 1024 * 1024
MAX_TESTS = 5_000
MAX_FAILED_TESTS = 300
MAX_NODES = 20_000
MAX_DEPTH = 32
MAX_FIELD = 500
MAX_EXCERPT = 500
SUITE_METADATA = {"properties", "system-out", "system-err"}
RESULTS = {"failure": "failures", "error": "errors", "skipped": "skipped"}


class _BoundedTree(ET.TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = 0
        self.depth = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self.nodes += 1
        self.depth += 1
        if self.nodes > MAX_NODES or self.depth > MAX_DEPTH:
            raise ValueError("JUnit XML exceeded its structural limits")
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError("JUnit DTDs are not supported")


def _report(archive: bytes) -> ET.Element:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            files = zipped.infolist()
            if len(files) != 1:
                raise ValueError("JUnit archive must have exactly one member")
            member = files[0]
            if (
                member.orig_filename != "junit.xml"
                or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG}
                or member.flag_bits & 1
                or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or not 0 < member.file_size <= MAX_XML
            ):
                raise ValueError("JUnit archive member is unsafe")
            with zipped.open(member) as source:
                document = source.read(MAX_XML + 1)
            if len(document) != member.file_size:
                raise ValueError("JUnit archive member is incomplete")
        # The target rejects DTDs before entity resolution and limits tree growth.
        parser = ET.XMLParser(target=_BoundedTree())  # noqa: S314
        return ET.fromstring(document.decode("utf-8", errors="strict"), parser=parser)  # noqa: S314
    except (
        ET.ParseError,
        OSError,
        EOFError,
        RuntimeError,
        zipfile.BadZipFile,
        zlib.error,
    ):
        raise ValueError("JUnit archive or XML is invalid") from None


def _field(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= MAX_FIELD
        and value.strip() == value
        and all(character.isprintable() for character in value)
    )


def _case(element: ET.Element) -> tuple[tuple[str, str], ET.Element | None]:
    path = element.get("file", "")
    classname, name = element.get("classname", ""), element.get("name", "")
    test_id = f"{classname}::{name}"
    if (
        not _field(path)
        or "\\" in path
        or ":" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or not _field(classname)
        or "::" in classname
        or not _field(name)
        or len(test_id) > MAX_FIELD
    ):
        raise ValueError("JUnit testcase has no safe, stable identity")
    outcomes = [child for child in element if child.tag in RESULTS]
    if len(outcomes) > 1 or any(
        child.tag not in RESULTS and child.tag not in SUITE_METADATA for child in element
    ):
        raise ValueError("JUnit testcase has an unknown or ambiguous outcome")
    outcome = outcomes[0] if outcomes else None
    skipped = outcome is not None and outcome.tag == "skipped"
    statuses = (None, "run", "notrun") if skipped else (None, "run")
    results = (None, "completed", "suppressed") if skipped else (None, "completed")
    if element.get("status") not in statuses or element.get("result") not in results:
        raise ValueError("JUnit testcase does not establish a completed result")
    return (path, test_id), outcome


def _certify_counts(suite: ET.Element, counts: Counter[str]) -> None:
    for key in ("tests", "failures", "errors", "skipped"):
        if key in suite.attrib and suite.attrib[key] != str(counts[key]):
            raise ValueError("JUnit suite counters do not match the testcases")
    if suite.get("disabled", "0") != "0":
        raise ValueError("JUnit suite has tests without completed results")


def _collect(
    suite: ET.Element,
    cases: dict[tuple[str, str], ET.Element | None],
) -> Counter[str]:
    if suite.tag not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit report has an unknown suite")
    counts: Counter[str] = Counter()
    for child in suite:
        if child.tag == "testsuite":
            counts.update(_collect(child, cases))
        elif child.tag == "testcase" and suite.tag == "testsuite":
            identity, outcome = _case(child)
            if identity in cases or len(cases) >= MAX_TESTS:
                raise ValueError("JUnit report has duplicate or too many testcases")
            cases[identity] = outcome
            counts["tests"] += 1
            if outcome is not None:
                counts[RESULTS[outcome.tag]] += 1
        elif child.tag not in SUITE_METADATA:
            raise ValueError("JUnit suite has unexpected data")
    _certify_counts(suite, counts)
    return counts


def junit_evidence(archive: bytes, *, passed: bool) -> list[Row]:
    cases: dict[tuple[str, str], ET.Element | None] = {}
    counts = _collect(_report(archive), cases)
    failed = counts["failures"] + counts["errors"]
    if bool(failed) == passed:
        raise ValueError("JUnit report does not confirm its job result")
    if failed > MAX_FAILED_TESTS:
        raise ValueError("JUnit report has too many failed testcases")
    evidence: list[Row] = []
    for (path, test_id), outcome in cases.items():
        if passed and outcome is None:
            evidence.append({"path": path, "test_id": test_id})
        elif not passed and outcome is not None and outcome.tag != "skipped":
            excerpt = (outcome.get("message") or outcome.text or "")[:MAX_EXCERPT]
            evidence.append({"path": path, "test_id": test_id, "excerpt": excerpt})
    return evidence
