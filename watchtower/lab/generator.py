"""Synthetic tenders with exact ground truth. Deterministic: same slug → same data."""

import hashlib
import io
import random
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import ContractNature, Document, Place, ProcedureType, Tender, TenderStatus

BERLIN = ZoneInfo("Europe/Berlin")
N_TENDERS = 23

TITLES = [
    "Neubau einer Kindertagesstätte", "Sanierung des Hallenbads", "IT-Dienstleistungen für das Bürgeramt",
    "Lieferung von Feuerwehrfahrzeugen", "Straßenbeleuchtung LED-Umrüstung", "Reinigung städtischer Schulen",
    "Planungsleistungen Radwegenetz", "Lieferung von Laptops für Schulen", "Winterdienst 2026/2027",
    "Erneuerung der Heizungsanlage Rathaus", "Rechenzentrumsbetrieb", "Grünflächenpflege Stadtpark",
    "Brückensanierung Kanalstraße", "Beschaffung von Büromöbeln", "Gebäudeautomation Klinikum",
    "Abfallentsorgung Gewerbegebiet", "Glasfaserausbau Ortsteil Nord", "Schulbuslinienverkehr",
    "Wartung Aufzugsanlagen", "Catering Kantine Landratsamt", "Dachsanierung Turnhalle",
    "Softwarelizenzen Fachverfahren", "Sicherheitsdienst Flüchtlingsunterkunft",
]
BUYERS = ["Stadt Aachen", "Landkreis Düren", "Stadtwerke Köln GmbH", "Universitätsklinikum Bonn",
          "Gemeinde Simmerath", "Land Nordrhein-Westfalen", "Kreis Heinsberg"]
PLACES = [("52062", "Aachen"), ("52349", "Düren"), ("50667", "Köln"), ("53127", "Bonn"), ("52152", "Simmerath")]
CPV = {ContractNature.works: ["45000000", "45210000", "45453000"],
       ContractNature.services: ["72000000", "90910000", "71240000", "55520000"],
       ContractNature.supplies: ["30213100", "34144210", "39130000"]}
PROC_LABEL = {ProcedureType.open: "Offenes Verfahren", ProcedureType.restricted: "Nicht offenes Verfahren",
              ProcedureType.negotiated: "Verhandlungsverfahren", ProcedureType.other: "Innovationspartnerschaft"}
NATURE_LABEL = {ContractNature.works: "Bauleistung", ContractNature.services: "Dienstleistung",
                ContractNature.supplies: "Lieferleistung"}
TODAY = date(2026, 11, 20)


@dataclass
class LabDoc:
    file_name: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass
class LabTender:
    id: str
    reference: str
    title: str
    buyer: str
    published: date
    deadline: datetime
    procedure: ProcedureType
    nature: ContractNature
    cpv: list[str]
    place: tuple[str, str]
    description: str | None
    docs: list[LabDoc] = field(default_factory=list)


def _pdf(text: str) -> bytes:
    return f"%PDF-1.4\n% {text}\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n".encode()


def make_tenders(slug: str) -> list[LabTender]:
    rng = random.Random(f"watchtower-{slug}")
    out = []
    for i in range(N_TENDERS):
        nature = rng.choice(list(ContractNature))
        tid = f"LAB-{i + 1:04d}"
        published = TODAY - timedelta(days=rng.randint(1, 30))
        deadline = datetime(2026, 12, 1, rng.choice([10, 12, 14]), 0, tzinfo=BERLIN) + timedelta(days=rng.randint(0, 60))
        docs = [LabDoc(f"Leistungsbeschreibung_{tid}.pdf", _pdf(f"{slug} {tid} Leistungsbeschreibung"))]
        if i % 2 == 0:
            docs.append(LabDoc(f"Vertragsbedingungen_{tid}.pdf", _pdf(f"{slug} {tid} Vertrag")))
        out.append(LabTender(
            id=tid, reference=f"VgV-{2026}-{rng.randint(100, 999)}-{i + 1}", title=TITLES[i % len(TITLES)],
            buyer=rng.choice(BUYERS), published=published, deadline=deadline,
            procedure=rng.choice(list(ProcedureType)), nature=nature,
            cpv=[] if i % 7 == 3 else rng.sample(CPV[nature], k=min(2, len(CPV[nature]))),
            place=rng.choice(PLACES),
            description=None if i % 5 == 2 else f"{TITLES[i % len(TITLES)]} gemäß beigefügter Leistungsbeschreibung.",
            docs=docs))
    return out


def zip_bytes(t: LabTender) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for d in t.docs:
            z.writestr(d.file_name, d.content)
    return buf.getvalue()


def truth(slug: str, base: str, state=None) -> list[Tender]:
    """What a perfect system returns for this lab portal (documents without API path)."""
    if state is None:
        from .app import STATE

        state = STATE
    out = []
    for t in make_tenders(slug):
        status = TenderStatus.open
        deadline = state.deadline_for(slug, t)
        if t.id in state.withdrawn.get(slug, set()):
            status = TenderStatus.withdrawn
        elif deadline < datetime.now(BERLIN):
            status = TenderStatus.closed
        kw = {}
        if t.description:
            kw["description"] = t.description
        if t.cpv:
            kw["cpvCodes"] = t.cpv
        out.append(Tender(
            id=t.id, url=f"{base}/lab/{slug}/tender/{t.id}", title=t.title, buyerName=t.buyer,
            publicationDate=t.published, submissionDeadline=deadline, status=status,
            documents=[Document(fileName=d.file_name, sizeBytes=len(d.content), sha256=d.sha256, path="")
                       for d in t.docs],
            lastChangedAt=datetime(2026, 11, 20, tzinfo=BERLIN), referenceNumber=t.reference,
            procedureType=t.procedure, contractNature=t.nature,
            placeOfPerformance=Place(postalCode=t.place[0], city=t.place[1], countryCode="DE"), **kw))
    return out
