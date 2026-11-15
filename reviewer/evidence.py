"""Evidence lookup: which studies a proposal names, and whether the project already holds each one. No model is involved.

The identifiers are the list in the submission marker, plus PubMed, PMC, DOI and trial-registry identifiers written in
the Draft. Each is looked up in the project's extracted evidence as it stood at the context commit, and in the wider
corpus list when that file exists at that commit.

Where the extracted evidence lives has changed over time, so the settings list the places in order and the first one
found at the commit is used. The record says which. The evidence files are large and are never sent to a model: only
the identifiers in them are kept.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .context import ContextUnavailable, resolve
from .github import GitHubError

MAX_SHOWN = 60
KINDS = ("pmid", "pmcid", "doi", "trial")
LABELS = {"pmid": "PubMed", "pmcid": "PMC", "doi": "DOI", "trial": "Trial"}

_IN_DRAFT = [
    ("pmid", re.compile(r"\bPMID[\s:#]{0,12}(\d{5,8})\b", re.IGNORECASE)),
    ("pmid", re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{5,8})\b", re.IGNORECASE)),
    ("pmid", re.compile(r"ncbi\.nlm\.nih\.gov/pubmed/(\d{5,8})\b", re.IGNORECASE)),
    ("pmcid", re.compile(r"\b(PMC\d{4,9})\b", re.IGNORECASE)),
    ("doi", re.compile(r"\b(10\.\d{4,9}/[A-Za-z0-9._;()/:<>\[\]-]{1,120})")),
    ("trial", re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)),
    ("trial", re.compile(r"\b(ISRCTN\d{8})\b", re.IGNORECASE)),
    ("trial", re.compile(r"\b(ACTRN\d{14})\b", re.IGNORECASE)),
    ("trial", re.compile(r"\bEudraCT[ \t]{0,4}(?:number|no\.?)?[\s:]{0,6}(\d{4}-\d{6}-\d{2})\b", re.IGNORECASE)),
]


def normal(kind: str, value) -> Optional[str]:
    """One spelling for each identifier, so the same study written two ways is the same study."""
    if value is None:
        return None
    text = str(value).strip()
    if kind == "pmid":
        text = re.sub(r"^pmid\s*:?\s*", "", text, flags=re.IGNORECASE).lstrip("0")
        return text if re.fullmatch(r"\d{1,8}", text) else None
    if kind == "pmcid":
        text = text.upper()
        return text if re.fullmatch(r"PMC\d{1,9}", text) else None
    if kind == "doi":
        text = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.IGNORECASE).lower()
        text = text.rstrip(".,;:")
        while text.endswith((")", "]", ">")) and text.count({")": "(", "]": "[", ">": "<"}[text[-1]]) < text.count(text[-1]):
            text = text[:-1].rstrip(".,;:")
        return text if re.fullmatch(r"10\.\d{4,9}/\S{1,120}", text) else None
    if kind == "trial":
        text = re.sub(r"\s+", "", text.upper())
        return text if re.fullmatch(r"[A-Z0-9-]{8,20}", text) else None
    return None


@dataclass(frozen=True)
class StudyId:
    kind: str
    value: str

    def shown(self) -> str:
        return f"{LABELS[self.kind]} {self.value}"


def find_identifiers(marker_pmids, draft: str) -> List[StudyId]:
    """The marker's list first, then the Draft's in the order written. Each study once."""
    found: List[StudyId] = []
    seen: Set[StudyId] = set()

    def add(kind: str, value) -> None:
        clean = normal(kind, value)
        if clean:
            study = StudyId(kind, clean)
            if study not in seen:
                seen.add(study)
                found.append(study)

    for pmid in marker_pmids or ():
        add("pmid", pmid)
    hits = []
    for kind, pattern in _IN_DRAFT:
        hits.extend((hit.start(1), kind, hit.group(1)) for hit in pattern.finditer(draft or ""))
    for _, kind, value in sorted(hits):
        add(kind, value)
    return found


@dataclass
class Holdings:
    """The identifiers in one evidence file. Nothing else from the file is kept."""

    ids: Dict[str, Set[str]] = field(default_factory=lambda: {kind: set() for kind in KINDS})

    def add(self, kind: str, value) -> None:
        clean = normal(kind, value)
        if clean:
            self.ids[kind].add(clean)

    def has(self, study: StudyId) -> bool:
        return study.value in self.ids[study.kind]


def from_graph(raw: bytes) -> Holdings:
    held = Holdings()
    for study in (json.loads(raw.decode("utf-8")).get("studies") or {}).values():
        held.add("pmid", study.get("pmid"))
        held.add("pmcid", study.get("pmcid"))
        held.add("doi", study.get("doi"))
        id_type = str(study.get("id_type") or "").lower()
        if id_type in ("pmid", "doi"):
            held.add(id_type, study.get("id_value"))
        elif id_type == "nct":
            held.add("trial", study.get("id_value"))
    return held


def from_lines(raw: bytes, inside: Optional[str] = None) -> Holdings:
    """A file with one study a line. `inside` names the part of each line that holds the identifiers, if any."""
    held = Holdings()
    for line in raw.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        row = (row.get(inside) or {}) if inside else row
        held.add("pmid", row.get("pmid"))
        held.add("pmcid", row.get("pmcid"))
        held.add("doi", row.get("doi"))
        held.add("trial", row.get("nct_id"))
    return held


@dataclass
class Lookup:
    source: Optional[str]  # the place used, from the settings; None when none of them is at that commit
    corpus_available: bool
    rows: List[Tuple[StudyId, Optional[bool], Optional[bool]]]  # (study, in the extracted evidence, in the corpus list)
    not_shown: int
    text: str


class EvidenceReader:
    """Looks studies up for one run. Each evidence file is read once, however many commits share it."""

    def __init__(self, source, places, corpus_list: Optional[str]):
        self._source = source
        self._places = tuple(places)
        self._corpus_list = corpus_list
        self._held: Dict[str, Holdings] = {}

    def _holdings(self, context, path: str, parse) -> Holdings:
        blob = context.paths[path]["sha"]  # the same file at two commits has the same blob, and is read once
        if blob not in self._held:
            try:
                self._held[blob] = parse(self._source.file_at(context.commit, path))
            except GitHubError:
                raise ContextUnavailable("evidence_unreadable", transient=True)
            except (ValueError, UnicodeDecodeError, AttributeError):
                raise ContextUnavailable("evidence_not_in_the_expected_form")
        return self._held[blob]

    def lookup(self, marker_pmids, draft: str, context) -> Lookup:
        studies = find_identifiers(marker_pmids, draft)
        place, path = None, None
        for candidate in self._places:
            path, _ = resolve(candidate.file, context.paths)
            if path is not None:
                place = candidate
                break
        corpus_path = self._corpus_list if self._corpus_list in context.paths else None

        rows: List[Tuple[StudyId, Optional[bool], Optional[bool]]] = []
        if studies:
            held = per_study = None
            if place is not None and place.per_study_folder:
                # One file per study, named by its PubMed number: the file list answers for those without a download.
                shape = re.compile("^" + re.escape(place.per_study_folder) + r"/(\d{1,8})\.json$")
                per_study = {normal("pmid", hit.group(1)) for hit in map(shape.match, context.paths) if hit}
                if any(study.kind != "pmid" for study in studies):
                    held = self._holdings(context, path, lambda raw: from_lines(raw, "study_metadata"))
            elif place is not None:
                held = self._holdings(context, path, from_graph)
            corpus = self._holdings(context, corpus_path, from_lines) if corpus_path else None
            for study in studies:
                if place is None:
                    extracted = None
                elif per_study is not None and study.kind == "pmid":
                    extracted = study.value in per_study
                else:
                    extracted = held.has(study)
                rows.append((study, extracted, corpus.has(study) if corpus else None))

        shown, not_shown = rows[:MAX_SHOWN], max(0, len(rows) - MAX_SHOWN)
        lines = []
        if not rows:
            lines.append("The proposal names no study by a PubMed, PMC, DOI or trial-registry identifier.")
        else:
            where = "at commit " + context.commit[:12]
            lines.append(f"Studies named in the proposal, looked up in the project's files {where}. This list was made by a script, not by a reviewer.")
            if place is None:
                lines.append("The project's extracted evidence was not found at that commit, so that part could not be checked.")
            if corpus_path is None:
                lines.append("The wider corpus list was not at that commit, so that part could not be checked.")
            for study, extracted, in_corpus in shown:
                parts = []
                if extracted is not None:
                    parts.append("already in the extracted evidence" if extracted else "not in the extracted evidence")
                if in_corpus is not None:
                    parts.append("in the wider corpus list" if in_corpus else "not in the wider corpus list")
                lines.append(f"- {study.shown()}: " + ("; ".join(parts) if parts else "not checked"))
            if not_shown:
                lines.append(f"- and {not_shown} more, not shown")
        return Lookup(place.name if place else None, corpus_path is not None, rows, not_shown, "\n".join(lines))
