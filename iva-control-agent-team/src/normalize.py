"""Modello dati e funzioni di normalizzazione comuni a tutti gli agenti.

Regola fondamentale: nessun dato inventato. Le funzioni di questo modulo
restituiscono ``None`` quando il valore non e' interpretabile, mai un default
silenzioso (0.0, data odierna, ecc.).
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Costanti di dominio
# ---------------------------------------------------------------------------

CARTELLE_ATTESE = [
    "01_xml_fatture",
    "02_registri_iva",
    "03_corrispettivi",
    "04_prima_nota",
    "05_bozza_liquidazione",
    "06_crediti_precedenti_f24_lipe",
]

CARTELLA_OUTPUT = "99_output_controllo"

#: Cartelle senza le quali il controllo non e' completabile.
FONTI_ESSENZIALI = ["05_bozza_liquidazione", "02_registri_iva"]

GRAVITA_BLOCCANTE = "Bloccante"
GRAVITA_VERIFICA = "Da verificare"
GRAVITA_INFO = "Informativa"

ESITO_OK = "OK"
ESITO_VERIFICA = "DA VERIFICARE"
ESITO_SOSPENDERE = "DA SOSPENDERE"

#: Ordinamento di severita' crescente degli esiti.
_ORDINE_ESITO = {ESITO_OK: 0, ESITO_VERIFICA: 1, ESITO_SOSPENDERE: 2}

IMPATTO_NON_DETERMINABILE = "non determinabile"

# Tolleranze (cfr. config/claude-code-agent-teams.json)
TOLL_RIGA = 0.01
TOLL_DOCUMENTO = 1.00
DIFF_RILEVANTE_EUR = 50.00
DIFF_RILEVANTE_PERC = 1.0
SOGLIA_MINIMA_VERSAMENTO = 25.82

TIPI_DOCUMENTO_REVERSE = {"TD16", "TD17", "TD18", "TD19", "TD28"}
TIPI_DOCUMENTO_NOTA_CREDITO = {"TD04"}


# ---------------------------------------------------------------------------
# Strutture dati
# ---------------------------------------------------------------------------

@dataclass
class Fonte:
    """Riferimento puntuale al documento di origine di un dato."""

    file: str = ""
    foglio: str = ""
    riga: int | None = None
    pagina: int | None = None
    numero_documento: str = ""

    def testo(self) -> str:
        parti = [self.file] if self.file else []
        if self.foglio:
            parti.append(f"foglio {self.foglio}")
        if self.pagina is not None:
            parti.append(f"pag. {self.pagina}")
        if self.riga is not None:
            parti.append(f"riga {self.riga}")
        if self.numero_documento:
            parti.append(f"doc. {self.numero_documento}")
        return " — ".join(parti) if parti else "fonte non indicata"


@dataclass
class Riga:
    """Riga normalizzata di riepilogo IVA, comune a XML, registri e corrispettivi."""

    origine: str = ""                 # xml_attiva | registro_vendite | corrispettivi | ...
    tipo_documento: str = ""          # TD01, TD04, ...
    numero: str = ""
    data: str = ""                    # ISO YYYY-MM-DD (data documento)
    data_registrazione: str = ""
    data_ricezione: str = ""          # data di consegna SDI, se nota
    controparte: str = ""
    piva: str = ""
    cf: str = ""
    imponibile: float | None = None
    aliquota: float | None = None
    imposta: float | None = None
    natura: str = ""
    esigibilita: str = ""             # I immediata, D differita, S scissione pagamenti
    split_payment: bool = False
    reverse_charge: bool = False
    indetraibile: float | None = None  # quota di imposta indetraibile, se esposta
    totale_documento: float | None = None
    sezionale: str = ""
    descrizione: str = ""
    competenza: str = ""              # periodo di liquidazione di imputazione
    fonte: Fonte = field(default_factory=Fonte)
    note: str = ""

    # -- chiavi di confronto -------------------------------------------------
    @property
    def chiave_numero(self) -> str:
        return normalizza_numero_documento(self.numero)

    @property
    def chiave_controparte(self) -> str:
        return normalizza_piva(self.piva) or normalizza_cf(self.cf)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        f = d.pop("fonte")
        d["fonte_file"] = f["file"]
        d["fonte_foglio"] = f["foglio"]
        d["fonte_riga"] = f["riga"]
        d["fonte_pagina"] = f["pagina"]
        d["fonte_numero_documento"] = f["numero_documento"]
        return d


@dataclass
class Anomalia:
    """Rilievo secondo lo schema obbligatorio del team."""

    codice: str
    gravita: str
    fonte: str
    descrizione: str
    impatto_iva: str = IMPATTO_NON_DETERMINABILE
    azione: str = "verifica manuale"
    stato: str = "Aperto"
    agente: str = ""

    def __post_init__(self) -> None:
        if self.gravita not in (GRAVITA_BLOCCANTE, GRAVITA_VERIFICA, GRAVITA_INFO):
            raise ValueError(f"gravita non ammessa: {self.gravita!r}")
        if not self.fonte.strip():
            # Il Quality Reviewer scarta i rilievi senza fonte: intercettiamolo prima.
            raise ValueError(f"anomalia {self.codice} priva di fonte")

    def as_dict(self) -> dict[str, Any]:
        return {
            "Codice": self.codice,
            "Gravita": self.gravita,
            "Fonte": self.fonte,
            "Descrizione": self.descrizione,
            "Impatto IVA": self.impatto_iva,
            "Azione proposta": self.azione,
            "Stato": self.stato,
            "Agente": self.agente,
            "Note studio": "",
        }


# ---------------------------------------------------------------------------
# Normalizzazione dei valori
# ---------------------------------------------------------------------------

_RE_NUM_PULIZIA = re.compile(r"[^\d,.\-+()]")


def parse_importo(valore: Any) -> float | None:
    """Converte un importo in float, gestendo i formati italiani.

    Riconosce ``1.234,56``, ``1234.56``, ``(1.234,56)`` e ``1.234,56-`` come
    negativi. Restituisce ``None`` se il valore non e' interpretabile.
    """
    if valore is None:
        return None
    if isinstance(valore, bool):
        return None
    if isinstance(valore, (int, float)):
        return round(float(valore), 2)

    testo = str(valore).strip()
    if not testo:
        return None

    negativo = False
    if testo.startswith("(") and testo.endswith(")"):
        negativo, testo = True, testo[1:-1]
    if testo.endswith("-"):
        negativo, testo = True, testo[:-1]
    if testo.endswith(("D", "DARE")):  # notazione di alcuni gestionali
        testo = testo.rstrip("DARE").strip()
    if testo.upper().endswith("A") and testo.upper().endswith("AVERE"):
        testo = testo[:-5].strip()

    testo = _RE_NUM_PULIZIA.sub("", testo).replace("+", "")
    if not testo or testo in {"-", ".", ","}:
        return None

    if "," in testo and "." in testo:
        # L'ultimo separatore che compare e' quello decimale.
        if testo.rfind(",") > testo.rfind("."):
            testo = testo.replace(".", "").replace(",", ".")
        else:
            testo = testo.replace(",", "")
    elif "," in testo:
        testo = testo.replace(",", ".")
    elif testo.count(".") > 1:
        testo = testo.replace(".", "")
    elif "." in testo:
        intero, _, decimali = testo.partition(".")
        if len(decimali) == 3 and len(intero) <= 3:
            # 1.234 -> separatore migliaia, non decimale
            testo = intero + decimali

    try:
        numero = float(testo)
    except ValueError:
        return None
    if negativo:
        numero = -abs(numero)
    return round(numero, 2)


_FORMATI_DATA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
                 "%d/%m/%y", "%Y%m%d", "%d %m %Y")


def parse_data(valore: Any) -> str | None:
    """Converte una data in stringa ISO ``YYYY-MM-DD`` o restituisce ``None``."""
    if valore is None:
        return None
    if isinstance(valore, _dt.datetime):
        return valore.date().isoformat()
    if isinstance(valore, _dt.date):
        return valore.isoformat()
    if isinstance(valore, (int, float)) and not isinstance(valore, bool):
        # Seriale Excel (epoca 1899-12-30).
        try:
            base = _dt.date(1899, 12, 30)
            return (base + _dt.timedelta(days=int(valore))).isoformat()
        except (OverflowError, ValueError):
            return None

    testo = str(valore).strip()
    if not testo:
        return None
    testo = testo.split("T")[0].split(" ")[0] if "T" in testo else testo.strip()
    for fmt in _FORMATI_DATA:
        try:
            return _dt.datetime.strptime(testo, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_aliquota(valore: Any) -> float | None:
    """Restituisce l'aliquota come percentuale numerica (22.0, 10.0, 0.0)."""
    numero = parse_importo(valore)
    if numero is None:
        return None
    if 0 < numero < 1:  # 0,22 espresso come frazione
        numero *= 100
    return round(numero, 2)


_RE_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalizza_piva(valore: Any) -> str:
    """Chiave di confronto per la partita IVA (senza prefisso nazionale ``IT``)."""
    if valore is None:
        return ""
    testo = _RE_NON_ALNUM.sub("", str(valore).upper())
    if testo.startswith("IT") and len(testo) > 11:
        testo = testo[2:]
    return testo


def normalizza_cf(valore: Any) -> str:
    if valore is None:
        return ""
    return _RE_NON_ALNUM.sub("", str(valore).upper())


_RE_BLOCCHI = re.compile(r"[A-Z]+|\d+")
_SUFFISSI_SEZIONALE = ("FPA", "FPR", "FE", "PA")


def normalizza_numero_documento(valore: Any) -> str:
    """Chiave di confronto tollerante per i numeri documento.

    Rimuove separatori e zeri iniziali dei blocchi numerici, cosi' che
    ``0001/A``, ``1/A`` e ``1 - A`` producano la stessa chiave.
    """
    if valore is None:
        return ""
    testo = str(valore).upper()
    testo = unicodedata.normalize("NFKD", testo)
    testo = _RE_NON_ALNUM.sub("", testo)
    if not testo:
        return ""
    blocchi = []
    for blocco in _RE_BLOCCHI.findall(testo):
        if blocco.isdigit():
            blocchi.append(blocco.lstrip("0") or "0")
        elif blocco in _SUFFISSI_SEZIONALE:
            continue  # marcatore di sezionale, non parte del numero
        else:
            blocchi.append(blocco)
    return "".join(blocchi)


def normalizza_natura(valore: Any) -> str:
    """Codice natura SDI in forma canonica (``N3.5``, ``N6.1``, ...)."""
    if valore is None:
        return ""
    testo = re.sub(r"[^A-Z0-9.]", "", str(valore).upper())
    if testo and not testo.startswith("N"):
        return ""
    return testo


def arrotonda(valore: float | None, decimali: int = 2) -> float | None:
    return None if valore is None else round(float(valore), decimali)


def euro(valore: float | None) -> str:
    """Formatta un importo in stile italiano (``1.234,56``)."""
    if valore is None:
        return "n.d."
    testo = f"{valore:,.2f}"
    return testo.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def quasi_uguali(a: float | None, b: float | None, tolleranza: float = TOLL_RIGA) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tolleranza + 1e-9


def somma(valori: Iterable[float | None]) -> float:
    """Somma ignorando i ``None`` (che restano tracciati a monte come dati mancanti)."""
    return round(sum(v for v in valori if v is not None), 2)


def peggior_esito(*esiti: str) -> str:
    """Restituisce l'esito piu' severo fra quelli passati."""
    validi = [e for e in esiti if e in _ORDINE_ESITO]
    if not validi:
        return ESITO_SOSPENDERE
    return max(validi, key=lambda e: _ORDINE_ESITO[e])


def periodo_di(data_iso: str | None) -> str:
    """Estrae ``YYYY-MM`` da una data ISO."""
    if not data_iso or len(data_iso) < 7:
        return ""
    return data_iso[:7]


def mesi_del_periodo(anno: str, periodo: str) -> list[str]:
    """Mesi coperti da un periodo espresso come ``01``..``12`` o ``T1``..``T4``.

    Restituisce una lista di ``YYYY-MM``; lista vuota se il periodo non e'
    riconosciuto (il chiamante deve trattarlo come "periodicita' non determinata").
    """
    testo = str(periodo).strip().upper()
    try:
        anno_i = int(anno)
    except (TypeError, ValueError):
        return []
    trimestre = re.fullmatch(r"(?:T|Q|TRIM)?([1-4])(?:T)?", testo)
    if testo.startswith(("T", "Q")) and trimestre:
        primo = (int(trimestre.group(1)) - 1) * 3 + 1
        return [f"{anno_i:04d}-{m:02d}" for m in range(primo, primo + 3)]
    if testo.isdigit() and 1 <= int(testo) <= 12:
        return [f"{anno_i:04d}-{int(testo):02d}"]
    return []
